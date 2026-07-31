#!/usr/bin/env python3
"""Run IELTS Codex directly from a source checkout."""

import sys
from pathlib import Path


if sys.version_info < (3, 10):
    found = ".".join(str(part) for part in sys.version_info[:3])
    print(
        "IELTS Codex requires Python 3.10 or later "
        f"(found Python {found}). Run ./run.sh to locate or install "
        "a compatible interpreter.",
        file=sys.stderr,
    )
    raise SystemExit(1)


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ielts_codex.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
