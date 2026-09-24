import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import helpers
from ielts_codex.models import Rating
from ielts_codex.storage import (
    AUTO_BACKUP_LIMIT, ProgressConflictError, ProgressFileError, ProgressStore,
)


FIXTURE = Path(__file__).parent / "fixtures" / "progress-v1.json"


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = ProgressStore(self.root)
        self.day = date(2026, 1, 1)

    def record(self, rating=Rating.GOOD, offset=0, **kwargs):
        return self.store.record_review("sustainable", rating, self.day + timedelta(days=offset), **kwargs)

    def test_v1_migration_preserves_data_and_exact_original(self):
        original = FIXTURE.read_bytes()
        self.store.path.write_bytes(original)
        self.store = ProgressStore(self.root)
        raw = json.loads(original)
        self.assertEqual(self.store.data.settings, raw["settings"])
        self.assertEqual(self.store.data.sessions, raw["sessions"])
        self.assertEqual(self.store.cards["sustainable"].to_dict(), raw["cards"]["sustainable"])
        self.assertEqual(self.store.data.attempts, [])
        self.assertEqual(self.store.stats(72)["stable_by_task"]["recall"], 0)
        self.assertEqual(self.store.path.read_bytes(), original)
        self.store.save()
        self.assertEqual(json.loads(self.store.path.read_bytes())["version"], 3)
        copies = list((self.root / "backups").glob("migration-v1-*.json"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), original)
        self.assertEqual(ProgressStore(self.root).cards["sustainable"].attempts, 30)

    def test_unknown_schema_and_corrupt_data_are_not_overwritten(self):
        for content in (b'{"version":99}', b'{"version": true}', b'{broken',
                        b'{"version":1,"cards":[]}', b'{"version":2,"attempts":[{}]}'):
            self.store.path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ProgressFileError):
                ProgressStore(self.root)
            self.assertEqual(self.store.path.read_bytes(), content)

    def test_stale_writer_cannot_overwrite_and_rolls_back_memory(self):
        other = ProgressStore(self.root)
        self.record()
        baseline = self.store.path.read_bytes()
        with self.assertRaises(ProgressConflictError):
            other.record_review("different", Rating.GOOD)
        self.assertEqual(self.store.path.read_bytes(), baseline)
        self.assertEqual(other.cards, {})
        self.assertEqual(other.data.attempts, [])

    def test_conflicting_setting_is_not_retained_in_memory(self):
        other = ProgressStore(self.root)
        self.store.set_daily_goal(25)
        with self.assertRaises(ProgressConflictError):
            other.set_daily_goal(30)
        self.assertEqual(other.daily_goal, 20)
        self.assertEqual(ProgressStore(self.root).daily_goal, 25)

    def test_interrupted_replace_preserves_last_completed_answer(self):
        self.record()
        baseline = self.store.path.read_bytes()
        with patch("ielts_codex.storage.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.record(Rating.AGAIN, offset=1)
        self.assertEqual(self.store.path.read_bytes(), baseline)
        self.assertEqual(len(self.store.data.attempts), 1)
        self.assertEqual(len(ProgressStore(self.root).data.attempts), 1)
        self.assertEqual(list(self.root.glob(".progress-*.tmp")), [])

    def test_manual_restore_works_even_when_current_file_is_corrupt(self):
        self.record()
        backup = self.store.backup()
        original = backup.read_bytes()
        self.record(offset=1)
        self.store.path.write_bytes(b"{broken")
        ProgressStore.restore_backup(self.root, backup.name)
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(len(ProgressStore(self.root).data.attempts), 1)
        rescue = list((self.root / "backups").glob("before-restore-*.json"))
        self.assertEqual(rescue[0].read_bytes(), b"{broken")
        with self.assertRaises(ProgressConflictError):
            self.store.record_review("another", Rating.GOOD)

    def test_restore_rejects_bad_backup_and_path_traversal(self):
        self.record()
        baseline = self.store.path.read_bytes()
        bad = self.root / "backups" / "bad.json"
        bad.parent.mkdir(exist_ok=True)
        bad.write_text('{"version":99}', encoding="utf-8")
        for name in ("../progress.json", "..\\progress.json", "bad.json"):
            with self.subTest(name=name), self.assertRaises(ProgressFileError):
                ProgressStore.restore_backup(self.root, name)
            self.assertEqual(self.store.path.read_bytes(), baseline)

    def test_automatic_backup_rotation_preserves_manual_copy(self):
        self.record()
        manual = self.store.backup()
        for i in range(AUTO_BACKUP_LIMIT + 3):
            self.store.set_daily_goal(30 + i)
        self.assertEqual(len(list((self.root / "backups").glob("progress-*.json"))), AUTO_BACKUP_LIMIT)
        self.assertTrue(manual.exists())

    def test_actual_competing_processes_allow_only_one_stale_snapshot_write(self):
        worker = Path(__file__).parent / "concurrent_writer.py"
        processes = [
            subprocess.Popen(
                [sys.executable, str(worker), str(self.root), name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for name in ("alpha", "beta")
        ]
        try:
            deadline = time.monotonic() + 10
            while not all((self.root / (name + ".ready")).exists() for name in ("alpha", "beta")):
                if time.monotonic() > deadline:
                    self.fail("writers did not become ready")
                if any(p.poll() is not None for p in processes):
                    self.fail("writer exited before barrier")
                time.sleep(0.01)
            (self.root / "go").touch()
            output = [p.communicate(timeout=10) for p in processes]
            self.assertEqual(sorted(p.returncode for p in processes), [0, 2], output)
            restored = ProgressStore(self.root)
            self.assertEqual(len(restored.cards), 1)
            self.assertEqual(len(restored.data.attempts), 1)
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill()
                p.communicate(timeout=5)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ProgressStore(self.directory.name)
        self.day = date(2026, 1, 1)

    def record(self, offset, rating=Rating.GOOD, **kwargs):
        return self.store.record_review("sustainable", rating, self.day + timedelta(days=offset), **kwargs)

    def test_stability_requires_three_delayed_passes_and_seven_day_gap(self):
        for offset in (0, 1, 4):
            self.record(offset)
        self.assertEqual(self.store.stats(1)["stable_by_task"]["recall"], 0)
        self.record(11)
        self.assertEqual(self.store.stats(1)["stable_by_task"]["recall"], 1)
        self.assertEqual(self.store.stats(1)["stable_by_task"]["spelling"], 0)

    def test_same_day_retries_do_not_build_stability(self):
        for _ in range(10):
            self.record(0)
        self.assertEqual(self.store.stats(1)["stable_by_task"]["recall"], 0)
        for offset in (1, 4, 11):
            self.record(offset, retry=True)
        self.assertEqual(self.store.stats(1)["stable_by_task"]["recall"], 0)

    def test_hint_is_saved_and_breaks_delayed_pass_streak(self):
        for offset in (0, 1, 4, 11):
            self.record(offset, task="spelling")
        self.assertEqual(self.store.stats(1)["stable_by_task"]["spelling"], 1)
        self.record(12, task="spelling", hint_level=1, correct=True, answer="sustainable")
        event = ProgressStore(self.directory.name).data.attempts[-1]
        self.assertEqual(event["hint_level"], 1)
        self.assertEqual(event["rating"], Rating.HARD)
        self.assertTrue(event["correct"])
        stats = self.store.stats(1)
        self.assertEqual(stats["stable_by_task"]["spelling"], 0)
        self.assertEqual(stats["by_task"]["spelling"]["assisted_correct"], 1)
        self.assertEqual(stats["by_task"]["spelling"]["independent_correct"], 4)

    def test_different_tasks_cannot_combine_into_three_passes(self):
        for offset, task in ((0, "recall"), (1, "spelling"), (4, "recall"), (11, "spelling")):
            self.record(offset, task=task)
        self.assertEqual(self.store.stats(1)["stable_by_task"], {"recall": 0, "spelling": 0, "context": 0, "game": 0})

    def test_failure_resets_stability_and_saves_error(self):
        for offset in (0, 1, 4, 11):
            self.record(offset, task="spelling")
        self.record(12, Rating.AGAIN, task="spelling", correct=False,
                    answer="sustainble", error_type="missing_letter")
        self.assertEqual(self.store.stats(1)["stable_by_task"]["spelling"], 0)
        self.assertEqual(self.store.data.attempts[-1]["error_type"], "missing_letter")

    def test_game_completion_is_not_inferred_to_be_a_correct_answer(self):
        self.record(0, task="game", hint_level=1, navigation_hint=True)
        event = self.store.data.attempts[-1]
        self.assertIsNone(event["correct"])
        self.assertEqual(event["hint_level"], 1)
        self.assertTrue(event["navigation_hint"])
        self.assertEqual(self.store.cards["sustainable"].correct, 0)
        self.assertEqual(self.store.stats(1)["by_task"]["game"]["independent_correct"], 0)

    def test_game_operation_failure_does_not_change_word_schedule(self):
        self.record(0)
        before = self.store.cards["sustainable"].to_dict()
        self.record(1, None, task="game", error_type="operation_failure", navigation_hint=True)
        self.assertEqual(self.store.cards["sustainable"].to_dict(), before)
        event = self.store.data.attempts[-1]
        self.assertIsNone(event["correct"])
        self.assertFalse(event["scheduled"])
        self.assertTrue(event["navigation_hint"])
        self.assertEqual(self.store.stats(1)["by_task"]["game"]["attempts"], 0)

    def test_interrupted_game_counts_as_recorded_exposure_not_delayed_success(self):
        self.record(0)
        self.record(7, None, task="game", error_type="operation_failure")
        self.record(7)
        self.assertEqual(self.store.data.attempts[-1]["elapsed_days"], 0)
        self.assertEqual(self.store.stats(1)["stable_by_task"]["recall"], 0)

    def test_history_can_reconstruct_previously_reviewed_day(self):
        self.record(0)
        self.record(1)
        self.assertEqual(self.store.reviewed_words_on(self.day), ("sustainable",))
        self.assertEqual(len(ProgressStore(self.directory.name).data.attempts), 2)


if __name__ == "__main__":
    unittest.main()
