"""Durable local progress storage with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import CardProgress, Rating
from .scheduler import schedule


SCHEMA_VERSION = 1
DEFAULT_DAILY_GOAL = 20


class ProgressFileError(RuntimeError):
    """Raised when a progress file exists but cannot be decoded safely."""


def default_data_dir() -> Path:
    override = os.environ.get("IELTS_CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ielts-codex"


@dataclass(slots=True)
class ProgressData:
    cards: dict[str, CardProgress] = field(default_factory=dict)
    sessions: dict[str, dict[str, int]] = field(default_factory=dict)
    settings: dict[str, Any] = field(
        default_factory=lambda: {"daily_goal": DEFAULT_DAILY_GOAL}
    )
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ProgressStore:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.path = self.data_dir / "progress.json"
        self.data = self._load()

    @property
    def cards(self) -> dict[str, CardProgress]:
        return self.data.cards

    @property
    def oewn_overlay_path(self) -> Path:
        from .oewn import OVERLAY_FILENAME

        return self.data_dir / OVERLAY_FILENAME

    @property
    def daily_goal(self) -> int:
        return int(self.data.settings.get("daily_goal", DEFAULT_DAILY_GOAL))

    def _load(self) -> ProgressData:
        if not self.path.exists():
            return ProgressData()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("root value is not an object")
            version = int(raw.get("version", 0))
            if version != SCHEMA_VERSION:
                raise ValueError(f"unsupported schema version {version}")
            cards = {
                name: CardProgress.from_dict(card)
                for name, card in raw.get("cards", {}).items()
            }
            sessions = {
                str(day): {
                    "reviewed": int(values.get("reviewed", 0)),
                    "correct": int(values.get("correct", 0)),
                    "learned": int(values.get("learned", 0)),
                }
                for day, values in raw.get("sessions", {}).items()
            }
            settings = dict(raw.get("settings", {}))
            settings.setdefault("daily_goal", DEFAULT_DAILY_GOAL)
            return ProgressData(
                cards=cards,
                sessions=sessions,
                settings=settings,
                created_at=str(raw.get("created_at", "")),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ProgressFileError(
                f"无法读取进度文件 {self.path}: {exc}。文件未被修改。"
            ) from exc

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "created_at": self.data.created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "settings": self.data.settings,
            "cards": {
                name: card.to_dict() for name, card in sorted(self.cards.items())
            },
            "sessions": self.data.sessions,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".progress-", suffix=".json", dir=self.data_dir
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def card_for(self, word: str, current_day: date | None = None) -> CardProgress:
        key = word.strip().lower()
        if key in self.cards:
            return self.cards[key]
        day = current_day or date.today()
        return CardProgress(word=key, due=day.isoformat())

    def record_review(
        self,
        word: str,
        rating: Rating,
        current_day: date | None = None,
    ) -> CardProgress:
        day = current_day or date.today()
        previous = self.card_for(word, day)
        was_new = previous.state == "new"
        updated = schedule(previous, rating, day)
        self.cards[updated.word] = updated

        key = day.isoformat()
        session = self.data.sessions.setdefault(
            key, {"reviewed": 0, "correct": 0, "learned": 0}
        )
        session["reviewed"] += 1
        session["correct"] += int(rating is not Rating.AGAIN)
        session["learned"] += int(was_new)
        self.save()
        return updated

    def set_daily_goal(self, value: int) -> None:
        if not 1 <= value <= 500:
            raise ValueError("Daily goal must be between 1 and 500.")
        self.data.settings["daily_goal"] = value
        self.save()

    def stats(self, total_words: int, current_day: date | None = None) -> dict[str, Any]:
        day = current_day or date.today()
        today_key = day.isoformat()
        attempts = sum(card.attempts for card in self.cards.values())
        correct = sum(card.correct for card in self.cards.values())
        due = sum(
            1 for card in self.cards.values() if card.due and card.due <= today_key
        )
        mastered = sum(
            1
            for card in self.cards.values()
            if card.interval >= 21 or card.repetitions >= 5
        )
        today_session = self.data.sessions.get(
            today_key, {"reviewed": 0, "correct": 0, "learned": 0}
        )
        return {
            "total": total_words,
            "learned": len(self.cards),
            "unseen": max(0, total_words - len(self.cards)),
            "due": due,
            "mastered": mastered,
            "attempts": attempts,
            "accuracy": round(correct / attempts * 100) if attempts else 0,
            "streak": self._streak(day),
            "today_reviewed": int(today_session.get("reviewed", 0)),
            "today_learned": int(today_session.get("learned", 0)),
            "daily_goal": self.daily_goal,
        }

    def _streak(self, current_day: date) -> int:
        active_days = {
            date.fromisoformat(key)
            for key, values in self.data.sessions.items()
            if int(values.get("reviewed", 0)) > 0
        }
        cursor = current_day
        if cursor not in active_days:
            cursor -= timedelta(days=1)
        streak = 0
        while cursor in active_days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak
