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
        elif final == "J" and 2 in values:
            self.cells = self._blank_screen()
            self.row = self.column = 0
        elif final in {"H", "f"}:
            self.row = max(0, min(self.rows - 1, (values[0] or 1) - 1))
            target = values[1] if len(values) > 1 else 1
            self.column = max(0, min(self.columns - 1, (target or 1) - 1))
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
        "ielts-codex 0.3.0  ·  real CLI session",
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


def _feed_expect(
    child: pexpect.spawn,
    terminal: MiniTerminal,
    pattern: str | Pattern[str] | object,
) -> None:
    child.expect(pattern)
    if isinstance(child.before, str):
        terminal.feed(child.before)
    if isinstance(child.after, str):
        terminal.feed(child.after)


def _record_session(
    cjk_regular_font: ImageFont.FreeTypeFont,
    cjk_bold_font: ImageFont.FreeTypeFont,
    latin_regular_font: ImageFont.FreeTypeFont,
    latin_bold_font: ImageFont.FreeTypeFont,
) -> tuple[list[Image.Image], list[int]]:
    terminal = MiniTerminal()
    frames: list[Image.Image] = []
    durations: list[int] = []

    def snapshot(caption: str, duration: int) -> None:
        frames.append(
            _render_frame(
                terminal,
                caption,
                cjk_regular_font,
                cjk_bold_font,
                latin_regular_font,
                latin_bold_font,
            )
        )
        durations.append(duration)

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
        )

        _feed_expect(child, terminal, "› ")
        snapshot("1 · Choose whether to update OEWN on startup", 1700)

        child.sendline("n")
        _feed_expect(child, terminal, "› ")
        snapshot("2 · Stay offline and open today's learning dashboard", 1500)

        child.sendline("/learn 1 environment")
        _feed_expect(child, terminal, "› ")
        snapshot("3 · Start a focused environment vocabulary card", 1700)

        child.sendline("")
        _feed_expect(child, terminal, "评价记忆程度 › ")
        snapshot("4 · Reveal bilingual details and OEWN attribution", 2400)

        child.sendline("3")
        _feed_expect(child, terminal, "› ")
        snapshot("5 · Rate recall for adaptive spaced repetition", 1800)

        child.sendline("/quiz 1 environment")
        _feed_expect(child, terminal, "answer › ")
        snapshot("6 · Spell the word from its Chinese definition", 1900)

        child.sendline("conservation")
        _feed_expect(child, terminal, "› ")
        snapshot("7 · Get immediate spelling feedback", 1700)

        child.sendline("conservation")
        _feed_expect(child, terminal, "› ")
        snapshot("8 · Search any word for its complete learning card", 2300)

        child.sendline("/stats")
        _feed_expect(child, terminal, "› ")
        snapshot("9 · Review learning progress and accuracy", 1800)

        child.sendline("/sync status")
        _feed_expect(child, terminal, "› ")
        snapshot("10 · Inspect the local OEWN synchronization status", 2100)

        child.sendline("/quit")
        _feed_expect(child, terminal, pexpect.EOF)
        snapshot("11 · Quit — progress is saved locally", 1800)
        child.close()
        if child.exitstatus not in (None, 0):
            raise RuntimeError(f"CLI demo exited with status {child.exitstatus}")

    return frames, durations


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
        optimize=True,
        disposal=2,
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
    print(f"Rendered {len(frames)} real CLI frames to {output} ({size:.2f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
