"""Boot intro state: black beat, materialize, wordmark, tagline, UI.

Fresh boot only — never on /new. Any key skips immediately.
GHOST_DESK_INTRO=off disables it entirely.
"""

from __future__ import annotations

import os
import select
import sys
import time
from dataclasses import dataclass
from typing import Callable

# Roughly 2.5s total at the 0.5s tick: beat, materialize, wordmark, tagline.
BEAT_TICKS = 1
MATERIALIZE_TICKS = 3
WORDMARK_TICKS = 2
TAGLINE_TICKS = 2

# Approved timing: materialize ~800ms, wordmark ~600ms, tagline ~400ms.
_BEAT_S = 0.30
_MATERIALIZE_S = 0.80
_WORDMARK_S = 0.60
_TAGLINE_S = 0.40
_HOLD_S = 0.45

_WORDMARK = "GHOST DESK"
_TAGLINE = "one ghost, your machine, your notes."


def intro_enabled() -> bool:
    return os.environ.get("GHOST_DESK_INTRO", "").strip().lower() not in {
        "off",
        "0",
        "no",
        "false",
    }


@dataclass
class IntroState:
    """Fresh-boot intro progress. Drive with tick(); skipped becomes done."""

    tick_count: int = 0
    skipped: bool = False
    done: bool = False

    @property
    def total_ticks(self) -> int:
        return BEAT_TICKS + MATERIALIZE_TICKS + WORDMARK_TICKS + TAGLINE_TICKS

    def tick(self) -> None:
        if self.done or self.skipped:
            self.done = True
            return
        self.tick_count += 1
        if self.tick_count >= self.total_ticks:
            self.done = True

    def skip(self) -> None:
        """Any key: jump straight to the UI."""
        self.skipped = True
        self.done = True

    @property
    def phase(self) -> str:
        """beat | materialize | wordmark | tagline | ui."""
        if self.done:
            return "ui"
        t = self.tick_count
        if t < BEAT_TICKS:
            return "beat"
        t -= BEAT_TICKS
        if t < MATERIALIZE_TICKS:
            return "materialize"
        t -= MATERIALIZE_TICKS
        if t < WORDMARK_TICKS:
            return "wordmark"
        return "tagline"

    @property
    def materialize_frac(self) -> float:
        """0..1 progress through the materialize phase."""
        t = self.tick_count - BEAT_TICKS
        if t <= 0:
            return 0.0
        return min(1.0, t / MATERIALIZE_TICKS)


def _ansi_for(style: str) -> str:
    """prompt_toolkit 'fg:#rrggbb' / 'bg:#rrggbb' -> ANSI 24-bit codes."""
    codes = []
    for part in style.split():
        if part.startswith("fg:#") and len(part) == 10:
            r, g, b = int(part[4:6], 16), int(part[6:8], 16), int(part[8:10], 16)
            codes.append(f"38;2;{r};{g};{b}")
        elif part.startswith("bg:#") and len(part) == 10:
            r, g, b = int(part[4:6], 16), int(part[6:8], 16), int(part[8:10], 16)
            codes.append(f"48;2;{r};{g};{b}")
    return f"\033[{';'.join(codes)}m" if codes else ""


def _render_frame(
    lines: list[list[tuple[str, str]]], write: Callable[[str], None]
) -> None:
    """Paint fragment rows centered on a cleared screen."""
    write("\033[2J\033[H")
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80
    for row in lines:
        text = "".join(
            (_ansi_for(style) + ch if style else ch) for style, ch in row
        )
        # Strip ANSI to measure visible width for centering.
        visible = "".join(ch for _, ch in row)
        pad = max(0, (width - len(visible)) // 2)
        write(" " * pad + text + "\033[0m\n")


def _key_pressed() -> bool:
    """Non-blocking stdin check: True if a key is waiting."""
    try:
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (OSError, ValueError):
        return False


def play_intro(
    render: Callable[[list[list[tuple[str, str]]]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    key_pressed: Callable[[], bool] = _key_pressed,
    write: Callable[[str], None] | None = None,
) -> None:
    """Play the fresh-boot intro. Any key skips to the UI immediately.

    Sequence: black beat -> ghost materializes (~800ms) -> GHOST DESK
    types in the 3x5 pixel font (~600ms) -> tagline fades in dim (~400ms)
    -> brief hold -> return. GHOST_DESK_INTRO=off disables entirely.
    """
    if not intro_enabled():
        return
    from ghost_desk.face import (
        dither_shade,
        fragments_from_grid,
        frame_grid,
        materialize_steps,
        pixel_text,
    )

    _write = write or (lambda s: sys.stdout.write(s) or sys.stdout.flush())
    paint = render or (lambda lines: _render_frame(lines, _write))
    state = IntroState()

    def skipped() -> bool:
        if key_pressed():
            state.skip()
            return True
        return False

    # Beat: black.
    paint([])
    state.tick()
    sleep(_BEAT_S)
    if skipped():
        return

    # Materialize: sparse static resolving into the sprite.
    ghost = dither_shade(frame_grid("neutral", width=22), seed=7)
    steps = materialize_steps(ghost, steps=12)
    per_step = _MATERIALIZE_S / len(steps)
    for step in steps:
        paint(fragments_from_grid(step))
        state.tick()
        sleep(per_step)
        if skipped():
            return

    # Wordmark: GHOST DESK types out in the pixel font.
    full = pixel_text(_WORDMARK)
    per_char = _WORDMARK_S / max(1, len(_WORDMARK))
    for n in range(1, len(_WORDMARK) + 1):
        paint(fragments_from_grid(ghost) + [[]] + fragments_from_grid(pixel_text(_WORDMARK[:n])))
        state.tick()
        sleep(per_char)
        if skipped():
            return

    # Tagline: fades in dim, then a brief hold on the full lockup.
    tagline = [[("fg:#6e5a6e", _TAGLINE)]]
    paint(fragments_from_grid(ghost) + [[]] + fragments_from_grid(full) + [[]] + tagline)
    state.tick()
    sleep(_TAGLINE_S)
    if skipped():
        return
    sleep(_HOLD_S)
    state.tick()
