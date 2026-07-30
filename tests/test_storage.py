from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ielts_codex.models import Rating
from ielts_codex.storage import ProgressFileError, ProgressStore


class ProgressStoreTests(unittest.TestCase):
    def test_review_round_trip_and_daily_stats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            today = date(2026, 7, 30)
            store = ProgressStore(directory)

            store.record_review("sustainable", Rating.GOOD, today)
            restored = ProgressStore(directory)
            stats = restored.stats(total_words=72, current_day=today)

            self.assertIn("sustainable", restored.cards)
            self.assertEqual(restored.cards["sustainable"].interval, 1)
            self.assertEqual(stats["learned"], 1)
            self.assertEqual(stats["today_reviewed"], 1)
            self.assertEqual(stats["today_learned"], 1)
            self.assertEqual(stats["accuracy"], 100)
            self.assertEqual(stats["streak"], 1)

    def test_atomic_save_produces_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ProgressStore(directory)
            store.set_daily_goal(35)

            payload = json.loads(
                (Path(directory) / "progress.json").read_text(encoding="utf-8")
            )

            self.assertEqual(payload["version"], 1)
            self.assertEqual(payload["settings"]["daily_goal"], 35)
            self.assertEqual(list(Path(directory).glob(".progress-*.json")), [])

    def test_invalid_file_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            path.write_text("{broken", encoding="utf-8")

            with self.assertRaises(ProgressFileError):
                ProgressStore(directory)

            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
