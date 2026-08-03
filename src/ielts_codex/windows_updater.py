"""Standalone Windows installer for a wheel staged by ``/update``.

This module intentionally has no package-relative imports.  The running copy is
placed beside the verified wheel before the interactive CLI exits, so it stays
available even while pip replaces the installed ``ielts_codex`` package.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_MANIFEST_BYTES = 16 * 1024
MAX_WHEEL_BYTES = 16 * 1024 * 1024
VERSION_RE = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_ENTRY_POINTS = {
    "ielts": "ielts_codex.cli:main",
    "ielts-codex": "ielts_codex.cli:main",
}
MANIFEST_KEYS = {
    "schema",
    "version",
    "wheel",
    "sha256",
    "user_install",
    "package_root",
    "scripts_root",
    "wait_pid",
}


class WindowsUpdateError(RuntimeError):
    """Raised when a deferred Windows update cannot finish safely."""


def _clean_python_environment() -> dict[str, str]:
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


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise WindowsUpdateError(
                "The pending-update manifest is missing or unsafe."
            )
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise WindowsUpdateError("The pending-update manifest is too large.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except WindowsUpdateError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WindowsUpdateError(
            f"Cannot read the pending-update manifest: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != MANIFEST_KEYS:
        raise WindowsUpdateError("The pending-update manifest has an invalid shape.")

    version = payload.get("version")
    digest = payload.get("sha256")
    wheel = payload.get("wheel")
    package_root = payload.get("package_root")
    scripts_root = payload.get("scripts_root")
    wait_pid = payload.get("wait_pid")
    if (
        payload.get("schema") != 1
        or not isinstance(version, str)
        or VERSION_RE.fullmatch(version) is None
        or not isinstance(digest, str)
        or SHA256_RE.fullmatch(digest) is None
        or wheel != f"ielts_codex-{version}-py3-none-any.whl"
        or not isinstance(payload.get("user_install"), bool)
        or not isinstance(package_root, str)
        or not package_root
        or not Path(package_root).is_absolute()
        or not isinstance(scripts_root, str)
        or not scripts_root
        or not Path(scripts_root).is_absolute()
        or isinstance(wait_pid, bool)
        or not isinstance(wait_pid, int)
        or wait_pid <= 0
    ):
        raise WindowsUpdateError("The pending-update manifest contains invalid values.")
    return payload


def _wait_for_process_exit(process_id: int, timeout_ms: int = 300_000) -> None:
    """Wait for the CLI that staged this update without shelling out."""

    if os.name != "nt":
        return
    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    wait_failed = 0xFFFFFFFF
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return
        raise WindowsUpdateError(
            "Cannot confirm that IELTS Codex has exited. Close every IELTS Codex "
            "window and run ielts-update.cmd again."
        )
    try:
        outcome = kernel32.WaitForSingleObject(handle, timeout_ms)
    finally:
        kernel32.CloseHandle(handle)
    if outcome == wait_timeout:
        raise WindowsUpdateError(
            "IELTS Codex is still running. Close it and run ielts-update.cmd again."
        )
    if outcome in {wait_failed} or outcome != wait_object_0:
        raise WindowsUpdateError("Windows could not wait for IELTS Codex to exit.")


def _wheel_path(manifest_path: Path, manifest: Mapping[str, Any]) -> Path:
    root = manifest_path.resolve().parent
    wheel = root / str(manifest["wheel"])
    try:
        metadata = wheel.lstat()
    except OSError as exc:
        raise WindowsUpdateError(f"Cannot access the staged wheel: {exc}") from exc
    if (
        wheel.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > MAX_WHEEL_BYTES
    ):
        raise WindowsUpdateError("The staged wheel is missing or unsafe.")
    digest = hashlib.sha256()
    try:
        with wheel.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise WindowsUpdateError(f"Cannot read the staged wheel: {exc}") from exc
    if digest.hexdigest() != manifest["sha256"]:
        raise WindowsUpdateError("The staged wheel failed its SHA-256 recheck.")
    return wheel


def _run_process(
    args: Sequence[str],
    *,
    cwd: Path,
    capture_output: bool,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            cwd=str(cwd),
            env=_clean_python_environment(),
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsUpdateError(f"Cannot run {args[0]}: {exc}") from exc


def _verify_install(manifest: Mapping[str, Any], *, cwd: Path) -> None:
    marker = "__IELTS_CODEX_WINDOWS_UPDATE__"
    verification_code = (
        "import importlib.metadata as m,json,ielts_codex,ielts_codex.cli as c;"
        "d=m.distribution('ielts-codex');"
        "e={x.name:x.value for x in d.entry_points if x.group=='console_scripts'};"
        f"print({marker!r}+json.dumps({{"
        "'metadata':d.version,'package':ielts_codex.__version__,"
        "'module':ielts_codex.__file__,'cli':c.__file__,'entries':e}))"
    )
    result = _run_process(
        [sys.executable, "-c", verification_code],
        cwd=cwd,
        capture_output=True,
        timeout=60,
    )
    try:
        payload = next(
            line[len(marker) :]
            for line in reversed((result.stdout or "").splitlines())
            if line.startswith(marker)
        )
        verified = json.loads(payload)
    except (StopIteration, json.JSONDecodeError, TypeError) as exc:
        raise WindowsUpdateError(
            "pip finished, but the installed package could not be inspected."
        ) from exc
    expected_init = Path(str(manifest["package_root"])) / "ielts_codex" / "__init__.py"
    expected_cli = Path(str(manifest["package_root"])) / "ielts_codex" / "cli.py"

    def normalized(value: object) -> str:
        try:
            return os.path.normcase(str(Path(str(value)).resolve()))
        except OSError:
            return ""

    if (
        result.returncode != 0
        or verified.get("metadata") != manifest["version"]
        or verified.get("package") != manifest["version"]
        or verified.get("entries") != EXPECTED_ENTRY_POINTS
        or normalized(verified.get("module")) != normalized(expected_init)
        or normalized(verified.get("cli")) != normalized(expected_cli)
    ):
        raise WindowsUpdateError(
            "pip finished, but the installed IELTS Codex files failed verification."
        )

    if os.name == "nt":
        scripts_root = Path(str(manifest["scripts_root"]))
        for command in EXPECTED_ENTRY_POINTS:
            if not any(
                (scripts_root / f"{command}{suffix}").is_file()
                for suffix in (".exe", ".cmd", ".bat", "")
            ):
                raise WindowsUpdateError(
                    f"pip did not create the expected {command} command launcher."
                )


def apply_pending_update(manifest_path: Path) -> None:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = _load_manifest(manifest_path)
    print(f"Preparing IELTS Codex {manifest['version']}...")
    _wait_for_process_exit(int(manifest["wait_pid"]))
    wheel = _wheel_path(manifest_path, manifest)
    command = [
        sys.executable,
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
    if manifest["user_install"]:
        command.append("--user")
    command.extend(("--upgrade", "--force-reinstall", str(wheel)))
    result = _run_process(
        command,
        cwd=manifest_path.parent,
        capture_output=False,
        timeout=300,
    )
    if result.returncode != 0:
        raise WindowsUpdateError(
            "pip could not install the release. The staged files were kept so this "
            "script can be run again after every IELTS Codex window is closed."
        )
    _verify_install(manifest, cwd=manifest_path.parent)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("Usage: windows_updater.py <pending-update.json>", file=sys.stderr)
        return 2
    try:
        apply_pending_update(Path(arguments[0]))
    except WindowsUpdateError as exc:
        print(f"IELTS Codex update failed: {exc}", file=sys.stderr)
        return 1
    print("IELTS Codex was updated and both command launchers were verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
