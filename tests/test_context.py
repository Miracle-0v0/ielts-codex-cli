import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from importlib import resources
from unittest.mock import patch

from ielts_codex.context import ContextExercise, exercises_for, load_exercises


def example_data():
    return {
        "id": "test-001", "word_id": "core-0011", "kind": "word_form",
        "prompt": "The plan must be ___.",
        "choices": ["sustainably", "sustainable", "sustainability", "sustain"],
        "answer_index": 1,
        "explanation": "be 后需要形容词 sustainable。",
        "hint": "这里需要形容词。",
    }


class ContextAnswerTests(unittest.TestCase):
    def setUp(self):
        self.exercise = ContextExercise.from_dict(example_data())

    def test_accepts_only_correct_number_or_exact_choice(self):
        for answer in ("2", " 2 ", "sustainable", " SUSTAINABLE\t"):
            with self.subTest(answer=answer):
                self.assertTrue(self.exercise.accepts(answer))
        for answer in ("", "1", "3", "4", "0", "5", "02", "+2", "2.", "2 sustainable",
                       "sustain123able", "sustain able", "sustainable.", "sustainability",
                       "sustainble", "sustain\nable", 2, None):
            with self.subTest(answer=answer):
                self.assertFalse(self.exercise.accepts(answer))

    def test_internal_spaces_and_punctuation_are_not_removed(self):
        exercise = replace(self.exercise, choices=("long-term plan", "short-term plan"), answer_index=0)
        self.assertTrue(exercise.accepts(" LONG-TERM PLAN "))
        for answer in ("longterm plan", "long-termplan", "long-term  plan", "long-term plan!"):
            self.assertFalse(exercise.accepts(answer))

    def test_exercises_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.exercise.answer_index = 0

    def test_missing_and_malformed_fields_rejected(self):
        for field in example_data():
            data = example_data()
            del data[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                ContextExercise.from_dict(data)
        malformed = {
            "id": (None, 1, ""), "word_id": (None, 2, " "),
            "kind": (None, "spelling", ""), "prompt": (None, [], " "),
            "explanation": (None, "", 5), "hint": (None, "", []),
            "choices": ("yes,no", ["one"], ["", "two"], [1, "two"],
                        ["YES", " yes "], ["1", "two"], None),
            "answer_index": (True, "1", 1.0, -1, 4, None),
        }
        for field, values in malformed.items():
            for value in values:
                data = example_data()
                data[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    ContextExercise.from_dict(data)
        for data in (None, [], "exercise"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                ContextExercise.from_dict(data)


class ContextPackTests(unittest.TestCase):
    def read_pack(self):
        return json.loads(resources.files("ielts_codex.data").joinpath("exercises.json").read_text(encoding="utf-8"))

    def load_payload(self, payload):
        with patch("ielts_codex.context.resources.files") as files:
            files.return_value.joinpath.return_value.read_text.return_value = json.dumps(payload)
            return load_exercises()

    def test_pack_contains_both_practice_types_and_declares_source(self):
        exercises = load_exercises()
        self.assertEqual(len(exercises), 24)
        self.assertEqual({exercise.kind for exercise in exercises}, {"word_form", "collocation"})
        self.assertEqual(len({exercise.id for exercise in exercises}), len(exercises))
        pack = self.read_pack()
        self.assertTrue(pack["content_version"])
        self.assertIn("project-original", pack["provenance"]["source"])
        self.assertFalse(pack["provenance"]["external_review_claimed"])
        self.assertFalse(pack["provenance"]["official_ielts_material"])

    def test_word_references_cover_all_nine_bundled_topics(self):
        words = json.loads(resources.files("ielts_codex.data").joinpath("words.json").read_text(encoding="utf-8"))
        # Core IDs were frozen in the original pack order when stable IDs were introduced.
        by_id = {f"core-{index:04d}": word for index, word in enumerate(words, start=1)}
        exercises = load_exercises()
        self.assertTrue(all(exercise.word_id in by_id for exercise in exercises))
        self.assertEqual({by_id[exercise.word_id]["topic"] for exercise in exercises},
                         {word["topic"] for word in words})

    def test_each_option_set_has_exactly_one_accepted_text_and_number(self):
        for exercise in load_exercises():
            with self.subTest(exercise=exercise.id):
                self.assertEqual(sum(exercise.accepts(choice) for choice in exercise.choices), 1)
                self.assertEqual(sum(exercise.accepts(str(index + 1))
                                     for index in range(len(exercise.choices))), 1)
                self.assertEqual(len({choice.strip().lower() for choice in exercise.choices}),
                                 len(exercise.choices))
                self.assertEqual(exercise.prompt.count("___"), 1)
                self.assertTrue(exercise.explanation.strip())

    def test_representative_grammar_and_collocations(self):
        expected = {
            "core-0001": "to", "core-0006": "assessed", "core-0011": "sustainable",
            "core-0012": "emit", "core-0023": "in", "core-0032": "into",
            "core-0035": "nutritionally", "core-0038": "diagnoses",
            "core-0042": "subsidies", "core-0049": "hypotheses",
            "core-0052": "between", "core-0053": "replicate", "core-0069": "with",
        }
        for word_id, answer in expected.items():
            with self.subTest(word_id=word_id):
                self.assertEqual(exercises_for(word_id)[0].answer, answer)
        self.assertEqual(exercises_for("personal-word-with-no-exercises"), ())

    def test_duplicate_ids_or_bad_pack_rejected(self):
        pack = self.read_pack()
        duplicate = copy.deepcopy(pack)
        duplicate["exercises"].append(copy.deepcopy(duplicate["exercises"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load_payload(duplicate)
        for payload in (None, [], {}, {"exercises": []},
                        {**pack, "exercises": []}, {**pack, "content_version": 1},
                        {**pack, "provenance": None}, {**pack, "exercises": "not-a-list"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.load_payload(payload)


if __name__ == "__main__":
    unittest.main()
