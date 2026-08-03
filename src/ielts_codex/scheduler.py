"""Small, deterministic spaced-repetition scheduler."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta

from .models import CardProgress, Rating


MIN_EASE = 1.3
MAX_INTERVAL = 365


def estimated_retention(
    elapsed_days: float,
    stability_days: float = 1.0,
) -> float:
    """Estimate retention with a simple exponential forgetting curve.

    ``stability_days`` controls the curve's scale rather than claiming to
    measure an individual's memory. The CLI derives it from current review
    intervals and labels the result as a conceptual estimate.
    """

    if not math.isfinite(elapsed_days) or elapsed_days < 0:
        raise ValueError("elapsed_days must be a finite non-negative number")
    if not math.isfinite(stability_days) or stability_days <= 0:
        raise ValueError("stability_days must be a finite positive number")
    return math.exp(-elapsed_days / stability_days)


def render_forgetting_curve(
    stability_days: float,
    *,
    plot_width: int = 31,
    plot_height: int = 6,
) -> tuple[str, ...]:
    """Render a compact terminal forgetting curve with a labelled day axis."""

    if not math.isfinite(stability_days) or stability_days <= 0:
        raise ValueError("stability_days must be a finite positive number")
    if plot_width < 12 or plot_height < 4:
        raise ValueError("the forgetting-curve plot is too small")

    horizon_days = min(30, max(7, math.ceil(stability_days * 4)))
    grid = [[" " for _ in range(plot_width)] for _ in range(plot_height)]
    previous_row: int | None = None
    for column in range(plot_width):
        elapsed = horizon_days * column / (plot_width - 1)
        retention = estimated_retention(elapsed, stability_days)
        row = round((1.0 - retention) * (plot_height - 1))
        if column in {0, plot_width - 1}:
            glyph = "●"
        elif previous_row is None or row == previous_row:
            glyph = "─"
        else:
            glyph = "╲"
        grid[row][column] = glyph
        if previous_row is not None and row - previous_row > 1:
            for bridge_row in range(previous_row + 1, row):
                grid[bridge_row][column] = "│"
        previous_row = row

    lines: list[str] = []
    for row, cells in enumerate(grid):
        percentage = round(100 * (plot_height - 1 - row) / (plot_height - 1))
        lines.append(f"{percentage:>3}% ┤{''.join(cells)}")
    lines.append("     └" + "─" * plot_width)
    end_label = f"{horizon_days}d"
    label_gap = max(1, plot_width - 2 - len(end_label))
    lines.append("      0d" + " " * label_gap + end_label)
    return tuple(lines)


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
