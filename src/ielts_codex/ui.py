"""Minimal ANSI terminal rendering used by the interactive shell."""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from typing import TextIO

from .models import CardProgress, Word


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class Palette:
    reset: str = "\033[0m"
    bold: str = "\033[1m"
    dim: str = "\033[2m"
    teal: str = "\033[38;5;80m"
    green: str = "\033[38;5;114m"
    yellow: str = "\033[38;5;221m"
    red: str = "\033[38;5;203m"
    blue: str = "\033[38;5;111m"
    violet: str = "\033[38;5;183m"
    gray: str = "\033[38;5;245m"


class TerminalUI:
    def __init__(
        self,
        *,
        color: bool | None = None,
        stream: TextIO = sys.stdout,
        input_stream: TextIO = sys.stdin,
    ) -> None:
        if color is None:
            color = bool(stream.isatty() and "NO_COLOR" not in os.environ)
        self.color = color
        self.stream = stream
        self.input_stream = input_stream
        self.palette = Palette()

    @property
    def width(self) -> int:
        return max(48, min(88, shutil.get_terminal_size((84, 24)).columns - 2))

    def style(self, text: str, *styles: str) -> str:
        if not self.color:
            return text
        return "".join(styles) + text + self.palette.reset

    def write(self, text: str = "", *, end: str = "\n") -> None:
        self.stream.write(text + end)
        self.stream.flush()

    def prompt(self, label: str = "› ") -> str:
        self.write(self.style(label, self.palette.bold, self.palette.teal), end="")
        line = self.input_stream.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\n")

    def clear(self) -> None:
        if self.stream.isatty():
            self.write("\033[2J\033[H", end="")

    def banner(self) -> None:
        logo = [
            "  ██╗███████╗██╗  ████████╗███████╗",
            "  ██║██╔════╝██║  ╚══██╔══╝██╔════╝",
            "  ██║█████╗  ██║     ██║   ███████╗",
            "  ██║██╔══╝  ██║     ██║   ╚════██║",
            "  ██║███████╗███████╗██║   ███████║",
            "  ╚═╝╚══════╝╚══════╝╚═╝   ╚══════╝",
        ]
        self.write()
        for line in logo:
            self.write(self.style(line, self.palette.bold, self.palette.teal))
        self.write(
            "  "
            + self.style("CODEX", self.palette.bold)
            + self.style(
                "  vocabulary trainer · adaptive review",
                self.palette.dim,
            )
        )
        self.write()

    def panel(self, title: str, lines: list[str] | tuple[str, ...]) -> None:
        width = self.width
        inner = width - 4
        title_text = f" {title} "
        top_fill = max(0, width - self.display_width(title_text) - 2)
        self.write(
            self.style("╭─", self.palette.gray)
            + self.style(title_text, self.palette.bold)
            + self.style("─" * top_fill + "╮", self.palette.gray)
        )
        for raw in lines:
            wrapped = self._wrap(raw, inner) or [""]
            for line in wrapped:
                padding = max(0, inner - self.display_width(line))
                self.write(
                    self.style("│ ", self.palette.gray)
                    + line
                    + (" " * padding)
                    + self.style(" │", self.palette.gray)
                )
        self.write(self.style("╰" + "─" * (width - 2) + "╯", self.palette.gray))

    def rule(self, label: str = "") -> None:
        if label:
            prefix = f"── {label} "
            fill = "─" * max(0, self.width - self.display_width(prefix))
            self.write(self.style(prefix + fill, self.palette.gray))
        else:
            self.write(self.style("─" * self.width, self.palette.gray))

    def success(self, text: str) -> None:
        self.write(self.style("✓ ", self.palette.green, self.palette.bold) + text)

    def warning(self, text: str) -> None:
        self.write(self.style("! ", self.palette.yellow, self.palette.bold) + text)

    def error(self, text: str) -> None:
        self.write(self.style("× ", self.palette.red, self.palette.bold) + text)

    def hint(self, text: str) -> None:
        self.write(self.style(text, self.palette.dim))

    def progress_bar(self, value: int, total: int, width: int = 24) -> str:
        ratio = min(1.0, value / total) if total else 0.0
        filled = round(width * ratio)
        bar = "█" * filled + "░" * (width - filled)
        return (
            self.style(bar[:filled], self.palette.teal)
            + self.style(bar[filled:], self.palette.gray)
            + f"  {value}/{total}"
        )

    def word_card(self, word: Word, progress: CardProgress | None = None) -> None:
        lines = [
            f"{word.word}  {word.phonetic}  {word.part_of_speech}  ·  Band {word.band}",
            "",
            f"中文    {word.meaning_zh}",
            f"English  {word.definition_en}",
            "",
            f"例句    {word.example}",
            f"        {word.example_zh}",
            f"近义词  {', '.join(word.synonyms)}",
            f"主题    {word.topic}",
            f"来源    {word.definition_source} · {word.definition_license}",
        ]
        if progress and progress.state != "new":
            lines.extend(
                (
                    "",
                    f"记忆    {progress.state} · 间隔 {progress.interval} 天 · "
                    f"重复 {progress.repetitions} 次",
                )
            )
        self.panel("word", lines)

    @staticmethod
    def display_width(text: str) -> int:
        plain = ANSI_RE.sub("", text)
        return sum(
            2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
            for char in plain
        )

    def _wrap(self, text: str, width: int) -> list[str]:
        if self.display_width(text) <= width:
            return [text]

        # Preserve word boundaries while measuring CJK glyphs as two cells.
        # Labelled rows such as ``例句    ...`` get a hanging indent.
        plain = ANSI_RE.sub("", text)
        label = re.match(r"^(\S+\s{2,})", plain)
        continuation = " " * self.display_width(label.group(1)) if label else ""
        tokens = re.findall(r"\s+|\S+", text)
        output: list[str] = []
        current = ""
        pending_space = ""

        for token in tokens:
            if token.isspace():
                pending_space = token
                continue

            candidate = current + pending_space + token
            if self.display_width(candidate) <= width:
                current = candidate
                pending_space = ""
                continue

            if current.strip():
                output.append(current.rstrip())
                current = continuation
                pending_space = ""

            # A URL or another unbroken token may be wider than the panel.
            remainder = token
            while remainder and self.display_width(current + remainder) > width:
                available = width - self.display_width(current)
                split_at = self._fit_prefix(remainder, available)
                if split_at == 0:
                    # A very narrow terminal plus a hanging indent: make
                    # progress without risking an infinite loop.
                    split_at = 1
                output.append(current + remainder[:split_at])
                remainder = remainder[split_at:]
                current = continuation
            current += remainder

        if current or not output:
            output.append(current.rstrip())
        return output

    def _fit_prefix(self, text: str, width: int) -> int:
        used = 0
        for index, char in enumerate(text):
            char_width = self.display_width(char)
            if used + char_width > width:
                return index
            used += char_width
        return len(text)
