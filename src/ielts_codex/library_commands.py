"""Personal vocabulary commands: preview imports, collect words, select packs."""

from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any

from .storage import ProgressFileError
from .word_bank import WordBank


def _safe(value: Any) -> str:
    """Keep filenames, historical metadata and error messages inert in a terminal."""
    return "".join(f"\\u{ord(char):04x}" if unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                   else char for char in str(value))


def _confirm(app: Any, question: str) -> bool:
    try:
        confirmed = app.ui.prompt(question + " [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        confirmed = False
        app.ui.write()
    if not confirmed:
        app.ui.hint("已取消，词库和学习记录未改动。")
    return confirmed


def refresh_bank(app: Any) -> None:
    """Rebuild all senses, then give the game the currently selected pack."""
    bank = WordBank((*app.base_bank.words, *app.deck_manager.words()))
    bind = getattr(app.store, "bind_bank", None)
    if bind is not None:
        bind(bank)
    app.bank = bank
    app.game_mode.bank = app.active_bank()


def _refresh_saved(app: Any) -> None:
    try:
        refresh_bank(app)
    except (ValueError, OSError, ProgressFileError) as exc:
        app.ui.error(f"词库已保存，但刷新失败：{_safe(exc)}。请重新打开应用。")


def _preview_lines(app: Any, label: str, values: tuple[str, ...], limit: int = 8) -> None:
    for value in values[:limit]:
        app.ui.write(f"  {label}：{_safe(value)}")
    if len(values) > limit:
        app.ui.hint(f"  另有 {len(values) - limit} 条{label}；请修正文件后重新预览。")


def handle_import(app: Any, args: list[str]) -> None:
    if args and args[0].lower() == "undo":
        _handle_undo(app, args[1:])
        return
    if not 1 <= len(args) <= 2:
        app.ui.warning('用法：/import "vocabulary.csv" [词包名]；撤销：/import undo [批次 ID]')
        return
    try:
        preview = app.deck_manager.preview(args[0], args[1] if len(args) == 2 else "personal")
    except (ValueError, OSError, ProgressFileError) as exc:
        app.ui.error(f"无法预览词库：{_safe(exc)}")
        return
    app.ui.rule("import · 导入预览")
    app.ui.write(f"来源：{_safe(preview.source)}；默认词包：{_safe(preview.deck)}")
    app.ui.write(
        f"新增 {len(preview.entries) - len(preview.updates)} · 补全 {len(preview.updates)} · "
        f"可训练 {len(preview.entries) - preview.pending_count} · 待补全 {preview.pending_count}"
    )
    app.ui.write(f"跳过重复 {len(preview.duplicates)} · 错误 {len(preview.errors)}")
    for word in preview.entries[:8]:
        status = "待补全" if word.status == "pending" else "可训练"
        meaning = f" · {word.part_of_speech} {word.meaning_zh}" if word.status == "ready" else ""
        app.ui.write(f"  {_safe(word.word)} [{status}] · {_safe(word.deck)}{_safe(meaning)}")
        app.ui.hint(f"    ID：{_safe(word.key)}")
    if len(preview.entries) > 8:
        app.ui.hint(f"  另有 {len(preview.entries) - 8} 个词条未展开。")
    _preview_lines(app, "重复", preview.duplicates)
    _preview_lines(app, "错误", preview.errors)
    if not preview.valid:
        app.ui.error("文件存在错误，本次不会导入任何词条；修正后可重新预览。")
        return
    if not preview.entries:
        app.ui.hint("没有新增或待补全的更新，词库未改动。")
        return
    if preview.pending_count:
        app.ui.hint("待补全词条仅收集，不会作为训练标准答案；未自动生成释义。")
    if not _confirm(app, "确认导入以上词条？"):
        return
    try:
        batch_id = app.deck_manager.commit(preview)
    except (ValueError, OSError, ProgressFileError) as exc:
        app.ui.error(f"导入未完成：{_safe(exc)}")
        return
    app.ui.success(f"已导入 {len(preview.entries)} 个词条；批次 ID：{_safe(batch_id)}")
    app.ui.hint(f"可用 /import undo {_safe(batch_id)} 撤销此批导入；学习记录会保留。")
    _refresh_saved(app)


def _handle_undo(app: Any, args: list[str]) -> None:
    if len(args) > 1:
        app.ui.warning("用法：/import undo [批次 ID]")
        return
    requested = args[0] if args else None
    batch = next((entry for entry in reversed(app.store.data.imports)
                  if not entry.get("undone_at") and (requested is None or entry["id"] == requested)), None)
    if batch is None:
        app.ui.warning("没有可撤销的导入批次。" if requested is None else "未找到这个可撤销的批次 ID。")
        return
    app.ui.rule("import · 撤销预览")
    app.ui.write(f"批次 ID：{_safe(batch['id'])}")
    app.ui.write(f"来源：{_safe(batch.get('source', '未知'))} · 时间：{_safe(batch.get('timestamp', '未知'))}")
    before = batch["before"]
    removed = sum(value is None for value in before.values())
    app.ui.write(f"移除新增词条 {removed} · 还原之前内容 {len(before) - removed}")
    for item in list(batch["after"].values())[:8]:
        app.ui.write(f"  {_safe(item.get('word', ''))} · ID：{_safe(item.get('id', ''))}")
    if len(before) > 8:
        app.ui.hint(f"  另有 {len(before) - 8} 个词条未展开。")
    app.ui.hint("撤销只还原这批词库内容；已完成的学习记录仍保留。")
    if not _confirm(app, "确认撤销这一批导入？"):
        return
    try:
        batch_id = app.deck_manager.undo(str(batch["id"]))
    except (ValueError, OSError, ProgressFileError) as exc:
        app.ui.error(f"未能撤销：{_safe(exc)}")
        return
    app.ui.success(f"已撤销批次 {_safe(batch_id)}；学习记录已保留。")
    _refresh_saved(app)
    selected = app.store.data.settings.get("active_deck", "all")
    if selected != "all" and not app.active_bank().words:
        app.ui.hint("当前词包已为空；可用 /decks all 切换到全部词条。")


def handle_add(app: Any, args: list[str]) -> None:
    if not args:
        app.ui.warning("用法：/add <英文单词或短语>；收进 personal 词包，稍后补全释义。")
        return
    name = " ".join(args)
    existing = {word.key for word in app.bank.words}
    try:
        word = app.deck_manager.add_pending(name)
    except (ValueError, OSError, ProgressFileError) as exc:
        app.ui.error(f"未能收集词条：{_safe(exc)}")
        return
    if word.key in existing:
        status = "待补全" if word.status == "pending" else "可训练"
        app.ui.hint(f"{_safe(word.word)} 已存在 [{status}]；ID：{_safe(word.key)}。未重复添加。")
        return
    app.ui.success(f"已收集 {_safe(word.word)} 到 personal，状态为待补全。")
    app.ui.write(f"ID：{_safe(word.key)}")
    app.ui.hint("保留此 ID，在 CSV/JSON 中补充 part_of_speech 和 meaning_zh 后重新导入。")
    app.ui.hint("补全前不会参与训练，也不会自动生成释义。")
    _refresh_saved(app)


def handle_decks(app: Any, args: list[str]) -> None:
    decks = sorted({word.deck for word in app.bank.words} | {"core"})
    selected = app.store.data.settings.get("active_deck", "all")
    if not args:
        app.ui.rule("decks · 词包")
        ready = Counter(word.deck for word in app.bank.words if word.status == "ready")
        pending = Counter(word.deck for word in app.bank.words if word.status == "pending")
        marker = "*" if selected == "all" else " "
        app.ui.write(f"{marker} all · 可训练 {sum(ready.values())} · 待补全 {sum(pending.values())}")
        for deck in decks:
            marker = "*" if selected == deck else " "
            app.ui.write(f"{marker} {_safe(deck)} · 可训练 {ready[deck]} · 待补全 {pending[deck]}")
        if selected != "all" and selected not in decks:
            app.ui.hint(f"当前词包 {_safe(selected)} 已为空；可用 /decks all 重新选择。")
        app.ui.hint("* 表示当前词包。使用 /decks <词包名|all> 选择；待补全词条不进入训练。")
        return
    requested = " ".join(args).strip()
    if requested.lower() in {"all", "core"}:
        requested = requested.lower()
    if requested != "all" and requested not in decks:
        app.ui.error(f"没有名为 {_safe(requested)} 的词包，当前选择未改变。")
        app.ui.hint("使用 /decks 查看词包名；含空格的名称可加引号。")
        return
    try:
        app.store.set_setting("active_deck", requested)
    except (ValueError, OSError, ProgressFileError) as exc:
        app.ui.error(f"词包选择未保存：{_safe(exc)}")
        return
    app.game_mode.bank = app.active_bank()
    ready_count = len(app.active_bank().ready_words)
    app.ui.success(f"已选择 {_safe(requested)}；可训练 {ready_count} 个词条。")
    if not ready_count:
        app.ui.hint("该词包尚无可训练词条；请补全词条后导入，或选择 /decks all。")
