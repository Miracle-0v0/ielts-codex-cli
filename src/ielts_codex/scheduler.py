"""Small, deterministic spaced-repetition scheduler."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from .models import CardProgress, Rating


MIN_EASE = 1.3
MAX_INTERVAL = 365


def schedule(
    card: CardProgress,
    rating: Rating,
    today: date | None = None,
) -> CardProgress:
    """Return an updated card after one recall rating.

    The intervals follow a conservative SM-2-inspired progression. An ``Again``
    card remains due today when it is new, allowing the current session to show
    it once more; a lapsed review returns the following day.
    """

    current_day = today or date.today()
    was_new = card.state == "new"
    repetitions = card.repetitions
    interval = card.interval
    ease = card.ease
    lapses = card.lapses

    if rating is Rating.AGAIN:
        repetitions = 0
        ease = max(MIN_EASE, ease - 0.20)
        if was_new:
            interval = 0
            due_day = current_day
            state = "learning"
        else:
            interval = 1
            due_day = current_day + timedelta(days=1)
            state = "relearning"
            lapses += 1
    elif rating is Rating.HARD:
        repetitions += 1
        ease = max(MIN_EASE, ease - 0.15)
        interval = 1 if interval == 0 else max(1, round(interval * 1.2))
        due_day = current_day + timedelta(days=interval)
        state = "review"
    elif rating is Rating.GOOD:
        repetitions += 1
        if interval == 0:
            interval = 1
        elif repetitions <= 2:
            interval = 3
        else:
            interval = max(interval + 1, round(interval * ease))
        interval = min(MAX_INTERVAL, interval)
        due_day = current_day + timedelta(days=interval)
        state = "review"
    else:
        repetitions += 1
        ease = min(3.2, ease + 0.15)
        if interval == 0:
            interval = 4
        elif repetitions <= 2:
            interval = 7
        else:
            interval = max(interval + 2, round(interval * ease * 1.3))
        interval = min(MAX_INTERVAL, interval)
        due_day = current_day + timedelta(days=interval)
        state = "review"

    return replace(
        card,
        state=state,
        repetitions=repetitions,
        interval=interval,
        ease=round(ease, 2),
        due=due_day.isoformat(),
        lapses=lapses,
        attempts=card.attempts + 1,
        correct=card.correct + (rating is not Rating.AGAIN),
        last_reviewed=current_day.isoformat(),
    )
