import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from helpers import app_for, word
from ielts_codex.cli import main
from ielts_codex.models import Rating
from ielts_codex.storage import ProgressStore


class StudyFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_new_word_again_returns_after_other_cards(self):
        app, output = app_for(self.root, "\n1\n\n3\n\n3\n\n3\n",
                              [word("alpha"), word("beta"), word("gamma")])
        result = app.learn(3)
        history = app.store.data.attempts
        self.assertEqual([event["word"] for event in history], ["alpha", "beta", "gamma", "alpha"])
        self.assertEqual([event["retry"] for event in history], [False, False, False, True])
        self.assertEqual(result.retries, 1)
        self.assertEqual(result.independent_correct, 2)
        self.assertEqual(app.store.cards["alpha"].interval, 1)

    def test_review_again_also_repeats(self):
        app, _ = app_for(self.root, "\n1\n\n3\n")
        app.store.record_review("sustainable", Rating.GOOD, date.today() - timedelta(days=10))
        result = app.review(1)
        self.assertEqual(result.reviewed, 2)
        self.assertEqual(result.retries, 1)
        self.assertEqual(app.store.cards["sustainable"].interval, 1)

    def test_retry_limit_is_bounded_and_remains_due(self):
        app, output = app_for(self.root, "\n1\n\n1\n\n1\n")
        result = app.learn(1)
        self.assertEqual(result.reviewed, 3)
        self.assertEqual(result.deferred, 1)
        self.assertIn("三次上限", output.getvalue())
        self.assertEqual(app.store.cards["sustainable"].due, date.today().isoformat())

    def test_hint_is_separate_from_self_rating(self):
        app, _ = app_for(self.root, "h\n\n4\n")
        app.learn(1)
        event = app.store.data.attempts[-1]
        self.assertEqual(event["hint_level"], 1)
        self.assertEqual(event["rating"], Rating.HARD)
        self.assertEqual(app.store.stats(1)["by_task"]["recall"]["independent"], 0)

    def test_quitting_preserves_already_answered_card(self):
        app, _ = app_for(self.root, "\n1\nq\n")
        result = app.learn(1)
        self.assertTrue(result.stopped)
        reloaded = ProgressStore(self.root)
        self.assertEqual(len(reloaded.data.attempts), 1)
        self.assertEqual(reloaded.cards["sustainable"].due, date.today().isoformat())

    def test_eof_and_skip_dont_create_an_answer(self):
        for answers in ("", "s\n"):
            app, _ = app_for(self.root, answers)
            app.learn(1)
            self.assertEqual(app.store.data.attempts, [])
            self.assertFalse(app.store.path.exists())

    def test_bad_spelling_repeats_and_hint_success_is_separate(self):
        app, _ = app_for(self.root, "sustain123able\nh\nSUSTAINABLE\n")
        app.store.record_review("sustainable", Rating.GOOD, date.today() - timedelta(days=1))
        result = app.quiz(1)
        events = app.store.data.attempts[-2:]
        self.assertFalse(events[0]["correct"])
        self.assertEqual(events[0]["error_type"], "unexpected_digit")
        self.assertTrue(events[1]["correct"])
        self.assertTrue(events[1]["retry"])
        self.assertEqual(events[1]["hint_level"], 1)
        self.assertEqual(result.independent_correct, 0)

    def test_explicit_spelling_variant_is_accepted(self):
        app, _ = app_for(self.root, "analyze\n",
                         [word("analyse", accepted_answers=("analyze",))])
        app.store.record_review("analyse", Rating.GOOD)
        result = app.quiz(1)
        self.assertEqual(result.correct, 1)
        self.assertEqual(app.store.data.attempts[-1]["answer"], "analyze")

    def test_offline_commands_and_stats_need_no_network(self):
        app, output = app_for(self.root, "\n3\nsustainable\n")
        with patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            app.learn(1)
            app.quiz(1)
            app.dispatch("/stats")
            app.dispatch("/backup")
            app.dispatch("/backups")
        text = output.getvalue()
        self.assertIn("稳定复习", text)
        self.assertIn("认义自评", text)
        self.assertNotIn("已掌握", text)
        self.assertNotIn("正确率", text)

    def test_recovery_command_works_without_loading_corrupt_main_file(self):
        app, output = app_for(self.root, "\n3\n")
        app.learn(1)
        backup = app.store.backup()
        app.store.path.write_bytes(b"{broken")
        with patch("ielts_codex.cli.TerminalUI", return_value=app.ui):
            self.assertEqual(main(["backups", "--data-dir", str(self.root)]), 0)
            self.assertEqual(main(["restore", backup.name, "--data-dir", str(self.root)]), 0)
            self.assertEqual(main(["restore", "--data-dir", str(self.root)]), 2)
        self.assertEqual(len(ProgressStore(self.root).data.attempts), 1)

    def test_game_faint_records_operation_without_rescheduling(self):
        from ielts_codex.game_engine import GameStatus
        app, output = app_for(self.root, "")
        app.store.record_review("sustainable", Rating.GOOD)
        before = app.store.cards["sustainable"].to_dict()
        with patch.object(app.game_mode, "_can_animate", return_value=False), \
             patch.object(app.game_mode, "_play_turn_based", return_value=GameStatus.DEAD):
            result = app.game_mode.run(1)
        self.assertEqual(result.fainted, 1)
        self.assertEqual(app.store.cards["sustainable"].to_dict(), before)
        self.assertEqual(app.store.data.attempts[-1]["error_type"], "operation_failure")
        self.assertIn("未判语言失败", output.getvalue())

    def test_main_reports_conflict_instead_of_success(self):
        app, output = app_for(self.root, "\n3\n")
        other = ProgressStore(self.root)
        other.set_daily_goal(25)
        with patch("ielts_codex.cli.ProgressStore", return_value=app.store), \
             patch("ielts_codex.cli.TerminalUI", return_value=app.ui):
            code = main(["learn", "-n", "1"])
        self.assertEqual(code, 2)
        self.assertIn("未写入", output.getvalue())
        self.assertEqual(ProgressStore(self.root).daily_goal, 25)


if __name__ == "__main__":
    unittest.main()
