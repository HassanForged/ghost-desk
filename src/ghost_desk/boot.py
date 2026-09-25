"""Ghost Desk boot. Left log, right photo. Same hood as the session."""

from __future__ import annotations

import os
import signal
import sys
import time

TEXT = "\033[38;2;214;214;214m"
DIM = "\033[38;2;138;138;138m"
RESET = "\033[0m"
HIDE = "\033[?25l"
SHOW = "\033[?25h"
LEFT_COLS = 42

_GHOST_CACHE: list[str] | None = None


def _enable_windows_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _ghost_rows() -> list[str]:
    global _GHOST_CACHE
    if _GHOST_CACHE is None:
        from ghost_desk.face import render_ansi

        _GHOST_CACHE = render_ansi()
    return _GHOST_CACHE


def _visible(text: str) -> int:
    return len(text)


def _paint(left: list[str], level: int = 4, cursor: bool = False) -> None:
    ghost = _ghost_rows()
    rows = max(len(left) + 1, len(ghost), 16)
    _write("\033[H")
    for i in range(rows):
        line = left[i] if i < len(left) else ""
        art = ghost[i] if i < len(ghost) else ""
        mark = "█" if cursor and i == min(len(left), rows - 1) and i == len(left) else ""
        shown = line + mark
        pad = max(2, LEFT_COLS - _visible(shown))
        _write("\033[2K" + TEXT + shown + RESET + (" " * pad) + art + "\n")
    _write("\033[J")


def _dots(left: list[str], level: int, stem: str) -> None:
    for n in range(1, 5):
        _paint(left + [stem + "." * n], level, True)
        time.sleep(0.07)


def _line(left: list[str], level: int, stem: str, done: str, pause: float = 0.18) -> list[str]:
    _dots(left, level, stem)
    left = left + [stem + done]
    _paint(left, level, True)
    time.sleep(pause)
    return left


def _read_key() -> str:
    if os.name == "nt":
        import msvcrt

        return msvcrt.getwch()
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_line() -> str:
    chars: list[str] = []
    while True:
        key = _read_key()
        if key in {"\x03"}:
            raise KeyboardInterrupt
        if key in {"\r", "\n"}:
            return "".join(chars)
        if key in {"\x08", "\x7f"}:
            if chars:
                chars.pop()
            continue
        if key in {"\x00", "\xe0"}:
            _read_key()
            continue
        if key.isprintable():
            chars.append(key)


def _type_title(left: list[str], level: int, title: str) -> list[str]:
    for count in range(1, len(title) + 1):
        _paint(left + [title[:count]], level, True)
        time.sleep(0.022)
    return left + [title]


def _menu() -> list[str]:
    return [
        "which brain",
        "  1  chatgpt subscription",
        "  2  claude subscription",
        "  3  grok subscription",
        "  4  api key",
        "  5  local model",
    ]


def _checks(level_step=None) -> list[str]:
    left: list[str] = []
    left = _type_title(left, 0, "GHOST DESK")
    steps = (
        (0, "waking local harness", " ready"),
        (1, "checking this pc", " ok"),
        (2, "memory", " on disk"),
        (3, "skills", " loaded"),
        (4, "provider", " unsigned"),
        (4, "sessions", " kept"),
        (4, "handoffs", " kept"),
    )
    for level, stem, done in steps:
        left = _line(left, level, stem, done)
        if level_step is not None:
            level_step(level)
    return left


def _choose(left: list[str]) -> str:
    frame = left + [""] + _menu()
    _paint(frame, 4, False)
    while True:
        key = _read_key()
        if key in {"\x00", "\xe0"}:
            _read_key()
            continue
        if key in {"\x03", "\x1b"}:
            raise KeyboardInterrupt
        if key in {"1", "2", "3", "4", "5"}:
            locked = frame + ["", "brain locked"]
            _paint(locked, 4, False)
            time.sleep(0.35)
            _paint(locked + ["ready"], 4, False)
            return key


class BootScreen:
    """Keep the boot canvas up through setup prompts."""

    def __init__(self) -> None:
        self.left: list[str] = []
        self._armed = False

    def enter(self) -> None:
        _enable_windows_ansi()
        _write(HIDE + "\033[2J\033[H")
        self._armed = True
        self._trap()

    def leave(self) -> None:
        _write(SHOW + RESET)
        self._armed = False

    def play_checks(self, *, signed: bool = False) -> None:
        self.left = []
        self.left = _type_title(self.left, 0, "GHOST DESK")
        steps = (
            ("waking local harness", " ready"),
            ("checking this pc", " ok"),
            ("memory", " on disk"),
            ("skills", " loaded"),
            ("provider", " signed" if signed else " unsigned"),
            ("sessions", " kept"),
            ("handoffs", " kept"),
        )
        for stem, done in steps:
            self.left = _line(self.left, 4, stem, done)

    def choose(self) -> str:
        return _choose(self.left)

    def log(self, line: str = "") -> None:
        text = str(line).rstrip()
        if text:
            self.left.append(text)
        _paint(self.left, 4, False)

    def ask(self, prompt: str) -> str:
        secret = any(word in prompt.lower() for word in ("api_key", "token", "password", "secret"))
        self.left.append(prompt.rstrip())
        _paint(self.left, 4, True)
        if not sys.stdin.isatty():
            value = sys.stdin.readline().rstrip("\n")
        else:
            value = _read_line()
        shown = "stored" if secret and value else value
        self.left[-1] = prompt.rstrip() + shown
        _paint(self.left, 4, False)
        return value

    def _trap(self) -> None:
        def _stop(_signum, _frame):
            _write(SHOW + RESET)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _stop)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _stop)


def run_boot(skip_to_menu: bool = False, choose: bool = True, signed: bool = False) -> str:
    """Play the boot. Return '1'..'5' when choosing a brain."""
    screen = BootScreen()
    screen.enter()
    try:
        if skip_to_menu:
            screen.left = ["GHOST DESK"]
            _paint(screen.left, 4, False)
        else:
            screen.play_checks(signed=signed)
        if not choose:
            screen.log("session open")
            time.sleep(0.35)
            return ""
        return screen.choose()
    finally:
        screen.leave()


if __name__ == "__main__":
    try:
        print(run_boot(skip_to_menu="--menu" in sys.argv))
    except KeyboardInterrupt:
        raise SystemExit(130)
