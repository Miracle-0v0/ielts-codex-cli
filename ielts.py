#!/usr/bin/env python3
"""Run IELTS Codex directly from a source checkout."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ielts_codex.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
