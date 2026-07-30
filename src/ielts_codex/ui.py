"""Minimal ANSI terminal rendering used by the interactive shell."""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from typing import Callable, Sequence, TextIO

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

    def command_prompt(
        self,
        commands: Sequence[tuple[str, str]],
        label: str = "› ",
        *,
        max_visible: int = 7,
    ) -> str:
        """Read a command with an inline, filterable slash-command menu.

        Typing ``/`` opens the menu. Up and down choose an item, Enter submits
        it, and Tab or Right completes it so arguments can be entered. Streams
        that are not attached to a capable terminal retain the ordinary
        line-based :meth:`prompt` behavior.
        """

        choices = self._normalise_commands(commands)
        terminal = self._command_terminal()
        if not choices or terminal is None:
            return self.prompt(label)

        columns, rows, input_fd = terminal
        visible_limit = min(max(1, max_visible), max(1, rows - 3))
        label_text = self.style(label, self.palette.bold, self.palette.teal)
        label_width = self.display_width(label)
        input_width = max(8, columns - label_width - 1)
        buffer: list[str] = []
        cursor = 0
        selected = 0
        result: str | None = None
        interrupted = False
        reached_eof = False
        restore: Callable[[], None] | None = None
        read_key: Callable[[], str]

        def matches() -> list[tuple[str, str]]:
            return self._command_matches("".join(buffer), choices)

        def redraw() -> None:
            active = matches()
            selected_index = min(selected, max(0, len(active) - 1))
            window_start = min(
                max(0, selected_index - visible_limit + 1),
                max(0, len(active) - visible_limit),
            )
            shown = active[window_start : window_start + visible_limit]
            view, view_cursor = self._input_view(
                "".join(buffer),
                cursor,
                input_width,
            )

            # The cursor always finishes back on the prompt row. Clearing from
            # there erases the previous menu in one operation, so repeated
            # arrow presses never append lines or leave stale entries behind.
            self.stream.write("\r\033[J")
            self.stream.write(label_text + view)
            for index, (command, description) in enumerate(shown):
                self.stream.write("\n")
                global_index = window_start + index
                marker = "›" if global_index == selected_index else " "
                available = max(8, columns - 4)
                command_width = min(
                    20,
                    max(8, max(self.display_width(item[0]) for item in shown)),
                )
                command_text = self._truncate_display(command, command_width)
                gap = " " * max(1, command_width - self.display_width(command_text) + 2)
                description_width = max(
                    0,
                    available - self.display_width(command_text) - len(gap),
                )
                description_text = self._truncate_display(
                    description,
                    description_width,
                )
                row = f"  {marker} {command_text}{gap}{description_text}"
                if global_index == selected_index:
                    row = self.style(row, self.palette.bold, self.palette.teal)
                else:
                    row = self.style(row, self.palette.dim)
                self.stream.write(row)

            if shown:
                self.stream.write(f"\033[{len(shown)}A")
            self.stream.write("\r")
            cursor_column = label_width + view_cursor
            if cursor_column:
                self.stream.write(f"\033[{cursor_column}C")
            self.stream.flush()

        def finish_line(suffix: str = "") -> None:
            self.stream.write("\r\033[J")
            self.stream.write(label_text + "".join(buffer) + suffix + "\n")
            self.stream.flush()

        try:
            read_key, restore = self._start_key_reader(input_fd)
        except (ImportError, OSError, ValueError):
            return self.prompt(label)

        try:
            redraw()
            while True:
                key = read_key()
                active = matches()

                if key == "enter":
                    if active:
                        picked = active[min(selected, len(active) - 1)][0]
                        buffer[:] = picked
                        cursor = len(buffer)
                    result = "".join(buffer).rstrip()
                    break

                if key == "interrupt":
                    interrupted = True
                    raise KeyboardInterrupt

                if key == "eof":
                    if not buffer:
                        reached_eof = True
                        raise EOFError
                    if cursor < len(buffer):
                        del buffer[cursor]

                elif key == "backspace":
                    if cursor:
                        cursor -= 1
                        del buffer[cursor]
                        selected = 0

                elif key == "delete":
                    if cursor < len(buffer):
                        del buffer[cursor]
                        selected = 0

                elif key == "left":
                    cursor = max(0, cursor - 1)

                elif key == "right":
                    # Right behaves like ordinary cursor movement unless the
                    # caret is at the end of a slash query, where it mirrors
                    # Codex-style completion.
                    if cursor < len(buffer):
                        cursor += 1
                    elif active:
                        picked = active[min(selected, len(active) - 1)][0]
                        buffer[:] = picked + " "
                        cursor = len(buffer)
                        selected = 0

                elif key == "home":
                    cursor = 0

                elif key == "end":
                    cursor = len(buffer)

                elif key == "up" and active:
                    selected = (selected - 1) % len(active)

                elif key == "down" and active:
                    selected = (selected + 1) % len(active)

                elif key == "tab" and active:
                    picked = active[min(selected, len(active) - 1)][0]
                    buffer[:] = picked + " "
                    cursor = len(buffer)
                    selected = 0

                elif key == "escape":
                    # Unknown/bare escape input is deliberately non-destructive.
                    pass

                elif len(key) == 1 and key.isprintable():
                    buffer.insert(cursor, key)
                    cursor += 1
                    selected = 0

                redraw()
        finally:
            if restore is not None:
                try:
                    restore()
                except (OSError, ValueError):
                    # A disconnected terminal may reject tcsetattr; still
                    # attempt to clear the menu and finish the prompt line.
                    pass
            if result is not None:
                finish_line()
            elif interrupted:
                finish_line("^C")
            elif reached_eof:
                finish_line()
            else:
                # Also clean up correctly if the terminal reader fails.
                finish_line()

        # ``result`` is assigned only by the Enter branch. The assertion keeps
        # static type checkers aware of the control flow after the finally.
        assert result is not None
        return result

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

    def _command_terminal(self) -> tuple[int, int, int] | None:
        """Return terminal geometry and stdin fd when raw input is safe."""

        try:
            if not self.stream.isatty() or not self.input_stream.isatty():
                return None
            if os.name != "nt" and os.environ.get("TERM", "").lower() == "dumb":
                return None
            input_fd = self.input_stream.fileno()
            output_fd = self.stream.fileno()
            size = os.get_terminal_size(output_fd)
        except (AttributeError, OSError, ValueError):
            return None

        # Below this size the prompt remains useful, but the menu would either
        # wrap or occupy nearly the entire viewport.
        if size.columns < 44 or size.lines < 8:
            return None
        return size.columns, size.lines, input_fd

    def _start_key_reader(
        self,
        input_fd: int,
    ) -> tuple[Callable[[], str], Callable[[], None]]:
        if os.name == "nt":
            import msvcrt

            def read_windows() -> str:
                char = msvcrt.getwch()
                if char in {"\x00", "\xe0"}:
                    return {
                        "H": "up",
                        "P": "down",
                        "K": "left",
                        "M": "right",
                        "G": "home",
                        "O": "end",
                        "S": "delete",
                    }.get(msvcrt.getwch(), "")
                return {
                    "\r": "enter",
                    "\n": "enter",
                    "\t": "tab",
                    "\x08": "backspace",
                    "\x03": "interrupt",
                    "\x04": "eof",
                    "\x1b": "escape",
                    "\x01": "home",
                    "\x05": "end",
                }.get(char, char)

            return read_windows, lambda: None

        import select
        import termios
        import tty

        previous = termios.tcgetattr(input_fd)
        tty.setraw(input_fd, when=termios.TCSANOW)
        restored = False

        def restore_posix() -> None:
            nonlocal restored
            if not restored:
                termios.tcsetattr(input_fd, termios.TCSADRAIN, previous)
                restored = True

        def read_byte(timeout: float | None = None) -> bytes:
            if timeout is not None:
                readable, _, _ = select.select([input_fd], [], [], timeout)
                if not readable:
                    return b""
            return os.read(input_fd, 1)

        def read_posix() -> str:
            first = read_byte()
            if first == b"":
                return "eof"
            if first == b"\x1b":
                second = read_byte(0.035)
                if second not in {b"[", b"O"}:
                    return "escape"
                sequence = bytearray(second)
                for _ in range(8):
                    part = read_byte(0.01)
                    if not part:
                        break
                    sequence.extend(part)
                    if part == b"~" or part.isalpha():
                        break
                return {
                    b"[A": "up",
                    b"OA": "up",
                    b"[B": "down",
                    b"OB": "down",
                    b"[C": "right",
                    b"OC": "right",
                    b"[D": "left",
                    b"OD": "left",
                    b"[H": "home",
                    b"OH": "home",
                    b"[1~": "home",
                    b"[7~": "home",
                    b"[F": "end",
                    b"OF": "end",
                    b"[4~": "end",
                    b"[8~": "end",
                    b"[3~": "delete",
                }.get(bytes(sequence), "")

            special = {
                b"\r": "enter",
                b"\n": "enter",
                b"\t": "tab",
                b"\x7f": "backspace",
                b"\x08": "backspace",
                b"\x03": "interrupt",
                b"\x04": "eof",
                b"\x01": "home",
                b"\x05": "end",
            }.get(first)
            if special is not None:
                return special

            # os.read avoids TextIO buffering swallowing the remainder of an
            # escape sequence. Reassemble one UTF-8 code point for normal text.
            lead = first[0]
            expected = (
                1
                if lead < 0x80
                else 2
                if lead & 0xE0 == 0xC0
                else 3
                if lead & 0xF0 == 0xE0
                else 4
                if lead & 0xF8 == 0xF0
                else 1
            )
            encoded = bytearray(first)
            for _ in range(expected - 1):
                part = read_byte(0.02)
                if not part:
                    break
                encoded.extend(part)
            try:
                return bytes(encoded).decode(
                    getattr(self.input_stream, "encoding", None) or "utf-8",
                )
            except (LookupError, UnicodeDecodeError):
                return ""

        return read_posix, restore_posix

    @staticmethod
    def _normalise_commands(
        commands: Sequence[tuple[str, str]],
    ) -> tuple[tuple[str, str], ...]:
        output: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_command, raw_description in commands:
            command = "".join(
                char for char in str(raw_command).strip() if char.isprintable()
            )
            if not command:
                continue
            if not command.startswith("/"):
                command = "/" + command
            # A completion is one command token. Descriptions carry the detail.
            command = command.split(maxsplit=1)[0]
            key = command.casefold()
            if key in seen:
                continue
            seen.add(key)
            description = ANSI_RE.sub("", str(raw_description))
            description = " ".join(description.split())
            output.append((command, description))
        return tuple(output)

    @staticmethod
    def _command_matches(
        buffer: str,
        commands: tuple[tuple[str, str], ...],
    ) -> list[tuple[str, str]]:
        if not buffer.startswith("/"):
            return []
        query = buffer[1:]
        if any(char.isspace() for char in query):
            return []
        query = query.casefold()
        if not query:
            return list(commands)

        prefixes: list[tuple[str, str]] = []
        contains: list[tuple[str, str]] = []
        for item in commands:
            name = item[0][1:].casefold()
            if name.startswith(query):
                prefixes.append(item)
            elif query in name:
                contains.append(item)
        return prefixes + contains

    def _input_view(self, text: str, cursor: int, width: int) -> tuple[str, int]:
        if self.display_width(text) <= width:
            return text, self.display_width(text[:cursor])

        start = 0
        # Reserve one cell to make horizontal clipping explicit.
        while (
            start < cursor
            and self.display_width(text[start:cursor]) > width - 1
        ):
            start += 1
        left = "‹" if start else ""
        available = max(1, width - self.display_width(left))
        end = start
        while (
            end < len(text)
            and self.display_width(text[start : end + 1]) <= available
        ):
            end += 1

        body = text[start:end]
        if end < len(text) and available > 1:
            while body and self.display_width(body + "…") > available:
                body = body[:-1]
            body += "…"
        return left + body, self.display_width(left + text[start:cursor])

    def _truncate_display(self, text: str, width: int) -> str:
        if width <= 0:
            return ""
        if self.display_width(text) <= width:
            return text
        if width == 1:
            return "…"
        return text[: self._fit_prefix(text, width - 1)] + "…"

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
