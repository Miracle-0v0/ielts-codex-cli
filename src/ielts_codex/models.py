"""Domain models shared by the word bank, scheduler, and CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any


class Rating(IntEnum):
    """Four-button recall rating used by the review scheduler."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @property
    def label(self) -> str:
        return {
            Rating.AGAIN: "Again",
            Rating.HARD: "Hard",
            Rating.GOOD: "Good",
            Rating.EASY: "Easy",
        }[self]

    @classmethod
    def parse(cls, value: str) -> "Rating | None":
        aliases = {
            "1": cls.AGAIN,
            "again": cls.AGAIN,
            "a": cls.AGAIN,
            "忘了": cls.AGAIN,
            "2": cls.HARD,
            "hard": cls.HARD,
            "h": cls.HARD,
            "困难": cls.HARD,
            "3": cls.GOOD,
            "good": cls.GOOD,
            "g": cls.GOOD,
            "记得": cls.GOOD,
            "4": cls.EASY,
            "easy": cls.EASY,
            "e": cls.EASY,
            "简单": cls.EASY,
        }
        return aliases.get(value.strip().lower())


@dataclass(frozen=True, slots=True)
class Word:
    word: str
    phonetic: str
    part_of_speech: str
    meaning_zh: str
    definition_en: str
    example: str
    example_zh: str
    synonyms: tuple[str, ...]
    topic: str
    # Legacy input only. The UI uses the project's editorial level instead.
    band: str = ""
    definition_source: str = "IELTS Codex curated dataset"
    definition_license: str = "MIT"
    definition_source_url: str = ""
    accepted_answers: tuple[str, ...] = ()
    id: str = ""
    deck: str = "core"
    level: str = "core"
    status: str = "ready"
    collocations: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()
    common_errors: tuple[str, ...] = ()
    usage: str = ""
    notes: str = ""
    source: str = ""

    @property
    def key(self) -> str:
        """Persisted sense identity; handmade legacy words keep their old key."""
        return self.id or self.word

    @property
    def level_label(self) -> str:
        return {"core": "核心", "advanced": "进阶", "extension": "拓展"}[self.level]

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        for name in ("synonyms", "accepted_answers", "collocations", "forms", "common_errors"):
            values[name] = list(values[name])
        return values

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Word":
        """Validate stored/imported content without silently coercing bad types."""
        import re
        import unicodedata

        if not isinstance(data, dict):
            raise ValueError("词条必须是对象")
        lists = {"synonyms", "accepted_answers", "collocations", "forms", "common_errors"}
        defaults = {
            "word": "", "phonetic": "", "part_of_speech": "", "meaning_zh": "",
            "definition_en": "", "example": "", "example_zh": "", "topic": "personal",
            "band": "", "definition_source": "IELTS Codex curated dataset",
            "definition_license": "MIT", "definition_source_url": "", "id": "",
            "deck": "core", "level": "core", "status": "ready", "usage": "",
            "notes": "", "source": "",
        }
        unknown = set(data).difference(defaults).difference(lists)
        if unknown:
            raise ValueError(f"不支持的词条字段：{', '.join(sorted(ascii(name) for name in unknown))}")

        def clean(value: Any, name: str, *, nonempty: bool = False) -> str:
            if not isinstance(value, str):
                raise ValueError(f"{name} 必须是文本")
            if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
                raise ValueError(f"{name} 不得含控制字符或不可见格式字符")
            text = value.strip()
            if len(text) > 4000 or (nonempty and not text):
                raise ValueError(f"{name} 不能为空且最多 4000 字符")
            return text

        values = {name: clean(data.get(name, default), name) for name, default in defaults.items()}
        for name in lists:
            entries = data.get(name, [])
            if not isinstance(entries, (list, tuple)) or len(entries) > 100:
                raise ValueError(f"{name} 必须是最多 100 项的文本列表")
            values[name] = tuple(clean(item, name, nonempty=True) for item in entries)
        values["word"] = values["word"].lower()
        if not re.fullmatch(r"[a-z]+(?:[-' ][a-z]+)*", values["word"]):
            raise ValueError("word 只能包含英文字母及词间空格、连字符、英文撇号")
        values["id"] = values["id"].lower()
        if values["id"] and not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,95}", values["id"]):
            raise ValueError("id 必须是最多 96 位的字母、数字、点、冒号、下划线或连字符")
        if not values["deck"] or len(values["deck"]) > 80:
            raise ValueError("deck 不能为空且最多 80 字符")
        if values["level"] not in {"core", "advanced", "extension"}:
            raise ValueError("level 必须是 core、advanced 或 extension")
        if values["status"] not in {"ready", "pending"}:
            raise ValueError("status 必须是 ready 或 pending")
        if values["status"] == "ready" and not all(values[name] for name in ("part_of_speech", "meaning_zh")):
            raise ValueError("可训练词条需要 part_of_speech 和 meaning_zh；仅收集单词请使用 pending")
        values["accepted_answers"] = tuple(item.lower() for item in values["accepted_answers"])
        for answer in values["accepted_answers"]:
            if not re.fullmatch(r"[a-z]+(?:[-' ][a-z]+)*", answer):
                raise ValueError("accepted_answers 只能包含英文单词或短语")
        values["topic"] = values["topic"].lower() or "personal"
        return cls(**values)


@dataclass(slots=True)
class CardProgress:
    word: str
    state: str = "new"
    repetitions: int = 0
    interval: int = 0
    ease: float = 2.5
    due: str = ""
    lapses: int = 0
    attempts: int = 0
    correct: int = 0
    last_reviewed: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CardProgress":
        return cls(
            word=str(data["word"]),
            state=str(data.get("state", "new")),
            repetitions=int(data.get("repetitions", 0)),
            interval=int(data.get("interval", 0)),
            ease=float(data.get("ease", 2.5)),
            due=str(data.get("due", "")),
            lapses=int(data.get("lapses", 0)),
            attempts=int(data.get("attempts", 0)),
            correct=int(data.get("correct", 0)),
            last_reviewed=data.get("last_reviewed"),
        )
