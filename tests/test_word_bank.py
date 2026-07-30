from __future__ import annotations

import random
import unittest

from ielts_codex.models import CardProgress
from ielts_codex.word_bank import WordBank


class WordBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bank = WordBank.bundled()

    def test_bundled_bank_shape(self) -> None:
        self.assertEqual(len(self.bank.words), 72)
        self.assertEqual(len(self.bank.topics), 9)
        self.assertEqual(len({word.word for word in self.bank.words}), 72)

    def test_searches_english_and_chinese(self) -> None:
        self.assertEqual(self.bank.search("ubiquitous")[0].word.word, "ubiquitous")
        chinese_results = {result.word.word for result in self.bank.search("生物多样性")}
        self.assertIn("biodiversity", chinese_results)

    def test_unseen_respects_topic_and_existing_cards(self) -> None:
        cards = {"emission": CardProgress(word="emission")}

        selected = self.bank.unseen(
            cards,
            count=20,
            topic="environment",
            rng=random.Random(7),
        )

        self.assertEqual(len(selected), 7)
        self.assertTrue(all(word.topic == "environment" for word in selected))
        self.assertNotIn("emission", {word.word for word in selected})


if __name__ == "__main__":
    unittest.main()
