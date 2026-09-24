"""Offline personal vocabulary: preview first, save as one reversible batch.

CSV uses UTF-8 (a BOM is allowed), one row per sense and pipe-separated list
fields. JSON accepts a list, or {"deck": "name", "words": [...]}. A ready entry
needs word, part_of_speech and meaning_zh. A word without teaching content is
collected as pending and never becomes a scored question until completed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid4, uuid5

from .models import Word

if TYPE_CHECKING:
    from .storage import ProgressStore

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ENTRIES = 10_000
LIST_FIELDS = ("synonyms", "accepted_answers", "collocations", "forms", "common_errors")


@dataclass(frozen=True, slots=True)
class ImportPreview:
    """entries are new or completed Words; updates lists completed pending IDs.

    errors prevent the entire commit. duplicates are readable row messages and
    are skipped. before is the undo snapshot for each changed ID. The private
    fingerprint prevents committing a preview after the personal deck changed.
    """

    source: str
    deck: str
    entries: tuple[Word, ...] = ()
    errors: tuple[str, ...] = ()
    duplicates: tuple[str, ...] = ()
    updates: tuple[str, ...] = ()
    before: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    _fingerprint: str = field(default="", repr=False)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def pending_count(self) -> int:
        return sum(word.status == "pending" for word in self.entries)


def _display_safe(value: Any) -> str:
    return "".join(f"\\u{ord(char):04x}" if unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                   else char for char in str(value))


def _sense(word: Word) -> tuple[str, str, str]:
    return word.word, word.part_of_speech.casefold(), " ".join(word.meaning_zh.casefold().split())


def _fingerprint(values: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _custom_deck(value: Any) -> str:
    # Reuse the content boundary: deck names are also displayed in terminals.
    word = Word.from_dict({"word": "pending", "status": "pending", "deck": value})
    if word.deck.casefold() in {"core", "all"}:
        raise ValueError("core 和 all 是保留词包名，请给个人词包使用其他名称")
    return word.deck


class DeckManager:
    def __init__(self, store: ProgressStore, base_words: Iterable[Word]) -> None:
        self.store = store
        self.base_words = tuple(base_words)
        self._base = {word.key: word for word in self.base_words}

    def words(self) -> tuple[Word, ...]:
        words = []
        for key, raw in self.store.data.vocabulary.items():
            word = Word.from_dict(raw)
            if word.key != key or key in self._base or key.startswith("core-") or word.deck.casefold() in {"core", "all"}:
                raise ValueError("个人词库的 ID 或词包名称与内置词库冲突")
            words.append(word)
        return tuple(words)

    def preview(self, path: Path | str, deck: str = "personal") -> ImportPreview:
        """Read a CSV/JSON file without changing vocabulary or learning data."""
        source = Path(path).expanduser()
        source_name = _display_safe(source.name)
        try:
            deck_override = deck != "personal"
            deck = _custom_deck(deck)
            with source.open("rb") as handle:
                content = handle.read(MAX_IMPORT_BYTES + 1)
            if len(content) > MAX_IMPORT_BYTES:
                raise ValueError("词库文件最多 5 MB")
            text = content.decode("utf-8-sig")
            if source.suffix.lower() == ".csv":
                reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
                if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise ValueError("CSV 需要不重复的字段名")
                if "word" not in reader.fieldnames:
                    raise ValueError("CSV 缺少 word 列")
                rows = []
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f"CSV 第 {reader.line_num} 行的列数不匹配")
                    cleaned: dict[str, Any] = {name: value for name, value in row.items() if value != ""}
                    for name in LIST_FIELDS:
                        if name in cleaned:
                            cleaned[name] = cleaned[name].split("|")
                    rows.append(cleaned)
                    if len(rows) > MAX_IMPORT_ENTRIES:
                        raise ValueError("一次最多导入 10000 个词条")
                offset = 2
            elif source.suffix.lower() == ".json":
                rows = json.loads(text)
                if isinstance(rows, dict):
                    if set(rows).difference({"deck", "words"}):
                        raise ValueError("JSON 词包对象仅支持 deck 和 words 字段")
                    deck_override = deck_override or "deck" in rows
                    deck = _custom_deck(rows.get("deck", deck))
                    rows = rows.get("words")
                if not isinstance(rows, list):
                    raise ValueError("JSON 必须是词条列表或含 words 列表的对象")
                offset = 1
            else:
                raise ValueError("仅支持 .csv 和 .json 词库")
            if not rows:
                raise ValueError("词库为空")
            if len(rows) > MAX_IMPORT_ENTRIES:
                raise ValueError("一次最多导入 10000 个词条")
            return self._preview_rows(rows, source_name, deck, offset, deck_override=deck_override)
        except (OSError, UnicodeError, ValueError, csv.Error) as exc:
            return ImportPreview(source_name, _display_safe(deck), errors=(f"无法导入：{_display_safe(exc)}",))

    def _preview_rows(
        self, rows: list[Any], source: str, deck: str, offset: int = 1, *,
        deck_override: bool = False,
    ) -> ImportPreview:
        current = {word.key: word for word in (*self.base_words, *self.words())}
        by_sense = {_sense(word): word for word in current.values()}
        entries: list[Word] = []
        errors: list[str] = []
        duplicates: list[str] = []
        updates: list[str] = []
        before: dict[str, dict[str, Any] | None] = {}
        for number, raw in enumerate(rows, offset):
            prefix = f"第 {number} 行"
            try:
                if not isinstance(raw, dict):
                    raise ValueError("词条必须是对象")
                values = dict(raw)
                raw_id = values.get("id")
                previous = current.get(raw_id.strip().lower()) if isinstance(raw_id, str) else None
                if previous is not None and previous.status == "pending":
                    if "deck" not in values and not deck_override:
                        values["deck"] = previous.deck
                    # Collected notes and provenance survive completion unless
                    # the import explicitly supplies their replacements.
                    for name in ("notes", "source", "usage"):
                        if getattr(previous, name):
                            values.setdefault(name, getattr(previous, name))
                values["deck"] = _custom_deck(values.get("deck", deck))
                values.setdefault("definition_source", "用户导入；内容由用户确认")
                values.setdefault("definition_license", "")
                values.setdefault("source", source)
                teaching_fields = ("meaning_zh", "definition_en", "example", "part_of_speech")
                if "status" not in values and not any(values.get(name) for name in teaching_fields):
                    values["status"] = "pending"
                candidate = Word.from_dict(values)
                if not candidate.id:
                    identity = json.dumps([candidate.deck, *_sense(candidate)], ensure_ascii=False)
                    values["id"] = "personal-" + uuid5(NAMESPACE_URL, identity).hex
                    candidate = Word.from_dict(values)
                if candidate.key in self._base or candidate.key.startswith("core-"):
                    raise ValueError("内置词条 ID 受保护；请省略 id 或使用个人词条 ID")
                old = current.get(candidate.key)
                if old is not None:
                    if old.status == "pending" and candidate.status == "ready" and old.word == candidate.word:
                        if candidate.key in before:
                            raise ValueError("同一文件内只能更新同一 ID 一次")
                        same = by_sense.get(_sense(candidate))
                        if same is not None and same.key != candidate.key:
                            raise ValueError(f"补全后与已有义项重复（{same.key}）；请保留已有词条")
                        before[candidate.key] = old.to_dict()
                        updates.append(candidate.key)
                    elif old.to_dict() == candidate.to_dict() or _sense(old) == _sense(candidate):
                        duplicates.append(f"{prefix}：{candidate.word} 已存在（{old.key}）")
                        continue
                    else:
                        raise ValueError(f"ID {candidate.key} 已被其他义项使用")
                else:
                    same = by_sense.get(_sense(candidate))
                    if same is not None:
                        duplicates.append(f"{prefix}：{candidate.word} 的相同义项已存在（{same.key}）")
                        continue
                    pending = [word for word in current.values() if word.word == candidate.word and word.status == "pending"]
                    if pending and candidate.status == "ready":
                        raise ValueError(f"请填写待补全词条的 id：{pending[0].key}")
                    before[candidate.key] = None
                entries.append(candidate)
                if old is not None:
                    by_sense.pop(_sense(old), None)
                current[candidate.key] = candidate
                by_sense[_sense(candidate)] = candidate
            except ValueError as exc:
                errors.append(f"{prefix}：{_display_safe(exc)}")
        return ImportPreview(source, deck, tuple(entries), tuple(errors), tuple(duplicates),
                             tuple(updates), before, _fingerprint(self.store.data.vocabulary))

    def commit(self, preview: ImportPreview) -> str:
        """Commit a valid non-empty preview atomically, or leave memory unchanged."""
        if preview.errors:
            raise ValueError("词库存在错误，本次未导入：" + "；".join(preview.errors))
        if not preview.entries:
            raise ValueError("没有可导入的新增或补全词条")
        if preview._fingerprint != _fingerprint(self.store.data.vocabulary):
            raise ValueError("预览后个人词库已改变，请重新预览")
        # Validate again rather than treating a caller-created preview as trusted.
        checked = self._preview_rows([word.to_dict() for word in preview.entries], preview.source, preview.deck)
        if checked.errors or checked.duplicates or checked.entries != preview.entries or checked.before != preview.before:
            raise ValueError("导入预览已失效，请重新预览")
        before_data = deepcopy(self.store.data)
        batch_id = uuid4().hex
        after = {word.key: word.to_dict() for word in checked.entries}
        try:
            self.store.data.vocabulary.update(deepcopy(after))
            self.store.data.imports.append({
                "id": batch_id, "source": preview.source,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "before": deepcopy(checked.before), "after": after, "undone_at": None,
            })
            self.store.save()
        except BaseException:
            self.store.data = before_data
            raise
        return batch_id

    def undo(self, batch_id: str | None = None) -> str:
        """Reverse an unchanged import; learning records remain under their IDs."""
        batch = next((entry for entry in reversed(self.store.data.imports)
                      if not entry.get("undone_at") and (batch_id is None or entry["id"] == batch_id)), None)
        if batch is None:
            raise ValueError("没有可以撤销的导入批次")
        for key, after in batch["after"].items():
            if self.store.data.vocabulary.get(key) != after:
                raise ValueError("该批词条后来已修改；请先撤销后续补全或修改")
        before_data = deepcopy(self.store.data)
        try:
            for key, previous in batch["before"].items():
                if previous is None:
                    self.store.data.vocabulary.pop(key, None)
                else:
                    self.store.data.vocabulary[key] = deepcopy(previous)
            batch["undone_at"] = datetime.now(timezone.utc).isoformat()
            self.store.save()
        except BaseException:
            self.store.data = before_data
            raise
        return str(batch["id"])

    def add_pending(self, name: str, deck: str = "personal") -> Word:
        """Collect a spelling without inventing definitions or scored content."""
        deck = _custom_deck(deck)
        item = Word.from_dict({"word": name, "deck": deck, "status": "pending"})
        existing = tuple(word for word in (*self.base_words, *self.words()) if word.word == item.word)
        if len(existing) == 1:
            return existing[0]
        if existing:
            raise ValueError("已有多个同名义项，请使用 /decks 或 /search 查看各自 ID")
        preview = self._preview_rows([{"word": name, "status": "pending"}], "/add", deck)
        self.commit(preview)
        return preview.entries[0]
