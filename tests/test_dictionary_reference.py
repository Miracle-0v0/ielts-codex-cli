import base64
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helpers import word
from ielts_codex import dictionary_reference as reference
from ielts_codex.models import Word
from ielts_codex.oewn import OVERLAY_FILENAME, OEWNSyncError
from ielts_codex.storage import ProgressConflictError
from ielts_codex.ui import TerminalUI
from ielts_codex.word_bank import WordBank


def overlay(version="2026", definition="Able to continue without exhausting resources."):
    return {
        "schema_version": 1,
        "synced_at": "2026-09-24T00:00:00+00:00",
        "provider": {"id": "oewn", "version": version, "license": "CC BY 4.0",
                     "release_url": f"https://github.com/globalwordnet/english-wordnet/releases/tag/{version}-edition"},
        "entries": {"sustainable": {"definition_en": definition, "synset_id": "test-0001-a", "match_score": 0.82}},
        "skipped": {},
    }


def content(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class FakeSynchronizer:
    def __init__(self, payload=None, *, up_to_date=False, after_stage=None):
        self.payload = payload if payload is not None else overlay()
        self.up_to_date = up_to_date
        self.after_stage = after_stage
        self.calls = []
        self.seeded = None

    def synchronize(self, words, data_dir, *, force=False, dry_run=False):
        directory = Path(data_dir)
        path = directory / OVERLAY_FILENAME
        self.calls.append((tuple(words), directory, force, dry_run))
        self.seeded = path.read_bytes() if path.exists() else None
        if not self.up_to_date:
            path.write_bytes(content(self.payload))
        if self.after_stage:
            self.after_stage()
        return SimpleNamespace(up_to_date=self.up_to_date)


class DictionaryReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.live = self.directory / OVERLAY_FILENAME
        self.receipt = self.directory / reference.ROLLBACK_FILENAME
        self.progress = self.directory / "progress.json"
        self.progress.write_text('{"untouched": true}', encoding="utf-8")

    def app(self, answers="", synchronizer=None):
        output = io.StringIO()
        core = word(id="core-0011")
        personal = Word.from_dict({"word": "orchard", "status": "pending", "id": "personal-example", "deck": "reading"})
        app = SimpleNamespace(
            store=SimpleNamespace(data_dir=self.directory, oewn_overlay_path=self.live),
            ui=TerminalUI(color=False, stream=output, input_stream=io.StringIO(answers)),
            base_bank=WordBank([core]), bank=WordBank([core, personal]),
            synchronizer=synchronizer or FakeSynchronizer(), output=output,
        )
        return app

    def test_dry_run_previews_actual_old_new_and_teaching_meanings_without_live_writes(self):
        old = content(overlay("2025", "Old external definition."))
        self.live.write_bytes(old)
        app = self.app()
        bank = app.bank
        self.assertTrue(reference.update_reference(app, dry_run=True))
        output = app.output.getvalue()
        self.assertIn("Old external definition.", output)
        self.assertIn("Able to continue without exhausting resources.", output)
        self.assertIn("教学释义：Able to continue.", output)
        self.assertIn("CC BY 4.0", output)
        self.assertNotIn("[y/N]", output)
        self.assertEqual(self.live.read_bytes(), old)
        self.assertFalse(self.receipt.exists())
        self.assertIs(app.bank, bank)
        self.assertEqual(self.progress.read_text(), '{"untouched": true}')
        call = app.synchronizer.calls[0]
        self.assertNotEqual(call[1], self.directory)
        self.assertFalse(call[3])
        self.assertEqual(app.synchronizer.seeded, old)
        self.assertFalse(call[1].exists())

    def test_decline_leaves_first_install_absent(self):
        app = self.app("n\n")
        self.assertFalse(reference.update_reference(app))
        self.assertFalse(self.live.exists())
        self.assertFalse(self.receipt.exists())
        self.assertIn("已取消", app.output.getvalue())

    def test_confirmed_install_preserves_snapshot_and_all_learning_content(self):
        old = content(overlay("2025", "Old external definition."))
        self.live.write_bytes(old)
        app = self.app("y\n")
        bank = app.bank
        original = tuple(item.to_dict() for item in app.bank.words)
        self.assertTrue(reference.update_reference(app, force=True))
        self.assertEqual(self.live.read_bytes(), content(overlay()))
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(base64.b64decode(receipt["previous_overlay_base64"]), old)
        self.assertEqual(receipt["installed_version"], "2026")
        self.assertIs(app.bank, bank)
        self.assertEqual(tuple(item.to_dict() for item in app.bank.words), original)
        self.assertEqual(self.progress.read_text(), '{"untouched": true}')
        self.assertTrue(app.synchronizer.calls[0][2])

    def test_rollback_restores_exact_previous_bytes_and_only_once(self):
        old = content(overlay("2025", "Old external definition.")) + b"\n\n"
        self.live.write_bytes(old)
        app = self.app("y\ny\n")
        self.assertTrue(reference.update_reference(app))
        self.assertTrue(reference.rollback_reference(app))
        self.assertEqual(self.live.read_bytes(), old)
        self.assertFalse(reference.rollback_reference(app))
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertIsNotNone(receipt["rolled_back_at"])
        self.assertEqual(self.progress.read_text(), '{"untouched": true}')

    def test_first_install_rollback_explicitly_removes_reference(self):
        app = self.app("y\ny\n")
        self.assertTrue(reference.update_reference(app))
        self.assertTrue(reference.rollback_reference(app))
        self.assertFalse(self.live.exists())
        self.assertTrue(self.receipt.exists())
        self.assertIn("这是首次安装", app.output.getvalue())
        self.assertIn("移除外部词典参考", app.output.getvalue())
        self.assertEqual(len(app.bank.words), 2)

    def test_rollback_decline_does_not_change_files(self):
        app = self.app("y\nn\n")
        self.assertTrue(reference.update_reference(app))
        installed = self.live.read_bytes()
        receipt = self.receipt.read_bytes()
        self.assertFalse(reference.rollback_reference(app))
        self.assertEqual(self.live.read_bytes(), installed)
        self.assertEqual(self.receipt.read_bytes(), receipt)

    def test_up_to_date_does_not_prompt_or_replace_live_file(self):
        existing = content(overlay())
        self.live.write_bytes(existing)
        app = self.app(synchronizer=FakeSynchronizer(up_to_date=True))
        self.assertTrue(reference.update_reference(app))
        self.assertEqual(self.live.read_bytes(), existing)
        self.assertFalse(self.receipt.exists())
        self.assertNotIn("[y/N]", app.output.getvalue())
        self.assertIn("已是 Open English WordNet 2026", app.output.getvalue())

    def test_concurrent_overlay_update_stops_commit_without_overwriting(self):
        self.live.write_bytes(content(overlay("2024", "Initial definition.")))
        concurrent = content(overlay("2025", "Another process definition."))
        synchronizer = FakeSynchronizer(after_stage=lambda: self.live.write_bytes(concurrent))
        app = self.app("y\n", synchronizer)
        self.assertFalse(reference.update_reference(app))
        self.assertEqual(self.live.read_bytes(), concurrent)
        self.assertFalse(self.receipt.exists())
        self.assertIn("其他实例修改", app.output.getvalue())

    def test_os_lock_failure_does_not_install(self):
        app = self.app("y\n")
        with patch.object(reference, "_write_lock", side_effect=ProgressConflictError("另一个实例正在保存")):
            self.assertFalse(reference.update_reference(app))
        self.assertFalse(self.live.exists())
        self.assertFalse(self.receipt.exists())

    def test_atomic_install_failure_restores_previous_receipt(self):
        old = content(overlay("2025", "Old external definition."))
        self.live.write_bytes(old)
        old_receipt = b'{"old receipt": true}'
        self.receipt.write_bytes(old_receipt)
        app = self.app("y\n")
        write = reference._atomic_write

        def fail_live(path, value):
            if path == self.live:
                raise OSError("disk write failed")
            write(path, value)

        with patch.object(reference, "_atomic_write", side_effect=fail_live):
            self.assertFalse(reference.update_reference(app))
        self.assertEqual(self.live.read_bytes(), old)
        self.assertEqual(self.receipt.read_bytes(), old_receipt)

    def test_invalid_candidate_cannot_replace_reference(self):
        old = content(overlay("2025"))
        self.live.write_bytes(old)
        invalid = overlay()
        invalid["entries"]["sustainable"]["definition_en"] = ""
        app = self.app("y\n", FakeSynchronizer(invalid))
        self.assertFalse(reference.update_reference(app))
        self.assertEqual(self.live.read_bytes(), old)
        self.assertFalse(self.receipt.exists())
        self.assertNotIn("[y/N]", app.output.getvalue())

    def test_corrupted_rollback_snapshot_does_not_delete_reference(self):
        app = self.app("y\ny\n")
        self.assertTrue(reference.update_reference(app))
        installed = self.live.read_bytes()
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        receipt["previous_overlay_base64"] = "invalid base64?"
        self.receipt.write_bytes(content(receipt))
        self.assertFalse(reference.rollback_reference(app))
        self.assertEqual(self.live.read_bytes(), installed)

    def test_concurrent_change_while_confirming_rollback_is_preserved(self):
        app = self.app("y\n")
        self.assertTrue(reference.update_reference(app))
        concurrent = content(overlay("2027", "Concurrent reference."))

        def confirm(_label):
            self.live.write_bytes(concurrent)
            return "y"

        app.ui.prompt = confirm
        self.assertFalse(reference.rollback_reference(app))
        self.assertEqual(self.live.read_bytes(), concurrent)
        self.assertIn("确认期间已改变", app.output.getvalue())

    def test_describe_is_separate_sanitized_and_not_applied_to_personal_senses(self):
        self.live.write_bytes(content(overlay(definition="Reference.\x1b[2J")))
        app = self.app()
        lines = reference.describe_reference(app.store, app.base_bank.words[0])
        self.assertIn("词典参考", lines[0])
        self.assertNotIn("\x1b", "\n".join(lines))
        self.assertIn("\\u001b", "\n".join(lines))
        personal = Word.from_dict({"word": "sustainable", "deck": "personal", "status": "ready",
                                   "part_of_speech": "adj.", "meaning_zh": "个人义项"})
        self.assertEqual(reference.describe_reference(app.store, personal), [])
        self.assertEqual(app.base_bank.words[0].definition_en, "Able to continue.")

    def test_status_handles_missing_and_corrupt_reference(self):
        app = self.app()
        reference.reference_status(app)
        self.assertIn("尚未安装", app.output.getvalue())
        self.live.write_bytes(b"not json")
        reference.reference_status(app)
        self.assertIn("不可读", app.output.getvalue())
        self.assertFalse(reference.rollback_reference(app))

    def test_repair_preserves_corrupt_original_but_does_not_restore_it(self):
        damaged = b"broken previous overlay"
        self.live.write_bytes(damaged)
        app = self.app("y\ny\n")
        self.assertTrue(reference.update_reference(app))
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(base64.b64decode(receipt["previous_overlay_base64"]), damaged)
        self.assertFalse(reference.rollback_reference(app))
        self.assertEqual(self.live.read_bytes(), content(overlay()))


if __name__ == "__main__":
    unittest.main()
