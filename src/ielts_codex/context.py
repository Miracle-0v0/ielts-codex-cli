"""Original, fixed-answer exercises for offline vocabulary-in-context practice.

Answers are limited to the listed choices. These exercises are project content,
not official IELTS questions or evidence of an IELTS score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any


KINDS = frozenset({"collocation", "word_form"})


@dataclass(frozen=True, slots=True)
class ContextExercise:
    id: str
    word_id: str
    kind: str
    prompt: str
    choices: tuple[str, ...]
    answer_index: int
    explanation: str
    hint: str

    def __post_init__(self) -> None:
        for field in ("id", "word_id", "kind", "prompt", "explanation", "hint"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Exercise {field} must be a non-empty string.")
        if self.kind not in KINDS:
            raise ValueError("Exercise kind must be collocation or word_form.")
        if not isinstance(self.choices, tuple) or not 2 <= len(self.choices) <= 6:
            raise ValueError("Exercise choices must contain 2 to 6 options.")
        if any(not isinstance(choice, str) or not choice.strip() for choice in self.choices):
            raise ValueError("Exercise choices must be non-empty strings.")
        normalized = [choice.strip().lower() for choice in self.choices]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Exercise choices must be distinct, ignoring case and outer whitespace.")
        if any(choice.isdecimal() for choice in normalized):
            raise ValueError("Numeric choices would conflict with option-number answers.")
        if type(self.answer_index) is not int or not 0 <= self.answer_index < len(self.choices):
            raise ValueError("Exercise answer_index must identify one listed choice.")

    @property
    def answer(self) -> str:
        return self.choices[self.answer_index]

    def accepts(self, answer: str) -> bool:
        """Accept the correct option number or exact text, ignoring case/outer whitespace."""
        if not isinstance(answer, str):
            return False
        normalized = answer.strip().lower()
        return normalized in (str(self.answer_index + 1), self.answer.strip().lower())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextExercise":
        if not isinstance(data, dict):
            raise ValueError("Each exercise must be a JSON object.")
        required = {"id", "word_id", "kind", "prompt", "choices", "answer_index", "explanation", "hint"}
        missing = required.difference(data)
        if missing:
            raise ValueError(f"Exercise is missing: {', '.join(sorted(missing))}")
        if not isinstance(data["choices"], list):
            raise ValueError("Exercise choices must be a JSON list.")
        return cls(
            id=data["id"], word_id=data["word_id"], kind=data["kind"],
            prompt=data["prompt"], choices=tuple(data["choices"]),
            answer_index=data["answer_index"], explanation=data["explanation"], hint=data["hint"],
        )


def load_exercises() -> tuple[ContextExercise, ...]:
    """Read the bundled exercise pack and reject malformed or duplicate entries."""
    resource = resources.files("ielts_codex.data").joinpath("exercises.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("exercises"), list):
        raise ValueError("exercises.json must contain an exercises list.")
    if not isinstance(payload.get("content_version"), str) or not payload["content_version"].strip():
        raise ValueError("The exercise pack must declare a content_version.")
    if not isinstance(payload.get("provenance"), dict):
        raise ValueError("The exercise pack must declare its provenance.")
    exercises = tuple(ContextExercise.from_dict(item) for item in payload["exercises"])
    if not exercises:
        raise ValueError("The exercise pack cannot be empty.")
    if len({exercise.id for exercise in exercises}) != len(exercises):
        raise ValueError("The exercise pack contains duplicate exercise IDs.")
    return exercises


def exercises_for(word_id: str) -> tuple[ContextExercise, ...]:
    return tuple(exercise for exercise in load_exercises() if exercise.word_id == word_id)
