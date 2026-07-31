"""Secure, explicit updates from the project's official GitHub releases."""

from __future__ import annotations

import base64
import csv
import configparser
import hashlib
import importlib.metadata
import json
import os
import re
import secrets
import site
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Literal, Mapping, Sequence
from urllib.parse import urlparse

from . import __version__


PROJECT_OWNER = "Miracle-0v0"
PROJECT_REPOSITORY = "ielts-codex-cli"
PROJECT_RELEASES_API = (
    f"https://api.github.com/repos/{PROJECT_OWNER}/{PROJECT_REPOSITORY}/"
    "releases?per_page=100"
)
PROJECT_GITHUB_URL = (
    f"https://github.com/{PROJECT_OWNER}/{PROJECT_REPOSITORY}"
)
PROJECT_REMOTE_URLS = {
    f"{PROJECT_GITHUB_URL}.git",
    PROJECT_GITHUB_URL,
}
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_WHEEL_BYTES = 16 * 1024 * 1024
MAX_WHEEL_MEMBERS = 512
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_METADATA_SECONDS = 60.0
MAX_DOWNLOAD_SECONDS = 180.0
VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)$"
)
TAG_RE = re.compile(
    r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*))$"
)
SHA256_RE = re.compile(r"^sha256:(?P<digest>[0-9a-f]{64})$", re.IGNORECASE)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_VERSION_RE = re.compile(r"\bgit version (\d+)\.(\d+)")
PYTHON_REQUIREMENT_RE = re.compile(r"^>=\s*(\d+)\.(\d+)$")
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}
RUNTIME_PACKAGE_PATHS = frozenset(
    {
        "ielts_codex/__init__.py",
        "ielts_codex/__main__.py",
        "ielts_codex/cli.py",
        "ielts_codex/game_engine.py",
        "ielts_codex/game_mode.py",
        "ielts_codex/models.py",
        "ielts_codex/oewn.py",
        "ielts_codex/pet_api.py",
        "ielts_codex/pixel_art.py",
        "ielts_codex/scheduler.py",
        "ielts_codex/storage.py",
        "ielts_codex/ui.py",
        "ielts_codex/updater.py",
        "ielts_codex/word_bank.py",
        "ielts_codex/data/__init__.py",
        "ielts_codex/data/words.json",
    }
)


class ProjectUpdateError(RuntimeError):
    """Raised when a project update cannot be completed safely."""


@dataclass(frozen=True, slots=True)
class ProjectRelease:
    version: str
    version_tuple: tuple[int, int, int]
    tag_name: str
    html_url: str
    published_at: str
    wheel_name: str | None = None
    wheel_url: str | None = None
    wheel_size: int | None = None
    wheel_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class InstallTarget:
    kind: Literal["source", "pip", "unsupported"]
    root: Path | None
    detail: str


@dataclass(frozen=True, slots=True)
class ProjectUpdateResult:
    status: Literal[
        "updated",
        "up_to_date",
        "ahead",
        "available",
        "unsupported",
    ]
    current_version: str
    latest_version: str
    install_kind: str
    restart_required: bool = False
    message: str = ""
    release_url: str = ""


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse the stable three-component versions used by this project."""

    match = VERSION_RE.fullmatch(value.strip())
    if match is None:
        raise ProjectUpdateError(f"Unsupported project version: {value!r}.")
    return tuple(
        int(match.group(name)) for name in ("major", "minor", "patch")
    )


def _project_toml_value(text: str, key: str) -> str | None:
    """Read one simple scalar from the TOML ``[project]`` table."""

    in_project = False
    key_pattern = re.compile(
        rf"^{re.escape(key)}\s*=\s*(?P<value>.+?)\s*$"
    )
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if not in_project or not line or line.startswith("#"):
            continue
        match = key_pattern.fullmatch(line)
        if match is not None:
            return match.group("value")
    return None


def _toml_string(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.fullmatch(r"""["']([^"'\\\r\n]*)["']""", value)
    return match.group(1) if match else None


def parse_release_metadata(payload: Mapping[str, Any]) -> ProjectRelease:
    """Validate a stable release and its optional pure-Python wheel."""

    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ProjectUpdateError("GitHub did not return a stable project release.")

    tag_name = str(payload.get("tag_name", "")).strip()
    tag_match = TAG_RE.fullmatch(tag_name)
    published_at = str(payload.get("published_at", "")).strip()
    html_url = str(payload.get("html_url", "")).strip()
    if tag_match is None or not published_at:
        raise ProjectUpdateError("Project release metadata is incomplete.")

    version = tag_match.group("version")
    expected_html_url = f"{PROJECT_GITHUB_URL}/releases/tag/{tag_name}"
    if html_url != expected_html_url:
        raise ProjectUpdateError("Unexpected project release URL.")

    assets_value = payload.get("assets", [])
    if not isinstance(assets_value, list):
        raise ProjectUpdateError("Project release assets metadata is invalid.")
    expected_wheel_name = f"ielts_codex-{version}-py3-none-any.whl"
    wheel_assets = [
        asset
        for asset in assets_value
        if isinstance(asset, Mapping)
        and str(asset.get("name", "")) == expected_wheel_name
    ]
    if len(wheel_assets) > 1:
        raise ProjectUpdateError("The project release contains duplicate wheels.")
    if not wheel_assets:
        return ProjectRelease(
            version=version,
            version_tuple=parse_version(version),
            tag_name=tag_name,
            html_url=html_url,
            published_at=published_at,
        )

    asset = wheel_assets[0]
    expected_wheel_url = (
        f"{PROJECT_GITHUB_URL}/releases/download/{tag_name}/"
        f"{expected_wheel_name}"
    )
    wheel_url = str(asset.get("browser_download_url", "")).strip()
    if wheel_url != expected_wheel_url:
        raise ProjectUpdateError("Unexpected project wheel download URL.")
    wheel_size = asset.get("size")
    if (
        isinstance(wheel_size, bool)
        or not isinstance(wheel_size, int)
        or wheel_size <= 0
        or wheel_size > MAX_WHEEL_BYTES
    ):
        raise ProjectUpdateError("Project wheel size metadata is invalid.")
    digest_match = SHA256_RE.fullmatch(str(asset.get("digest", "")).strip())
    if digest_match is None:
        raise ProjectUpdateError(
            "Project wheel is missing its GitHub SHA-256 digest."
        )

    return ProjectRelease(
        version=version,
        version_tuple=parse_version(version),
        tag_name=tag_name,
        html_url=html_url,
        published_at=published_at,
        wheel_name=expected_wheel_name,
        wheel_url=wheel_url,
        wheel_size=wheel_size,
        wheel_sha256=digest_match.group("digest").lower(),
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class _TrustedDownloadRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_download_url(value: str) -> None:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ProjectUpdateError("GitHub returned an invalid wheel URL.") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ProjectUpdateError("GitHub redirected the wheel to an unsafe URL.")


class ProjectUpdater:
    """Check and apply a newer stable project release on explicit request."""

    def __init__(
        self,
        *,
        current_version: str = __version__,
        timeout: float = 30.0,
        opener: Callable[..., Any] | None = None,
        download_opener: Callable[..., Any] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        module_file: Path | str | None = None,
        executable: Path | str | None = None,
        distribution_getter: Callable[[str], Any] | None = None,
    ) -> None:
        self.current_version = current_version
        self.timeout = timeout
        self._opener = opener or urllib.request.build_opener(
            _NoRedirectHandler()
        ).open
        self._download_opener = download_opener or urllib.request.build_opener(
            _TrustedDownloadRedirectHandler()
        ).open
        self._runner = runner or subprocess.run
        self.module_file = Path(module_file or __file__).resolve()
        self.executable = str(executable or sys.executable)
        self._distribution_getter = (
            distribution_getter or importlib.metadata.distribution
        )
        self.user_agent = (
            f"ielts-codex/{current_version} (+{PROJECT_GITHUB_URL})"
        )

    def discover_latest(self) -> ProjectRelease:
        request = urllib.request.Request(
            PROJECT_RELEASES_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": self.user_agent,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = self._read_limited(
                    response,
                    MAX_METADATA_BYTES,
                    deadline=time.monotonic() + MAX_METADATA_SECONDS,
                )
        except ProjectUpdateError:
            raise
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise ProjectUpdateError(
                f"Cannot check the latest IELTS Codex release: {exc}"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectUpdateError(
                "GitHub returned invalid project release metadata."
            ) from exc
        if not isinstance(payload, list):
            raise ProjectUpdateError(
                "GitHub returned unexpected project release metadata."
            )
        versioned_payloads: list[
            tuple[tuple[int, int, int], Mapping[str, Any]]
        ] = []
        for item in payload:
            if (
                not isinstance(item, dict)
                or item.get("draft") is not False
                or item.get("prerelease") is not False
            ):
                continue
            tag_match = TAG_RE.fullmatch(
                str(item.get("tag_name", "")).strip()
            )
            if tag_match is None:
                continue
            versioned_payloads.append(
                (parse_version(tag_match.group("version")), item)
            )
        if not versioned_payloads:
            raise ProjectUpdateError(
                "GitHub did not return a stable project release."
            )
        _version, latest_payload = max(
            versioned_payloads,
            key=lambda item: item[0],
        )
        return parse_release_metadata(latest_payload)

    def detect_install(self) -> InstallTarget:
        source_root = self._source_root()
        if source_root is not None:
            return InstallTarget(
                "source",
                source_root,
                f"Git source checkout: {source_root}",
            )

        try:
            distribution = self._distribution_getter("ielts-codex")
        except importlib.metadata.PackageNotFoundError:
            return InstallTarget(
                "unsupported",
                None,
                "unmanaged source copy",
            )
        except (OSError, ValueError) as exc:
            return InstallTarget(
                "unsupported",
                None,
                f"unreadable package metadata: {exc}",
            )

        try:
            distribution_name = str(
                distribution.metadata.get("Name", "")
            ).strip().lower()
            installed_version = str(distribution.version).strip()
            installer = str(
                distribution.read_text("INSTALLER") or ""
            ).strip().lower()
            files = distribution.files
            direct_url_text = distribution.read_text("direct_url.json")
        except (AttributeError, OSError, UnicodeError, ValueError) as exc:
            return InstallTarget(
                "unsupported",
                None,
                f"unreadable installation metadata: {exc}",
            )
        if (
            distribution_name != "ielts-codex"
            or installed_version != self.current_version
        ):
            return InstallTarget(
                "unsupported",
                None,
                "installed package identity does not match the running version",
            )
        if installer != "pip":
            return InstallTarget(
                "unsupported",
                None,
                f"package is managed by {installer or 'an unknown installer'}",
            )
        if not files:
            return InstallTarget(
                "unsupported",
                None,
                "package metadata has no owned-file manifest",
            )
        if direct_url_text:
            try:
                direct_url = json.loads(direct_url_text)
            except json.JSONDecodeError:
                return InstallTarget(
                    "unsupported",
                    None,
                    "installation origin metadata is invalid",
                )
            if not isinstance(direct_url, dict):
                return InstallTarget(
                    "unsupported",
                    None,
                    "installation origin metadata is invalid",
                )
            if (
                isinstance(direct_url.get("dir_info"), dict)
                and direct_url["dir_info"].get("editable") is True
            ):
                return InstallTarget(
                    "unsupported",
                    None,
                    "editable install outside a supported Git checkout",
                )

        try:
            updater_entries = [
                entry
                for entry in files
                if str(entry).replace("\\", "/")
                == "ielts_codex/updater.py"
            ]
            owned_updaters = [
                Path(distribution.locate_file(entry)).resolve()
                for entry in updater_entries
            ]
        except (AttributeError, OSError, ValueError):
            return InstallTarget(
                "unsupported",
                None,
                "package metadata does not own the running module",
            )
        if self.module_file not in owned_updaters:
            return InstallTarget(
                "unsupported",
                None,
                "package metadata does not own the running updater module",
            )
        package_root = self.module_file.parent.parent
        if package_root not in self._supported_pip_roots():
            return InstallTarget(
                "unsupported",
                None,
                "custom pip target cannot be updated in place safely",
            )
        if os.name == "nt":
            return InstallTarget(
                "unsupported",
                None,
                "Windows pip updates must run after the CLI has exited",
            )
        scheme_variables = (
            "PYTHONHOME",
            "PYTHONPLATLIBDIR",
            "PYTHONUSERBASE",
            "PYTHONEXECUTABLE",
        )
        configured_variables = [
            name for name in scheme_variables if os.environ.get(name)
        ]
        if configured_variables:
            return InstallTarget(
                "unsupported",
                None,
                "Python installation scheme is customized by "
                + ", ".join(configured_variables),
            )
        user_site = self._user_site_root()
        scripts_root = self._pip_scripts_root(
            user_install=user_site is not None and package_root == user_site
        )
        if scripts_root is None:
            return InstallTarget(
                "unsupported",
                None,
                "cannot identify the pip scripts directory",
            )
        if not all(
            self._directory_target_is_writable(path)
            for path in (package_root, scripts_root)
        ):
            return InstallTarget(
                "unsupported",
                None,
                "pip package or scripts directory is not writable",
            )
        return InstallTarget(
            "pip",
            package_root,
            f"Python package: {package_root}",
        )

    def update(self, *, dry_run: bool = False) -> ProjectUpdateResult:
        current_tuple = parse_version(self.current_version)
        release = self.discover_latest()
        target = self.detect_install()
        common = {
            "current_version": self.current_version,
            "latest_version": release.version,
            "install_kind": target.kind,
            "release_url": release.html_url,
        }

        if release.version_tuple < current_tuple:
            return ProjectUpdateResult(
                "ahead",
                message="The local version is newer than the latest stable release.",
                **common,
            )
        if release.version_tuple == current_tuple:
            return ProjectUpdateResult(
                "up_to_date",
                message="IELTS Codex is already on the latest stable release.",
                **common,
            )
        if target.kind == "unsupported":
            return ProjectUpdateResult(
                "unsupported",
                message=(
                    "Automatic updates are unavailable for this installation: "
                    f"{target.detail}."
                ),
                **common,
            )
        if dry_run:
            return ProjectUpdateResult(
                "available",
                message="A newer stable release is available; no files were changed.",
                **common,
            )

        try:
            if target.kind == "source" and target.root is not None:
                self._update_source(target.root, release)
            elif target.kind == "pip" and target.root is not None:
                self._update_pip(release, target)
            else:  # Defensive guard for future target kinds.
                raise ProjectUpdateError("Unsupported project installation.")
        except ProjectUpdateError:
            raise
        except OSError as exc:
            raise ProjectUpdateError(
                f"The project update could not access the filesystem: {exc}"
            ) from exc
        return ProjectUpdateResult(
            "updated",
            restart_required=True,
            message="The new release was installed successfully.",
            **common,
        )

    def _source_root(self) -> Path | None:
        try:
            root = self.module_file.parents[2]
        except IndexError:
            return None
        expected_module = root / "src" / "ielts_codex" / self.module_file.name
        if (
            self.module_file != expected_module.resolve()
            or not (root / ".git").exists()
            or not (root / "pyproject.toml").is_file()
            or not (root / "ielts.py").is_file()
        ):
            return None
        result = self._git_process(
            root,
            ("rev-parse", "--show-toplevel"),
            timeout=15,
        )
        if result.returncode != 0:
            return None
        try:
            discovered = Path(result.stdout.strip()).resolve()
        except OSError:
            return None
        return root if discovered == root else None

    def _update_source(self, root: Path, release: ProjectRelease) -> None:
        branch = self._git_output(root, "branch", "--show-current").strip()
        if branch != "main":
            raise ProjectUpdateError(
                "Source updates require the main branch; the checkout was not changed."
            )
        remote = self._git_output(root, "remote", "get-url", "origin").strip()
        if remote not in PROJECT_REMOTE_URLS:
            raise ProjectUpdateError(
                "Source updates require the official HTTPS origin; "
                "the checkout was not changed."
            )
        self._verify_git_runtime(root)
        self._verify_git_transport(root, remote)
        if self._git_output(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=normal",
        ):
            raise ProjectUpdateError(
                "The Git worktree has local changes; commit or stash them first."
            )
        old_head = self._git_output(root, "rev-parse", "HEAD").strip()
        if COMMIT_RE.fullmatch(old_head) is None:
            raise ProjectUpdateError("Cannot identify the current Git commit.")
        self._verify_source_index(root)

        namespace = (
            "refs/ielts-codex-update/"
            f"{os.getpid()}-{secrets.token_hex(8)}"
        )
        tag_ref = f"{namespace}/tag"
        main_ref = f"{namespace}/main"
        try:
            self._git_run(
                root,
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-write-fetch-head",
                "--no-recurse-submodules",
                "origin",
                f"refs/tags/{release.tag_name}:{tag_ref}",
                timeout=120,
            )
            candidate = self._git_output(
                root, "rev-parse", f"{tag_ref}^{{commit}}"
            ).strip()
            if COMMIT_RE.fullmatch(candidate) is None:
                raise ProjectUpdateError("Cannot identify the release commit.")

            self._git_run(
                root,
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-write-fetch-head",
                "--no-recurse-submodules",
                "origin",
                f"refs/heads/main:{main_ref}",
                timeout=120,
            )
            remote_main = self._git_output(
                root, "rev-parse", f"{main_ref}^{{commit}}"
            ).strip()
            if COMMIT_RE.fullmatch(remote_main) is None:
                raise ProjectUpdateError(
                    "Cannot identify the official main commit."
                )
            self._git_ancestor(
                root,
                candidate,
                remote_main,
                "The release tag is not part of the official main branch.",
            )
            self._git_ancestor(
                root,
                old_head,
                candidate,
                "The source checkout cannot fast-forward to the release.",
            )
            self._verify_source_version(root, candidate, release.version)
            self._verify_no_source_conflicts(root, old_head, candidate)

            current_head = self._git_output(root, "rev-parse", "HEAD").strip()
            current_branch = self._git_output(
                root, "branch", "--show-current"
            ).strip()
            current_remote = self._git_output(
                root, "remote", "get-url", "origin"
            ).strip()
            current_status = self._git_output(
                root,
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            )
            if (
                current_head != old_head
                or current_branch != "main"
                or current_remote != remote
                or current_status
            ):
                raise ProjectUpdateError(
                    "The checkout changed while the release was downloading; "
                    "no merge was attempted."
                )
            self._verify_source_index(root)
            self._verify_no_source_conflicts(root, old_head, candidate)
            self._git_run(
                root,
                "merge",
                "--ff-only",
                candidate,
                timeout=60,
            )
            if self._git_output(root, "rev-parse", "HEAD").strip() != candidate:
                raise ProjectUpdateError(
                    "Git completed without selecting the expected release commit."
                )
            if self._git_output(
                root,
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ):
                raise ProjectUpdateError(
                    "The release commit was selected, but the worktree is not clean."
                )
            self._verify_source_version(root, candidate, release.version)
        finally:
            for temporary_ref in (tag_ref, main_ref):
                self._git_process(
                    root,
                    ("update-ref", "-d", temporary_ref),
                )

    def _verify_source_index(self, root: Path) -> None:
        sparse = self._git_process(
            root,
            ("config", "--bool", "core.sparseCheckout"),
        )
        if sparse.returncode == 0 and sparse.stdout.strip().lower() == "true":
            raise ProjectUpdateError(
                "Source updates do not support sparse checkouts."
            )
        index_lines = self._git_output(root, "ls-files", "-v").splitlines()
        if any(line and line[0] != "H" for line in index_lines):
            raise ProjectUpdateError(
                "Source updates require a normal index without hidden file flags."
            )

    def _verify_git_runtime(self, root: Path) -> None:
        result = self._git_process(root, ("version",))
        match = (
            GIT_VERSION_RE.search(result.stdout)
            if result.returncode == 0
            else None
        )
        if match is None:
            raise ProjectUpdateError(
                "Cannot identify Git; source updates require Git 2.29 or newer."
            )
        version = (int(match.group(1)), int(match.group(2)))
        if version < (2, 29):
            raise ProjectUpdateError(
                "Source updates require Git 2.29 or newer so fetch metadata "
                "can remain untouched."
            )

    def _verify_git_transport(self, root: Path, remote: str) -> None:
        local_ssl_verify = self._git_process(
            root,
            (
                "config",
                "--local",
                "--get-urlmatch",
                "http.sslVerify",
                remote,
            ),
        )
        if local_ssl_verify.returncode not in {0, 1}:
            raise ProjectUpdateError(
                "Cannot inspect the repository Git TLS configuration."
            )
        if (
            local_ssl_verify.returncode == 0
            and local_ssl_verify.stdout.strip().lower() != "true"
        ):
            raise ProjectUpdateError(
                "The repository disables Git TLS verification for its origin."
            )
        local_ssl_ca_info = self._git_process(
            root,
            (
                "config",
                "--local",
                "--get-urlmatch",
                "http.sslCAInfo",
                remote,
            ),
        )
        if local_ssl_ca_info.returncode not in {0, 1}:
            raise ProjectUpdateError(
                "Cannot inspect the repository Git TLS CA configuration."
            )
        if (
            local_ssl_ca_info.returncode == 0
            and local_ssl_ca_info.stdout.strip()
        ):
            raise ProjectUpdateError(
                "Source updates do not accept a repository-specific TLS CA."
            )

        ssl_verify = self._git_process(
            root,
            ("config", "--get-urlmatch", "http.sslVerify", remote),
        )
        if (
            ssl_verify.returncode != 0
            or ssl_verify.stdout.strip().lower() != "true"
        ):
            raise ProjectUpdateError(
                "Git TLS verification is not enabled for the official origin."
            )
        ssl_ca_info = self._git_process(
            root,
            ("config", "--get-urlmatch", "http.sslCAInfo", remote),
        )
        if ssl_ca_info.returncode not in {0, 1}:
            raise ProjectUpdateError(
                "Cannot verify the Git TLS configuration."
            )
        if ssl_ca_info.returncode == 0 and ssl_ca_info.stdout.strip():
            raise ProjectUpdateError(
                "Source updates do not accept a repository-specific TLS CA."
            )

    def _verify_no_source_conflicts(
        self,
        root: Path,
        old_head: str,
        candidate: str,
    ) -> None:
        changed = self._git_output(
            root,
            "diff",
            "--name-only",
            "-z",
            old_head,
            candidate,
        ).split("\0")
        tracked = set(
            self._git_output(root, "ls-files", "-z").split("\0")
        )
        for relative_name in changed:
            if not relative_name or relative_name in tracked:
                continue
            relative = PurePosixPath(relative_name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ProjectUpdateError(
                    "The release contains an unsafe source path."
                )
            local_path = root.joinpath(*relative.parts)
            if local_path.exists() or local_path.is_symlink():
                raise ProjectUpdateError(
                    "An ignored or untracked path would be overwritten: "
                    f"{relative_name}"
                )
            for parent in local_path.parents:
                if parent == root:
                    break
                parent_name = parent.relative_to(root).as_posix()
                if (
                    parent_name not in tracked
                    and (parent.is_file() or parent.is_symlink())
                ):
                    raise ProjectUpdateError(
                        "An ignored or untracked parent path blocks the update: "
                        f"{parent.relative_to(root)}"
                    )

    def _verify_source_version(
        self,
        root: Path,
        commit: str,
        expected_version: str,
    ) -> None:
        required_source_paths = {
            "ielts.py",
            "pyproject.toml",
            *(f"src/{path}" for path in RUNTIME_PACKAGE_PATHS),
        }
        for required_path in sorted(required_source_paths):
            self._git_output(
                root,
                "cat-file",
                "-e",
                f"{commit}:{required_path}",
            )
        init_text = self._git_output(
            root,
            "show",
            f"{commit}:src/ielts_codex/__init__.py",
        )
        init_match = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
            init_text,
            re.MULTILINE,
        )
        pyproject_text = self._git_output(
            root,
            "show",
            f"{commit}:pyproject.toml",
        )
        project_name = _toml_string(
            _project_toml_value(pyproject_text, "name")
        )
        project_version = _toml_string(
            _project_toml_value(pyproject_text, "version")
        )
        python_requirement = _toml_string(
            _project_toml_value(pyproject_text, "requires-python")
        )
        requirement_match = (
            PYTHON_REQUIREMENT_RE.fullmatch(python_requirement)
            if python_requirement is not None
            else None
        )
        dependencies = _project_toml_value(pyproject_text, "dependencies")
        if (
            init_match is None
            or init_match.group(1) != expected_version
            or project_name != "ielts-codex"
            or project_version != expected_version
            or requirement_match is None
        ):
            raise ProjectUpdateError(
                "The release source identity does not match its metadata."
            )
        minimum_python = tuple(
            int(requirement_match.group(index)) for index in (1, 2)
        )
        if sys.version_info[:2] < minimum_python:
            raise ProjectUpdateError(
                "The release requires a newer Python interpreter."
            )
        if dependencies not in {None, "[]"}:
            raise ProjectUpdateError(
                "Automatic source updates require a zero-dependency release."
            )

    def _update_pip(
        self,
        release: ProjectRelease,
        target: InstallTarget,
    ) -> None:
        if not all(
            (
                release.wheel_name,
                release.wheel_url,
                release.wheel_size,
                release.wheel_sha256,
            )
        ):
            raise ProjectUpdateError(
                "The release has no digest-verified pure-Python wheel."
            )
        if target.root is None:
            raise ProjectUpdateError("Cannot identify the pip installation.")
        with self._pip_update_lock(target.root):
            self._install_pip_release(release, target)

    def _install_pip_release(
        self,
        release: ProjectRelease,
        target: InstallTarget,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="ielts-codex-update-") as temporary:
            temporary_root = Path(temporary)
            wheel_path = self._download_wheel(release, temporary_root)
            self._validate_wheel(wheel_path, release)
            stage = temporary_root / "stage"
            stage.mkdir(mode=0o700)
            common = [
                self.executable,
                "-m",
                "pip",
                "--isolated",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--no-deps",
                "--no-index",
                "--no-compile",
                "--no-cache-dir",
            ]
            self._pip_run(
                [
                    *common,
                    "--target",
                    str(stage),
                    str(wheel_path),
                ],
                cwd=temporary_root,
            )
            staged_init = stage / "ielts_codex" / "__init__.py"
            if (
                not staged_init.is_file()
                or self._version_from_init(staged_init) != release.version
            ):
                raise ProjectUpdateError(
                    "The staged wheel does not contain the expected package."
                )
            user_site = self._user_site_root()
            install_scope = (
                ["--user"]
                if user_site is not None and target.root == user_site
                else []
            )
            self._pip_run(
                [*common, *install_scope, "--upgrade", str(wheel_path)],
                cwd=temporary_root,
            )
            marker = f"__IELTS_CODEX_UPDATE_{secrets.token_hex(16)}__"
            verification = self._run_process(
                [
                    self.executable,
                    "-c",
                    (
                        "import importlib.metadata as m,json,ielts_codex,"
                        "ielts_codex.cli as c;"
                        f"print({marker!r}+json.dumps({{"
                        "'metadata':m.version('ielts-codex'),"
                        "'package':ielts_codex.__version__,"
                        "'module':ielts_codex.__file__,"
                        "'cli':c.__file__"
                        "}))"
                    ),
                ],
                cwd=temporary_root,
                env=self._python_environment(),
                timeout=30,
            )
            try:
                payload = next(
                    line[len(marker) :]
                    for line in reversed(verification.stdout.splitlines())
                    if line.startswith(marker)
                )
                verified = json.loads(payload)
                verified_module = Path(verified["module"]).resolve()
                verified_cli = Path(verified["cli"]).resolve()
            except (
                json.JSONDecodeError,
                KeyError,
                StopIteration,
                OSError,
                TypeError,
                ValueError,
            ):
                verified = {}
                verified_module = Path()
                verified_cli = Path()
            expected_init = self.module_file.parent / "__init__.py"
            expected_cli = self.module_file.parent / "cli.py"
            if (
                verification.returncode != 0
                or verified.get("metadata") != release.version
                or verified.get("package") != release.version
                or verified_module != expected_init
                or verified_cli != expected_cli
                or self._version_from_init(expected_init) != release.version
            ):
                raise ProjectUpdateError(
                    "pip finished, but the installed version could not be verified."
                )

    @contextmanager
    def _pip_update_lock(self, target_root: Path) -> Iterator[None]:
        """Prevent concurrent pip processes from mutating one installation."""

        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - pip is refused on Windows.
            raise ProjectUpdateError(
                "This platform does not support safe in-process pip updates."
            ) from exc

        lock_path = target_root / ".ielts-codex-update.lock"
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ProjectUpdateError(
                f"Cannot create the pip update lock: {exc}"
            ) from exc
        try:
            lock_stat = os.fstat(descriptor)
            owner = getattr(os, "geteuid", lambda: lock_stat.st_uid)()
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_uid != owner
                or lock_stat.st_nlink != 1
                or lock_stat.st_mode & 0o077
            ):
                raise ProjectUpdateError(
                    "The pip update lock has unsafe ownership or permissions."
                )
            try:
                fcntl.flock(
                    descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as exc:
                raise ProjectUpdateError(
                    "Another IELTS Codex update is already running."
                ) from exc
            except OSError as exc:
                raise ProjectUpdateError(
                    f"Cannot lock the pip installation: {exc}"
                ) from exc
            try:
                yield
            finally:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            with suppress(OSError):
                os.close(descriptor)

    def _download_wheel(
        self,
        release: ProjectRelease,
        directory: Path,
    ) -> Path:
        assert release.wheel_name is not None
        assert release.wheel_url is not None
        assert release.wheel_size is not None
        assert release.wheel_sha256 is not None
        _validate_download_url(release.wheel_url)
        destination = directory / release.wheel_name
        request = urllib.request.Request(
            release.wheel_url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": self.user_agent,
            },
        )
        digest = hashlib.sha256()
        received = 0
        deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
        descriptor: int | None = None
        try:
            descriptor = os.open(
                destination,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            output = os.fdopen(descriptor, "wb")
            descriptor = None
            with output:
                try:
                    with self._download_opener(
                        request, timeout=self.timeout
                    ) as response:
                        final_url = getattr(response, "geturl", lambda: "")()
                        if final_url:
                            _validate_download_url(final_url)
                        length_value = getattr(response, "headers", {}).get(
                            "Content-Length"
                        )
                        if length_value:
                            try:
                                content_length = int(length_value)
                            except (TypeError, ValueError) as exc:
                                raise ProjectUpdateError(
                                    "GitHub returned an invalid wheel size."
                                ) from exc
                            if content_length != release.wheel_size:
                                raise ProjectUpdateError(
                                    "The wheel size does not match release metadata."
                                )
                        while True:
                            if time.monotonic() > deadline:
                                raise ProjectUpdateError(
                                    "The project wheel download timed out."
                                )
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > MAX_WHEEL_BYTES:
                                raise ProjectUpdateError(
                                    "The downloaded wheel exceeded the size limit."
                                )
                            output.write(chunk)
                            digest.update(chunk)
                except ProjectUpdateError:
                    raise
                except (
                    OSError,
                    urllib.error.URLError,
                    urllib.error.HTTPError,
                ) as exc:
                    raise ProjectUpdateError(
                        f"Cannot download the project wheel: {exc}"
                    ) from exc
                output.flush()
                os.fsync(output.fileno())
            if received != release.wheel_size:
                raise ProjectUpdateError(
                    "The downloaded wheel size does not match release metadata."
                )
            if digest.hexdigest() != release.wheel_sha256:
                raise ProjectUpdateError(
                    "The downloaded wheel failed GitHub SHA-256 verification."
                )
            return destination
        except ProjectUpdateError:
            with suppress(OSError):
                destination.unlink(missing_ok=True)
            raise
        except OSError as exc:
            with suppress(OSError):
                destination.unlink(missing_ok=True)
            raise ProjectUpdateError(
                f"Cannot save the project wheel: {exc}"
            ) from exc
        except BaseException:
            with suppress(OSError):
                destination.unlink(missing_ok=True)
            raise
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)

    def _validate_wheel(
        self,
        wheel_path: Path,
        release: ProjectRelease,
    ) -> None:
        expected_dist_info = (
            f"ielts_codex-{release.version}.dist-info"
        )
        try:
            with zipfile.ZipFile(wheel_path) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_WHEEL_MEMBERS:
                    raise ProjectUpdateError(
                        "The project wheel has an invalid member count."
                    )
                names = [info.filename for info in infos]
                canonical_paths = [
                    PurePosixPath(info.filename).as_posix()
                    for info in infos
                ]
                if (
                    len(names) != len(set(names))
                    or len(canonical_paths)
                    != len(
                        {
                            unicodedata.normalize("NFC", path).casefold()
                            for path in canonical_paths
                        }
                    )
                ):
                    raise ProjectUpdateError(
                        "The project wheel contains duplicate paths."
                    )
                total_size = 0
                for info in infos:
                    name = info.filename
                    path = PurePosixPath(name)
                    canonical_name = path.as_posix()
                    if info.is_dir():
                        canonical_name += "/"
                    mode = (info.external_attr >> 16) & 0o170000
                    allowed_root = (
                        name.startswith("ielts_codex/")
                        or name.startswith(f"{expected_dist_info}/")
                    )
                    native_suffix = name.lower().endswith(
                        (".so", ".pyd", ".dll", ".dylib", ".exe")
                    )
                    if (
                        not allowed_root
                        or name != canonical_name
                        or "\\" in name
                        or path.is_absolute()
                        or ".." in path.parts
                        or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or info.flag_bits & 0x1
                        or name.lower().endswith(".pth")
                        or native_suffix
                        or any(ord(character) < 32 for character in name)
                    ):
                        raise ProjectUpdateError(
                            "The project wheel contains an unsafe path."
                        )
                    total_size += info.file_size
                    if total_size > MAX_UNCOMPRESSED_BYTES:
                        raise ProjectUpdateError(
                            "The project wheel expands beyond the safe limit."
                        )
                file_paths = {
                    PurePosixPath(info.filename)
                    for info in infos
                    if not info.is_dir()
                }
                for info in infos:
                    path = PurePosixPath(info.filename)
                    if any(parent in file_paths for parent in path.parents):
                        raise ProjectUpdateError(
                            "The project wheel contains a file/directory "
                            "path conflict."
                        )
                if archive.testzip() is not None:
                    raise ProjectUpdateError(
                        "The project wheel failed its CRC check."
                    )
                archive_names = set(names)
                metadata_name = f"{expected_dist_info}/METADATA"
                wheel_name = f"{expected_dist_info}/WHEEL"
                record_name = f"{expected_dist_info}/RECORD"
                entry_points_name = (
                    f"{expected_dist_info}/entry_points.txt"
                )
                required = {
                    metadata_name,
                    wheel_name,
                    record_name,
                    entry_points_name,
                    *RUNTIME_PACKAGE_PATHS,
                }
                if not required.issubset(archive_names):
                    raise ProjectUpdateError(
                        "The project wheel is missing required package files."
                    )
                self._verify_wheel_record(archive, infos, record_name)
                self._verify_wheel_entry_points(
                    archive,
                    entry_points_name,
                )
                metadata = Parser().parsestr(
                    archive.read(metadata_name).decode("utf-8")
                )
                wheel_metadata = Parser().parsestr(
                    archive.read(wheel_name).decode("utf-8")
                )
        except ProjectUpdateError:
            raise
        except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            raise ProjectUpdateError(f"The project wheel is invalid: {exc}") from exc
        if (
            metadata.get("Name", "").lower() != "ielts-codex"
            or metadata.get("Version") != release.version
            or wheel_metadata.get("Root-Is-Purelib", "").lower() != "true"
            or "py3-none-any" not in wheel_metadata.get_all("Tag", [])
            or metadata.get_all("Requires-Dist")
        ):
            raise ProjectUpdateError(
                "The project wheel identity does not match the release."
            )
        python_requirement = metadata.get("Requires-Python", "")
        requirement_match = PYTHON_REQUIREMENT_RE.fullmatch(
            python_requirement
        )
        if requirement_match is None:
            raise ProjectUpdateError(
                "The project wheel has an unsupported Python requirement."
            )
        minimum_python = tuple(
            int(requirement_match.group(index)) for index in (1, 2)
        )
        if sys.version_info[:2] < minimum_python:
            raise ProjectUpdateError(
                "The project wheel requires a newer Python interpreter."
            )

    @staticmethod
    def _verify_wheel_entry_points(
        archive: zipfile.ZipFile,
        entry_points_name: str,
    ) -> None:
        parser = configparser.ConfigParser(
            interpolation=None,
            strict=True,
        )
        parser.optionxform = str
        try:
            parser.read_string(
                archive.read(entry_points_name).decode("utf-8")
            )
        except (
            configparser.Error,
            UnicodeDecodeError,
        ) as exc:
            raise ProjectUpdateError(
                "The project wheel has invalid entry points."
            ) from exc
        if (
            parser.defaults()
            or parser.sections() != ["console_scripts"]
            or dict(parser.items("console_scripts"))
            != {"ielts-codex": "ielts_codex.cli:main"}
        ):
            raise ProjectUpdateError(
                "The project wheel entry points do not match IELTS Codex."
            )

    @staticmethod
    def _verify_wheel_record(
        archive: zipfile.ZipFile,
        infos: Sequence[zipfile.ZipInfo],
        record_name: str,
    ) -> None:
        try:
            rows = list(
                csv.reader(
                    archive.read(record_name).decode("utf-8").splitlines()
                )
            )
        except (csv.Error, UnicodeDecodeError) as exc:
            raise ProjectUpdateError(
                "The project wheel has an invalid RECORD."
            ) from exc
        if any(len(row) != 3 for row in rows):
            raise ProjectUpdateError(
                "The project wheel has an invalid RECORD."
            )
        record_paths = [row[0] for row in rows]
        if (
            len(record_paths) != len(set(record_paths))
            or len(record_paths)
            != len(
                {
                    unicodedata.normalize("NFC", path).casefold()
                    for path in record_paths
                }
            )
        ):
            raise ProjectUpdateError(
                "The project wheel RECORD contains duplicate paths."
            )
        file_names = {
            info.filename for info in infos if not info.is_dir()
        }
        if set(record_paths) != file_names:
            raise ProjectUpdateError(
                "The project wheel RECORD does not match its files."
            )
        for path, hash_value, size_value in rows:
            if path == record_name:
                if hash_value or size_value:
                    raise ProjectUpdateError(
                        "The project wheel RECORD entry is invalid."
                    )
                continue
            algorithm, separator, encoded_digest = hash_value.partition("=")
            try:
                expected_size = int(size_value)
            except ValueError as exc:
                raise ProjectUpdateError(
                    "The project wheel RECORD size is invalid."
                ) from exc
            content = archive.read(path)
            actual_digest = base64.urlsafe_b64encode(
                hashlib.sha256(content).digest()
            ).rstrip(b"=").decode("ascii")
            if (
                algorithm != "sha256"
                or separator != "="
                or not encoded_digest
                or encoded_digest != actual_digest
                or expected_size != len(content)
            ):
                raise ProjectUpdateError(
                    "The project wheel failed its internal RECORD verification."
                )

    @staticmethod
    def _version_from_init(path: Path) -> str | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        match = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
            text,
            re.MULTILINE,
        )
        return match.group(1) if match else None

    def _pip_run(self, args: Sequence[str], *, cwd: Path) -> None:
        result = self._run_process(
            args,
            cwd=cwd,
            env=self._python_environment(),
            timeout=120,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if len(detail) > 500:
                detail = detail[-500:]
            suffix = f": {detail}" if detail else ""
            raise ProjectUpdateError(f"pip could not install the release{suffix}")

    @staticmethod
    def _python_environment() -> dict[str, str]:
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.startswith("PIP_") or name in {
                "PYTHONHOME",
                "PYTHONPATH",
                "PYTHONPLATLIBDIR",
                "PYTHONSTARTUP",
                "PYTHONINSPECT",
                "PYTHONUSERBASE",
                "PYTHONNOUSERSITE",
                "PYTHONSAFEPATH",
                "PYTHONEXECUTABLE",
            }:
                environment.pop(name)
        environment["PIP_CONFIG_FILE"] = os.devnull
        return environment

    @staticmethod
    def _user_site_root() -> Path | None:
        if getattr(site, "ENABLE_USER_SITE", None) is not True:
            return None
        try:
            value = site.getusersitepackages()
        except (AttributeError, OSError):
            return None
        if not isinstance(value, str) or not value:
            return None
        try:
            return Path(value).resolve()
        except OSError:
            return None

    @classmethod
    def _supported_pip_roots(cls) -> frozenset[Path]:
        values: set[str] = set()
        for key in ("purelib", "platlib"):
            value = sysconfig.get_path(key)
            if value:
                values.add(value)
        user_site = cls._user_site_root()
        roots = {
            Path(value).resolve()
            for value in values
            if isinstance(value, str) and value
        }
        if user_site is not None:
            roots.add(user_site)
        return frozenset(roots)

    @staticmethod
    def _pip_scripts_root(*, user_install: bool) -> Path | None:
        try:
            scheme = (
                sysconfig.get_preferred_scheme("user")
                if user_install
                else sysconfig.get_default_scheme()
            )
            value = sysconfig.get_path("scripts", scheme=scheme)
            return Path(value).resolve() if value else None
        except (AttributeError, OSError, ValueError):
            return None

    @staticmethod
    def _directory_target_is_writable(path: Path) -> bool:
        try:
            candidate = path
            while not candidate.exists():
                parent = candidate.parent
                if parent == candidate:
                    return False
                candidate = parent
            return candidate.is_dir() and os.access(
                candidate,
                os.W_OK | os.X_OK,
            )
        except OSError:
            return False

    def _git_output(self, root: Path, *args: str) -> str:
        result = self._git_process(root, args)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ProjectUpdateError(
                f"Git command failed ({' '.join(args[:2])}): {detail}"
            )
        return result.stdout

    def _git_run(
        self,
        root: Path,
        *args: str,
        timeout: float = 30,
    ) -> None:
        result = self._git_process(root, args, timeout=timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ProjectUpdateError(
                f"Git command failed ({' '.join(args[:2])}): {detail}"
            )

    def _git_ancestor(
        self,
        root: Path,
        ancestor: str,
        descendant: str,
        message: str,
    ) -> None:
        result = self._git_process(
            root,
            ("merge-base", "--is-ancestor", ancestor, descendant),
        )
        if result.returncode != 0:
            raise ProjectUpdateError(message)

    def _git_process(
        self,
        root: Path,
        args: Sequence[str],
        *,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.startswith("GIT_") or name in {
                "CURL_CA_BUNDLE",
                "SSL_CERT_DIR",
                "SSL_CERT_FILE",
            }:
                environment.pop(name)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        return self._run_process(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "credential.helper=",
                "-c",
                "http.sslVerify=true",
                "-c",
                "http.https://github.com/.sslVerify=true",
                "-c",
                (
                    "http.https://github.com/Miracle-0v0/"
                    "ielts-codex-cli.git.sslVerify=true"
                ),
                "-c",
                (
                    "http.https://github.com/Miracle-0v0/"
                    "ielts-codex-cli.sslVerify=true"
                ),
                "-c",
                (
                    "http.https://github.com/Miracle-0v0/"
                    "ielts-codex-cli.git.sslCAInfo="
                ),
                "-c",
                (
                    "http.https://github.com/Miracle-0v0/"
                    "ielts-codex-cli.sslCAInfo="
                ),
                "-c",
                "protocol.file.allow=never",
                "-c",
                "protocol.ext.allow=never",
                "-C",
                str(root),
                *args,
            ],
            env=environment,
            timeout=timeout,
        )

    def _run_process(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        cwd: Path | str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                list(args),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                env=dict(env) if env is not None else None,
                cwd=str(cwd) if cwd is not None else None,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProjectUpdateError(f"Cannot run {args[0]}: {exc}") from exc

    @staticmethod
    def _read_limited(
        response: Any,
        limit: int,
        *,
        deadline: float,
    ) -> bytes:
        chunks: list[bytes] = []
        received = 0
        while True:
            if time.monotonic() > deadline:
                raise ProjectUpdateError(
                    "Project release metadata download timed out."
                )
            chunk = response.read(min(64 * 1024, limit + 1 - received))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            received += len(chunk)
            if received > limit:
                raise ProjectUpdateError(
                    "Project release metadata exceeded the safe size limit."
                )
