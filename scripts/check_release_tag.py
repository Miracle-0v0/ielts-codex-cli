#!/usr/bin/env python3
"""Check the tested 1.0.0 wheel and tag before creating a GitHub release."""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile


VERSION = "1.0.0"
TAG = "v" + VERSION


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8",
        errors="replace", stderr=subprocess.STDOUT,
    ).strip()


def runtime_version(text: str) -> str:
    matches = re.findall(r'''^__version__\s*=\s*["']([^"'\r\n]+)["']\s*$''', text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("Expected exactly one runtime version declaration.")
    return matches[0]


def verify(root: Path, tag: str, wheel: Path) -> str:
    if tag != TAG:
        raise ValueError(f"This release job accepts only {TAG}.")
    project = (root / "pyproject.toml").read_text(encoding="utf-8")
    table = re.search(r"(?ms)^\[project\][ \t]*\r?\n(.*?)(?=^\[|\Z)", project)
    matches = re.findall(r'''^version\s*=\s*["']([^"'\r\n]+)["']\s*$''', table[1] if table else "", re.MULTILINE)
    if matches != [VERSION]:
        raise ValueError("pyproject.toml must declare the final version 1.0.0.")
    source_init = (root / "src/ielts_codex/__init__.py").read_text(encoding="utf-8")
    if runtime_version(source_init) != VERSION:
        raise ValueError("The runtime version must be final 1.0.0.")
    commit = git(root, "rev-parse", "--verify", f"refs/tags/{TAG}^{{commit}}")
    if commit != git(root, "rev-parse", "--verify", "HEAD"):
        raise ValueError("The checked-out commit does not match the release tag.")
    # origin/main is freshly fetched by the workflow before this check.
    git(root, "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main")
    if wheel.name != f"ielts_codex-{VERSION}-py3-none-any.whl":
        raise ValueError("Unexpected release wheel filename.")
    with zipfile.ZipFile(wheel) as archive:
        metadata = Parser().parsestr(archive.read(f"ielts_codex-{VERSION}.dist-info/METADATA").decode("utf-8"))
        if metadata.get("Name") != "ielts-codex" or metadata.get("Version") != VERSION:
            raise ValueError("The downloaded wheel has the wrong package identity.")
        if runtime_version(archive.read("ielts_codex/__init__.py").decode("utf-8")) != VERSION:
            raise ValueError("The wheel contains a different runtime version.")
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        commit = verify(root, args.tag, args.wheel)
        digest = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Release check failed: {exc}\n")
    print(f"Verified {args.tag} at {commit}: {args.wheel.name} SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
