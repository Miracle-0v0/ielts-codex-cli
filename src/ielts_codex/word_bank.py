"""Bundled IELTS vocabulary access and selection helpers."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from importlib import resources
from pathlib import Path
from typing import Iterable, Mapping

from .models import CardProgress, Word


CONTENT_VERSION = "2026.09-core72"


@dataclass(frozen=True, slots=True)
class SearchResult:
    word: Word
    score: float


class WordBank:
    def __init__(self, words: Iterable[Word]) -> None:
        items = tuple(words)
        by_key = {item.key: item for item in items}
        if len(by_key) != len(items):
            raise ValueError("The word bank contains duplicate IDs.")
        self.words = items
        self._by_key = by_key
        self._by_name: dict[str, tuple[Word, ...]] = {}
        for item in items:
            self._by_name[item.word] = (*self._by_name.get(item.word, ()), item)

    @classmethod
    def bundled(cls, overlay_path: Path | str | None = None) -> "WordBank":
        resource = resources.files("ielts_codex.data").joinpath("words.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("words.json must contain a JSON list.")
        words = tuple(Word.from_dict(item) for item in payload)
        # The overlay is a separate dictionary reference, never a replacement
        # for a curated teaching definition. Keep the argument for older callers.
        return cls(words)

    @property
    def topics(self) -> tuple[str, ...]:
        return tuple(sorted({item.topic for item in self.words}))

    def get(self, name: str) -> Word | None:
        key = name.strip().lower()
        if key in self._by_key:
            return self._by_key[key]
        matches = self.matches(key)
        return matches[0] if len(matches) == 1 else None

    def matches(self, name: str) -> tuple[Word, ...]:
        """All senses with this spelling, including pending collected entries."""
        return self._by_name.get(name.strip().lower(), ())

    @property
    def ready_words(self) -> tuple[Word, ...]:
        return tuple(item for item in self.words if item.status == "ready")

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        needle = query.strip().lower()
        if not needle:
            return []
        results: list[SearchResult] = []
        for item in self.words:
            searchable = " ".join(
                (
                    item.word,
                    item.meaning_zh,
                    item.definition_en.lower(),
                    " ".join(item.synonyms).lower(),
                )
            )
            if needle == item.word:
                score = 1.0
            elif needle in item.word:
                score = 0.92
            elif needle in searchable:
                score = 0.80
            else:
                score = SequenceMatcher(None, needle, item.word).ratio() * 0.72
            if score >= 0.38:
                results.append(SearchResult(item, score))
        return sorted(results, key=lambda result: (-result.score, result.word.word))[:limit]

    def unseen(
        self,
        cards: Mapping[str, CardProgress],
        count: int,
        topic: str | None = None,
        rng: random.Random | None = None,
    ) -> list[Word]:
        candidates = [
            item
            for item in self.words
            if item.status == "ready" and item.key not in cards
            and (topic is None or item.topic == topic)
        ]
        picker = rng or random
        picker.shuffle(candidates)
        return candidates[:count]

    def due(
        self,
        cards: Mapping[str, CardProgress],
        current_day: date,
        count: int,
        topic: str | None = None,
    ) -> list[Word]:
        due_cards = [
            card
            for card in cards.values()
            if card.due
            and card.due <= current_day.isoformat()
            and card.word in self._by_key
            and self._by_key[card.word].status == "ready"
            and (topic is None or self._by_key[card.word].topic == topic)
        ]
        due_cards.sort(key=lambda card: (card.due, card.interval, card.word))
        return [self._by_key[card.word] for card in due_cards[:count]]

    def learned(
        self,
        cards: Mapping[str, CardProgress],
        topic: str | None = None,
    ) -> list[Word]:
        return [
            item
            for item in self.words
            if item.status == "ready" and item.key in cards
            and (topic is None or item.topic == topic)
        ]
