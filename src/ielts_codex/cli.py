"""Command-line entry point and Codex-inspired interactive shell."""

from __future__ import annotations

import argparse
import random
import re
import shlex
import sys
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence

from . import __version__
from .models import Rating, Word
from .storage import ProgressFileError, ProgressStore
from .ui import TerminalUI
from .word_bank import WordBank


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


@dataclass(slots=True)
class SessionResult:
    reviewed: int = 0
    correct: int = 0
    again: int = 0
    stopped: bool = False


class IELTSApp:
    def __init__(
        self,
        bank: WordBank,
        store: ProgressStore,
        ui: TerminalUI,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self.bank = bank
        self.store = store
        self.ui = ui
        self.rng = rng or random.Random()
        self.running = True

    def run(self) -> int:
        self.ui.banner()
        self.show_today(compact=True)
        self.ui.hint(
            "  输入 /learn 开始，/help 查看命令；直接输入单词可以查询。"
        )
        self.ui.write()

        while self.running:
            try:
                line = self.ui.prompt()
            except EOFError:
                self.ui.write()
                break
            except KeyboardInterrupt:
                self.ui.write()
                self.ui.hint("已取消当前输入；再按 Ctrl+C 或输入 /quit 退出。")
                continue

            if not line.strip():
                continue
            self.dispatch(line)

        self.ui.success("已退出，学习进度已保存在本地。")
        return 0

    def dispatch(self, line: str) -> None:
        try:
            tokens = shlex.split(line)
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
            "s": "stats",
            "find": "search",
            "?": "help",
            "exit": "quit",
        }
        command = aliases.get(command, command)

        handlers = {
            "learn": self._command_learn,
            "review": self._command_review,
            "quiz": self._command_quiz,
            "search": self._command_search,
            "words": self._command_words,
            "topics": self._command_topics,
            "stats": self._command_stats,
            "today": self._command_today,
            "goal": self._command_goal,
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
    ) -> int:
        if command == "learn":
            self.learn(count, topic)
        elif command == "review":
            self.review(count, topic)
        elif command == "quiz":
            self.quiz(count, topic)
        elif command == "stats":
            self.show_stats()
        elif command == "today":
            self.show_today()
        elif command == "topics":
            self.show_topics()
        elif command == "search":
            if not query:
                self.ui.error("search 需要一个单词或中文释义。")
                return 2
            self.search(query)
        return 0

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

    def _command_today(self, _args: list[str]) -> None:
        self.show_today()

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

    def _command_help(self, _args: list[str]) -> None:
        self.show_help()

    def _command_clear(self, _args: list[str]) -> None:
        self.ui.clear()
        self.ui.banner()

    def _command_quit(self, _args: list[str]) -> None:
        self.running = False

    def _parse_session_args(
        self, args: list[str]
    ) -> tuple[int, str | None] | None:
        count = DEFAULT_SESSION_SIZE
        topic: str | None = None
        for arg in args:
            if arg.isdigit():
                count = int(arg)
            elif topic is None:
                topic = self._normalize_topic(arg)
            else:
                self.ui.error("参数过多。示例：/learn 10 environment")
                return None
        if not 1 <= count <= 100:
            self.ui.error("每组数量须在 1 到 100 之间。")
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
        words = self.bank.unseen(self.store.cards, count, topic, self.rng)
        if not words:
            scope = f"“{topic}”主题" if topic else "词库"
            self.ui.success(f"{scope}中已没有未学习单词。可以试试 /review。")
            return SessionResult()
        if len(words) < count:
            self.ui.warning(f"当前范围只剩 {len(words)} 个新词。")
        self.ui.write()
        self.ui.rule(f"learn · {topic or 'all topics'}")
        result = SessionResult()
        for index, word in enumerate(words, start=1):
            outcome = self._recall_card(word, index, len(words), is_new=True)
            if outcome is None:
                result.stopped = True
                break
            if outcome == "skip":
                continue
            result.reviewed += 1
            result.correct += int(outcome is not Rating.AGAIN)
            result.again += int(outcome is Rating.AGAIN)
        self._session_summary("learn", result)
        return result

    def review(self, count: int, topic: str | None = None) -> SessionResult:
        words = self.bank.due(self.store.cards, date.today(), count, topic)
        if not words:
            self.ui.success("当前没有到期单词。先 /learn 一组，或用 /quiz 自测。")
            return SessionResult()
        self.ui.write()
        self.ui.rule(f"review · {topic or 'due now'}")
        result = SessionResult()
        for index, word in enumerate(words, start=1):
            outcome = self._recall_card(word, index, len(words), is_new=False)
            if outcome is None:
                result.stopped = True
                break
            if outcome == "skip":
                continue
            result.reviewed += 1
            result.correct += int(outcome is not Rating.AGAIN)
            result.again += int(outcome is Rating.AGAIN)
        self._session_summary("review", result)
        return result

    def _recall_card(
        self,
        word: Word,
        index: int,
        total: int,
        *,
        is_new: bool,
    ) -> Rating | str | None:
        self.ui.write()
        label = "new" if is_new else "recall"
        progress = self.store.cards.get(word.word)
        state_line = (
            "新词 · 先猜含义"
            if progress is None
            else f"{progress.state} · 上次间隔 {progress.interval} 天"
        )
        self.ui.panel(
            f"{label} {index}/{total}",
            [
                f"{word.word}  {word.phonetic}  {word.part_of_speech}",
                state_line,
            ],
        )

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
        updated = self.store.record_review(word.word, rating)
        if rating is Rating.AGAIN and updated.due == date.today().isoformat():
            due_text = "仍在今日队列"
        else:
            due_text = f"{updated.interval} 天后复习"
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
        return rating

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
        candidates = self.bank.learned(self.store.cards, topic)
        if not candidates:
            self.ui.warning("还没有可测验的已学单词。先运行 /learn。")
            return SessionResult()
        chosen = self.rng.sample(candidates, min(count, len(candidates)))
        self.ui.write()
        self.ui.rule(f"quiz · {topic or 'learned words'}")
        result = SessionResult()

        for index, word in enumerate(chosen, start=1):
            self.ui.write()
            self.ui.panel(
                f"quiz {index}/{len(chosen)}",
                [
                    word.meaning_zh,
                    f"{word.part_of_speech} · {word.topic}",
                    "请输入对应英文单词。",
                ],
            )
            used_hint = False
            while True:
                try:
                    answer = self.ui.prompt("  answer › ").strip()
                except (EOFError, KeyboardInterrupt):
                    self.ui.write()
                    result.stopped = True
                    self._session_summary("quiz", result)
                    return result
                lowered = answer.lower()
                if lowered in {"q", "/quit"}:
                    result.stopped = True
                    self._session_summary("quiz", result)
                    return result
                if lowered in {"s", "/skip"}:
                    self.ui.hint(f"跳过。答案是 {word.word}。")
                    break
                if lowered in {"h", "/hint"}:
                    used_hint = True
                    self._show_hint(word)
                    continue
                if not answer:
                    self.ui.hint("输入答案，或输入 h 获取提示、s 跳过、q 结束。")
                    continue

                similarity = SequenceMatcher(
                    None, self._normalize_answer(answer), word.word
                ).ratio()
                is_correct = similarity == 1.0
                if is_correct:
                    self.ui.success(f"{word.word}  {word.phonetic}")
                    rating = Rating.HARD if used_hint else Rating.GOOD
                    result.correct += 1
                else:
                    self.ui.error(f"{answer}  →  {word.word}  {word.phonetic}")
                    if similarity >= 0.72:
                        self.ui.hint("很接近，注意拼写。")
                    rating = Rating.AGAIN
                    result.again += 1
                self.ui.hint(word.example)
                self.store.record_review(word.word, rating)
                result.reviewed += 1
                break

        self._session_summary("quiz", result)
        return result

    @staticmethod
    def _normalize_answer(value: str) -> str:
        return re.sub(r"[^a-z-]", "", value.strip().lower())

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
        accuracy = round(result.correct / result.reviewed * 100) if result.reviewed else 0
        stats = self.store.stats(len(self.bank.words))
        status = "提前结束" if result.stopped else "完成"
        self.ui.write()
        self.ui.panel(
            f"{mode} {status}",
            [
                f"本组动作  {result.reviewed}",
                f"记住      {result.correct}  ·  Again {result.again}  ·  正确率 {accuracy}%",
                f"今日进度  {stats['today_reviewed']}/{stats['daily_goal']}",
                f"当前到期  {stats['due']}",
            ],
        )

    def search(self, query: str) -> None:
        exact = self.bank.get(query)
        if exact:
            self.ui.word_card(exact, self.store.cards.get(exact.word))
            return
        results = self.bank.search(query)
        if not results:
            self.ui.warning(f"内置词库中没有找到“{query}”。")
            return
        if len(results) == 1 or results[0].score >= 0.90:
            top = results[0].word
            self.ui.word_card(top, self.store.cards.get(top.word))
            return
        lines = [
            f"{item.word.word:<16} {item.word.part_of_speech:<7} "
            f"{item.word.meaning_zh}"
            for item in results
        ]
        lines.append("")
        lines.append("输入具体单词查看完整卡片。")
        self.ui.panel(f"search · {query}", lines)

    def show_topics(self) -> None:
        lines = []
        for topic in self.bank.topics:
            total = sum(word.topic == topic for word in self.bank.words)
            learned = sum(
                word.topic == topic and word.word in self.store.cards
                for word in self.bank.words
            )
            lines.append(f"{topic:<14} {learned:>2}/{total:<2} 已学")
        self.ui.panel("topics", lines)

    def show_words(self, topic: str | None = None) -> None:
        words = [
            word for word in self.bank.words if topic is None or word.topic == topic
        ]
        lines = []
        for word in words:
            card = self.store.cards.get(word.word)
            marker = "●" if card else "○"
            lines.append(
                f"{marker} {word.word:<16} {word.part_of_speech:<7} {word.meaning_zh}"
            )
        if len(lines) > 24:
            lines = lines[:24] + [f"… 还有 {len(words) - 24} 个；用 /search 查询。"]
        self.ui.panel(f"words · {topic or 'all'}", lines)

    def show_today(self, *, compact: bool = False) -> None:
        stats = self.store.stats(len(self.bank.words))
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
                    "建议路径：/review 10  →  /learn 10  →  /quiz 5",
                )
            )
        self.ui.panel("today", lines)

    def show_stats(self) -> None:
        stats = self.store.stats(len(self.bank.words))
        learned_percent = (
            round(stats["learned"] / stats["total"] * 100) if stats["total"] else 0
        )
        lines = [
            f"词库覆盖  {stats['learned']}/{stats['total']}  ({learned_percent}%)",
            f"已掌握    {stats['mastered']}  ·  到期 {stats['due']}  ·  "
            f"未学 {stats['unseen']}",
            f"累计动作  {stats['attempts']}  ·  正确率 {stats['accuracy']}%",
            f"连续学习  {stats['streak']} 天",
            "",
            "今日",
            f"复习动作  {stats['today_reviewed']}  ·  新学 {stats['today_learned']}",
            self.ui.progress_bar(
                stats["today_reviewed"], stats["daily_goal"], width=32
            ),
        ]
        self.ui.panel("stats", lines)

    def show_help(self) -> None:
        self.ui.panel(
            "commands",
            [
                "/learn [数量] [主题]    学习未见单词，默认 10 个",
                "/review [数量] [主题]   复习今天到期的卡片",
                "/quiz [数量] [主题]     中文 → 英文拼写测验",
                "/search <内容>          查单词、中文释义或近义词",
                "/words [主题]           浏览词表；● 表示已学习",
                "/topics                 查看各主题覆盖进度",
                "/today                  查看今天的学习计划",
                "/stats                  查看累计学习数据",
                "/goal <数量>            修改每日目标",
                "/clear                  清屏",
                "/quit                   保存并退出",
                "",
                "数量与主题顺序可互换，例如：",
                "/learn environment 8    或    /review 15 教育",
                "",
                "学习卡片中：Enter 显示答案 · h 提示 · s 跳过 · q 结束",
            ],
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ielts-codex",
        description="Codex 风格的雅思词汇终端训练器",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("learn", "review", "quiz", "stats", "today", "topics", "search"),
        help="直接执行一个命令；省略则进入交互模式",
    )
    parser.add_argument("query", nargs="?", help="search 命令的查询内容")
    parser.add_argument("-n", "--count", type=int, default=DEFAULT_SESSION_SIZE)
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
    if not 1 <= args.count <= 100:
        parser.error("--count 须在 1 到 100 之间")
    topic = IELTSApp._normalize_topic(args.topic) if args.topic else None

    ui = TerminalUI(color=False if args.no_color else None)
    try:
        bank = WordBank.bundled()
        if topic and topic not in bank.topics:
            parser.error(
                f"未知主题 {args.topic!r}；可选：{', '.join(bank.topics)}"
            )
        store = ProgressStore(args.data_dir)
    except ProgressFileError as exc:
        ui.error(str(exc))
        return 2
    except (OSError, ValueError) as exc:
        ui.error(f"初始化失败：{exc}")
        return 2

    app = IELTSApp(
        bank,
        store,
        ui,
        rng=random.Random(args.seed),
    )
    try:
        if args.command:
            return app.execute_direct(
                args.command,
                count=args.count,
                topic=topic,
                query=args.query,
            )
        return app.run()
    except KeyboardInterrupt:
        ui.write()
        ui.success("已退出，学习进度已保存在本地。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
