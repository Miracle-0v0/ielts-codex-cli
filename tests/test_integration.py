"""End-to-end terminal flows with real stores in disposable directories."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import OrderedRandom, word
from ielts_codex.cli import IELTSApp, main
from ielts_codex.storage import ProgressStore, SCHEMA_VERSION
from ielts_codex.ui import TerminalUI
from ielts_codex.word_bank import WordBank


class IntegratedFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def app(self, answers="", bank=None):
        output = io.StringIO()
        ui = TerminalUI(color=False, stream=output, input_stream=io.StringIO(answers))
        return IELTSApp(bank or WordBank.bundled(), ProgressStore(self.root), ui,
                        rng=OrderedRandom(0)), output

    def test_import_two_senses_then_learn_and_search_by_id(self):
        source = self.root / "my words.json"
        source.write_text(json.dumps([
            dict(word="charge", part_of_speech="n.", meaning_zh="费用"),
            dict(word="charge", part_of_speech="v.", meaning_zh="指控"),
        ]), encoding="utf-8")
        app, output = self.app("y\n\n3\n\n3\n")
        app.dispatch(f'/import "{source}" mydeck')
        app.dispatch("/decks mydeck")
        self.assertEqual(len(app.active_bank().ready_words), 2)
        app.learn(2)
        senses = app.bank.matches("charge")
        self.assertEqual(len(senses), 2)
        self.assertNotEqual(senses[0].key, senses[1].key)
        self.assertTrue(all(sense.key in app.store.cards_for("recall") for sense in senses))
        self.assertNotIn("charge", app.store.cards)
        app.search("charge")
        self.assertTrue(all(sense.key in output.getvalue() for sense in senses))
        self.assertEqual(json.loads(app.store.path.read_text(encoding="utf-8"))["version"], SCHEMA_VERSION)

    def test_collection_pending_does_not_enter_learning(self):
        app, _ = self.app()
        app.dispatch("/add lexicography")
        pending = app.bank.get("lexicography")
        self.assertEqual(pending.status, "pending")
        app.dispatch("/decks personal")
        self.assertEqual(app.active_bank().ready_words, ())
        self.assertEqual(app.learn(5).reviewed, 0)
        self.assertNotIn(pending.key, app.store.cards)

    def test_study_resume_uses_saved_order_without_replaying_completed_answer(self):
        bank = WordBank([word("alpha"), word("beta")])
        app, _ = self.app("\n\n3\nq\n", bank)
        app.store.set_setting("study_profile", dict(minutes=8, focus="spelling", deck="all"))
        app.dispatch("/study 8")
        self.assertEqual(len(app.store.data.attempts), 1)
        first_id = app.store.data.attempts[0]["id"]
        remaining = [item["word"] for item in app.store.data.study["items"]]
        self.assertEqual(remaining, ["beta", "alpha", "beta"])
        resumed, _ = self.app("\n\n3\nalpha\nbeta\n", bank)
        resumed.dispatch("/study")
        self.assertEqual(resumed.store.data.study["status"], "complete")
        self.assertEqual(len(resumed.store.data.attempts), 4)
        self.assertEqual(resumed.store.data.attempts[0]["id"], first_id)
        self.assertEqual(set(resumed.store.cards_for("recall")), {"alpha", "beta"})
        self.assertEqual(set(resumed.store.cards_for("spelling")), {"alpha", "beta"})
        resumed.dispatch("/study")
        self.assertEqual(len(resumed.store.data.attempts), 4)

    def test_personal_vocabulary_is_reloaded_after_restore(self):
        source = self.root / "words.json"
        source.write_text('[{"word":"lexicography","part_of_speech":"n.","meaning_zh":"词典编纂"}]',
                          encoding="utf-8")
        app, _ = self.app("y\n")
        app.dispatch(f'/import "{source}"')
        backup = app.store.backup()
        app.deck_manager.undo()
        from ielts_codex.library_commands import refresh_bank
        refresh_bank(app)
        self.assertIsNone(app.bank.get("lexicography"))
        app.dispatch("/restore " + backup.name)
        self.assertEqual(app.bank.get("lexicography").status, "ready")

    def test_update_without_domain_is_offline_and_domains_are_independent(self):
        app, output = self.app()
        with patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            app.dispatch("/update")
        self.assertIn("核心词库", output.getvalue())
        with patch.object(app, "update_project", return_value=True) as program, \
             patch("ielts_codex.cli.update_reference", return_value=True) as dictionary:
            app.dispatch("/update program")
            program.assert_called_once()
            dictionary.assert_not_called()
            app.dispatch("/update dictionary --dry-run")
            dictionary.assert_called_once_with(app, force=False, dry_run=True)

    def test_direct_import_accepts_file_path_and_deck(self):
        source = self.root / "word list.csv"
        source.write_text("word,part_of_speech,meaning_zh\nlexicography,n.,词典编纂\n", encoding="utf-8")
        ui = TerminalUI(color=False, stream=io.StringIO(), input_stream=io.StringIO("y\n"))
        with patch("ielts_codex.cli.TerminalUI", return_value=ui):
            result = main(["import", str(source), "reading", "--data-dir", str(self.root)])
        self.assertEqual(result, 0)
        saved = ProgressStore(self.root)
        self.assertEqual(next(iter(saved.data.vocabulary.values()))["deck"], "reading")

    def test_editorial_level_replaces_band_label(self):
        app, output = self.app()
        app.search(app.bank.ready_words[0].key)
        self.assertNotIn("Band ", output.getvalue())
        self.assertIn("词包", output.getvalue())


if __name__ == "__main__":
    unittest.main()
