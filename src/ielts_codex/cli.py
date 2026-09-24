"""Command-line entry point and Codex-inspired interactive shell."""

from __future__ import annotations

import argparse
import os
import random
import re
import shlex
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from typing import Sequence

from . import __version__
from . import training
from .decks import DeckManager
from .library_commands import handle_import, handle_add, handle_decks, refresh_bank
from .dictionary_reference import update_reference, reference_status, rollback_reference, describe_reference
from .game_mode import GAME_DEFAULT_COUNT, GAME_MAX_COUNT, GameMode
from .learning import LearningQueue, accepts_answer, normalize_answer, spelling_error
from .models import Rating, Word
from .oewn import OEWNSyncError, OEWNSynchronizer, load_overlay
from .scheduler import render_forgetting_curve
from .storage import ProgressFileError, ProgressStore
from .ui import TerminalUI
from .updater import ProjectUpdateError, ProjectUpdater
from .word_bank import WordBank, CONTENT_VERSION


DEFAULT_SESSION_SIZE = 10
TOPIC_ALIASES = {
    "环境": "environment",
    "教育": "education",
    "科技": "technology",
    "技术": "technology",
    "社会": "society",
    "健康": "health",
    "经济": "economy",
    "科学": "science",
    "文化": "culture",
    "工作": "work",
}
SLASH_COMMANDS = (
    ("/study", "开始或继续今日学习"),
    ("/import", "导入个人 CSV/JSON 词库"),
    ("/add", "收集待补全词条"),
    ("/decks", "选择学习词包"),
    ("/context", "搭配与词形练习"),
    ("/mistakes", "按能力复习错项"),
    ("/learn", "学习未练认义的词条"),
    ("/review", "复习到期卡片"),
    ("/quiz", "中文到英文拼写"),
    ("/game", "口袋像素拼写远征"),
    ("/search", "查询单词或释义"),
    ("/words", "浏览词表"),
    ("/topics", "查看主题"),
    ("/today", "查看今日计划与中英词单"),
    ("/curve", "查看艾宾浩斯遗忘曲线"),
    ("/stats", "查看学习统计"),
    ("/goal", "修改每日目标"),
    ("/backup", "备份学习进度"),
    ("/backups", "查看可恢复备份"),
    ("/restore", "从备份恢复进度"),
    ("/update", "更新知识库与程序"),
    ("/clear", "清屏"),
    ("/help", "查看命令帮助"),
    ("/quit", "保存并退出"),
)


@dataclass(slots=True)
class SessionResult:
    reviewed: int = 0
    correct: int = 0
    again: int = 0
    stopped: bool = False
    words: list[str] = field(default_factory=list)
    independent_attempts: int = 0
    independent_correct: int = 0
    assisted: int = 0
    retries: int = 0
    deferred: int = 0


@dataclass(frozen=True, slots=True)
class RecallOutcome:
    rating: Rating
    hint_level: int = 0


class IELTSApp:
    def __init__(
        self,
        bank: WordBank,
        store: ProgressStore,
        ui: TerminalUI,
        *,
        rng: random.Random | None = None,
        synchronizer: OEWNSynchronizer | None = None,
        project_updater: ProjectUpdater | None = None,
    ) -> None:
        self.base_bank = bank
        self.store = store
        self.deck_manager = DeckManager(store, bank.words)
        self.bank = WordBank((*bank.words, *self.deck_manager.words()))
        self.store.bind_bank(self.bank)
        self.ui = ui
        self.rng = rng or random.Random()
        self.synchronizer = synchronizer or OEWNSynchronizer()
        self.project_updater = project_updater or ProjectUpdater(
            update_root=store.data_dir / "update"
        )
        self._restart_required_version: str | None = None
        self._pending_update_version: str | None = None
        self._pending_update_command: str | None = None
        self.game_mode = GameMode(self.active_bank(), store, ui, rng=self.rng)
        self.running = True

    def run(self) -> int:
        self.ui.banner(__version__)
        self.show_today(compact=True)
        self.ui.hint(
            "  输入 /study 开始今日学习，/import 加入自己的词；"
            "/help 查看命令。"
        )
        self.ui.write()

        while self.running:
            try:
                line = self.ui.command_prompt(SLASH_COMMANDS)
            except EOFError:
                self.ui.write()
                break
            except KeyboardInterrupt:
                self.ui.write()
                self.ui.hint("已取消当前输入；输入 /quit 退出。")
                continue

            if not line.strip():
                continue
            self.dispatch(line)

        self.ui.success("已退出，学习进度已保存在本地。")
        return 0

    def dispatch(self, line: str) -> None:
        try:
            lexer = shlex.shlex(line, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.escape = ""
            tokens = list(lexer)
        except ValueError as exc:
            self.ui.error(f"命令格式有误：{exc}")
            return
        if not tokens:
            return

        raw_command = tokens[0]
        command = raw_command.removeprefix("/").lower()
        args = tokens[1:]
        aliases = {
            "l": "learn",
            "r": "review",
            "q": "quiz",
            "g": "game",
            "s": "stats",
            "find": "search",
            "?": "help",
            "exit": "quit",
        }
        command = aliases.get(command, command)

        handlers = {
            "study": self._command_study,
            "context": self._command_context,
            "mistakes": self._command_mistakes,
            "import": self._command_import,
            "add": self._command_add,
            "decks": self._command_decks,
            "learn": self._command_learn,
            "review": self._command_review,
            "quiz": self._command_quiz,
            "search": self._command_search,
            "words": self._command_words,
            "topics": self._command_topics,
            "stats": self._command_stats,
            "today": self._command_today,
            "curve": self._command_curve,
            "goal": self._command_goal,
            "backup": self._command_backup,
            "backups": self._command_backups,
            "restore": self._command_restore,
            "update": self._command_update,
            # Kept for scripts written before /update; intentionally omitted
            # from the slash palette and help.
            "sync": self._command_sync,
            "game": self._command_game,
            "help": self._command_help,
            "clear": self._command_clear,
            "quit": self._command_quit,
        }
        if command in handlers:
            handlers[command](args)
            return

        # A bare token behaves like the Codex prompt: natural intent first,
        # then a dictionary lookup when it resembles a word.
        if not raw_command.startswith("/") and len(tokens) == 1:
            self.search(raw_command)
            return
        self.ui.error(f"未知命令：{raw_command}")
        self.ui.hint("输入 /help 查看可用命令。")

    def execute_direct(
        self,
        command: str,
        *,
        count: int,
        topic: str | None,
        query: str | None = None,
        extra: Sequence[str] = (),
        force: bool = False,
        dry_run: bool = False,
    ) -> int:
        values = ([query] if query is not None else []) + list(extra)
        if command in {"study", "import", "add", "decks", "mistakes"}:
            getattr(self, "_command_" + command)(values)
        elif command == "context":
            if values:
                self._command_context(values)
            else:
                training.run_context(self, count, topic)
        elif command == "learn":
            self.learn(count, topic)
        elif command == "review":
            self.review(count, topic)
        elif command == "quiz":
            self.quiz(count, topic)
        elif command == "backup":
            if query is not None:
                self.ui.error("用法：ielts backup")
                return 2
            self._command_backup([])
        elif command == "stats":
            self.show_stats()
        elif command == "today":
            if query is None:
                self.show_today()
            elif query.lower() in {"words", "list"}:
                self.show_today_words()
            else:
                self.ui.error("用法：ielts today [words]")
                return 2
        elif command == "topics":
            self.show_topics()
        elif command == "curve":
            if query is not None:
                self.ui.error("用法：ielts curve")
                return 2
            self.show_forgetting_curve()
        elif command == "search":
            if not query:
                self.ui.error("search 需要一个单词或中文释义。")
                return 2
            self.search(query)
        elif command in {"update", "sync"}:
            if command == "sync":
                values.insert(0, "dictionary")
            if force:
                values.append("--force")
            if dry_run:
                values.append("--dry-run")
            return int(not self._command_update(values))
        elif command == "game":
            self.game_mode.run(count, topic)
        return 0

    def active_bank(self) -> WordBank:
        selected = self.store.data.settings.get("active_deck", "all")
        return self.bank if selected == "all" else WordBank(
            word for word in self.bank.words if word.deck == selected
        )

    def _stats(self):
        keys = {word.key for word in self.active_bank().ready_words}
        return self.store.stats(len(keys), word_ids=keys)

    def _command_import(self, args: list[str]) -> None:
        handle_import(self, args)

    def _command_add(self, args: list[str]) -> None:
        handle_add(self, args)

    def _command_decks(self, args: list[str]) -> None:
        handle_decks(self, args)

    def _command_study(self, args: list[str]) -> None:
        training.handle_study(self, args)

    def _command_mistakes(self, args: list[str]) -> None:
        training.handle_mistakes(self, args)

    def _command_context(self, args: list[str]) -> None:
        parsed = self._parse_session_args(args, default_count=5, example="/context 5 education")
        if parsed:
            training.run_context(self, *parsed)

    def _command_learn(self, args: list[str]) -> None:
        parsed = self._parse_session_args(args)
        if parsed:
            self.learn(*parsed)

    def _command_review(self, args: list[str]) -> None:
        parsed = self._parse_session_args(args)
        if parsed:
            self.review(*parsed)

    def _command_quiz(self, args: list[str]) -> None:
        parsed = self._parse_session_args(args)
        if parsed:
            self.quiz(*parsed)

    def _command_search(self, args: list[str]) -> None:
        if not args:
            self.ui.warning("用法：/search <单词或中文释义>")
            return
        self.search(" ".join(args))

    def _command_words(self, args: list[str]) -> None:
        topic = self._normalize_topic(args[0]) if args else None
        if topic and topic not in self.bank.topics:
            self._unknown_topic(topic)
            return
        self.show_words(topic)

    def _command_topics(self, _args: list[str]) -> None:
        self.show_topics()

    def _command_stats(self, _args: list[str]) -> None:
        self.show_stats()

    def _command_today(self, args: list[str]) -> None:
        if not args:
            self.show_today()
            return
        if len(args) == 1 and args[0].lower() in {"words", "list", "词单"}:
            self.show_today_words()
            return
        self.ui.warning("用法：/today [words]")

    def _command_curve(self, args: list[str]) -> None:
        if args:
            self.ui.warning("用法：/curve")
            return
        self.show_forgetting_curve()

    def _command_goal(self, args: list[str]) -> None:
        if len(args) != 1 or not args[0].isdigit():
            self.ui.warning("用法：/goal <1-500>")
            return
        try:
            self.store.set_daily_goal(int(args[0]))
        except ValueError:
            self.ui.error("每日目标须在 1 到 500 之间。")
            return
        self.ui.success(f"每日目标已设为 {self.store.daily_goal} 个复习动作。")

    def _command_update(self, args: list[str]) -> bool:
        if not args or args == ["status"]:
            self.show_update_status()
            self.ui.hint("/update program 更新程序；/update dictionary 预览外部词典参考。")
            return True
        domain, rest = args[0], args[1:]
        if domain == "program" and all(arg in {"--dry-run"} for arg in rest):
            return self.update_project(dry_run="--dry-run" in rest)
        if domain == "dictionary":
            if rest == ["status"]:
                reference_status(self)
                return True
            if rest == ["rollback"]:
                return rollback_reference(self)
            if all(arg in {"--force", "--dry-run"} for arg in rest):
                return update_reference(self, force="--force" in rest, dry_run="--dry-run" in rest)
        self.ui.warning("用法：/update [status|program|dictionary]；词典可用 status、rollback、--force、--dry-run。")
        return False

    def _command_sync(self, args: list[str]) -> None:
        self._command_update(["dictionary", *args])

    def _command_game(self, args: list[str]) -> None:
        if not args:
            self.game_mode.run()
            return
        action = args[0].lower()
        if action in {"help", "?"}:
            self.game_mode.show_help()
            return
        if action in {"providers", "provider", "apis"}:
            self.game_mode.show_providers()
            return
        if action in {"code", "secret"}:
            code = "".join(args[1:]) if len(args) > 1 else None
            self.game_mode.enter_secret_code(code)
            return
        if action in {"music", "bgm", "sound"}:
            if len(args) > 2:
                self.ui.warning("用法：/game music [on|off|status]")
                return
            self.game_mode.configure_bgm(args[1] if len(args) == 2 else None)
            return
        if action == "pet":
            if len(args) == 2 and args[1].lower() == "status":
                self.game_mode.show_pet_status()
                return
            if len(args) >= 3 and args[1].lower() == "create":
                self.game_mode.create_pet(" ".join(args[2:]))
                return
            if len(args) >= 2 and args[1].lower() not in {"create", "status"}:
                self.game_mode.create_pet(" ".join(args[1:]))
                return
            self.ui.warning("用法：/game pet create <图片路径>  或  /game pet status")
            return

        parsed = self._parse_session_args(
            args,
            default_count=GAME_DEFAULT_COUNT,
            maximum=GAME_MAX_COUNT,
            example="/game 3 environment",
        )
        if parsed:
            self.game_mode.run(*parsed)

    def _command_help(self, _args: list[str]) -> None:
        self.show_help()

    def _command_clear(self, _args: list[str]) -> None:
        self.ui.clear()
        self.ui.banner(__version__)

    def _command_quit(self, _args: list[str]) -> None:
        self.running = False

    def _local_oewn_version(self) -> str | None:
        path = self.store.oewn_overlay_path
        if not path.exists():
            return None
        try:
            payload = load_overlay(path)
        except OEWNSyncError:
            self.ui.warning("  本地 OEWN 缓存不可读；联网更新可自动修复。")
            return None
        return str(payload["provider"].get("version", "未知"))

    def update_all(self, *, force: bool = False, dry_run: bool = False) -> bool:
        """Legacy entry now shows the separate update choices without downloading."""
        self.show_update_status()
        self.ui.hint("请分别使用 /update program 和 /update dictionary。")
        return True

    def update_project(self, *, dry_run: bool = False) -> bool:
        """Check and safely install the latest stable GitHub release."""

        if self._restart_required_version is not None:
            self.ui.success(
                "程序已在本次会话更新至 "
                f"{self._restart_required_version}；请先退出并重新启动。"
            )
            return True
        if self._pending_update_version is not None:
            self.ui.success(
                "程序更新包已准备至 "
                f"{self._pending_update_version}；请先退出 IELTS Codex。"
            )
            if self._pending_update_command:
                self.ui.hint(
                    f"退出后双击运行：{self._pending_update_command}"
                )
            return True

        mode = "预览更新" if dry_run else "检查更新"
        self.ui.write()
        self.ui.rule(f"update · IELTS Codex · {mode}")
        self.ui.hint("正在读取官方 GitHub stable release；不会安装测试版本或降级…")
        try:
            result = self.project_updater.update(dry_run=dry_run)
        except (ProjectUpdateError, OSError) as exc:
            self.ui.error(f"程序更新失败：{exc}")
            self.ui.hint(
                "当前进程仍运行原版本；请重启后用 --version 确认安装状态。"
            )
            self.ui.hint("知识库更新结果不受影响。")
            return False

        if result.status == "updated":
            self._restart_required_version = result.latest_version
            self.ui.panel(
                "application update complete",
                [
                    f"版本      {result.current_version} → {result.latest_version}",
                    f"安装方式  {result.install_kind}",
                    f"发布页    {result.release_url}",
                ],
            )
            self.ui.success("程序已安全更新；退出并重新启动后使用新版本。")
            self._refresh_command_launcher(result.install_kind)
            return True
        if result.status == "staged":
            if not result.completion_command:
                self.ui.error("Windows 更新包已下载，但安装脚本路径缺失。")
                return False
            self._pending_update_version = result.latest_version
            self._pending_update_command = result.completion_command
            self.ui.panel(
                "application update ready",
                [
                    f"版本      {result.current_version} → {result.latest_version}",
                    f"安装方式  {result.install_kind} · Windows 退出后安装",
                    f"安装脚本  {result.completion_command}",
                    f"发布页    {result.release_url}",
                ],
            )
            self.ui.success("更新包已下载并验证；当前安装没有被修改。")
            self.ui.hint(
                "退出所有 IELTS Codex 窗口后，双击运行上面的 "
                "ielts-update.cmd；完成后重新打开终端。"
            )
            return True
        if result.status == "up_to_date":
            self.ui.success(
                f"程序已是最新 stable 版本：IELTS Codex {result.latest_version}"
            )
            self._refresh_command_launcher(result.install_kind)
            return True
        if result.status == "ahead":
            self.ui.success(
                f"本地版本 {result.current_version} 高于 GitHub stable "
                f"{result.latest_version}；不会降级。"
            )
            return True
        if result.status == "available":
            self.ui.panel(
                "application update preview",
                [
                    f"当前      {result.current_version}",
                    f"可更新    {result.latest_version}",
                    f"安装方式  {result.install_kind}",
                    "结果      仅预览，未修改程序文件",
                    f"发布页    {result.release_url}",
                ],
            )
            return True

        self.ui.warning(result.message)
        if result.release_url:
            self.ui.hint(f"可手动安装官方 release：{result.release_url}")
        return False

    def _refresh_command_launcher(self, install_kind: str) -> None:
        """Re-run the source installer so the ``ielts`` command stays registered.

        Source checkouts are only reachable through the ``ielts`` shim that
        ``install.bat`` writes into the user PATH, so a successful ``/update``
        refreshes that registration as well.
        """

        if os.name != "nt" or install_kind != "source":
            return
        try:
            target = self.project_updater.detect_install()
        except (ProjectUpdateError, OSError) as exc:
            self.ui.warning(f"无法定位源码目录，ielts 命令未刷新（{exc}）。")
            return
        if target.kind != "source" or target.root is None:
            return
        installer = target.root / "install.bat"
        if not installer.is_file():
            return
        try:
            completed = subprocess.run(
                ["cmd.exe", "/c", str(installer)],
                cwd=str(target.root),
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.ui.warning(f"ielts 命令刷新失败：{exc}")
            return
        if completed.returncode != 0:
            self.ui.warning(
                "ielts 命令刷新失败；可在源码目录手动运行 install.bat。"
            )
            return
        self.ui.success("ielts 命令已注册；新开终端后可直接运行 ielts。")

    def show_update_status(self) -> None:
        """Show local application and OEWN state without network access."""

        try:
            target = self.project_updater.detect_install()
            install_detail = target.detail
        except (ProjectUpdateError, OSError) as exc:
            install_detail = f"无法识别（{exc}）"

        version = self._local_oewn_version()
        overlay_path = self.store.oewn_overlay_path
        vocabulary_status = (
            f"Open English WordNet {version}"
            if version
            else "OEWN 尚未下载；教学词库独立保留"
        )
        application_status = f"IELTS Codex {self.project_updater.current_version}"
        if self._restart_required_version is not None:
            application_status += (
                f" → {self._restart_required_version}（等待重启）"
            )
        elif self._pending_update_version is not None:
            application_status += (
                f" → {self._pending_update_version}（等待退出后安装）"
            )
        self.ui.panel(
            "update status · local only",
            [
                f"程序      {application_status}",
                f"安装      {install_detail}",
                f"核心词库  {CONTENT_VERSION}",
                f"外部参考  {vocabulary_status}",
                f"参考文件  {overlay_path}",
                "联网      未检查；/update status 始终保持离线",
                "更新      明确选择 /update program 或 dictionary 后才联网",
            ],
        )

    def sync_vocabulary(self, *, force: bool = False, dry_run: bool = False) -> bool:
        return update_reference(self, force=force, dry_run=dry_run)

    def show_sync_status(self) -> None:
        reference_status(self)

    def _parse_session_args(
        self,
        args: list[str],
        *,
        default_count: int = DEFAULT_SESSION_SIZE,
        maximum: int = 100,
        example: str = "/learn 10 environment",
    ) -> tuple[int, str | None] | None:
        count = default_count
        topic: str | None = None
        for arg in args:
            if arg.isdigit():
                count = int(arg)
            elif topic is None:
                topic = self._normalize_topic(arg)
            else:
                self.ui.error(f"参数过多。示例：{example}")
                return None
        if not 1 <= count <= maximum:
            self.ui.error(f"每组数量须在 1 到 {maximum} 之间。")
            return None
        if topic and topic not in self.bank.topics:
            self._unknown_topic(topic)
            return None
        return count, topic

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        value = topic.strip().lower()
        return TOPIC_ALIASES.get(value, value)

    def _unknown_topic(self, topic: str) -> None:
        self.ui.error(f"未知主题：{topic}")
        self.ui.hint("可选主题：" + " · ".join(self.bank.topics))

    def learn(self, count: int, topic: str | None = None) -> SessionResult:
        words = self.active_bank().unseen(self.store.cards_for("recall"), count, topic, self.rng)
        if not words:
            scope = f"“{topic}”主题" if topic else "词库"
            self.ui.success(f"{scope}中已没有未学习单词。可以试试 /review。")
            return SessionResult()
        if len(words) < count:
            self.ui.warning(f"当前范围只剩 {len(words)} 个新词。")
        self.ui.rule(f"learn · {topic or 'all topics'}")
        result = self._recall_session(words, is_new=True)
        self._session_summary("learn", result)
        if result.reviewed:
            self.show_forgetting_curve(result.words)
        return result

    def review(self, count: int, topic: str | None = None) -> SessionResult:
        words = self.active_bank().due(self.store.cards_for("recall"), date.today(), count, topic)
        if not words:
            self.ui.success("当前没有到期单词。先 /learn 一组，或用 /quiz 自测。")
            return SessionResult()
        self.ui.rule(f"review · {topic or 'due now'}")
        result = self._recall_session(words, is_new=False)
        self._session_summary("review", result)
        return result

    def _recall_session(self, words: list[Word], *, is_new: bool) -> SessionResult:
        queue = LearningQueue(words)
        result = SessionResult()
        index = 0
        self.ui.hint("Again 会在本组重练；每词最多回答三次，不增加同日长期间隔。")
        while queue:
            card = queue.pop()
            index += 1
            outcome = self._recall_card(
                card.word, index, index + len(queue.pending),
                is_new=is_new and not card.retry, retry=card.retry,
            )
            if outcome is None:
                result.stopped = True
                break
            if outcome == "skip":
                continue
            self._count_answer(
                result, card.word, outcome.rating is not Rating.AGAIN,
                hint_level=outcome.hint_level, retry=card.retry,
            )
            if outcome.rating is Rating.AGAIN:
                self._repeat_failed(queue, card.word, result)
        return result

    def _repeat_failed(self, queue: LearningQueue, word: Word, result: SessionResult) -> None:
        if queue.repeat(word):
            self.ui.hint("已加入本组重练；重练成功后先安排次日复习。")
        else:
            result.deferred += 1
            self.ui.hint(f"{word.word} 已达到本组三次上限，保留到期，稍后用 /review 继续。")

    @staticmethod
    def _count_answer(
        result: SessionResult, word: Word, correct: bool, *, hint_level: int, retry: bool
    ) -> None:
        result.reviewed += 1
        result.correct += int(correct)
        result.again += int(not correct)
        result.assisted += int(hint_level > 0)
        result.retries += int(retry)
        if not hint_level and not retry:
            result.independent_attempts += 1
            result.independent_correct += int(correct)
        if word.key not in result.words:
            result.words.append(word.key)

    def _recall_card(
        self,
        word: Word,
        index: int,
        total: int,
        *,
        is_new: bool,
        retry: bool = False,
        study_item_id: str | None = None,
    ) -> RecallOutcome | str | None:
        self.ui.write()
        label = "relearn" if retry else ("new" if is_new else "recall")
        progress = self.store.cards_for("recall").get(word.key)
        state_line = (
            "新词 · 先猜含义" if progress is None
            else f"{progress.state} · 上次间隔 {progress.interval} 天"
        )
        self.ui.panel(
            f"{label} {index}/{total}",
            [f"{word.word}  {word.phonetic}  {word.part_of_speech}", state_line],
        )
        hint_level = 0
        while True:
            try:
                action = self.ui.prompt(
                    "  Enter 显示答案  ·  h 提示  ·  s 跳过  ·  q 结束  › "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.ui.write()
                return None
            if action in {"", "show", "answer", "a"}:
                break
            if action in {"h", "hint"}:
                hint_level = 1
                self._show_hint(word)
                continue
            if action in {"s", "skip"}:
                self.ui.hint("已跳过；本次不改变记忆进度。")
                return "skip"
            if action in {"q", "quit", "exit"}:
                return None
            self.ui.hint("请输入 Enter、h、s 或 q。")

        self.ui.word_card(word, progress)
        rating = self._ask_rating()
        if rating is None:
            return None
        if hint_level and rating > Rating.HARD:
            rating = Rating.HARD
            self.ui.hint("本次使用过提示，按 Hard 保存，并单独记录提示。")
        updated = self.store.record_review(
            word.key, rating, task="recall", hint_level=hint_level, retry=retry,
            study_item_id=study_item_id,
            error_type="meaning_recall" if rating is Rating.AGAIN else None,
        )
        due_text = "仍在今日到期队列" if updated.due == date.today().isoformat() else f"{updated.due} 复习"
        color = {
            Rating.AGAIN: self.ui.palette.red,
            Rating.HARD: self.ui.palette.yellow,
            Rating.GOOD: self.ui.palette.green,
            Rating.EASY: self.ui.palette.blue,
        }[rating]
        self.ui.write(
            self.ui.style(f"  {rating.label}", color, self.ui.palette.bold)
            + self.ui.style(f"  →  {due_text}", self.ui.palette.dim)
        )
        return RecallOutcome(rating, hint_level)

    def _ask_rating(self) -> Rating | None:
        self.ui.write(
            "  "
            + self.ui.style("1 Again", self.ui.palette.red)
            + "   "
            + self.ui.style("2 Hard", self.ui.palette.yellow)
            + "   "
            + self.ui.style("3 Good", self.ui.palette.green)
            + "   "
            + self.ui.style("4 Easy", self.ui.palette.blue)
        )
        while True:
            try:
                value = self.ui.prompt("  评价记忆程度 › ")
            except (EOFError, KeyboardInterrupt):
                self.ui.write()
                return None
            rating = Rating.parse(value)
            if rating:
                return rating
            self.ui.hint("请选择 1、2、3 或 4。")

    def quiz(self, count: int, topic: str | None = None) -> SessionResult:
        return training.run_spelling(self, count, topic)

    @staticmethod
    def _normalize_answer(value: str) -> str:
        return normalize_answer(value)

    def _show_hint(self, word: Word) -> None:
        masked = re.sub(
            re.escape(word.word),
            "_" * len(word.word),
            word.example,
            flags=re.IGNORECASE,
        )
        pattern = word.word[0] + " " + "· " * (len(word.word) - 1)
        self.ui.hint(f"提示：{pattern.strip()}  |  {masked}")

    def _session_summary(self, mode: str, result: SessionResult) -> None:
        if result.reviewed == 0 and result.stopped:
            self.ui.hint("本组已结束，没有修改进度。")
            return
        stats = self._stats()
        status = "提前结束" if result.stopped else "完成"
        label = "答对" if mode in {"quiz", "context"} else "自评记得"
        self.ui.write()
        self.ui.panel(
            f"{mode} {status}",
            [
                f"本组动作  {result.reviewed}  ·  本组重练 {result.retries}",
                f"本次{label}  {result.correct}（含提示与重练） · Again {result.again}",
                f"无提示首次{label}  {result.independent_correct}/{result.independent_attempts}",
                f"使用提示  {result.assisted} 次  ·  达到重试上限 {result.deferred} 词",
                f"今日动作  {stats['today_reviewed']}/{stats['daily_goal']}",
                f"当前到期  {stats['due']}",
            ],
        )

    def search(self, query: str) -> None:
        exact = self.bank.get(query)
        if exact:
            self.ui.word_card(exact, self.store.cards_for("recall").get(exact.key))
            for line in describe_reference(self.store, exact):
                self.ui.hint(line)
            return
        results = self.bank.search(query)
        if not results:
            self.ui.warning(f"词库中没有找到“{query}”。可用 /add 收集为待补全词条。")
            return
        if len(results) == 1:
            self.search(results[0].word.key)
            return
        lines = [
            f"{item.word.word} · {item.word.part_of_speech} · {item.word.meaning_zh}\n"
            f"  {item.word.key} · {item.word.deck}"
            for item in results
        ]
        lines.append("同一拼写可能有多个义项。使用 /search <词条 ID> 查看指定词条。")
        self.ui.panel(f"search · {query}", lines)

    def show_topics(self) -> None:
        lines = []
        for topic in self.active_bank().topics:
            total = sum(word.topic == topic for word in self.active_bank().words)
            learned = sum(
                word.topic == topic and word.key in self.store.cards
                for word in self.active_bank().words
            )
            lines.append(f"{topic:<14} {learned:>2}/{total:<2} 已学")
        self.ui.panel("topics", lines)

    def show_words(self, topic: str | None = None) -> None:
        words = [
            word for word in self.active_bank().words if topic is None or word.topic == topic
        ]
        lines = []
        for word in words:
            card = self.store.cards.get(word.key)
            marker = "●" if card else "○"
            lines.append(
                f"{marker} {word.word:<16} {word.part_of_speech:<7} {word.meaning_zh}"
            )
        if len(lines) > 24:
            lines = lines[:24] + [f"… 还有 {len(words) - 24} 个；用 /search 查询。"]
        self.ui.panel(f"words · {topic or 'all'}", lines)

    def show_today(self, *, compact: bool = False) -> None:
        stats = self._stats()
        progress = self.ui.progress_bar(
            stats["today_reviewed"], stats["daily_goal"], 20 if compact else 28
        )
        lines = [
            f"今日进度  {progress}",
            f"待复习    {stats['due']} 个  ·  新词 {stats['unseen']} 个  ·  "
            f"连续 {stats['streak']} 天",
        ]
        if not compact:
            remaining = max(0, stats["daily_goal"] - stats["today_reviewed"])
            lines.extend(
                (
                    "",
                    f"今日还差 {remaining} 个动作达到目标。",
                    "输入 /study 自动安排认义、拼写和语境；未答任务可继续。",
                )
            )
        self.ui.panel("today", lines)
        if not compact:
            self.show_today_words()

    def show_today_words(self, current_day: date | None = None) -> None:
        """Display today's completed and still-due words bilingually."""

        day = current_day or date.today()
        day_key = day.isoformat()
        studied_names = set(self.store.reviewed_words_on(day))
        studied = sorted(
            (
                word
                for name in studied_names
                if (word := self.active_bank().get(name)) is not None
            ),
            key=lambda word: word.word,
        )
        due = self.active_bank().due(
            self.store.cards,
            day,
            len(self.active_bank().words),
        )
        pending = [word for word in due if word.key not in studied_names]
        lines: list[str] = []

        if studied:
            lines.append(f"今日已学 / 已复习  {len(studied)}")
            for word in studied:
                card = self.store.cards[word.key]
                marker = "↻" if card.due and card.due <= day_key else "✓"
                lines.append(f"{marker} {word.word:<18} {word.meaning_zh}")
        else:
            lines.append("今天还没有完成单词；运行 /learn 开始今日词单。")

        if pending:
            if lines:
                lines.append("")
            lines.append(f"今日待复习  {len(pending)}")
            lines.extend(
                f"• {word.word:<18} {word.meaning_zh}" for word in pending
            )

        if studied:
            lines.extend(("", "✓ 已完成 · ↻ 今天仍需再看 · • 待复习"))
        self.ui.panel("today words · 中英对照", lines)

    def show_forgetting_curve(
        self,
        word_names: Sequence[str] | None = None,
        *,
        current_day: date | None = None,
    ) -> None:
        """Display an interval-scaled conceptual Ebbinghaus curve."""

        day = current_day or date.today()
        names = (
            list(dict.fromkeys(word_names))
            if word_names is not None
            else list(self.store.reviewed_words_on(day))
        )
        if not names:
            names = sorted(self.store.cards)
        cards = [
            self.store.cards[name]
            for name in names
            if name in self.store.cards
        ]
        if not cards:
            self.ui.warning("还没有记忆数据；先运行 /learn 学习一组单词。")
            return

        stability = float(median(max(1, card.interval) for card in cards))
        stability_text = (
            str(int(stability)) if stability.is_integer() else f"{stability:.1f}"
        )
        chart = tuple(
            self.ui.style(line, self.ui.palette.teal)
            for line in render_forgetting_curve(stability)
        )
        due_counts: Counter[int] = Counter()
        for card in cards:
            if not card.due:
                continue
            try:
                offset = max(0, (date.fromisoformat(card.due) - day).days)
            except ValueError:
                continue
            due_counts[offset] += 1
        review_nodes = []
        for offset, count in sorted(due_counts.items())[:5]:
            when = (
                "今天"
                if offset == 0
                else "明天"
                if offset == 1
                else f"{offset}天后"
            )
            review_nodes.append(f"{when} {count}词")

        lines = [
            f"当前样本  {len(cards)} 词 · 曲线尺度 S={stability_text} 天",
            "无复习趋势估算  R(t) = e^(-t/S)",
            "",
            *chart,
            "",
            "计划复习  " + (" · ".join(review_nodes) if review_nodes else "尚未安排"),
            "说明：曲线用于展示自然遗忘趋势；实际复习以卡片到期日为准。",
        ]
        self.ui.panel("Ebbinghaus · 艾宾浩斯遗忘曲线", lines)

    def show_stats(self) -> None:
        stats = self._stats()
        stable = stats["stable_by_task"]
        lines = [
            f"已接触    {stats['learned']}/{stats['total']}  ·  到期 {stats['due']}  ·  未学 {stats['unseen']}",
            f"稳定复习  认义 {stable['recall']} · 拼写 {stable['spelling']} · 语境 {stable['context']} 词",
            "条件：同题型连续至少 3 次跨日无提示 Good/Easy，最近一次间隔 ≥7 天。",
            "错误、Hard 或提示会中断连续通过；本组重练不计入。",
            "",
        ]
        for task, label in (("recall", "认义自评"), ("spelling", "拼写测验"), ("context", "语境选择")):
            item = stats["by_task"][task]
            lines.append(
                f"{label}  无提示首次通过 {item['independent_correct']}/{item['independent']}"
                f"  ·  提示后通过 {item['assisted_correct']}/{item['assisted']}"
                f"  ·  重练 {item['retries']} 次"
            )
        lines.extend([
            "以上按新记录统计；认义来自自评，游戏不计通过率或稳定复习。",
            f"累计动作  {stats['attempts']}（含旧记录） · 连续学习 {stats['streak']} 天",
            "",
            f"今日动作  {stats['today_reviewed']}  ·  新接触 {stats['today_learned']}",
            self.ui.progress_bar(stats["today_reviewed"], stats["daily_goal"], width=32),
        ])
        self.ui.panel("stats", lines)

    def show_backups(self) -> None:
        backups = self.store.list_backups(self.store.data_dir)
        self.ui.panel("backups", [str(path.name) for path in backups] or ["尚无备份。"])
        self.ui.hint("使用 /backup 手动备份，/restore <备份名> 恢复；恢复前也会保留当前文件。")

    def _command_backup(self, args: list[str]) -> None:
        if args:
            self.ui.warning("用法：/backup")
            return
        self.ui.success(f"已备份：{self.store.backup()}")

    def _command_backups(self, args: list[str]) -> None:
        if args:
            self.ui.warning("用法：/backups")
            return
        self.show_backups()

    def _command_restore(self, args: list[str]) -> None:
        if len(args) != 1:
            self.ui.warning("用法：/restore <backups 中的备份名>")
            return
        self.store.restore_backup(self.store.data_dir, args[0])
        self.store.reload()
        refresh_bank(self)
        self.ui.success("已恢复备份；恢复前的文件保存在 before-restore 备份中。")

    def show_help(self) -> None:
        self.ui.panel("commands", [
            "/study [分钟]           开始或继续今日学习，5–60 分钟",
            "/study new [分钟]       重排未答任务，保留已完成记录",
            "/learn [数量] [主题]    学习尚未练过认义的词条",
            "/review [数量] [主题]   复习到期认义卡片",
            "/quiz [数量] [主题]     独立拼写练习，优先到期和错词",
            "/context [数量] [主题]  搭配与词形选择题",
            "/mistakes [题型]        查看认义、拼写、语境的错项",
            "/mistakes practice [题型] 开始错项练习",
            '/import "文件" [词包名]  预览并导入 CSV/JSON',
            "/import undo [批次 ID]  撤销词库导入，保留学习记录",
            "/add <单词或短语>       收集待补全词条",
            "/decks [词包名|all]     查看或选择学习词包",
            "/search <单词或 ID>     查看词条与外部词典参考",
            "/words [主题] · /topics 浏览当前词包",
            "/today [words] · /stats 查看当天记录与分项表现",
            "/curve · /goal <数量>   遗忘曲线示意与每日动作目标",
            "/backup · /backups      手动备份与备份列表",
            "/restore <备份名>       恢复进度，保留恢复前文件",
            "/update status          查看程序、词库与词典版本",
            "/update program         检查并更新程序",
            "/update dictionary      预览并确认外部词典参考更新",
            "/update dictionary rollback 回退上一份词典参考",
            "/game [数量] [主题]     像素拼写远征",
            "/game pet create <图片> · /game music [on|off]",
            "/clear · /quit          清屏或退出",
            "题型可用 recall / spelling / context；学习中 q 返回。",
        ])



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ielts",
        description="Codex 风格的雅思词汇终端训练器",
    )
    parser.add_argument(
        "command",
        nargs="?",
        metavar="command",
        help=(
            "直接执行 study/import/add/decks/context/mistakes/learn/review/quiz/game/search/stats/today/update；"
            "省略则进入交互模式"
        ),
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="查询内容、词库路径、学习分钟或子命令",
    )
    parser.add_argument("extra", nargs="*", help="命令附加参数")
    parser.add_argument("-n", "--count", type=int)
    parser.add_argument("-t", "--topic", help="限定 IELTS 主题")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="进度目录（默认 ~/.ielts-codex）",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="禁用 ANSI 颜色",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="update 时即使 OEWN 版本相同也重新下载",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="update 时预览知识库与程序更新但不写入",
    )
    parser.add_argument("--seed", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    direct_commands = {
        "study", "import", "add", "decks", "context", "mistakes",
        "learn",
        "review",
        "quiz",
        "stats",
        "backup",
        "backups",
        "restore",
        "today",
        "curve",
        "topics",
        "search",
        "game",
        "update",
        # Backward-compatible but intentionally absent from user-facing help.
        "sync",
    }
    if args.command is not None and args.command not in direct_commands:
        parser.error(f"未知命令：{args.command}")
    if args.extra and args.command not in {"study", "import", "add", "decks", "context", "mistakes", "update", "sync"}:
        parser.error("此命令不接受多余参数")
    default_count = (
        GAME_DEFAULT_COUNT if args.command == "game" else DEFAULT_SESSION_SIZE
    )
    count = args.count if args.count is not None else default_count
    maximum = GAME_MAX_COUNT if args.command == "game" else 100
    if not 1 <= count <= maximum:
        parser.error(f"--count 须在 1 到 {maximum} 之间")
    if (args.force or args.dry_run) and args.command not in {"update", "sync"}:
        parser.error("--force 和 --dry-run 仅适用于 update 命令")
    topic = IELTSApp._normalize_topic(args.topic) if args.topic else None

    ui = TerminalUI(color=False if args.no_color else None)
    if args.command in {"backups", "restore"}:
        try:
            if args.command == "backups":
                if args.query is not None:
                    ui.error("用法：ielts backups")
                    return 2
                paths = ProgressStore.list_backups(args.data_dir)
                ui.panel("backups", [path.name for path in paths] or ["尚无备份。"])
            else:
                if not args.query:
                    ui.error("用法：ielts restore <备份名>")
                    return 2
                target = ProgressStore.restore_backup(args.data_dir, args.query)
                ui.success(f"已恢复：{target}；恢复前的文件已备份。")
            return 0
        except (ProgressFileError, OSError) as exc:
            ui.error(str(exc))
            return 2

    try:
        store = ProgressStore(args.data_dir)
    except ProgressFileError as exc:
        ui.error(str(exc))
        return 2
    except OSError as exc:
        ui.error(f"初始化失败：{exc}")
        return 2

    try:
        bank = WordBank.bundled()
        app = IELTSApp(bank, store, ui, rng=random.Random(args.seed))
        if topic and topic not in app.active_bank().topics:
            parser.error(f"当前词包没有主题 {args.topic!r}")
    except (ProgressFileError, OSError, ValueError) as exc:
        ui.error(f"初始化失败：{exc}")
        return 2

    try:
        if args.command:
            return app.execute_direct(
                args.command,
                count=count,
                topic=topic,
                query=args.query,
                extra=args.extra,
                force=args.force,
                dry_run=args.dry_run,
            )
        return app.run()
    except (ProgressFileError, OSError) as exc:
        ui.error(f"无法继续保存：{exc}")
        ui.hint("本次未完成的保存不会计入进度；此前成功保存的记录仍在。")
        return 2
    except KeyboardInterrupt:
        ui.write()
        ui.success("已退出，学习进度已保存在本地。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
