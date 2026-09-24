"""A real competing process for the shared-progress test; not a test runner."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ielts_codex.models import Rating
from ielts_codex.storage import ProgressConflictError, ProgressStore

directory = Path(sys.argv[1])
name = sys.argv[2]
store = ProgressStore(directory)
(directory / (name + ".ready")).touch()
deadline = time.monotonic() + 10
while not (directory / "go").exists():
    if time.monotonic() > deadline:
        raise SystemExit(3)
    time.sleep(0.01)
try:
    store.record_review(name, Rating.GOOD)
except ProgressConflictError:
    raise SystemExit(2)
