"""Exercise the real Windows console path through ConPTY (stdlib only).

Run with the Python that should be checked::

    python scripts/smoke_windows_terminal.py --output terminal-results/windows

Add --installed to test an already-installed wheel instead of src/. All learning
records live in a fresh temporary directory; the user's profile is never opened.
This checks console behavior and saved data, not visual screenshots or layout.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[()][A-Z0-9]|\x1b[=>]")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def child(data_dir: str, installed: bool) -> int:
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel.SetStdHandle.restype = wintypes.BOOL
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetConsoleMode.restype = wintypes.BOOL
    # A CI runner can pass redirected standard handles into CreateProcess even
    # when it attaches the child to a pseudoconsole. Bind its actual console
    # devices before importing the CLI (whose UI defaults capture sys streams).
    # These are genuine console handles; no isatty or application code is patched.
    original_streams = (sys.stdin, sys.stdout, sys.stderr)
    for index, device in enumerate(("CONIN$", "CONOUT$", "CONOUT$")):
        handle = kernel.CreateFileW(device, 0xC0000000, 3, None, 3, 0, None)
        check(handle != ctypes.c_void_p(-1).value, "Pseudoconsole device unavailable")
        descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY)
        os.dup2(descriptor, index)
        os.close(descriptor)
        check(kernel.SetStdHandle(-10 - index, msvcrt.get_osfhandle(index)), "Cannot bind console handle")
    sys.stdin = open(0, "r", encoding="utf-8", closefd=False)
    sys.stdout = open(1, "w", encoding="utf-8", buffering=1, closefd=False)
    sys.stderr = open(2, "w", encoding="utf-8", buffering=1, closefd=False)
    if not installed:
        sys.path.insert(0, str(ROOT / "src"))
    from ielts_codex.cli import main
    from ielts_codex.ui import TerminalUI

    modes = []
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        mode = wintypes.DWORD()
        modes.append(bool(kernel.GetConsoleMode(msvcrt.get_osfhandle(stream.fileno()), ctypes.byref(mode))))
    tty = all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))
    terminal = TerminalUI()._command_terminal()
    check(tty and all(modes) and terminal is not None, "Child does not have genuine console handles")
    print("CONPTY_PROBE isatty=3/3 GetConsoleMode=3/3 palette=enabled", flush=True)
    return main(["--data-dir", data_dir, "--no-color", "--seed", "17"])


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEX(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class Console:
    """A hidden ConPTY with real console child handles and byte-oriented host I/O."""

    def __init__(self, argv, columns=100, rows=32):
        self.kernel = k = ctypes.WinDLL("kernel32", use_last_error=True)
        handle_ptr = ctypes.POINTER(wintypes.HANDLE)
        dword_ptr = ctypes.POINTER(wintypes.DWORD)
        k.CreatePipe.argtypes = [handle_ptr, handle_ptr, ctypes.c_void_p, wintypes.DWORD]
        k.CreatePipe.restype = wintypes.BOOL
        k.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, handle_ptr]
        k.CreatePseudoConsole.restype = ctypes.c_long
        k.ResizePseudoConsole.argtypes = [wintypes.HANDLE, COORD]
        k.ResizePseudoConsole.restype = ctypes.c_long
        k.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        k.ClosePseudoConsole.restype = None
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        k.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        k.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k.UpdateProcThreadAttribute.restype = wintypes.BOOL
        k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k.DeleteProcThreadAttributeList.restype = None
        k.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                    ctypes.POINTER(STARTUPINFOEX), ctypes.POINTER(PROCESS_INFORMATION)]
        k.CreateProcessW.restype = wintypes.BOOL
        k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, dword_ptr, ctypes.c_void_p]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, dword_ptr, ctypes.c_void_p]
        k.WriteFile.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, dword_ptr]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        self.input = wintypes.HANDLE()
        self.output = wintypes.HANDLE()
        self.pseudo = wintypes.HANDLE()
        self.process = PROCESS_INFORMATION()
        self.chunks = bytearray()
        self.lock = threading.Lock()
        self.reader = None
        self.offset = 0
        self.closed = False
        read_input, write_output = wintypes.HANDLE(), wintypes.HANDLE()
        attributes = None
        attributes_ready = False
        try:
            self._ok(k.CreatePipe(ctypes.byref(read_input), ctypes.byref(self.input), None, 0))
            self._ok(k.CreatePipe(ctypes.byref(self.output), ctypes.byref(write_output), None, 0))
            self._hr(k.CreatePseudoConsole(COORD(columns, rows), read_input, write_output, 0, ctypes.byref(self.pseudo)))
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            size = ctypes.c_size_t()
            k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            self._ok(k.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
            attributes_ready = True
            self._ok(k.UpdateProcThreadAttribute(attributes, 0, 0x00020016,
                                                 self.pseudo, ctypes.sizeof(self.pseudo), None, None))
            startup = STARTUPINFOEX()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", NO_COLOR="1")
            block = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0")
            # ConPTY supplies the child's console. No visible window is created.
            self._ok(k.CreateProcessW(None, command, None, None, False, 0x00080000 | 0x00000400,
                                      block, str(ROOT), ctypes.byref(startup), ctypes.byref(self.process)))
        except BaseException:
            self.close()
            raise
        finally:
            if attributes_ready:
                k.DeleteProcThreadAttributeList(attributes)
            for handle in (read_input, write_output):
                if handle:
                    k.CloseHandle(handle)

    @staticmethod
    def _ok(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _hr(result):
        if result < 0:
            raise OSError(f"ConPTY HRESULT 0x{result & 0xffffffff:08x}")

    def _read(self):
        buffer = ctypes.create_string_buffer(16384)
        count = wintypes.DWORD()
        while self.kernel.ReadFile(self.output, buffer, len(buffer), ctypes.byref(count), None):
            if not count.value:
                break
            with self.lock:
                self.chunks.extend(buffer.raw[:count.value])

    @property
    def text(self):
        with self.lock:
            raw = bytes(self.chunks)
        return ANSI.sub("", raw.decode("utf-8", errors="replace")).replace("\r", "")

    def send(self, keys):
        data = keys.encode("utf-8")
        count = wintypes.DWORD()
        self._ok(self.kernel.WriteFile(self.input, data, len(data), ctypes.byref(count), None))
        check(count.value == len(data), "Console input was truncated")

    def expect(self, text, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            transcript = self.text
            index = transcript.find(text, self.offset)
            if index >= 0:
                self.offset = index + len(text)
                return transcript[index:self.offset]
            if self.kernel.WaitForSingleObject(self.process.hProcess, 0) == 0:
                # The reader may still be draining the final console frame.
                time.sleep(0.1)
                if text not in self.text[self.offset:]:
                    break
            time.sleep(0.02)
        raise AssertionError(f"Timed out waiting for {text!r}\nRecent output:\n{self.text[-2400:]}")

    def resize(self, columns, rows):
        self._hr(self.kernel.ResizePseudoConsole(self.pseudo, COORD(columns, rows)))

    def finish(self, expected=0):
        check(self.kernel.WaitForSingleObject(self.process.hProcess, 10000) == 0, "Child did not exit")
        code = wintypes.DWORD()
        self._ok(self.kernel.GetExitCodeProcess(self.process.hProcess, ctypes.byref(code)))
        check(code.value == expected, f"Child exit code {code.value}, expected {expected}")

    def close(self):
        if self.closed:
            return
        self.closed = True
        k = self.kernel
        if self.process.hProcess and k.WaitForSingleObject(self.process.hProcess, 0) != 0:
            k.TerminateProcess(self.process.hProcess, 99)
            k.WaitForSingleObject(self.process.hProcess, 3000)
        if self.pseudo:
            k.ClosePseudoConsole(self.pseudo)
        if self.reader:
            self.reader.join(timeout=3)
        for handle in (self.input, self.output, self.process.hThread, self.process.hProcess):
            if handle:
                k.CloseHandle(handle)


def read_progress(data_dir):
    return json.loads((data_dir / "progress.json").read_text(encoding="utf-8"))


def run(output, installed):
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text('{"status": "running"}', encoding="utf-8")
    sessions = []
    results = []
    with tempfile.TemporaryDirectory(prefix="ielts-conpty-") as temporary:
        data_dir = Path(temporary) / "学习记录 with spaces"
        data_dir.mkdir()
        argv = [sys.executable, "-X", "utf8", "-u", str(Path(__file__).resolve()),
                "--child", str(data_dir)] + (["--installed"] if installed else [])
        try:
            console = Console(argv)
            sessions.append(console)
            console.expect("CONPTY_PROBE isatty=3/3 GetConsoleMode=3/3 palette=enabled")
            console.expect("输入 /study")
            results.append("stdin/stdout/stderr are real TTY console handles; palette enabled")

            console.send("/")
            console.expect("/import")
            console.send("\x1b[B")
            console.expect("› /import")
            console.send("\x1b[A")
            console.expect("› /study")
            console.send("\x03")
            console.expect("已取消当前输入")
            results.append("slash menu, down/up arrows, Ctrl+C cancel")

            console.send("/lea\t")
            console.expect("› /learn")
            console.send("1\r")
            for attempt in range(3):
                console.expect("Enter 显示答案")
                console.send("\r")
                console.expect("评价记忆程度")
                console.send("1\r")
                if attempt < 2:
                    console.expect("relearn")
            console.expect("learn 完成")
            progress = read_progress(data_dir)
            attempts = progress["attempts"]
            check(len(attempts) == 3, "Again must create exactly three saved attempts")
            check([item["retry"] for item in attempts] == [False, True, True], "Again retry labels are wrong")
            check(all(item["task"] == "recall" and item["rating"] == 1 for item in attempts), "Again ratings are wrong")
            results.append("Tab completion and Enter dispatch; Again twice requeued and capped at three")

            if not installed:
                sys.path.insert(0, str(ROOT / "src"))
            from ielts_codex.word_bank import WordBank
            word = WordBank.bundled().get(attempts[0]["word"]).word
            console.send("/quiz 1\r")
            console.expect("answer ›")
            console.send(word[:2] + "123" + word[2:] + "\r")
            console.expect("不会自动忽略")
            console.expect("quiz relearn")
            console.expect("answer ›")
            console.send("h\r")
            console.expect("提示：")
            console.expect("answer ›")
            console.send(word.upper() + "\r")
            console.expect("quiz 完成")
            spelling = read_progress(data_dir)["attempts"][-2:]
            check(spelling[0]["correct"] is False and spelling[0]["error_type"] == "unexpected_digit",
                  "Embedded digits must be rejected rather than normalized away")
            check(spelling[1]["correct"] is True and spelling[1]["hint_level"] == 1
                  and spelling[1]["rating"] == 2 and spelling[1]["retry"] is True,
                  "Hint-assisted spelling must remain distinct from independent success")
            results.append("Spelling rejects embedded digits; uppercase accepted; hints save Hard and assistance")

            console.send("/study 5\r")
            console.expect("每日分钟")
            console.send("\r")
            console.expect("薄弱项")
            console.send("1\r")
            console.expect("词包")
            console.send("\r")
            console.expect("Enter 开始/继续")
            console.send("\r")
            console.expect("Enter 显示答案")
            console.send("\r")
            console.expect("评价记忆程度")
            console.send("3\r")
            console.expect("Enter 显示答案")
            console.send("q\r")
            console.expect("已保存已完成回答和剩余任务")
            saved = read_progress(data_dir)
            check(saved["study"]["completed"] == 1 and saved["study"]["items"], "Study checkpoint missing")
            check(len(saved["attempts"]) == 6, "Completed study answer was not saved exactly once")
            results.append("Study onboarding, one completed answer, q preserves pending work")

            console.send("/quit\r")
            console.expect("已退出")
            console.finish()
            console.close()
            console = Console(argv)
            sessions.append(console)
            console.expect("CONPTY_PROBE isatty=3/3 GetConsoleMode=3/3 palette=enabled")
            console.expect("输入 /study")
            console.send("/study\r")
            console.expect("已处理 1 项")
            console.expect("Enter 开始/继续")
            console.send("\r")
            console.expect("Enter 显示答案")
            console.send("q\r")
            console.expect("已保存已完成回答和剩余任务")
            resumed = read_progress(data_dir)
            check(resumed["study"] == saved["study"], "Resume changed unanswered study tasks")
            check(resumed["attempts"] == saved["attempts"], "Resume duplicated completed answers")
            results.append("Fresh process resumes exact checkpoint without duplicating answers")

            console.resize(60, 16)
            console.send("/help\r")
            console.expect("commands")
            console.expect("学习中 q 返回")
            console.send("/")
            console.expect("/import")
            console.send("\x03")
            console.expect("已取消当前输入")
            console.send("/stats\r")
            console.expect("稳定复习")
            console.send("/quit\r")
            console.expect("已退出")
            console.finish()
            results.append("Resize 100x32 to 60x16; palette and Ctrl+C survive; stats and clean quit")
            check("Traceback" not in "\n".join(item.text for item in sessions), "Unexpected traceback")
            (output / "progress-fixture.json").write_text(json.dumps(read_progress(data_dir), ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            for index, session in enumerate(sessions, 1):
                session.close()
                (output / f"session-{index}.txt").write_text(session.text, encoding="utf-8")
                (output / f"session-{index}.ansi").write_bytes(bytes(session.chunks))
    summary = {"platform": sys.platform, "python": sys.version, "backend": "Windows ConPTY",
               "target": "installed" if installed else "source",
               "package_file": str(Path(sys.modules["ielts_codex"].__file__).resolve()),
               "package_version": sys.modules["ielts_codex"].__version__,
               "checks": results, "status": "passed"}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("terminal-results/windows"))
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This test requires Windows 10 1809+ / Windows Server 2019+ with ConPTY")
    if args.child:
        return child(args.child, args.installed)
    try:
        return run(args.output, args.installed)
    except BaseException as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "summary.json").write_text(
            json.dumps({"platform": sys.platform, "python": sys.version,
                        "backend": "Windows ConPTY", "status": "failed",
                        "error": str(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
