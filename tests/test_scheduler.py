from __future__ import annotations

import unittest
from datetime import date

from ielts_codex.models import CardProgress, Rating
from ielts_codex.scheduler import MAX_INTERVAL, schedule


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 7, 30)

    def test_new_good_card_returns_tomorrow(self) -> None:
        card = CardProgress(word="sustainable", due=self.today.isoformat())

        updated = schedule(card, Rating.GOOD, self.today)

        self.assertEqual(updated.state, "review")
        self.assertEqual(updated.interval, 1)
        self.assertEqual(updated.due, "2026-07-31")
        self.assertEqual(updated.repetitions, 1)
        self.assertEqual(updated.attempts, 1)
        self.assertEqual(updated.correct, 1)

    def test_new_again_card_remains_due_today(self) -> None:
        card = CardProgress(word="ubiquitous", due=self.today.isoformat())

        updated = schedule(card, Rating.AGAIN, self.today)

        self.assertEqual(updated.state, "learning")
        self.assertEqual(updated.due, self.today.isoformat())
        self.assertEqual(updated.interval, 0)
        self.assertEqual(updated.correct, 0)

    def test_lapse_is_due_next_day_and_reduces_ease(self) -> None:
        card = CardProgress(
            word="empirical",
            state="review",
            repetitions=4,
            interval=12,
            ease=2.5,
            due=self.today.isoformat(),
        )

        updated = schedule(card, Rating.AGAIN, self.today)

        self.assertEqual(updated.state, "relearning")
        self.assertEqual(updated.due, "2026-07-31")
        self.assertEqual(updated.lapses, 1)
        self.assertEqual(updated.ease, 2.3)

    def test_interval_is_capped(self) -> None:
        card = CardProgress(
            word="innovation",
            state="review",
            repetitions=8,
            interval=300,
            ease=3.0,
            due=self.today.isoformat(),
        )

        updated = schedule(card, Rating.EASY, self.today)

        self.assertEqual(updated.interval, MAX_INTERVAL)


if __name__ == "__main__":
    unittest.main()
