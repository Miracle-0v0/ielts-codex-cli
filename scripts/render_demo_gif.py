#!/usr/bin/env python3
"""Record the real interactive CLI and render ``docs/demo.gif``.

The recorder is deliberately separate from the package's runtime dependencies.
It uses a pseudo-terminal so every panel, prompt, answer, and status message in
the GIF comes from the actual application.  The session stays offline and uses
a one-entry, CC BY 4.0 OEWN overlay fixture for the synchronization screens.

Development requirements:

    python -m pip install pexpect Pillow
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Pattern

try:
    import pexpect
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover - developer-facing helper
    raise SystemExit(
        "Demo rendering requires pexpect and Pillow: "
        "python -m pip install pexpect Pillow"
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "demo.gif"
COLS = 94
ROWS = 27
FONT_SIZE = 18
CELL_WIDTH = 9
LINE_HEIGHT = 24
SIDE_MARGIN = 24
TITLE_HEIGHT = 44
CAPTION_HEIGHT = 50
FRAME_DURATION_MS = 80
OUTPUT_LINES_PER_FRAME = 2
BACKGROUND = (13, 17, 23)
DEFAULT_FOREGROUND = (201, 209, 217)
CSI_RE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")


ANSI_BASE = (
    (0, 0, 0),
    (205, 49, 49),
    (13, 188, 121),
    (229, 229, 16),
    (36, 114, 200),
    (188, 63, 188),
    (17, 168, 205),
    (229, 229, 229),
    (102, 102, 102),
    (241, 76, 76),
    (35, 209, 139),
    (245, 245, 67),
    (59, 142, 234),
    (214, 112, 214),
    (41, 184, 219),
    (255, 255, 255),
)


@dataclass(frozen=True, slots=True)
class Style:
    color: tuple[int, int, int] = DEFAULT_FOREGROUND
    bold: bool = False
    dim: bool = False


@dataclass(slots=True)
class Cell:
    char: str = " "
    style: Style = Style()


class MiniTerminal:
    """The small ANSI subset emitted by :class:`TerminalUI`."""

    def __init__(self, columns: int = COLS, rows: int = ROWS) -> None:
        self.columns = columns
        self.rows = rows
        self.cells = self._blank_screen()
        self.row = 0
        self.column = 0
        self.style = Style()

    def _blank_row(self) -> list[Cell]:
        return [Cell() for _ in range(self.columns)]

    def _blank_screen(self) -> list[list[Cell]]:
        return [self._blank_row() for _ in range(self.rows)]

    def feed(self, value: str) -> None:
        index = 0
        while index < len(value):
            char = value[index]
            if char == "\x1b":
                match = CSI_RE.match(value, index)
                if match:
                    self._control(match.group(1), match.group(2))
                    index = match.end()
                    continue
                if value.startswith("\x1b]", index):
                    bell = value.find("\x07", index + 2)
                    if bell >= 0:
                        index = bell + 1
                        continue
                index += 1
                continue
            if char == "\r":
                self.column = 0
            elif char == "\n":
                self._newline()
            elif char == "\b":
                self.column = max(0, self.column - 1)
            elif char == "\t":
                spaces = 8 - (self.column % 8)
                for _ in range(spaces):
                    self._put(" ")
            elif char >= " " and char != "\x7f":
                self._put(char)
            index += 1

    def _put(self, char: str) -> None:
        width = _display_width(char)
        if width == 0:
            if self.column:
                self.cells[self.row][self.column - 1].char += char
            return
        if self.column >= self.columns or (
            width == 2 and self.column == self.columns - 1
        ):
            self.column = 0
            self._newline()
        self.cells[self.row][self.column] = Cell(char, self.style)
        if width == 2 and self.column + 1 < self.columns:
            self.cells[self.row][self.column + 1] = Cell("", self.style)
        self.column += width

    def _newline(self) -> None:
        self.row += 1
        if self.row >= self.rows:
            self.cells.pop(0)
            self.cells.append(self._blank_row())
            self.row = self.rows - 1

    def _control(self, parameters: str, final: str) -> None:
        values = [
            int(item) if item else 0
            for item in parameters.removeprefix("?").split(";")
        ]
        if final == "m":
            self._sgr(values)
        elif final == "J":
            mode = values[0] if values else 0
            if mode == 2:
                self.cells = self._blank_screen()
                self.row = self.column = 0
            elif mode == 0:
                for column in range(self.column, self.columns):
                    self.cells[self.row][column] = Cell()
                for row in range(self.row + 1, self.rows):
                    self.cells[row] = self._blank_row()
        elif final in {"H", "f"}:
            self.row = max(0, min(self.rows - 1, (values[0] or 1) - 1))
            target = values[1] if len(values) > 1 else 1
            self.column = max(0, min(self.columns - 1, (target or 1) - 1))
        elif final == "A":
            self.row = max(0, self.row - (values[0] or 1))
        elif final == "B":
            self.row = min(self.rows - 1, self.row + (values[0] or 1))
        elif final == "C":
            self.column = min(self.columns - 1, self.column + (values[0] or 1))
        elif final == "D":
            self.column = max(0, self.column - (values[0] or 1))
        elif final == "K":
            for column in range(self.column, self.columns):
                self.cells[self.row][column] = Cell()

    def _sgr(self, values: list[int]) -> None:
        if not values:
            values = [0]
        index = 0
        while index < len(values):
            value = values[index]
            if value == 0:
                self.style = Style()
            elif value == 1:
                self.style = Style(self.style.color, True, self.style.dim)
            elif value == 2:
                self.style = Style(self.style.color, self.style.bold, True)
            elif value == 22:
                self.style = Style(self.style.color, False, False)
            elif value == 39:
                self.style = Style(
                    DEFAULT_FOREGROUND, self.style.bold, self.style.dim
                )
            elif 30 <= value <= 37:
                self.style = Style(
                    ANSI_BASE[value - 30], self.style.bold, self.style.dim
                )
            elif 90 <= value <= 97:
                self.style = Style(
                    ANSI_BASE[value - 90 + 8], self.style.bold, self.style.dim
                )
            elif (
                value == 38
                and index + 2 < len(values)
                and values[index + 1] == 5
            ):
                self.style = Style(
                    _xterm_color(values[index + 2]),
                    self.style.bold,
                    self.style.dim,
                )
                index += 2
            index += 1


def _xterm_color(code: int) -> tuple[int, int, int]:
    if 0 <= code < 16:
        return ANSI_BASE[code]
    if 16 <= code <= 231:
        offset = code - 16
        levels = (0, 95, 135, 175, 215, 255)
        return (
            levels[offset // 36],
            levels[(offset % 36) // 6],
            levels[offset % 6],
        )
    if 232 <= code <= 255:
        gray = 8 + (code - 232) * 10
        return (gray, gray, gray)
    return DEFAULT_FOREGROUND


def _display_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _font_from_fontconfig(family: str) -> Path | None:
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", family],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    candidate = Path(result.stdout.strip())
    return candidate if candidate.is_file() else None


def _find_fonts(
    font_override: Path | None,
) -> tuple[Path, Path, int, Path, Path]:
    if font_override:
        if not font_override.is_file():
            raise SystemExit(f"Font does not exist: {font_override}")
        return font_override, font_override, 0, font_override, font_override

    regular = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    bold = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
    # DejaVu Sans Mono covers IPA, box drawing, and block elements.  Noto's
    # CJK collection handles the double-width Chinese glyphs separately.
    latin_regular = Path(
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    )
    latin_bold = Path(
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
    )
    if regular.is_file():
        if not latin_regular.is_file():
            latin_regular = regular
            latin_bold = bold
        return (
            regular,
            bold if bold.is_file() else regular,
            7,
            latin_regular,
            latin_bold if latin_bold.is_file() else latin_regular,
        )

    matched = _font_from_fontconfig("Noto Sans Mono CJK SC")
    if matched:
        return matched, matched, 0, matched, matched
    raise SystemExit(
        "A CJK monospace font is required. Install Noto Sans Mono CJK "
        "or pass --font PATH."
    )


def _dim(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(
        round(channel * 0.58 + BACKGROUND[index] * 0.42)
        for index, channel in enumerate(color)
    )


def _render_frame(
    terminal: MiniTerminal,
    caption: str,
    cursor_strength: float,
    cjk_regular_font: ImageFont.FreeTypeFont,
    cjk_bold_font: ImageFont.FreeTypeFont,
    latin_regular_font: ImageFont.FreeTypeFont,
    latin_bold_font: ImageFont.FreeTypeFont,
) -> Image.Image:
    width = SIDE_MARGIN * 2 + COLS * CELL_WIDTH
    height = TITLE_HEIGHT + ROWS * LINE_HEIGHT + CAPTION_HEIGHT
    image = Image.new("RGB", (width, height), (8, 11, 16))
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, TITLE_HEIGHT), fill=(31, 35, 41))
    for x, color in (
        (20, (255, 95, 86)),
        (40, (255, 189, 46)),
        (60, (39, 201, 63)),
    ):
        draw.ellipse((x - 6, 16, x + 6, 28), fill=color)
    draw.text(
        (width // 2, 22),
        "ielts-codex 0.3.1  ·  real CLI session",
        font=latin_bold_font,
        fill=(210, 214, 220),
        anchor="mm",
    )
    draw.rectangle(
        (0, TITLE_HEIGHT, width, TITLE_HEIGHT + ROWS * LINE_HEIGHT),
        fill=BACKGROUND,
    )

    for row_index, row in enumerate(terminal.cells):
        y = TITLE_HEIGHT + row_index * LINE_HEIGHT
        for column, cell in enumerate(row):
            if not cell.char or cell.char == " ":
                continue
            color = _dim(cell.style.color) if cell.style.dim else cell.style.color
            is_wide = _display_width(cell.char[0]) == 2
            if is_wide:
                font = cjk_bold_font if cell.style.bold else cjk_regular_font
            else:
                font = latin_bold_font if cell.style.bold else latin_regular_font
            draw.text(
                (SIDE_MARGIN + column * CELL_WIDTH, y),
                cell.char,
                font=font,
                fill=color,
            )

    cursor_base = (95, 215, 215)
    cursor_color = tuple(
        round(
            BACKGROUND[index]
            + (channel - BACKGROUND[index]) * cursor_strength
        )
        for index, channel in enumerate(cursor_base)
    )
    cursor_column = min(terminal.column, COLS - 1)
    cursor_x = SIDE_MARGIN + cursor_column * CELL_WIDTH
    cursor_y = TITLE_HEIGHT + terminal.row * LINE_HEIGHT + LINE_HEIGHT - 4
    draw.rectangle(
        (
            cursor_x,
            cursor_y,
            cursor_x + CELL_WIDTH - 2,
            cursor_y + 2,
        ),
        fill=cursor_color,
    )

    caption_top = TITLE_HEIGHT + ROWS * LINE_HEIGHT
    draw.rectangle((0, caption_top, width, height), fill=(21, 25, 31))
    draw.line((0, caption_top, width, caption_top), fill=(48, 54, 61), width=1)
    draw.text(
        (SIDE_MARGIN, caption_top + CAPTION_HEIGHT // 2),
        caption,
        font=latin_bold_font,
        fill=(95, 215, 215),
        anchor="lm",
    )
    return image


def _build_palette() -> Image.Image:
    """Build one deterministic palette for compact delta-encoded frames."""

    terminal_colors = (
        DEFAULT_FOREGROUND,
        _xterm_color(80),
        _xterm_color(114),
        _xterm_color(221),
        _xterm_color(203),
        _xterm_color(111),
        _xterm_color(183),
        _xterm_color(245),
    )
    pairs = [(color, BACKGROUND) for color in terminal_colors]
    pairs.extend(
        (
            ((210, 214, 220), (31, 35, 41)),
            ((95, 215, 215), (21, 25, 31)),
            ((255, 95, 86), (31, 35, 41)),
            ((255, 189, 46), (31, 35, 41)),
            ((39, 201, 63), (31, 35, 41)),
        )
    )
    colors: list[tuple[int, int, int]] = [
        (8, 11, 16),
        BACKGROUND,
        (21, 25, 31),
        (31, 35, 41),
        (48, 54, 61),
    ]
    for foreground, background in pairs:
        for step in range(1, 17):
            alpha = step / 16
            blended = tuple(
                round(
                    background[index]
                    + (channel - background[index]) * alpha
                )
                for index, channel in enumerate(foreground)
            )
            if blended not in colors:
                colors.append(blended)
    if len(colors) > 256:
        raise RuntimeError("Demo palette unexpectedly exceeds 256 colors.")
    flat = [channel for color in colors for channel in color]
    flat.extend([0] * (768 - len(flat)))
    palette = Image.new("P", (1, 1))
    palette.putpalette(flat)
    return palette


def _output_chunks(
    value: str,
    lines_per_frame: int = OUTPUT_LINES_PER_FRAME,
) -> Iterator[str]:
    """Yield complete ANSI-safe lines in small playback chunks."""

    lines = value.splitlines(keepends=True)
    if not lines and value:
        yield value
        return
    for index in range(0, len(lines), lines_per_frame):
        yield "".join(lines[index : index + lines_per_frame])


class Animation:
    """Turn a real CLI transcript into smooth, fixed-rate terminal frames."""

    def __init__(
        self,
        terminal: MiniTerminal,
        cjk_regular_font: ImageFont.FreeTypeFont,
        cjk_bold_font: ImageFont.FreeTypeFont,
        latin_regular_font: ImageFont.FreeTypeFont,
        latin_bold_font: ImageFont.FreeTypeFont,
    ) -> None:
        self.terminal = terminal
        self.cjk_regular_font = cjk_regular_font
        self.cjk_bold_font = cjk_bold_font
        self.latin_regular_font = latin_regular_font
        self.latin_bold_font = latin_bold_font
        self.palette = _build_palette()
        self.frames: list[Image.Image] = []
        self.durations: list[int] = []

    def snapshot(self, caption: str) -> None:
        pulse = (0.42, 0.64, 0.92, 0.64)[len(self.frames) % 4]
        rendered = _render_frame(
            self.terminal,
            caption,
            pulse,
            self.cjk_regular_font,
            self.cjk_bold_font,
            self.latin_regular_font,
            self.latin_bold_font,
        )
        self.frames.append(
            rendered.quantize(palette=self.palette, dither=Image.Dither.NONE)
        )
        self.durations.append(FRAME_DURATION_MS)

    def output(self, value: str, caption: str) -> None:
        for chunk in _output_chunks(value):
            self.terminal.feed(chunk)
            self.snapshot(caption)

    def type_command(self, value: str, caption: str) -> None:
        # TerminalUI resets prompt styling before the terminal echoes input.
        self.terminal.feed("\x1b[0m")
        for char in value:
            self.terminal.feed(char)
            self.snapshot(caption)
        self.press_enter(caption)

    def press_enter(self, caption: str) -> None:
        self.terminal.feed("\r\n")
        self.snapshot(caption)

    def hold(self, caption: str, frame_count: int) -> None:
        for _ in range(frame_count):
            self.snapshot(caption)


def _demo_overlay() -> dict[str, object]:
    """A minimal real OEWN 2025 record used only in the offline demo."""

    return {
        "schema_version": 1,
        "created_at": "2026-07-30T00:00:00+00:00",
        "synced_at": "2026-07-30T00:00:00+00:00",
        "provider": {
            "id": "oewn",
            "name": "Open English WordNet",
            "version": "2025",
            "tag_name": "2025-edition",
            "published_at": "2025-12-31T14:39:12Z",
            "release_url": (
                "https://github.com/globalwordnet/english-wordnet/"
                "releases/tag/2025-edition"
            ),
            "download_url": (
                "https://github.com/globalwordnet/english-wordnet/releases/"
                "download/2025-edition/english-wordnet-2025-json.zip"
            ),
            "homepage": "https://en-word.net/",
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "archive_sha256": (
                "7d749f6e2c39e6970e4997839dcf6e42"
                "fd281f3c2fae0171d2192bae8cfa4b51"
            ),
            "attribution": (
                "Open English WordNet, derived from Princeton WordNet, "
                "licensed under CC BY 4.0."
            ),
        },
        "entries": {
            "conservation": {
                "definition_en": (
                    "the preservation and careful management of the "
                    "environment and of natural resources"
                ),
                "synonyms": [],
                "synset_id": "00820935-n",
                "part_of_speech": "n",
                "match_score": 0.4522,
            }
        },
        "skipped": {},
    }


@contextmanager
def _demo_data_directory() -> Iterator[Path]:
    """Reserve a stable path so repeated renders are byte-for-byte stable."""

    data_dir = Path(tempfile.gettempdir()) / "ielts-codex-gif-demo"
    try:
        data_dir.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(
            f"Reserved demo directory already exists: {data_dir}. "
            "Move it aside before rendering; the script will not overwrite it."
        ) from exc
    try:
        yield data_dir
    finally:
        for filename in ("progress.json", "oewn_overlay.json"):
            (data_dir / filename).unlink(missing_ok=True)
        try:
            data_dir.rmdir()
        except OSError:
            # Do not delete unexpected files from a shared temporary directory.
            print(
                f"Warning: preserved non-demo files in {data_dir}",
                file=sys.stderr,
            )


def _read_expect(
    child: pexpect.spawn,
    pattern: str | Pattern[str] | object,
) -> str:
    child.expect(pattern)
    output = ""
    if isinstance(child.before, str):
        output += child.before
    if isinstance(child.after, str):
        output += child.after
    return output


def _record_session(
    cjk_regular_font: ImageFont.FreeTypeFont,
    cjk_bold_font: ImageFont.FreeTypeFont,
    latin_regular_font: ImageFont.FreeTypeFont,
    latin_bold_font: ImageFont.FreeTypeFont,
) -> tuple[list[Image.Image], list[int]]:
    terminal = MiniTerminal()
    animation = Animation(
        terminal,
        cjk_regular_font,
        cjk_bold_font,
        latin_regular_font,
        latin_bold_font,
    )

    with _demo_data_directory() as data_dir:
        (data_dir / "oewn_overlay.json").write_text(
            json.dumps(_demo_overlay(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.pop("NO_COLOR", None)
        source_dir = str(REPO_ROOT / "src")
        environment["PYTHONPATH"] = (
            source_dir
            + (
                os.pathsep + environment["PYTHONPATH"]
                if environment.get("PYTHONPATH")
                else ""
            )
        )
        environment.update(
            {
                "TERM": "xterm-256color",
                "COLUMNS": str(COLS),
                "LINES": str(ROWS),
                "PYTHONUNBUFFERED": "1",
            }
        )
        child = pexpect.spawn(
            sys.executable,
            [
                "-m",
                "ielts_codex",
                "--seed",
                "4",
                "--data-dir",
                str(data_dir),
            ],
            cwd=str(REPO_ROOT),
            env=environment,
            encoding="utf-8",
            timeout=15,
            dimensions=(ROWS, COLS),
            echo=False,
        )

        caption = "1 · Choose whether to update OEWN on startup"
        animation.output(_read_expect(child, "› "), caption)
        animation.hold(caption, 5)

        animation.type_command("n", caption)
        child.sendline("n")
        caption = "2 · Stay offline and open today's learning dashboard"
        animation.output(_read_expect(child, "› "), caption)
        animation.hold(caption, 5)

        caption = "3 · Start a focused environment vocabulary card"
        child.sendline("/learn 1 environment")
        animation.output(_read_expect(child, "q 结束  › "), caption)
        animation.hold(caption, 5)

        caption = "4 · Reveal bilingual details and OEWN attribution"
        animation.press_enter(caption)
        child.sendline("")
        animation.output(_read_expect(child, "评价记忆程度 › "), caption)
        animation.hold(caption, 8)

        caption = "5 · Rate recall for adaptive spaced repetition"
        animation.type_command("3", caption)
        child.sendline("3")
        animation.output(_read_expect(child, "› "), caption)
        animation.hold(caption, 6)

        caption = "6 · Spell the word from its Chinese definition"
        child.sendline("/quiz 1 environment")
        animation.output(_read_expect(child, "answer › "), caption)
        animation.hold(caption, 6)

        caption = "7 · Get immediate spelling feedback"
        animation.type_command("conservation", caption)
        child.sendline("conservation")
        animation.output(_read_expect(child, "› "), caption)
        animation.hold(caption, 6)

        caption = "8 · Search any word for its complete learning card"
        child.sendline("conservation")
        search_output = _read_expect(child, "来源")
        search_output += _read_expect(child, "› ")
        animation.output(search_output, caption)
        animation.hold(caption, 8)

        caption = "9 · Review learning progress and accuracy"
        child.sendline("/stats")
        stats_output = _read_expect(child, "累计动作")
        stats_output += _read_expect(child, "› ")
        animation.output(stats_output, caption)
        animation.hold(caption, 7)

        caption = "10 · Inspect the local OEWN synchronization status"
        child.sendline("/sync status")
        sync_output = _read_expect(child, "位置")
        sync_output += _read_expect(child, "› ")
        animation.output(sync_output, caption)
        animation.hold(caption, 8)

        caption = "11 · Quit — progress is saved locally"
        child.sendline("/quit")
        animation.output(_read_expect(child, pexpect.EOF), caption)
        animation.hold(caption, 8)
        child.close()
        if child.exitstatus not in (None, 0):
            raise RuntimeError(f"CLI demo exited with status {child.exitstatus}")

    return animation.frames, animation.durations


def _save_gif(
    frames: list[Image.Image],
    durations: list[int],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
        disposal=1,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record the real IELTS Codex CLI and render its README GIF."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"GIF destination (default: {DEFAULT_OUTPUT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--font",
        type=Path,
        help="CJK monospace font override (Noto Sans Mono CJK is auto-detected)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    (
        regular_path,
        bold_path,
        font_index,
        latin_regular_path,
        latin_bold_path,
    ) = _find_fonts(args.font)
    cjk_regular_font = ImageFont.truetype(
        str(regular_path), FONT_SIZE, index=font_index
    )
    cjk_bold_font = ImageFont.truetype(
        str(bold_path), FONT_SIZE, index=font_index
    )
    latin_size = FONT_SIZE if latin_regular_path == regular_path else 15
    latin_regular_font = ImageFont.truetype(
        str(latin_regular_path), latin_size
    )
    latin_bold_font = ImageFont.truetype(str(latin_bold_path), latin_size)
    frames, durations = _record_session(
        cjk_regular_font,
        cjk_bold_font,
        latin_regular_font,
        latin_bold_font,
    )
    output = args.output.resolve()
    _save_gif(frames, durations, output)
    size = output.stat().st_size / (1024 * 1024)
    total_seconds = sum(durations) / 1000
    print(
        f"Rendered {len(frames)} real CLI frames ({total_seconds:.1f}s, "
        f"{FRAME_DURATION_MS}ms/frame) to {output} ({size:.2f} MiB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
