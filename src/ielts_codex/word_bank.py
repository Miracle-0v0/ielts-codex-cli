"""Bundled IELTS vocabulary access and selection helpers."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from importlib import resources
from typing import Iterable, Mapping

from .models import CardProgress, Word


@dataclass(frozen=True, slots=True)
class SearchResult:
    word: Word
    score: float


class WordBank:
    def __init__(self, words: Iterable[Word]) -> None:
        items = tuple(words)
        if not items:
            raise ValueError("The word bank cannot be empty.")
        by_name = {item.word: item for item in items}
        if len(by_name) != len(items):
            raise ValueError("The word bank contains duplicate entries.")
        self.words = items
        self._by_name = by_name

    @classmethod
    def bundled(cls) -> "WordBank":
        resource = resources.files("ielts_codex.data").joinpath("words.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("words.json must contain a JSON list.")
        return cls(Word.from_dict(item) for item in payload)

    @property
    def topics(self) -> tuple[str, ...]:
        return tuple(sorted({item.topic for item in self.words}))

    def get(self, name: str) -> Word | None:
        return self._by_name.get(name.strip().lower())

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
            if item.word not in cards and (topic is None or item.topic == topic)
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
            and card.word in self._by_name
            and (topic is None or self._by_name[card.word].topic == topic)
        ]
        due_cards.sort(key=lambda card: (card.due, card.interval, card.word))
        return [self._by_name[card.word] for card in due_cards[:count]]

    def learned(
        self,
        cards: Mapping[str, CardProgress],
        topic: str | None = None,
    ) -> list[Word]:
        return [
            item
            for item in self.words
            if item.word in cards and (topic is None or item.topic == topic)
        ]
