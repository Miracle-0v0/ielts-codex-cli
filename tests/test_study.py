"""Daily planning checks against isolated progress directories."""
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta

from helpers import word, app_for
from test_upgrade import Store
from ielts_codex.models import Rating
from ielts_codex.word_bank import WordBank
from ielts_codex.context import load_exercises
from ielts_codex.study import build_plan
from ielts_codex import training


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.bank = WordBank.bundled()
        self.day = date(2026, 9, 24)

    def test_backlog_limits_plan_and_stops_new_words(self):
        for item in self.bank.ready_words[:25]:
            self.store.record_review(item.key, Rating.GOOD, self.day - timedelta(days=1))
        plan = build_plan(self.store, self.bank, 10, today=self.day)
        self.assertEqual(len(plan["items"]), 10)
        self.assertEqual(plan["due_count"], 25)
        self.assertEqual(plan["deferred_due"], 15)
        self.assertTrue(all(item["reason"] == "due" for item in plan["items"]))
        self.assertLessEqual(plan["max_actions"], 20)

    def test_recovery_after_three_days_reduces_workload(self):
        for item in self.bank.ready_words[:20]:
            self.store.record_review(item.key, Rating.GOOD, self.day - timedelta(days=7))
        plan = build_plan(self.store, self.bank, 40, today=self.day)
        self.assertTrue(plan["recovery"])
        self.assertLessEqual(len(plan["items"]), 10)
        self.assertFalse(any(item["reason"] == "new" for item in plan["items"]))

    def test_first_plan_is_bounded_and_does_not_duplicate_task_word(self):
        plan = build_plan(self.store, self.bank, 20, today=self.day)
        self.assertLessEqual(len(plan["items"]), 20)
        self.assertLessEqual(sum(item["reason"] == "new" for item in plan["items"]), 5)
        identities = [(item["word"], item["task"]) for item in plan["items"]]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(all(self.bank.get(item["word"]) for item in plan["items"]))

    def test_pending_and_missing_context_are_not_fabricated(self):
        bank = WordBank([replace(word(), id="personal-one", deck="personal", status="pending")])
        plan = build_plan(self.store, bank, 20, today=self.day)
        self.assertEqual(plan["items"], [])
        bank = WordBank([replace(word(), id="personal-two", deck="personal")])
        plan = build_plan(self.store, bank, 20, today=self.day)
        self.assertFalse(any(item["task"] == "context" for item in plan["items"]))

    def test_minute_limits(self):
        for value in (0, 4, 61, True, "20"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_plan(self.store, self.bank, value, today=self.day)

    def test_context_items_point_to_fixed_exercises(self):
        plan = build_plan(self.store, self.bank, 20, focus="context", today=self.day)
        by_id = {item.id: item for item in load_exercises()}
        for item in plan["items"]:
            if item["task"] == "context":
                self.assertEqual(by_id[item["exercise_id"]].word_id, item["word"])


class PracticeInteractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def app(self, answers):
        app, output = app_for(self.temp.name, answers)
        app.store = Store(self.temp.name)
        app.bank = WordBank.bundled()
        app.active_bank = lambda: app.bank
        return app, output

    def test_unlisted_context_answer_is_not_automatically_wrong(self):
        exercise = load_exercises()[0]
        app, output = self.app("another plausible expression\n" + str(exercise.answer_index + 1) + "\n")
        item = app.bank.get(exercise.word_id)
        outcome = training.context_card(app, item, exercise, 1, 1)
        self.assertTrue(outcome.correct)
        self.assertEqual(len(app.store.data.attempts), 1)
        self.assertIn("不在本题的判分范围", output.getvalue())

    def test_hint_and_wrong_form_have_separate_evidence(self):
        exercise = load_exercises()[0]
        wrong = (exercise.answer_index + 1) % len(exercise.choices) + 1
        app, _ = self.app("h\n" + str(wrong) + "\n")
        training.context_card(app, app.bank.get(exercise.word_id), exercise, 1, 1)
        event = app.store.data.attempts[-1]
        self.assertEqual(event["task"], "context")
        self.assertEqual(event["hint_level"], 1)
        self.assertFalse(event["correct"])
        self.assertEqual(event["error_type"], exercise.kind)
        self.assertEqual(event["exercise_id"], exercise.id)

    def test_context_resume_position_is_saved_with_answer(self):
        exercise = load_exercises()[0]
        app, _ = self.app(str(exercise.answer_index + 1) + "\n")
        plan = dict(id="plan",day=date.today().isoformat(),minutes=5,deck="all",
                    status="active",completed=0,max_actions=2,items=[
                        dict(id="item",word=exercise.word_id,task="context",retries=0,
                             exercise_id=exercise.id)])
        app.store.save_study(plan)
        training.context_card(app, app.bank.get(exercise.word_id), exercise, 1, 1,
                              study_item_id="item")
        loaded = Store(self.temp.name)
        self.assertEqual(loaded.data.study["status"], "complete")
        self.assertEqual(len(loaded.data.attempts), 1)

    def test_mistakes_show_three_distinct_abilities(self):
        app, output = self.app("")
        key = app.bank.ready_words[0].key
        app.store.record_review(key, Rating.GOOD, task="recall")
        app.store.record_review(key, Rating.AGAIN, task="spelling",
                                correct=False, error_type="missing_letter")
        training.handle_mistakes(app, [])
        text = output.getvalue()
        self.assertIn("认义：本次通过", text)
        self.assertIn("拼写：需要复习", text)
        self.assertIn("语境：尚未验证", text)
        self.assertIn("字母遗漏", text)


if __name__ == "__main__":
    unittest.main()
