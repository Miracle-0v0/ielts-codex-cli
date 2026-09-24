"""Upgrade tests use synthetic temp data; never open a personal progress directory."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import helpers
from ielts_codex.models import Rating
from ielts_codex.word_bank import WordBank

from ielts_codex import storage

Store = storage.ProgressStore


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.day = date(2026, 1, 1)

    def plan(self):
        return dict(id="plan", day=self.day.isoformat(), minutes=20, deck="all",
                    status="active", completed=0, max_actions=40, items=[
                        dict(id="first", word="test", task="recall", retries=0),
                        dict(id="second", word="other", task="spelling", retries=0)])

    def test_original_v1_is_preserved_and_rollback_restores_identical_bytes(self):
        original = (Path(__file__).parent / "fixtures/progress-v1.json").read_bytes()
        (self.root / "progress.json").write_bytes(original)
        store = Store(self.root)
        bank = WordBank.bundled()
        key = bank.get("sustainable").key
        store.bind_bank(bank)
        self.assertEqual(store.cards[key].attempts, 30)
        self.assertEqual(store.cards_for("recall")[key].interval, 30)
        self.assertEqual(store.cards_for("spelling"), {})
        self.assertEqual(store.data.settings["daily_goal"], 15)
        self.assertEqual((self.root / "progress.json").read_bytes(), original)
        store.save()
        backup = next((self.root / "backups").glob("migration-v1-*.json"))
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(json.loads(store.path.read_bytes())["version"], 3)
        Store.restore_backup(self.root, backup.name)
        self.assertEqual(store.path.read_bytes(), original)
        self.assertEqual(Store(self.root).cards["sustainable"].attempts, 30)

    def test_v2_detailed_evidence_survives_and_does_not_fill_spelling_schedule(self):
        original = json.loads((Path(__file__).parent / "fixtures/progress-v1.json").read_bytes())
        original["version"] = 2
        original["attempts"] = [{
            "id": "legacy-spelling-1", "word": "sustainable", "task": "spelling",
            "timestamp": "2026-01-01T10:00:00+00:00", "day": "2026-01-01",
            "rating": 3, "correct": True, "hint_level": 0, "retry": False,
            "elapsed_days": 0, "answer": "sustainable", "error_type": None,
            "navigation_hint": False, "scheduled": True,
        }]
        self.store.path.write_text(json.dumps(original), encoding="utf-8")
        original_bytes = self.store.path.read_bytes()
        upgraded = Store(self.root)
        self.assertEqual(upgraded.data.attempts, original["attempts"])
        self.assertEqual(upgraded.cards_for("spelling"), {})
        upgraded.save()
        backup = next((self.root / "backups").glob("migration-v2-*.json"))
        self.assertEqual(backup.read_bytes(), original_bytes)
        self.assertEqual(Store(self.root).data.attempts, original["attempts"])

    def test_abilities_have_independent_due_dates_and_repetition_counts(self):
        self.store.record_review("test", Rating.EASY, self.day)
        recall = self.store.cards_for("recall")["test"].to_dict()
        self.store.record_review("test", Rating.AGAIN, self.day, task="spelling")
        self.assertEqual(self.store.cards_for("recall")["test"].to_dict(), recall)
        self.assertEqual(self.store.cards_for("spelling")["test"].interval, 0)
        self.store.record_review("test", Rating.GOOD, self.day, task="context")
        loaded = Store(self.root)
        self.assertEqual(loaded.cards_for("context")["test"].interval, 1)
        self.assertEqual(loaded.cards_for("recall")["test"].interval, 4)
        self.assertEqual(loaded.cards["test"].attempts, 3)

    def test_attempt_and_daily_plan_cursor_are_one_atomic_save(self):
        self.store.save_study(self.plan())
        self.store.record_review("test", Rating.GOOD, self.day, study_item_id="first")
        loaded = Store(self.root)
        self.assertEqual(len(loaded.data.attempts), 1)
        self.assertEqual(loaded.data.study["items"][0]["id"], "second")
        self.assertEqual(loaded.data.study["completed"], 1)

    def test_failed_daily_save_keeps_both_answer_and_position_uncommitted(self):
        self.store.save_study(self.plan())
        before = self.store.path.read_bytes()
        with patch.object(storage.os, "replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.store.record_review("test", Rating.GOOD, self.day, study_item_id="first")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.data.study["items"][0]["id"], "first")
        self.assertEqual(self.store.data.attempts, [])

    def test_daily_retry_is_persisted_and_bounded(self):
        plan = self.plan()
        plan["items"] = plan["items"][:1]
        self.store.save_study(plan)
        for _ in range(3):
            item = self.store.data.study["items"][0]
            self.store.record_review("test", Rating.AGAIN, self.day,
                                     retry=bool(item["retries"]), study_item_id=item["id"])
            self.store = Store(self.root)
        self.assertEqual(self.store.data.study["status"], "complete")
        self.assertEqual(self.store.data.study["items"], [])
        self.assertEqual(len(self.store.data.attempts), 3)

    def test_stale_writer_cannot_clobber_new_plan_or_import_fields(self):
        other = Store(self.root)
        self.store.save_study(self.plan())
        with self.assertRaises(storage.ProgressConflictError):
            other.set_setting("active_deck", "other")
        loaded = Store(self.root)
        self.assertEqual(loaded.data.study["id"], "plan")
        self.assertNotIn("active_deck", loaded.data.settings)

    def test_game_due_date_does_not_create_a_core_study_backlog(self):
        self.store.record_review("game-only", Rating.AGAIN, self.day, task="game")
        self.assertEqual(self.store.stats(1, self.day)["due"], 0)
        self.store.record_review("game-only", Rating.EASY, self.day, task="recall")
        self.store.record_review("game-only", Rating.AGAIN, self.day, task="game")
        self.assertEqual(self.store.cards["game-only"].due, "2026-01-05")
        self.assertEqual(self.store.cards_for("game")["game-only"].due, "2026-01-01")

    def test_unknown_schema_stays_untouched(self):
        self.store.path.write_bytes(b'{"version":100}')
        with self.assertRaises(storage.ProgressFileError):
            Store(self.root)
        self.assertEqual(self.store.path.read_bytes(), b'{"version":100}')

    def test_pending_words_roundtrip_and_mistakes_remain_per_task(self):
        from dataclasses import replace
        from helpers import word
        pending = replace(word(), id="personal-1", deck="personal", status="pending")
        with self.store.transaction():
            self.store.data.vocabulary[pending.key] = pending.to_dict()
        self.store.record_review("test", Rating.AGAIN, self.day, task="spelling",
                                 correct=False, error_type="missing_letter")
        self.store.record_review("test", Rating.GOOD, self.day, task="recall")
        loaded = Store(self.root)
        self.assertIn(pending.key, loaded.data.vocabulary)
        self.assertEqual(loaded.ability_status("test", "recall"), "本次通过")
        self.assertEqual(loaded.ability_status("test", "spelling"), "需要复习")
        self.assertEqual(loaded.ability_status("test", "context"), "尚未验证")
        self.assertEqual(loaded.mistakes()[0]["task"], "spelling")

    def test_removed_deck_records_can_be_excluded_from_active_statistics(self):
        self.store.record_review("archived", Rating.GOOD, self.day)
        self.store.record_review("active", Rating.GOOD, self.day)
        result = self.store.stats(1, word_ids={"active"})
        self.assertEqual(result["learned"], 1)
        self.assertEqual(result["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
