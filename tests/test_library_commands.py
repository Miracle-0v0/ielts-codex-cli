import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from helpers import word
from ielts_codex.decks import DeckManager
from ielts_codex.library_commands import handle_add, handle_decks, handle_import, refresh_bank
from ielts_codex.storage import ProgressConflictError
from ielts_codex.ui import TerminalUI
from ielts_codex.word_bank import WordBank


class MemoryStore:
    def __init__(self):
        self.data = SimpleNamespace(vocabulary={}, imports=[], settings={"active_deck": "all"},
                                    cards={}, attempts=[])
        self.saved = 0
        self.fail_save = False
        self.bound_bank = None

    def save(self):
        if self.fail_save:
            raise ProgressConflictError("另一个实例已经更新进度")
        self.saved += 1

    def set_setting(self, name, value):
        previous = deepcopy(self.data.settings)
        self.data.settings[name] = value
        try:
            self.save()
        except BaseException:
            self.data.settings = previous
            raise

    def bind_bank(self, bank):
        self.bound_bank = bank


class LibraryApp:
    def __init__(self, answers=""):
        self.output = io.StringIO()
        self.ui = TerminalUI(color=False, stream=self.output, input_stream=io.StringIO(answers))
        self.store = MemoryStore()
        self.base_bank = WordBank([word(id="core-0011")])
        self.bank = self.base_bank
        self.deck_manager = DeckManager(self.store, self.base_bank.words)
        self.game_mode = SimpleNamespace(bank=self.bank)

    def active_bank(self):
        selected = self.store.data.settings.get("active_deck", "all")
        if selected == "all":
            return self.bank
        return WordBank(item for item in self.bank.words if item.deck == selected)


class LibraryCommandsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def write_json(self, rows, name="vocabulary.json"):
        path = Path(self.directory.name) / name
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_preview_and_decline_never_writes(self):
        app = LibraryApp("n\n")
        path = self.write_json([{"word": "orchard", "part_of_speech": "n.", "meaning_zh": "果园"}])
        handle_import(app, [path, "reading"])
        self.assertEqual(app.store.saved, 0)
        self.assertEqual(app.store.data.vocabulary, {})
        self.assertIsNone(app.bank.get("orchard"))
        self.assertIn("导入预览", app.output.getvalue())
        self.assertIn("果园", app.output.getvalue())
        self.assertIn("已取消", app.output.getvalue())

    def test_confirmed_mixed_import_refreshes_bank(self):
        app = LibraryApp("y\n")
        path = self.write_json([{"word": "orchard", "part_of_speech": "n.", "meaning_zh": "果园"},
                                {"word": "harvest"}])
        handle_import(app, [path, "reading"])
        self.assertEqual(app.store.saved, 1)
        self.assertEqual(app.bank.get("harvest").status, "pending")
        self.assertEqual(app.bank.get("orchard").status, "ready")
        self.assertIs(app.store.bound_bank, app.bank)
        self.assertIs(app.game_mode.bank, app.bank)
        self.assertIn("待补全 1", app.output.getvalue())
        self.assertIn("批次 ID", app.output.getvalue())

    def test_file_errors_block_every_entry_and_do_not_prompt(self):
        app = LibraryApp("y\n")
        path = self.write_json([{"word": "orchard"}, {"word": "bad123"}])
        handle_import(app, [path])
        self.assertEqual(app.store.saved, 0)
        self.assertFalse(app.store.data.vocabulary)
        self.assertIn("错误 1", app.output.getvalue())
        self.assertNotIn("[y/N]", app.output.getvalue())

    def test_duplicate_only_import_is_reported_without_write_or_prompt(self):
        app = LibraryApp("y\n")
        path = self.write_json([{"word": "sustainable", "part_of_speech": "adj.", "meaning_zh": "可持续的"}])
        handle_import(app, [path])
        self.assertEqual(app.store.saved, 0)
        self.assertIn("跳过重复 1", app.output.getvalue())
        self.assertNotIn("[y/N]", app.output.getvalue())

    def test_preview_shows_first_eight_entries_and_remainder_count(self):
        app = LibraryApp("n\n")
        names = ["apple", "banana", "cherry", "date", "fig", "grape", "lemon", "mango", "orange", "pear"]
        path = self.write_json([{"word": name} for name in names])
        handle_import(app, [path])
        output = app.output.getvalue()
        self.assertIn("mango [待补全]", output)
        self.assertNotIn("orange [待补全]", output)
        self.assertIn("另有 2 个词条未展开", output)
        self.assertEqual(app.store.saved, 0)

    def test_add_phrase_stays_pending_and_duplicate_is_not_added(self):
        app = LibraryApp()
        handle_add(app, ["in", "contrast"])
        item = app.bank.get("in contrast")
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.meaning_zh, "")
        self.assertEqual(item.definition_en, "")
        self.assertEqual(app.store.saved, 1)
        handle_add(app, ["in contrast"])
        self.assertEqual(app.store.saved, 1)
        self.assertEqual(len(app.store.data.vocabulary), 1)
        self.assertIn("未重复添加", app.output.getvalue())

    def test_add_existing_ready_word_does_not_create_pending_duplicate(self):
        app = LibraryApp()
        handle_add(app, ["sustainable"])
        self.assertEqual(app.store.saved, 0)
        self.assertFalse(app.store.data.vocabulary)
        self.assertIn("已存在 [可训练]", app.output.getvalue())

    def test_selected_deck_drives_game_and_unknown_name_preserves_selection(self):
        app = LibraryApp()
        handle_add(app, ["orchard"])
        handle_decks(app, ["personal"])
        self.assertEqual(app.store.data.settings["active_deck"], "personal")
        self.assertEqual([item.word for item in app.game_mode.bank.words], ["orchard"])
        self.assertEqual(app.game_mode.bank.ready_words, ())
        saved = app.store.saved
        handle_decks(app, ["missing"])
        self.assertEqual(app.store.saved, saved)
        self.assertEqual(app.store.data.settings["active_deck"], "personal")
        handle_decks(app, [])
        self.assertIn("* personal · 可训练 0 · 待补全 1", app.output.getvalue())
        handle_decks(app, ["all"])
        self.assertEqual(len(app.game_mode.bank.words), 2)

    def test_undo_requires_confirmation_and_preserves_learning_records(self):
        app = LibraryApp("n\ny\n")
        handle_add(app, ["orchard"])
        item = app.bank.get("orchard")
        batch_id = app.store.data.imports[-1]["id"]
        app.store.data.cards[item.key] = {"attempts": 3}
        app.store.data.attempts.append({"word_id": item.key, "correct": False})
        evidence = deepcopy((app.store.data.cards, app.store.data.attempts))
        handle_import(app, ["undo", batch_id])
        self.assertIsNotNone(app.bank.get("orchard"))
        self.assertEqual(app.store.saved, 1)
        handle_import(app, ["undo", batch_id])
        self.assertIsNone(app.bank.get("orchard"))
        self.assertEqual((app.store.data.cards, app.store.data.attempts), evidence)
        self.assertIn("撤销预览", app.output.getvalue())
        self.assertIn(batch_id, app.output.getvalue())
        self.assertIsNotNone(app.store.data.imports[-1]["undone_at"])

    def test_undo_latest_can_leave_selected_deck_empty_without_switching(self):
        app = LibraryApp("y\n")
        handle_add(app, ["orchard"])
        handle_decks(app, ["personal"])
        handle_import(app, ["undo"])
        self.assertEqual(app.store.data.settings["active_deck"], "personal")
        self.assertEqual(app.game_mode.bank.words, ())
        self.assertIn("当前词包已为空", app.output.getvalue())

    def test_failed_commit_retains_existing_state(self):
        app = LibraryApp("y\n")
        app.store.fail_save = True
        path = self.write_json([{"word": "orchard"}])
        handle_import(app, [path])
        self.assertFalse(app.store.data.vocabulary)
        self.assertFalse(app.store.data.imports)
        self.assertIsNone(app.bank.get("orchard"))
        self.assertIn("导入未完成", app.output.getvalue())
        self.assertNotIn("已导入", app.output.getvalue())

    def test_missing_batch_and_invalid_usage_do_not_write(self):
        app = LibraryApp("y\n")
        handle_import(app, ["undo", "missing"])
        handle_import(app, [])
        handle_import(app, ["file.json", "one", "two"])
        handle_add(app, [])
        self.assertEqual(app.store.saved, 0)
        self.assertIn("用法", app.output.getvalue())
        self.assertNotIn("[y/N]", app.output.getvalue())

    def test_control_characters_never_reach_terminal_from_user_paths(self):
        app = LibraryApp()
        handle_import(app, ["bad\x1b[2J.json"])
        handle_decks(app, ["bad\x1b[2J"])
        handle_add(app, ["bad\x1b[2J"])
        self.assertNotIn("\x1b", app.output.getvalue())
        self.assertEqual(app.store.saved, 0)

    def test_refresh_honours_current_deck(self):
        app = LibraryApp()
        app.deck_manager.add_pending("orchard")
        app.store.data.settings["active_deck"] = "core"
        refresh_bank(app)
        self.assertEqual(len(app.bank.words), 2)
        self.assertEqual([item.word for item in app.game_mode.bank.words], ["sustainable"])


if __name__ == "__main__":
    unittest.main()
