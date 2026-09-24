"""Answer rules and a bounded queue for practice within one session."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .models import Word


def normalize_answer(value: str) -> str:
    """Only ignore surrounding whitespace and letter case."""
    return value.strip().lower()


def accepts_answer(word: Word, answer: str) -> bool:
    return normalize_answer(answer) in {
        normalize_answer(item) for item in (word.word, *word.accepted_answers)
    }


def spelling_error(word: Word, answer: str) -> str:
    """Give feedback without using similarity to accept an answer."""
    actual, expected = normalize_answer(answer), normalize_answer(word.word)
    if any(char.isdigit() for char in actual):
        return "unexpected_digit"
    if len(actual) + 1 == len(expected) and any(
        expected[:i] + expected[i + 1:] == actual for i in range(len(expected))
    ):
        return "missing_letter"
    if len(actual) == len(expected) + 1 and any(
        actual[:i] + actual[i + 1:] == expected for i in range(len(actual))
    ):
        return "extra_character"
    return "spelling"


@dataclass(frozen=True, slots=True)
class PracticeCard:
    word: Word
    retry: bool = False


class LearningQueue:
    """Place a failed word after two others, with at most three attempts."""

    def __init__(self, words: list[Word], *, spacing: int = 2, maximum: int = 3):
        if spacing < 0 or maximum < 1:
            raise ValueError("Invalid learning queue limits")
        self.pending = [PracticeCard(word) for word in words]
        self.attempts: Counter[str] = Counter()
        self.spacing = spacing
        self.maximum = maximum

    def __bool__(self) -> bool:
        return bool(self.pending)

    def pop(self) -> PracticeCard:
        card = self.pending.pop(0)
        self.attempts[card.word.key] += 1
        return card

    def repeat(self, word: Word) -> bool:
        if self.attempts[word.key] >= self.maximum:
            return False
        self.pending.insert(min(self.spacing, len(self.pending)), PracticeCard(word, True))
        return True
