"""Personal imports and stable sense identities with real isolated stores."""
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from helpers import word
from ielts_codex.decks import DeckManager
from ielts_codex.models import CardProgress, Rating, Word
from ielts_codex.storage import ProgressConflictError, ProgressStore
from ielts_codex.word_bank import WordBank


class IdentityTests(unittest.TestCase):
    def test_bundled_ids_are_explicit_and_frozen(self):
        bank = WordBank.bundled()
        self.assertEqual(len({item.key for item in bank.words}), 72)
        self.assertEqual(bank.get("curriculum").key, "core-0001")
        self.assertEqual(bank.get("sustainable").key, "core-0011")
        self.assertEqual(bank.get("morale").key, "core-0072")
        self.assertTrue(all(not item.band for item in bank.words))

    def test_same_spelling_has_independent_senses(self):
        noun = replace(word("record"), id="personal-record-n", part_of_speech="n.", meaning_zh="记录")
        verb = replace(noun, id="personal-record-v", part_of_speech="v.", meaning_zh="录制")
        bank = WordBank([noun, verb])
        self.assertIsNone(bank.get("record"))
        self.assertEqual(bank.matches("record"), (noun, verb))
        self.assertEqual(bank.get(noun.key), noun)
        cards = {noun.key: CardProgress(noun.key, due="2026-01-01")}
        self.assertEqual(bank.unseen(cards, 10), [verb])
        self.assertEqual(bank.due(cards, date(2026, 1, 1), 10), [noun])

    def test_pending_and_empty_decks_never_produce_questions(self):
        pending = Word.from_dict({"word": "collect", "id": "personal-collect", "status": "pending"})
        bank = WordBank([pending])
        card = CardProgress(pending.key, due="2026-01-01")
        self.assertEqual(bank.unseen({}, 10), [])
        self.assertEqual(bank.due({pending.key: card}, date(2026, 1, 1), 10), [])
        self.assertEqual(bank.learned({pending.key: card}), [])
        self.assertEqual(bank.ready_words, ())
        self.assertEqual(WordBank([]).words, ())

    def test_external_overlay_leaves_curated_definition_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oewn_overlay.json"
            path.write_text(json.dumps({"entries": {"curriculum": {"definition_en": "Other sense"}}}), encoding="utf-8")
            self.assertEqual(WordBank.bundled(path).get("curriculum").definition_en,
                             WordBank.bundled().get("curriculum").definition_en)


class DeckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = ProgressStore(self.directory / "progress")
        self.manager = DeckManager(self.store, WordBank.bundled().words)

    def preview(self, rows):
        path = self.directory / "words.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return self.manager.preview(path)

    def entry(self, **changes):
        return {"word": "adapt", "part_of_speech": "v.", "meaning_zh": "适应", **changes}

    def test_preview_readonly_commit_reload_and_duplicate(self):
        first = self.preview([self.entry()])
        self.assertTrue(first.valid)
        self.assertEqual(self.store.data.vocabulary, {})
        self.assertFalse(self.store.path.exists())
        batch = self.manager.commit(first)
        loaded = DeckManager(ProgressStore(self.store.data_dir), WordBank.bundled().words)
        self.assertEqual(loaded.words(), first.entries)
        self.assertEqual(loaded.store.data.imports[0]["id"], batch)
        self.assertEqual(loaded.words()[0].definition_en, "")
        second = self.preview([self.entry()])
        self.assertTrue(second.valid)
        self.assertFalse(second.entries)
        self.assertEqual(len(second.duplicates), 1)
        with self.assertRaises(ValueError):
            self.manager.commit(second)

    def test_csv_bom_and_explicit_variants(self):
        path = self.directory / "words.csv"
        path.write_text("word,part_of_speech,meaning_zh,accepted_answers,collocations\nanalyse,v.,分析,analyze,analyse data|analyse evidence\n", encoding="utf-8-sig")
        preview = self.manager.preview(path, "my-writing")
        self.assertTrue(preview.valid, preview.errors)
        self.assertEqual(preview.entries[0].accepted_answers, ("analyze",))
        self.assertEqual(preview.entries[0].collocations, ("analyse data", "analyse evidence"))
        self.assertEqual(preview.entries[0].deck, "my-writing")

    def test_bad_row_blocks_whole_import(self):
        preview = self.preview([self.entry(), self.entry(word="broken", part_of_speech="")])
        self.assertFalse(preview.valid)
        self.assertIn("第 2 行", preview.errors[0])
        with self.assertRaises(ValueError):
            self.manager.commit(preview)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.store.data.vocabulary, {})

    def test_known_fields_have_strict_shapes(self):
        bad = {"meaning_zh": 123, "word": ["word"], "synonyms": "wrong",
               "collocations": [12], "forms": {}, "common_errors": [""], "notes": False,
               "source": {}, "usage": [], "accepted_answers": ["bad123"],
               "level": "band7", "status": "verified", "id": "a/b"}
        for field, value in bad.items():
            with self.subTest(field=field):
                self.assertFalse(self.preview([self.entry(**{field: value})]).valid)

    def test_terminal_controls_are_rejected_and_never_echoed(self):
        for field in ("word", "meaning_zh", "deck", "source", "notes", "bad\x1b[2J"):
            with self.subTest(field=field):
                preview = self.preview([self.entry(**{field: "bad\x1b[2J"})])
                self.assertFalse(preview.valid)
                self.assertNotIn("\x1b", "".join(preview.errors))

    def test_pending_completion_is_explicit_and_reversible(self):
        pending = self.manager.add_pending("adapt")
        self.assertEqual(pending.status, "pending")
        self.assertEqual(self.manager.add_pending("adapt"), pending)
        self.assertFalse(self.preview([self.entry()]).valid)
        filled = self.preview([self.entry(id=pending.key)])
        self.assertTrue(filled.valid, filled.errors)
        self.assertEqual(filled.updates, (pending.key,))
        self.manager.commit(filled)
        self.assertEqual(self.manager.words()[0].status, "ready")
        self.assertEqual(self.manager.words()[0].key, pending.key)
        self.manager.undo()
        self.assertEqual(self.manager.words()[0], pending)
        pure = self.preview([{"word": "pendingword"}])
        self.assertTrue(pure.valid)
        self.assertEqual(pure.pending_count, 1)
        self.assertEqual(pure.entries[0].meaning_zh, "")

    def test_pending_completion_keeps_deck_notes_unless_overridden(self):
        self.manager.commit(self.preview({"deck": "reading", "words": [
            {"word": "adapt", "id": "collected", "notes": "My reading note"}]}))
        filled = self.preview([self.entry(id="collected")])
        self.assertTrue(filled.valid, filled.errors)
        self.assertEqual(filled.entries[0].deck, "reading")
        self.assertEqual(filled.entries[0].notes, "My reading note")
        explicit = self.preview([self.entry(id="collected", deck="writing", notes="New note")])
        self.assertEqual(explicit.entries[0].deck, "writing")
        self.assertEqual(explicit.entries[0].notes, "New note")
        envelope = self.preview({"deck": "speaking", "words": [self.entry(id="collected")]})
        self.assertEqual(envelope.entries[0].deck, "speaking")
        self.manager.commit(filled)
        self.assertEqual(self.manager.words()[0].deck, "reading")

    def test_completion_cannot_duplicate_another_ready_sense(self):
        self.manager.commit(self.preview([self.entry(id="ready")]))
        self.manager.commit(self.preview([{"id": "collected", "word": "adapt", "status": "pending"}]))
        filled = self.preview([self.entry(id="collected")])
        self.assertFalse(filled.valid)
        self.assertIn("重复", filled.errors[0])
        self.assertEqual(len(self.manager.words()), 2)

    def test_different_senses_allowed_duplicate_senses_skipped(self):
        rows = [self.entry(word="record", meaning_zh="记录", part_of_speech="n."),
                self.entry(word="record", meaning_zh="录制", part_of_speech="v."),
                self.entry(word="record", meaning_zh="记录", part_of_speech="n.")]
        preview = self.preview(rows)
        self.assertTrue(preview.valid)
        self.assertEqual(len(preview.entries), 2)
        self.assertEqual(len(preview.duplicates), 1)
        self.manager.commit(preview)
        self.assertEqual(len(self.manager.words()), 2)

    def test_reserved_and_conflicting_ids_rejected(self):
        for changes in ({"id": "core-0001"}, {"id": "core-future"}, {"deck": "core"},
                        {"deck": "ALL"}):
            with self.subTest(changes=changes):
                self.assertFalse(self.preview([self.entry(**changes)]).valid)
        self.manager.commit(self.preview([self.entry(id="mine")]))
        self.assertFalse(self.preview([self.entry(id="mine", meaning_zh="改编")]).valid)

    def test_undo_preserves_progress_and_reimport_identity(self):
        preview = self.preview([self.entry()])
        batch = self.manager.commit(preview)
        key = preview.entries[0].key
        self.store.record_review(key, Rating.GOOD)
        attempts = list(self.store.data.attempts)
        self.assertEqual(self.manager.undo(batch), batch)
        self.assertEqual(self.manager.words(), ())
        self.assertIn(key, self.store.cards)
        self.assertEqual(self.store.data.attempts, attempts)
        again = self.preview([self.entry()])
        self.assertEqual(again.entries[0].key, key)
        self.manager.commit(again)
        self.assertIn(key, self.store.cards)

    def test_undo_requires_later_completion_reverted_first(self):
        pending = self.manager.add_pending("adapt")
        first = self.store.data.imports[0]["id"]
        second = self.manager.commit(self.preview([self.entry(id=pending.key)]))
        with self.assertRaises(ValueError):
            self.manager.undo(first)
        self.manager.undo(second)
        self.manager.undo(first)
        self.assertEqual(self.manager.words(), ())

    def test_stale_preview_rejected(self):
        preview = self.preview([self.entry()])
        self.manager.add_pending("collected")
        with self.assertRaises(ValueError):
            self.manager.commit(preview)
        self.assertEqual([item.word for item in self.manager.words()], ["collected"])

    def test_conflict_rolls_back_import_and_undo(self):
        preview = self.preview([self.entry()])
        with patch.object(self.store, "save", side_effect=ProgressConflictError("other writer")):
            with self.assertRaises(ProgressConflictError):
                self.manager.commit(preview)
        self.assertEqual(self.manager.words(), ())
        batch = self.manager.commit(preview)
        with patch.object(self.store, "save", side_effect=ProgressConflictError("other writer")):
            with self.assertRaises(ProgressConflictError):
                self.manager.undo(batch)
        self.assertEqual(self.manager.words(), preview.entries)
        self.assertIsNone(self.store.data.imports[0]["undone_at"])

    def test_bad_headers_and_json_shapes_return_errors(self):
        path = self.directory / "bad.csv"
        for text in ("word,word\na,a\n", "word,meaning_zh\na,b,c\n", "word,meaning_zh\na\n"):
            path.write_text(text, encoding="utf-8")
            self.assertFalse(self.manager.preview(path).valid)
        for raw in ({"word": "adapt"}, 12, [42], []):
            self.assertFalse(self.preview(raw).valid)

    def test_example_files_are_usable(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        for filename in ("vocabulary.csv", "vocabulary.json"):
            with self.subTest(filename=filename):
                preview = self.manager.preview(examples / filename)
                self.assertTrue(preview.valid, preview.errors)
                self.assertTrue(preview.entries)
                self.assertEqual(preview.pending_count, 1)


if __name__ == "__main__":
    unittest.main()
