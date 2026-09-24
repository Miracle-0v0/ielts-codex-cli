"""Shared in-memory terminal and isolated study fixtures."""
import io
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ielts_codex.cli import IELTSApp
from ielts_codex.models import Word
from ielts_codex.storage import ProgressStore
from ielts_codex.ui import TerminalUI
from ielts_codex.word_bank import WordBank


def word(name="sustainable", **kwargs):
    return Word(
        word=name, phonetic="", part_of_speech="adj.", meaning_zh="可持续的",
        definition_en="Able to continue.", example=f"A {name} approach.",
        example_zh="一种可持续的方法。", synonyms=(), topic="environment", band="7.0",
        **kwargs,
    )


class OrderedRandom(random.Random):
    def shuffle(self, values):
        pass

    def sample(self, values, count):
        return list(values)[:count]


def app_for(directory, answers, words=None):
    output = io.StringIO()
    ui = TerminalUI(color=False, stream=output, input_stream=io.StringIO(answers))
    app = IELTSApp(
        WordBank(words or [word()]), ProgressStore(directory), ui,
        rng=OrderedRandom(0),
    )
    return app, output
