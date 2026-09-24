"""Ghost Desk first-run boot. Stdlib only. Drop in and call run_boot()."""

from __future__ import annotations

import os
import signal
import sys
import time

TEXT = "\033[38;2;214;214;214m"
GHOST_DIM = "\033[38;2;138;138;138m"
GHOST_MID = "\033[38;2;176;176;176m"
GHOST_HOT = "\033[38;2;207;207;207m"
RESET = "\033[0m"
HIDE = "\033[?25l"
SHOW = "\033[?25h"

GHOST = [
    r"""
                 · · ·
              ·         ·
            ·             ·
""",
    r"""
                 · · · · ·
              ·             ·
            ·     ·     ·     ·
           ·                   ·
            ·                 ·
""",
    r"""
                · · · · · · ·
             ·                 ·
           ·     ·       ·       ·
          ·    ·           ·      ·
          ·      ·       ·        ·
           ·       · · ·         ·
            ·                   ·
              ·               ·
                · · · · · · ·
""",
    r"""
               · · · · · · · · ·
            ·                     ·
          ·      ·         ·        ·
         ·     ·             ·       ·
         ·    ·     ·   ·     ·      ·
         ·     ·             ·       ·
          ·      ·         ·        ·
           ·        · · ·          ·
            ·                     ·
              ·                 ·
               ·               ·
                 · · · · · · ·
                  ·         ·
                   ·       ·
""",
    r"""
              · · · · · · · · · · ·          ·                         ·
         ·        ·           ·        ·
        ·       ·               ·       ·
        ·      ·      ·     ·    ·      ·
        ·     ·     ·         ·   ·     ·
        ·      ·      ·     ·    ·      ·
         ·       ·               ·     ·
          ·        ·           ·      ·
           ·          · · ·          ·
            ·                       ·
              ·                   ·
               ·                 ·
                ·               ·
                 ·             ·
                  · ·       · ·
                    · · · · ·
                     ·     ·
                      ·   ·
                       · ·
""",
]


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


def _ghost_lines(level: int) -> list[str]:
    raw = GHOST[max(0, min(level, len(GHOST) - 1))]
    return raw.strip("\n").splitlines()


def _paint(left: list[str], level: int, cursor: bool) -> None:
    ghost = _ghost_lines(level)
    color = (GHOST_DIM, GHOST_DIM, GHOST_MID, GHOST_HOT, GHOST_HOT)[min(level, 4)]
    rows = max(len(left), len(ghost), 16)
    _write("\033[H")
    for i in range(rows):
        line = left[i] if i < len(left) else ""
        art = ghost[i] if i < len(ghost) else ""
        pad = max(1, 42 - len(line))
        shown = line + ("█" if cursor and i == len(left) else "")
        _write("\033[2K" + TEXT + shown + RESET + (" " * pad) + color + art + RESET + "\n")
    _write("\033[J")


def _dots(left: list[str], level: int, stem: str) -> None:
    for n in range(1, 6):
        _paint(left + [stem + "." * n], level, True)
        time.sleep(0.09)


def _line(left: list[str], level: int, stem: str, done: str, pause: float = 0.28) -> list[str]:
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


def _type_title(left: list[str], level: int, title: str) -> list[str]:
    for count in range(1, len(title) + 1):
        _paint(left + [title[:count]], level, True)
        time.sleep(0.028)
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


def _checks(level_step) -> list[str]:
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
        level_step(level)
    return left


def _choose(left: list[str]) -> str:
    frame = left + _menu()
    _paint(frame, 4, False)
    while True:
        key = _read_key()
        if key in {"\x00", "\xe0"}:
            _read_key()
            continue
        if key in {"\x03", "\x1b"}:
            raise KeyboardInterrupt
        if key in {"1", "2", "3", "4", "5"}:
            locked = frame + ["brain locked"]
            _paint(locked, 4, False)
            time.sleep(0.4)
            _paint(locked + ["ready"], 4, False)
            return key


def run_boot(skip_to_menu: bool = False) -> str:
    """Play the boot. Return '1'..'5'. Later setup passes skip_to_menu=True."""
    _enable_windows_ansi()
    _write(HIDE + "\033[2J\033[H")

    def _stop(_signum, _frame):
        _write(SHOW + RESET)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)
    try:
        if skip_to_menu:
            left: list[str] = []
        else:
            left = _checks(lambda _level: None)
        return _choose(left)
    finally:
        _write(SHOW + RESET)


if __name__ == "__main__":
    try:
        print(run_boot(skip_to_menu="--menu" in sys.argv))
    except KeyboardInterrupt:
        raise SystemExit(130)
