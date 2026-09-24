import unittest
from datetime import date, timedelta

from helpers import word
from ielts_codex.learning import LearningQueue, accepts_answer, spelling_error
from ielts_codex.models import CardProgress, Rating, Word
from ielts_codex.scheduler import schedule
from ielts_codex.word_bank import WordBank


class AnswerTests(unittest.TestCase):
    def test_only_case_and_surrounding_whitespace_are_ignored(self):
        self.assertTrue(accepts_answer(word(), " SUSTAINABLE \t"))
        for answer in ("sustain123able", "sustain able", "sustain!able", "sustainable.",
                       "sustain\nable", "sustain_able", "sustainable123"):
            with self.subTest(answer=answer):
                self.assertFalse(accepts_answer(word(), answer))

    def test_variants_must_be_explicit(self):
        self.assertFalse(accepts_answer(word("analyse"), "analyze"))
        self.assertTrue(accepts_answer(word("analyse", accepted_answers=("analyze",)), "Analyze"))
        self.assertFalse(accepts_answer(word("analyse", accepted_answers=("analyze",)), "analyzes"))

    def test_phrase_spacing_and_hyphens_are_preserved(self):
        self.assertTrue(accepts_answer(word("long-term"), " LONG-TERM "))
        self.assertFalse(accepts_answer(word("long-term"), "longterm"))
        self.assertFalse(accepts_answer(word("in contrast"), "incontrast"))

    def test_error_category_is_feedback_only(self):
        self.assertEqual(spelling_error(word(), "sustain123able"), "unexpected_digit")
        self.assertEqual(spelling_error(word(), "sustainble"), "missing_letter")
        self.assertEqual(spelling_error(word(), "sustainabble"), "extra_character")

    def test_existing_vocabulary_loads_without_variants(self):
        bank = WordBank.bundled()
        self.assertEqual(len(bank.words), 72)
        self.assertTrue(all(isinstance(item.accepted_answers, tuple) for item in bank.words))

    def test_malformed_variants_rejected(self):
        from dataclasses import asdict
        data = asdict(word())
        for value in ("analyze", [1], [""], None):
            data["accepted_answers"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                Word.from_dict(data)


class QueueTests(unittest.TestCase):
    def test_retry_is_after_two_other_cards(self):
        queue = LearningQueue([word("a"), word("b"), word("c"), word("d")])
        first = queue.pop()
        self.assertTrue(queue.repeat(first.word))
        remaining = []
        while queue:
            remaining.append(queue.pop())
        self.assertEqual([item.word.word for item in remaining], ["b", "c", "a", "d"])
        self.assertTrue(remaining[2].retry)

    def test_single_word_stops_after_three_attempts(self):
        queue = LearningQueue([word()])
        repeats = []
        while queue:
            card = queue.pop()
            repeats.append(queue.repeat(card.word))
        self.assertEqual(repeats, [True, True, False])


class SchedulerTests(unittest.TestCase):
    today = date(2026, 1, 1)

    def test_same_day_success_never_increases_interval(self):
        card = schedule(CardProgress("test"), Rating.GOOD, self.today)
        first = card.to_dict()
        for _ in range(20):
            card = schedule(card, Rating.EASY, self.today)
        self.assertEqual(card.interval, first["interval"])
        self.assertEqual(card.repetitions, first["repetitions"])
        self.assertEqual(card.due, first["due"])
        self.assertEqual(card.attempts, 21)

    def test_five_hard_ratings_are_not_successful_repetitions(self):
        card = CardProgress("test")
        for offset in range(5):
            card = schedule(card, Rating.HARD, self.today + timedelta(days=offset))
        self.assertEqual(card.repetitions, 0)
        self.assertEqual(card.interval, 1)

    def test_failed_review_stays_due_until_relearned(self):
        card = CardProgress("test", state="review", interval=30, repetitions=8,
                            due=self.today.isoformat(), last_reviewed="2025-12-01")
        failed = schedule(card, Rating.AGAIN, self.today)
        self.assertEqual(failed.due, self.today.isoformat())
        self.assertEqual(failed.lapses, 1)
        repeated = schedule(failed, Rating.AGAIN, self.today)
        self.assertEqual(repeated.ease, failed.ease)
        self.assertEqual(repeated.lapses, 1)
        recovered = schedule(repeated, Rating.EASY, self.today)
        self.assertEqual(recovered.interval, 1)
        self.assertEqual(recovered.due, "2026-01-02")

    def test_new_word_retry_graduates_to_next_day(self):
        failed = schedule(CardProgress("test"), Rating.AGAIN, self.today)
        self.assertEqual(failed.state, "learning")
        self.assertEqual(schedule(failed, Rating.GOOD, self.today).interval, 1)

    def test_early_practice_does_not_push_due_date_away(self):
        card = CardProgress("test", state="review", interval=30, repetitions=5,
                            due="2026-01-31", last_reviewed="2025-12-31")
        updated = schedule(card, Rating.GOOD, self.today)
        self.assertEqual(updated.interval, 30)
        self.assertEqual(updated.due, "2026-01-31")
        self.assertEqual(updated.repetitions, 5)

    def test_due_success_still_advances(self):
        card = schedule(CardProgress("test"), Rating.GOOD, self.today)
        card = schedule(card, Rating.GOOD, self.today + timedelta(days=1))
        self.assertEqual(card.interval, 3)
        card = schedule(card, Rating.GOOD, self.today + timedelta(days=4))
        self.assertGreater(card.interval, 3)


if __name__ == "__main__":
    unittest.main()
