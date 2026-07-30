from __future__ import annotations

import io
import unittest

from ielts_codex.ui import TerminalUI


class UITests(unittest.TestCase):
    def test_mixed_language_wrap_keeps_punctuation_with_word(self) -> None:
        ui = TerminalUI(
            color=False,
            stream=io.StringIO(),
            input_stream=io.StringIO(),
        )

        lines = ui._wrap(
            "例句    Wildlife conservation requires cooperation across national borders.",
            74,
        )

        self.assertGreater(len(lines), 1)
        self.assertFalse(any(line.strip() == "." for line in lines))
        self.assertEqual(lines[-1].strip(), "borders.")
        self.assertTrue(lines[-1].startswith(" " * 8))


if __name__ == "__main__":
    unittest.main()
