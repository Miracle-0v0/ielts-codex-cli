from __future__ import annotations

import io
import random
import tempfile
import unittest

from ielts_codex.cli import IELTSApp
from ielts_codex.models import Rating
from ielts_codex.storage import ProgressStore
from ielts_codex.ui import TerminalUI
from ielts_codex.word_bank import WordBank


class CLITests(unittest.TestCase):
    def test_scripted_learn_session_persists_one_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            user_input = io.StringIO("/learn 1 environment\n\n3\n/quit\n")
            ui = TerminalUI(color=False, stream=output, input_stream=user_input)
            store = ProgressStore(directory)
            app = IELTSApp(
                WordBank.bundled(),
                store,
                ui,
                rng=random.Random(4),
            )

            exit_code = app.run()

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(store.cards), 1)
            self.assertIn("learn 完成", output.getvalue())
            self.assertIn("Good", output.getvalue())

    def test_bare_word_opens_dictionary_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            ui = TerminalUI(
                color=False,
                stream=output,
                input_stream=io.StringIO(""),
            )
            app = IELTSApp(
                WordBank.bundled(),
                ProgressStore(directory),
                ui,
            )

            app.dispatch("empirical")

            rendered = output.getvalue()
            self.assertIn("实证的", rendered)
            self.assertIn("observation or experiment", rendered)

    def test_quiz_correct_spelling_advances_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            ui = TerminalUI(
                color=False,
                stream=output,
                input_stream=io.StringIO("conservation\n"),
            )
            bank = WordBank.bundled()
            store = ProgressStore(directory)
            store.record_review("conservation", Rating.GOOD)
            app = IELTSApp(bank, store, ui, rng=random.Random(1))

            result = app.quiz(1, "environment")

            self.assertEqual(result.reviewed, 1)
            self.assertEqual(result.correct, 1)
            self.assertIn("✓ conservation", output.getvalue())
            self.assertEqual(store.cards["conservation"].interval, 3)

    def test_quiz_hint_and_miss_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            ui = TerminalUI(
                color=False,
                stream=output,
                input_stream=io.StringIO("h\nconservasion\n"),
            )
            bank = WordBank.bundled()
            store = ProgressStore(directory)
            store.record_review("conservation", Rating.GOOD)
            app = IELTSApp(bank, store, ui, rng=random.Random(1))

            result = app.quiz(1, "environment")

            self.assertEqual(result.reviewed, 1)
            self.assertEqual(result.again, 1)
            self.assertIn("提示：", output.getvalue())
            self.assertIn("conservasion  →  conservation", output.getvalue())
            self.assertEqual(store.cards["conservation"].state, "relearning")


if __name__ == "__main__":
    unittest.main()
