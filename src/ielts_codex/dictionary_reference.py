"""Preview and rollback external dictionary references without changing word cards."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Word
from .oewn import OEWN_HOMEPAGE, OEWN_LICENSE, OEWNSyncError, OVERLAY_FILENAME, load_overlay
from .storage import ProgressConflictError, ProgressFileError, _atomic_write, _read_bytes, _write_lock


ROLLBACK_FILENAME = "oewn_reference_rollback.json"


def _safe(value: Any) -> str:
    return "".join(f"\\u{ord(char):04x}" if unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                   else char for char in str(value))


def _digest(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _validated_overlay(path: Path) -> dict[str, Any]:
    payload = load_overlay(path)
    for name, entry in payload["entries"].items():
        if not name or not isinstance(entry.get("definition_en"), str) or not entry["definition_en"].strip():
            raise OEWNSyncError("外部词典包含缺少英文释义的条目，未安装。")
    return payload


def _confirm(app: Any, question: str) -> bool:
    try:
        confirmed = app.ui.prompt(question + " [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        confirmed = False
        app.ui.write()
    if not confirmed:
        app.ui.hint("已取消，本地词典参考未改动。")
    return confirmed


def _restore_bytes(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, content)


def _show_preview(app: Any, previous: dict[str, Any] | None, candidate: dict[str, Any]) -> None:
    old_entries = previous["entries"] if previous else {}
    new_entries = candidate["entries"]
    old_version = previous["provider"]["version"] if previous else "未安装或不可读"
    version = candidate["provider"]["version"]
    app.ui.rule("dictionary · 外部释义参考预览")
    app.ui.write(f"Open English WordNet：{_safe(old_version)} → {_safe(version)} · {OEWN_LICENSE}")
    app.ui.hint("词典义项由程序自动匹配，仅作参考；教学释义、个人词库和训练答案保持原内容。")
    changed = 0
    for word in app.base_bank.words:
        before = old_entries.get(word.word, {})
        after = new_entries.get(word.word, {})
        if before.get("definition_en") == after.get("definition_en") and before.get("synset_id") == after.get("synset_id"):
            continue
        changed += 1
        app.ui.write(f"  {_safe(word.word)} · {_safe(word.part_of_speech)}")
        app.ui.write(f"    教学释义：{_safe(word.definition_en)}")
        app.ui.write(f"    原参考：{_safe(before.get('definition_en') or '（无）')}")
        app.ui.write(f"    新参考：{_safe(after.get('definition_en') or '（本版没有匹配参考）')}")
        if after.get("synset_id"):
            app.ui.hint(f"    词典义项：{_safe(after['synset_id'])}")
    app.ui.write(f"参考变化 {changed} 个 · 已匹配 {len(new_entries)} 个 · 未匹配 {len(candidate.get('skipped', {}))} 个")
    if not changed:
        app.ui.hint("现有词条的参考释义与义项未变化；版本或来源元数据可能更新。")
    app.ui.hint(f"来源：{_safe(candidate['provider'].get('release_url') or OEWN_HOMEPAGE)}")


def update_reference(app: Any, force: bool = False, dry_run: bool = False) -> bool:
    """Fetch into staging, show actual changes, then optionally install atomically."""
    directory = Path(app.store.data_dir)
    live_path = Path(app.store.oewn_overlay_path)
    receipt_path = directory / ROLLBACK_FILENAME
    try:
        baseline = _read_bytes(live_path)
        receipt_baseline = _read_bytes(receipt_path)
        cache = directory / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="oewn-preview-", dir=cache) as temporary:
            staging = Path(temporary)
            staged_path = staging / OVERLAY_FILENAME
            previous = None
            if baseline is not None:
                staged_path.write_bytes(baseline)
                try:
                    previous = _validated_overlay(staged_path)
                except (OEWNSyncError, UnicodeError):
                    app.ui.warning("现有外部参考不可读；本次可预览修复，原文件仍会保留在回退记录中。")
            # The synchronizer may write freely here; this is an isolated preview,
            # including for the user-facing dry-run operation.
            result = app.synchronizer.synchronize(
                app.base_bank.words, staging, force=force, dry_run=False,
            )
            candidate = _validated_overlay(staged_path)
            content = staged_path.read_bytes()
            if result.up_to_date and content == baseline:
                app.ui.success(f"外部词典参考已是 Open English WordNet {_safe(candidate['provider']['version'])}。")
                app.ui.hint("教学词卡与个人词库未改变。")
                return True
            _show_preview(app, previous, candidate)
            if dry_run:
                app.ui.hint("仅预览完成；未安装外部参考，也未创建回退记录。")
                return True
            if not _confirm(app, "确认安装这份外部词典参考？"):
                return False
            receipt = {
                "schema_version": 1,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "installed_version": candidate["provider"]["version"],
                "installed_sha256": _digest(content),
                "previous_sha256": _digest(baseline),
                "previous_overlay_base64": base64.b64encode(baseline).decode("ascii") if baseline is not None else None,
                "rolled_back_at": None,
            }
            with _write_lock(directory):
                if _read_bytes(live_path) != baseline or _read_bytes(receipt_path) != receipt_baseline:
                    raise ProgressConflictError("外部参考在预览期间已被其他实例修改，请重新预览。")
                # Save the recovery snapshot first. A process exit between these
                # two atomic writes leaves the old reference intact and detectable.
                _atomic_write(receipt_path, _json_bytes(receipt))
                try:
                    _atomic_write(live_path, content)
                except BaseException:
                    _restore_bytes(receipt_path, receipt_baseline)
                    raise
            app.ui.success(f"已安装 Open English WordNet {_safe(candidate['provider']['version'])} 外部参考。")
            app.ui.hint("上一份参考已保留，可使用 /update dictionary rollback 回退。")
            return True
    except (OEWNSyncError, ProgressFileError, OSError, ValueError, UnicodeError) as exc:
        app.ui.error(f"外部词典参考更新未完成：{_safe(exc)}")
        return False


def _read_receipt(content: bytes) -> tuple[dict[str, Any], bytes | None]:
    try:
        receipt = json.loads(content.decode("utf-8"))
        if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
            raise ValueError("不支持的回退记录格式")
        installed = receipt.get("installed_sha256")
        if not isinstance(installed, str) or len(installed) != 64 or any(char not in "0123456789abcdef" for char in installed):
            raise ValueError("安装记录校验值不完整")
        encoded = receipt.get("previous_overlay_base64")
        if encoded is not None and not isinstance(encoded, str):
            raise ValueError("备份内容格式无效")
        previous = base64.b64decode(encoded, validate=True) if encoded is not None else None
        if _digest(previous) != receipt.get("previous_sha256"):
            raise ValueError("备份校验未通过")
        return receipt, previous
    except (UnicodeError, ValueError, TypeError) as exc:
        raise OEWNSyncError(f"词典回退记录不可读：{exc}") from exc


def rollback_reference(app: Any) -> bool:
    """Restore one prior reference; a first installation rolls back to no reference."""
    directory = Path(app.store.data_dir)
    live_path = Path(app.store.oewn_overlay_path)
    receipt_path = directory / ROLLBACK_FILENAME
    try:
        receipt_bytes = _read_bytes(receipt_path)
        if receipt_bytes is None:
            app.ui.warning("没有可回退的外部词典更新记录。")
            return False
        receipt, previous = _read_receipt(receipt_bytes)
        baseline = _read_bytes(live_path)
        if receipt.get("rolled_back_at") or _digest(baseline) == receipt.get("previous_sha256"):
            app.ui.hint("外部参考已处于更新前状态；没有需要执行的回退。")
            return False
        if _digest(baseline) != receipt["installed_sha256"]:
            raise ProgressConflictError("当前外部参考与安装记录不符，未覆盖；请先检查当前文件。")
        previous_version = None
        if previous is not None:
            cache = directory / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="oewn-rollback-", dir=cache) as temporary:
                snapshot = Path(temporary) / OVERLAY_FILENAME
                snapshot.write_bytes(previous)
                previous_version = _validated_overlay(snapshot)["provider"]["version"]
        app.ui.rule("dictionary · 回退预览")
        app.ui.write(f"当前安装：Open English WordNet {_safe(receipt.get('installed_version', '未知'))}")
        if previous is None:
            app.ui.write("这是首次安装；回退将移除外部词典参考，恢复为仅使用教学词卡。")
        else:
            app.ui.write(f"将恢复：Open English WordNet {_safe(previous_version)}")
        app.ui.hint("教学词卡、个人词库及学习记录保持原内容。")
        if not _confirm(app, "确认回退外部词典参考？"):
            return False
        with _write_lock(directory):
            if _read_bytes(live_path) != baseline or _read_bytes(receipt_path) != receipt_bytes:
                raise ProgressConflictError("外部参考在确认期间已改变，未执行回退。")
            _restore_bytes(live_path, previous)
            receipt["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            try:
                _atomic_write(receipt_path, _json_bytes(receipt))
            except OSError:
                # Recovery is already complete; the previous checksum records
                # this fact even if updating the receipt is interrupted.
                app.ui.warning("参考已恢复；回退时间未能写入，但原备份仍在。")
        app.ui.success("外部词典参考已回退。")
        return True
    except (OEWNSyncError, ProgressFileError, OSError, ValueError, UnicodeError) as exc:
        app.ui.error(f"词典回退未完成：{_safe(exc)}")
        return False


def reference_status(app: Any) -> None:
    path = Path(app.store.oewn_overlay_path)
    app.ui.rule("dictionary · 外部参考状态")
    if not path.exists():
        app.ui.hint("尚未安装外部词典参考；离线教学词卡可正常使用。")
    else:
        try:
            payload = _validated_overlay(path)
            app.ui.write(f"Open English WordNet {_safe(payload['provider']['version'])} · {OEWN_LICENSE}")
            app.ui.write(f"已匹配 {len(payload['entries'])} 个 · 未匹配 {len(payload.get('skipped', {}))} 个")
            app.ui.write(f"最近同步：{_safe(payload.get('synced_at', '未知'))}")
            app.ui.hint(f"来源：{_safe(payload['provider'].get('release_url') or OEWN_HOMEPAGE)}")
        except (OEWNSyncError, UnicodeError, OSError) as exc:
            app.ui.warning(f"本地外部参考不可读：{_safe(exc)}")
    app.ui.hint("外部义项由程序自动匹配，仅供参考，不覆盖教学释义或训练答案。")
    try:
        receipt_bytes = _read_bytes(Path(app.store.data_dir) / ROLLBACK_FILENAME)
        if receipt_bytes is not None:
            receipt, _ = _read_receipt(receipt_bytes)
            current_hash = _digest(_read_bytes(path))
            if not receipt.get("rolled_back_at") and current_hash == receipt["installed_sha256"]:
                app.ui.hint("可使用 /update dictionary rollback 回退最近一次安装。")
            elif current_hash == receipt.get("previous_sha256"):
                app.ui.hint("已处于上次更新前的参考状态。")
            else:
                app.ui.warning("当前参考与回退记录不一致，自动回退将停止。")
    except (OEWNSyncError, OSError) as exc:
        app.ui.warning(_safe(exc))


def describe_reference(store: Any, word: Word) -> list[str]:
    """Return separate reference lines only for the core sense that was matched."""
    if word.deck != "core" or word.status != "ready":
        return []
    path = Path(store.oewn_overlay_path)
    if not path.exists():
        return []
    try:
        payload = _validated_overlay(path)
        entry = payload["entries"].get(word.word)
        if entry is None:
            return []
        lines = [
            f"词典参考：Open English WordNet {_safe(payload['provider']['version'])} · {OEWN_LICENSE}",
            f"参考释义：{_safe(entry['definition_en'])}",
        ]
        if entry.get("synset_id"):
            lines.append(f"参考义项：{_safe(entry['synset_id'])}")
        lines.append(f"来源：{_safe(payload['provider'].get('release_url') or OEWN_HOMEPAGE)}")
        lines.append("此义项由程序自动匹配，仅供参考；教学释义与训练答案保持原内容。")
        return lines
    except (OEWNSyncError, UnicodeError, OSError):
        return ["本地外部词典参考不可读；教学词卡仍可使用。"]
