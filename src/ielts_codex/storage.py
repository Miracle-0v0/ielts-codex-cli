"""Local progress, per-attempt evidence, migration and recoverable atomic saves."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .models import CardProgress, Rating
from .scheduler import schedule


SCHEMA_VERSION = 3
DEFAULT_DAILY_GOAL = 20
TASKS = ("recall", "spelling", "context", "game")
AUTO_BACKUP_LIMIT = 10


class ProgressFileError(RuntimeError):
    """Progress cannot be read or saved safely."""


class ProgressConflictError(ProgressFileError):
    """Another instance has changed or is writing the same progress."""


def default_data_dir() -> Path:
    override = os.environ.get("IELTS_CODEX_HOME")
    return Path(override).expanduser() if override else Path.home() / ".ielts-codex"


def _data_dir(value: Path | str | None) -> Path:
    return Path(value).expanduser() if value is not None else default_data_dir()


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@contextmanager
def _write_lock(data_dir: Path) -> Iterator[None]:
    """OS locks release on process exit; the persistent file is not a stale lock."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".progress.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ProgressConflictError(
                "另一个实例正在保存进度。本次未写入，请关闭后重新打开。"
            ) from exc
        try:
            yield
        finally:
            with suppress(OSError):
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".progress-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        with suppress(FileNotFoundError):
            temporary_path.unlink()


def _backup(data_dir: Path, content: bytes, prefix: str) -> Path:
    directory = data_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = directory / f"{prefix}-{stamp}-{uuid4().hex[:8]}.json"
    with target.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return target


def _prune_backups(data_dir: Path) -> None:
    # Migration, manual and pre-restore copies are kept until explicitly removed.
    for path in sorted((data_dir / "backups").glob("progress-*.json"))[:-AUTO_BACKUP_LIMIT]:
        with suppress(OSError):
            path.unlink()


@dataclass(slots=True)
class ProgressData:
    cards: dict[str, CardProgress] = field(default_factory=dict)
    sessions: dict[str, dict[str, int]] = field(default_factory=dict)
    settings: dict[str, Any] = field(
        default_factory=lambda: {"daily_goal": DEFAULT_DAILY_GOAL}
    )
    attempts: list[dict[str, Any]] = field(default_factory=list)
    skill_cards: dict[str, dict[str, CardProgress]] = field(default_factory=dict)
    vocabulary: dict[str, dict[str, Any]] = field(default_factory=dict)
    imports: list[dict[str, Any]] = field(default_factory=list)
    study: dict[str, Any] | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def _cards_from_dict(card_values: Any) -> dict[str, CardProgress]:
    if not isinstance(card_values, dict):
        raise ValueError("cards must be an object")
    cards = {}
    for name, value in card_values.items():
        if not isinstance(value, dict):
            raise ValueError("invalid card")
        card = CardProgress.from_dict(value)
        if not name or name != card.word or card.state not in {
            "new", "learning", "review", "relearning"
        }:
            raise ValueError("invalid card name or state")
        if min(card.attempts, card.correct, card.interval, card.lapses, card.repetitions) < 0:
            raise ValueError("negative card counts")
        if card.correct > card.attempts or not 1.3 <= card.ease <= 3.2:
            raise ValueError("invalid card counts or ease")
        for day in (card.due, card.last_reviewed):
            if day:
                date.fromisoformat(day)
        cards[name] = card
    return cards


def _decode(content: bytes, path: Path) -> tuple[ProgressData, int]:
    try:
        raw = json.loads(content.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("root value is not an object")
        version = raw.get("version")
        if type(version) is not int or version not in {1, 2, SCHEMA_VERSION}:
            raise ValueError(f"unsupported schema version {version!r}")
        card_values = raw.get("cards", {})
        sessions = deepcopy(raw.get("sessions", {}))
        settings = deepcopy(raw.get("settings", {}))
        if not all(isinstance(item, dict) for item in (card_values, sessions, settings)):
            raise ValueError("cards, sessions and settings must be objects")
        cards = _cards_from_dict(card_values)
        for day, values in sessions.items():
            date.fromisoformat(day)
            if not isinstance(values, dict):
                raise ValueError("invalid daily summary")
            for key in ("reviewed", "correct", "learned"):
                number = values.setdefault(key, 0)
                if type(number) is not int or number < 0:
                    raise ValueError("invalid daily count")
        goal = settings.setdefault("daily_goal", DEFAULT_DAILY_GOAL)
        if type(goal) is not int or not 1 <= goal <= 500:
            raise ValueError("invalid daily goal")
        attempts = raw.get("attempts", []) if version >= 2 else []
        if not isinstance(attempts, list):
            raise ValueError("attempts must be a list")
        for attempt in attempts:
            if not isinstance(attempt, dict):
                raise ValueError("invalid attempt")
            if attempt["task"] not in TASKS or not isinstance(attempt["word"], str):
                raise ValueError("invalid attempt task or word")
            date.fromisoformat(attempt["day"])
            datetime.fromisoformat(attempt["timestamp"])
            for key in ("hint_level", "elapsed_days"):
                if type(attempt[key]) is not int or attempt[key] < 0:
                    raise ValueError(f"invalid {key}")
            for key in ("retry", "navigation_hint", "scheduled"):
                if type(attempt[key]) is not bool:
                    raise ValueError(f"invalid {key}")
            if attempt["correct"] is not None and type(attempt["correct"]) is not bool:
                raise ValueError("invalid correctness")
            rating = attempt["rating"]
            if rating is not None and (type(rating) is not int or rating not in {1, 2, 3, 4}):
                raise ValueError("invalid rating")
            for key in ("answer", "error_type"):
                if attempt[key] is not None and not isinstance(attempt[key], str):
                    raise ValueError(f"invalid {key}")
        skill_values = raw.get("skill_cards", {}) if version >= 3 else {"recall": card_values}
        if not isinstance(skill_values, dict) or any(task not in TASKS for task in skill_values):
            raise ValueError("invalid skill cards")
        skill_cards = {task: _cards_from_dict(values) for task, values in skill_values.items()}
        vocabulary, imports, study = raw.get("vocabulary", {}), raw.get("imports", []), raw.get("study")
        if not isinstance(vocabulary, dict) or not isinstance(imports, list):
            raise ValueError("invalid personal vocabulary or import history")
        from .models import Word
        for key, value in vocabulary.items():
            if Word.from_dict(value).key != key:
                raise ValueError("personal vocabulary ID does not match its key")
        for batch in imports:
            if (not isinstance(batch, dict) or not isinstance(batch.get("id"), str)
                    or not isinstance(batch.get("before"), dict)
                    or not isinstance(batch.get("after"), dict)):
                raise ValueError("invalid import history")
        if study is not None:
            if not isinstance(study, dict) or not isinstance(study["items"], list):
                raise ValueError("invalid daily plan")
            for key in ("completed", "max_actions", "minutes"):
                if type(study[key]) is not int or study[key] < 0:
                    raise ValueError("invalid daily plan counts")
            if study["status"] not in {"active", "complete"}:
                raise ValueError("invalid daily plan status")
            date.fromisoformat(study["day"])
            for item in study["items"]:
                if (not isinstance(item, dict) or not isinstance(item["id"], str)
                        or not isinstance(item["word"], str) or item["task"] not in TASKS):
                    raise ValueError("invalid daily plan item")
        return ProgressData(
            cards=cards, sessions=sessions, settings=settings, attempts=attempts,
            skill_cards=skill_cards, vocabulary=vocabulary, imports=imports, study=study,
            created_at=str(raw.get("created_at", "")),
        ), version
    except (UnicodeError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ProgressFileError(
            f"无法读取进度文件 {path}: {exc}。文件未被修改。"
            "可用 ielts backups 查看备份，再用 ielts restore <备份名> 恢复"
            "（自定义目录请同时传 --data-dir）。"
        ) from exc


class ProgressStore:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = _data_dir(data_dir)
        self.path = self.data_dir / "progress.json"
        self.reload()

    def reload(self) -> None:
        content = _read_bytes(self.path)
        if content is None:
            data, version = ProgressData(), SCHEMA_VERSION
        else:
            data, version = _decode(content, self.path)
        self.data = data
        self._loaded_version = version
        self._base_bytes = content

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

    def _check_current(self) -> bytes | None:
        current = _read_bytes(self.path)
        if current != self._base_bytes:
            raise ProgressConflictError(
                "进度已被另一个实例修改。本次答案或设置未写入，也未覆盖已有记录。"
                "请重新打开程序以读取最新进度，再继续学习。"
            )
        return current

    def save(self) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "created_at": self.data.created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "settings": self.data.settings,
            "cards": {name: card.to_dict() for name, card in sorted(self.cards.items())},
            "sessions": self.data.sessions,
            "attempts": self.data.attempts,
            "skill_cards": {task: {key: card.to_dict() for key, card in cards.items()}
                            for task, cards in self.data.skill_cards.items()},
            "vocabulary": self.data.vocabulary,
            "imports": self.data.imports,
            "study": self.data.study,
        }
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with _write_lock(self.data_dir):
            current = self._check_current()
            if current is not None:
                prefix = f"migration-v{self._loaded_version}" if self._loaded_version < SCHEMA_VERSION else "progress"
                _backup(self.data_dir, current, prefix)
            _atomic_write(self.path, content)
            self._base_bytes = content
            self._loaded_version = SCHEMA_VERSION
            _prune_backups(self.data_dir)

    @classmethod
    def list_backups(cls, data_dir: Path | str | None = None) -> tuple[Path, ...]:
        return tuple(sorted((_data_dir(data_dir) / "backups").glob("*.json"), reverse=True))

    def backup(self) -> Path:
        if self._base_bytes is None:
            self.save()
        with _write_lock(self.data_dir):
            content = self._check_current()
            assert content is not None
            return _backup(self.data_dir, content, "manual")

    @classmethod
    def restore_backup(cls, data_dir: Path | str | None, name: str) -> Path:
        directory = _data_dir(data_dir)
        backup_dir = directory / "backups"
        candidate = backup_dir / name
        if not name or "/" in name or "\\" in name or candidate.resolve().parent != backup_dir.resolve():
            raise ProgressFileError("请使用 backups 列出的备份文件名。")
        try:
            content = candidate.read_bytes()
            _decode(content, candidate)
        except OSError as exc:
            raise ProgressFileError(f"无法读取备份 {name}: {exc}") from exc
        target = directory / "progress.json"
        with _write_lock(directory):
            current = _read_bytes(target)
            if current is not None:
                _backup(directory, current, "before-restore")
            _atomic_write(target, content)
        return target

    def card_for(self, word: str, current_day: date | None = None, *, task: str = "recall") -> CardProgress:
        key = word.strip()
        return self.cards_for(task).get(key) or CardProgress(
            word=key, due=(current_day or date.today()).isoformat()
        )

    def record_review(
        self,
        word: str,
        rating: Rating | None,
        current_day: date | None = None,
        *,
        task: str = "recall",
        correct: bool | None = None,
        hint_level: int = 0,
        retry: bool = False,
        answer: str | None = None,
        error_type: str | None = None,
        navigation_hint: bool = False,
        exercise_id: str | None = None,
        study_item_id: str | None = None,
    ) -> CardProgress:
        if task not in TASKS or type(hint_level) is not int or hint_level < 0:
            raise ValueError("Invalid practice task or hint level")
        if correct is not None and type(correct) is not bool:
            raise ValueError("correct must be a boolean or None")
        if rating is not None:
            rating = Rating(rating)
            if correct is None and task != "game":
                correct = rating is not Rating.AGAIN
            if hint_level and rating > Rating.HARD:
                rating = Rating.HARD
            if correct is False:
                rating = Rating.AGAIN
        day = current_day or date.today()
        previous = self.card_for(word, day, task=task)
        last_practice = next(
            (event["day"] for event in reversed(self.data.attempts) if event["word"] == previous.word),
            previous.last_reviewed,
        )
        elapsed = max(0, (day - date.fromisoformat(last_practice)).days) if last_practice else 0
        updated = schedule(previous, rating, day) if rating is not None else previous
        if rating is not None:
            updated.correct = previous.correct + int(correct is True)
        before = deepcopy(self.data)
        try:
            if rating is not None:
                self.data.skill_cards.setdefault(task, {})[updated.word] = updated
                aggregate = self.cards.get(updated.word)
                from dataclasses import replace
                related = [group[updated.word] for kind, group in self.data.skill_cards.items()
                           if kind != "game" and updated.word in group]
                urgent = min(related, key=lambda card: card.due) if related else replace(
                    updated, state="new", due="", interval=0, repetitions=0
                )
                self.cards[updated.word] = replace(
                    urgent, attempts=(aggregate.attempts if aggregate else 0) + 1,
                    correct=(aggregate.correct if aggregate else 0) + int(correct is True),
                    last_reviewed=day.isoformat(),
                )
                session = self.data.sessions.setdefault(
                    day.isoformat(), {"reviewed": 0, "correct": 0, "learned": 0}
                )
                session["reviewed"] += 1
                session["correct"] += int(correct is True)
                session["learned"] += int(aggregate is None)
            self.data.attempts.append({
                "id": uuid4().hex,
                "word": updated.word,
                "task": task,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "day": day.isoformat(),
                "rating": int(rating) if rating is not None else None,
                "correct": correct,
                "hint_level": hint_level,
                "retry": retry,
                "elapsed_days": elapsed,
                "answer": answer,
                "error_type": error_type,
                "navigation_hint": navigation_hint,
                "scheduled": rating is not None,
                "exercise_id": exercise_id,
            })
            if study_item_id:
                self._advance_study(study_item_id, rating)
            self.save()
        except BaseException:
            self.data = before
            raise
        return updated

    def set_daily_goal(self, value: int) -> None:
        if type(value) is not int or not 1 <= value <= 500:
            raise ValueError("Daily goal must be between 1 and 500.")
        before = deepcopy(self.data.settings)
        self.data.settings["daily_goal"] = value
        try:
            self.save()
        except BaseException:
            self.data.settings = before
            raise

    def reviewed_words_on(self, current_day: date | None = None) -> tuple[str, ...]:
        day_key = (current_day or date.today()).isoformat()
        names = {
            attempt["word"] for attempt in self.data.attempts
            if attempt["day"] == day_key and attempt["scheduled"]
        }
        names.update(card.word for card in self.cards.values() if card.last_reviewed == day_key)
        return tuple(sorted(names))

    def _evidence(self, word_ids: set[str] | None = None) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
        metrics = {task: dict(attempts=0, correct=0, independent=0, independent_correct=0,
                              assisted=0, assisted_correct=0, retries=0) for task in TASKS}
        streaks: dict[tuple[str, str], tuple[int, int, str]] = {}
        seen: set[tuple[str, str]] = set()
        for event in self.data.attempts:
            task, name = event["task"], event["word"]
            if word_ids is not None and name not in word_ids:
                continue
            item = metrics[task]
            if event["correct"] is not None:
                item["attempts"] += 1
                item["correct"] += int(event["correct"])
                if event["hint_level"]:
                    item["assisted"] += 1
                    item["assisted_correct"] += int(event["correct"])
                elif not event["retry"]:
                    item["independent"] += 1
                    item["independent_correct"] += int(event["correct"])
                item["retries"] += int(event["retry"])
            if task == "game":
                continue
            key = (task, name)
            passes, gap, last_day = streaks.get(key, (0, 0, ""))
            if event["correct"] is False or event["hint_level"] or event["rating"] in {1, 2}:
                passes, gap = 0, 0
            elif (key in seen and event["correct"] is True
                  and event["rating"] in {3, 4} and not event["retry"]
                  and event["elapsed_days"] >= 1 and event["day"] != last_day):
                passes += 1
                gap, last_day = event["elapsed_days"], event["day"]
            streaks[key] = passes, gap, last_day
            seen.add(key)
        stable = {task: 0 for task in TASKS}
        for (task, _word), (passes, gap, _day) in streaks.items():
            if passes >= 3 and gap >= 7:
                stable[task] += 1
        return metrics, stable

    def stats(self, total_words: int, current_day: date | None = None, *, word_ids: set[str] | None = None) -> dict[str, Any]:
        day = current_day or date.today()
        today_key = day.isoformat()
        metrics, stable = self._evidence(word_ids)
        cards = {key: card for key, card in self.cards.items() if word_ids is None or key in word_ids}
        today_session = self.data.sessions.get(today_key, {})
        return {
            "total": total_words,
            "learned": len(cards),
            "unseen": max(0, total_words - len(cards)),
            "due": sum(1 for card in cards.values() if card.due and card.due <= today_key),
            "stable_by_task": stable,
            "by_task": metrics,
            "attempts": sum(card.attempts for card in cards.values()),
            "recorded_attempts": len(self.data.attempts),
            "streak": self._streak(day),
            "today_reviewed": int(today_session.get("reviewed", 0)),
            "today_learned": int(today_session.get("learned", 0)),
            "daily_goal": self.daily_goal,
        }

    def _streak(self, current_day: date) -> int:
        active_days = {
            date.fromisoformat(key) for key, values in self.data.sessions.items()
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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        before = deepcopy(self.data)
        try:
            yield
            self.save()
        except BaseException:
            self.data = before
            raise

    def bind_bank(self, bank: Any) -> None:
        names = set(self.cards)
        names.update(event["word"] for event in self.data.attempts)
        mapping = {}
        for name in names:
            word = bank.get(name)
            if word is not None and word.key != name and word.word == name.lower():
                mapping[name] = word.key
        for group in [self.cards, *self.data.skill_cards.values()]:
            for old, new in mapping.items():
                if old in group:
                    if new in group:
                        raise ProgressFileError(f"进度中同时存在旧键和词条 ID：{old} / {new}，未合并。")
                    card = group.pop(old)
                    card.word = new
                    group[new] = card
        for event in self.data.attempts:
            event["word"] = mapping.get(event["word"], event["word"])
        if self.data.study:
            for item in self.data.study["items"]:
                item["word"] = mapping.get(item["word"], item["word"])

    def cards_for(self, task: str) -> dict[str, CardProgress]:
        if task not in TASKS:
            raise ValueError(f"Unknown task: {task}")
        return self.data.skill_cards.get(task, {})

    def set_setting(self, key: str, value: Any) -> None:
        with self.transaction():
            self.data.settings[key] = value

    def save_study(self, plan: dict[str, Any]) -> None:
        with self.transaction():
            self.data.study = deepcopy(plan)

    def _advance_study(self, item_id: str, rating: Rating | None) -> None:
        plan = self.data.study
        if not plan or not plan["items"] or plan["items"][0]["id"] != item_id:
            raise ProgressFileError("今日学习位置已变化，请重新打开 /study。")
        item = plan["items"].pop(0)
        plan["completed"] += 1
        if rating is Rating.AGAIN and item.get("retries", 0) < 2:
            item = dict(item, id=uuid4().hex, retries=item.get("retries", 0) + 1)
            plan["items"].insert(min(2, len(plan["items"])), item)
        if plan["completed"] >= plan["max_actions"]:
            plan["items"] = []
        if not plan["items"]:
            plan["status"] = "complete"
            if rating is not None:
                completed_days = self.data.settings.setdefault("completed_study_days", [])
                if plan["day"] not in completed_days:
                    completed_days.append(plan["day"])

    def skip_study(self, item_id: str) -> None:
        with self.transaction():
            self._advance_study(item_id, None)

    def ability_status(self, word: str, task: str) -> str:
        events = [event for event in self.data.attempts
                  if event["word"] == word and event["task"] == task]
        if not events:
            return "尚未验证"
        last = events[-1]
        if last["correct"] is not True or last["hint_level"] or last["rating"] in {1, 2}:
            return "需要复习"
        if last["retry"]:
            return "本组重练通过"
        if last["elapsed_days"] >= 1:
            return "跨日通过"
        return "本次通过"

    def mistakes(self, task: str | None = None) -> list[dict[str, Any]]:
        latest = {}
        for event in self.data.attempts:
            if event["task"] == "game" or (task and event["task"] != task):
                continue
            key = (event["word"], event["task"])
            if event["correct"] is False or event["hint_level"] or event["rating"] in {1, 2}:
                latest[key] = event
            elif event["correct"] is True and not event["retry"]:
                latest.pop(key, None)
        return sorted(latest.values(), key=lambda event: event["timestamp"], reverse=True)
