#!/usr/bin/env python3
"""Exercise the real CLI through a POSIX pseudo-terminal, without dependencies.

Run after installation on Linux/macOS:
    python scripts/smoke_posix_terminal.py --report-dir artifacts/terminal

For a source checkout, set PYTHONPATH=src. This sends real terminal input; it
neither mocks TerminalUI nor uses subprocess pipes for the application's stdio.
Reports contain synthetic learning data and terminal text, never user progress.
"""
from __future__ import annotations

import argparse
import codecs
import errno
import json
import os
from pathlib import Path
import platform
import re
import signal
import struct
import subprocess
import sys
import tempfile
import time

if os.name == "posix":
    import fcntl
    import pty
    import select
    import termios


PROMPT = "\r\x1b[J› \r\x1b[2C"
REVEAL = "Enter 显示答案"
RATING = "评价记忆程度 › "
CONTINUE = "Enter 开始/继续 · q 返回 › "
SAVED = "已保存已完成回答和剩余任务。下次 /study 从这里继续。"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def child_main(data_dir):
    # start_new_session isolates signals; acquiring the slave makes Ctrl+C a
    # real terminal-generated signal, rather than merely a byte in a pipe.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())
    import ielts_codex
    from ielts_codex import __version__
    from ielts_codex.cli import main
    from ielts_codex.ui import TerminalUI

    initial_mode = termios.tcgetattr(0)[3]
    terminal = TerminalUI(color=False)._command_terminal()
    evidence = {
        "os": platform.system(), "python": platform.python_version(),
        "version": __version__, "module_path": ielts_codex.__file__,
        "stdin_tty": sys.stdin.isatty(),
        "stdout_tty": sys.stdout.isatty(), "stderr_tty": sys.stderr.isatty(),
        "terminal": list(terminal[:2]) if terminal else None,
        "controlling_tty": os.tcgetpgrp(0) == os.getpgrp(),
    }
    print("__IELTS_TTY__" + json.dumps(evidence), flush=True)
    result = main(["--data-dir", data_dir, "--no-color", "--seed", "17"])
    # Darwin invalidates the parent's slave handle after this controlling
    # process exits. Measure restoration here while the terminal is still live.
    restored_mode = termios.tcgetattr(0)[3]
    mask = termios.ICANON | termios.ECHO | termios.ISIG
    restoration = {
        "stdin_tty": sys.stdin.isatty(), "mask": mask,
        "before": initial_mode & mask, "after": restored_mode & mask,
        "cli_exit": result,
    }
    print("__IELTS_TTY_RESTORED__" + json.dumps(restoration), flush=True)
    return result


class Terminal:
    def __init__(self, data_dir, report_dir, name, *, columns=100, rows=32):
        self.master, self.slave = pty.openpty()
        self.before = termios.tcgetattr(self.slave)
        self.resize(columns, rows)
        self.raw = ""
        self.cursor = 0
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.report_dir, self.name = report_dir, name
        self.deadline = time.monotonic() + 120
        environment = dict(os.environ)
        environment.update(TERM="xterm-256color", PYTHONUTF8="1", NO_COLOR="1",
                           IELTS_CODEX_HOME=str(data_dir))
        # Terminal geometry must come from ioctl, not the runner's environment.
        environment.pop("COLUMNS", None)
        environment.pop("LINES", None)
        self.process = None
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-u", str(Path(__file__).resolve()),
                 "--child", str(data_dir)],
                stdin=self.slave, stdout=self.slave, stderr=self.slave,
                env=environment, start_new_session=True, close_fds=True,
            )
        except BaseException:
            os.close(self.master)
            os.close(self.slave)
            raise

    def resize(self, columns, rows):
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, columns, 0, 0))

    def read(self, timeout=0.1):
        readable, _, _ = select.select([self.master], [], [], timeout)
        if readable:
            try:
                data = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return
                raise
            self.raw += self.decoder.decode(data)

    def expect(self, pattern, *, regex=False, timeout=15):
        expression = re.compile(pattern if regex else re.escape(pattern))
        deadline = min(self.deadline, time.monotonic() + timeout)
        while time.monotonic() < deadline:
            match = expression.search(self.raw, self.cursor)
            if match:
                self.cursor = match.end()
                return match
            if self.process.poll() is not None:
                self.read(0)
                match = expression.search(self.raw, self.cursor)
                if match:
                    self.cursor = match.end()
                    return match
                break
            self.read(min(0.1, max(0, deadline - time.monotonic())))
        tail = ANSI.sub("", self.raw[-5000:])
        raise AssertionError(f"{self.name}: expected {pattern!r}; "
                             f"exit={self.process.poll()}\n{tail}")

    def send(self, keys):
        data = keys.encode("utf-8")
        while data:
            written = os.write(self.master, data)
            data = data[written:]

    def ready(self):
        self.expect(PROMPT)
        mode = termios.tcgetattr(self.slave)[3]
        check(not (mode & termios.ICANON), "command prompt did not enable cbreak")
        check(not (mode & termios.ECHO), "command prompt is unexpectedly echoing")

    def command(self, command):
        self.send(command + "\r")

    def evidence(self):
        match = self.expect(r"__IELTS_TTY__(\{[^\r\n]+\})", regex=True)
        evidence = json.loads(match.group(1))
        check(all(evidence[key] for key in ("stdin_tty", "stdout_tty", "stderr_tty",
                                           "controlling_tty")), "not a real controlling TTY")
        check(evidence["terminal"] is not None, "slash menu silently used line-input fallback")
        self.ready()
        return evidence

    def restored(self):
        current = termios.tcgetattr(self.slave)
        mask = termios.ICANON | termios.ECHO | termios.ISIG
        check(current[3] & mask == self.before[3] & mask,
              "terminal input flags were not restored")

    def quit(self):
        self.command("/quit")
        self.expect("已退出，学习进度已保存在本地。")
        restored = self.expect(r"__IELTS_TTY_RESTORED__(\{[^\r\n]+\})", regex=True)
        evidence = json.loads(restored.group(1))
        check(evidence["stdin_tty"] and evidence["before"] == evidence["after"],
              "child terminal input flags were not restored before exit")
        check(evidence["cli_exit"] == 0, "CLI returned a nonzero status")
        deadline = min(self.deadline, time.monotonic() + 10)
        while self.process.poll() is None and time.monotonic() < deadline:
            self.read()
        check(self.process.poll() == 0, f"unclean child exit: {self.process.poll()}")
        return evidence

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
        for _ in range(10):
            previous = len(self.raw)
            self.read(0)
            if len(self.raw) == previous:
                break
        self.report_dir.mkdir(parents=True, exist_ok=True)
        (self.report_dir / f"{self.name}.ansi.txt").write_text(self.raw, encoding="utf-8")
        (self.report_dir / f"{self.name}.txt").write_text(ANSI.sub("", self.raw), encoding="utf-8")
        os.close(self.master)
        os.close(self.slave)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def read_progress(data_dir):
    return json.loads((data_dir / "progress.json").read_text(encoding="utf-8"))


def run(report_dir):
    from ielts_codex.word_bank import WordBank

    results = {"os": platform.system(), "python": platform.python_version(),
               "transport": "POSIX openpty + controlling terminal", "checks": []}
    report_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="ielts-terminal-") as temporary:
            data_dir = Path(temporary) / "isolated-profile"
            with Terminal(data_dir, report_dir, "learning") as terminal:
                results["tty"] = terminal.evidence()
                terminal.send("/")
                terminal.expect("› /study")
                terminal.send("\x1b[B")
                terminal.expect("› /import")
                terminal.send("\x1b[A")
                terminal.expect("› /study")
                terminal.send("\x03")
                terminal.expect("已取消当前输入")
                terminal.ready()
                results["checks"].append("slash menu, up/down keys, terminal Ctrl+C")

                terminal.send("/lea")
                terminal.expect("› /learn")
                terminal.send("\t")
                terminal.expect("› /learn ")
                terminal.send("1\r")
                for attempt in range(3):
                    terminal.expect(REVEAL)
                    terminal.restored()
                    terminal.send("\r")
                    terminal.expect(RATING)
                    terminal.send("1\r")
                    terminal.expect("已加入本组重练" if attempt < 2 else "本组三次上限")
                terminal.ready()
                progress = read_progress(data_dir)
                attempts = progress["attempts"]
                check(len(attempts) == 3, "Again did not stop after three answers")
                check([a["retry"] for a in attempts] == [False, True, True], "retry evidence lost")
                check(len({a["word"] for a in attempts}) == 1, "Again repeated the wrong word")
                check(all(a["task"] == "recall" and a["rating"] == 1 for a in attempts),
                      "recall ratings were not recorded accurately")
                key = attempts[0]["word"]
                check(progress["skill_cards"]["recall"][key]["repetitions"] == 0,
                      "repeated Again incorrectly produced successful repetitions")
                results["checks"].append("Tab completion, reveal/rating, bounded Again and saved retries")

                word = WordBank.bundled().get(key)
                check(word is not None, "saved word is missing from installed content")
                terminal.command("/quiz 1")
                terminal.expect("answer › ")
                terminal.send(word.word[:2] + "123" + word.word[2:] + "\r")
                terminal.expect("数字、标点和词内空格不会自动忽略。")
                terminal.expect("answer › ")
                terminal.send("h\r")
                terminal.expect("answer › ")
                terminal.send(word.word.upper() + "\r")
                terminal.ready()
                spelling = read_progress(data_dir)["attempts"][-2:]
                check(spelling[0]["correct"] is False and spelling[0]["error_type"] == "unexpected_digit",
                      "embedded digits were incorrectly accepted")
                check(spelling[1]["correct"] is True and spelling[1]["hint_level"] == 1
                      and spelling[1]["rating"] == 2 and spelling[1]["retry"] is True,
                      "hinted retry did not retain distinct evidence")
                results["checks"].append("strict spelling answer, hint, separate skill and retry records")

                terminal.command("/study 5")
                terminal.expect("每日分钟")
                terminal.send("\r")
                terminal.expect("薄弱项")
                terminal.send("1\r")
                terminal.expect("词包")
                terminal.send("\r")
                terminal.expect(CONTINUE)
                terminal.send("\r")
                terminal.expect(REVEAL)
                terminal.send("\r")
                terminal.expect(RATING)
                terminal.send("3\r")
                terminal.expect(r"Enter 显示答案|answer › |choice › ", regex=True)
                terminal.send("q\r")
                terminal.expect(SAVED)
                terminal.ready()
                saved = read_progress(data_dir)
                check(saved["study"]["completed"] == 1 and saved["study"]["items"],
                      "study did not preserve its next unanswered item")
                check(len(saved["attempts"]) == 6, "study answer was lost or duplicated")
                results["terminal_restoration"] = [terminal.quit()]
                results["checks"].append("study onboarding, completed answer saved before q, clean exit")

            with Terminal(data_dir, report_dir, "resume") as terminal:
                terminal.evidence()
                terminal.command("/study")
                terminal.expect(CONTINUE)
                check(read_progress(data_dir)["study"] == saved["study"], "restart changed pending plan")
                terminal.send("\r")
                terminal.expect(r"Enter 显示答案|answer › |choice › ", regex=True)
                terminal.send("q\r")
                terminal.expect(SAVED)
                terminal.ready()
                resumed = read_progress(data_dir)
                check(resumed["study"] == saved["study"], "unanswered card changed the study cursor")
                check(resumed["attempts"] == saved["attempts"], "resume duplicated answers")
                terminal.resize(60, 16)
                check(tuple(os.get_terminal_size(terminal.slave)) == (60, 16), "PTY resize did not apply")
                results["resized_terminal"] = [60, 16]
                terminal.command("/help")
                terminal.expect("commands")
                terminal.ready()
                terminal.send("/sta\x1b[C")
                terminal.expect("› /stats ")
                terminal.send("\r")
                terminal.expect("累计动作")
                terminal.ready()
                terminal.send("\x03")
                terminal.expect("已取消当前输入")
                terminal.ready()
                results["terminal_restoration"].append(terminal.quit())
                results["checks"].append("process restart, exact study resume, resize, Right completion, terminal restoration")
            results["attempts_saved"] = len(read_progress(data_dir)["attempts"])
        results["passed"] = True
    except BaseException as exc:
        results["passed"] = False
        results["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (report_dir / "summary.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=Path("artifacts/terminal"))
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("This harness requires Linux/macOS PTYs; use the Windows ConPTY harness on Windows.")
    if args.child is not None:
        return child_main(str(args.child))
    return run(args.report_dir.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
