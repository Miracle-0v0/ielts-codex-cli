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
    band: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Word":
        required = {
            "word",
            "phonetic",
            "part_of_speech",
            "meaning_zh",
            "definition_en",
            "example",
            "example_zh",
            "synonyms",
            "topic",
            "band",
        }
        missing = required.difference(data)
        if missing:
            raise ValueError(f"Word entry is missing: {', '.join(sorted(missing))}")
        return cls(
            word=str(data["word"]).strip().lower(),
            phonetic=str(data["phonetic"]).strip(),
            part_of_speech=str(data["part_of_speech"]).strip(),
            meaning_zh=str(data["meaning_zh"]).strip(),
            definition_en=str(data["definition_en"]).strip(),
            example=str(data["example"]).strip(),
            example_zh=str(data["example_zh"]).strip(),
            synonyms=tuple(str(item).strip() for item in data["synonyms"]),
            topic=str(data["topic"]).strip().lower(),
            band=str(data["band"]).strip(),
        )


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
