"""The ghost's little life: idle bob, blink, glances, sleep, flinch, bounce.

A tiny deterministic state machine. The TUI feeds it events (ticks,
tool activity, typing, errors) and asks which sprite frame to draw.
No I/O, no threads — the existing 0.5s animation loop drives it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# Blink about every 4s; the TUI ticks every 0.5s.
BLINK_EVERY_TICKS = 8
# An idle glance every ~25s.
GLANCE_EVERY_TICKS = 50
# Sleep after 60s of no activity.
SLEEP_AFTER_TICKS = 120
# Error flinch lasts 2 ticks (~1s); success bounce lasts 4 ticks (~2s).
FLINCH_TICKS = 2
BOUNCE_TICKS = 4


@dataclass
class GhostLife:
    """Which frame the ghost should wear, and tiny one-shot animations."""

    _rng: random.Random = field(default_factory=lambda: random.Random(0x6A05))
    tick_count: int = 0
    idle_ticks: int = 0
    busy: bool = False
    typing: bool = False
    sleeping: bool = False
    flinch_left: int = 0
    bounce_left: int = 0
    did_tool_work: bool = False
    _next_blink: int = BLINK_EVERY_TICKS
    _next_glance: int = GLANCE_EVERY_TICKS
    _glance_until: int = 0
    _glance_frame: str = "neutral"
    _blink_tick: int = 0

    def tick(self) -> None:
        """Advance one 0.5s animation tick."""
        self.tick_count += 1
        self.idle_ticks += 1
        if self.flinch_left:
            self.flinch_left -= 1
        if self.bounce_left:
            self.bounce_left -= 1
        self._blink_tick = 0
        if self.tick_count >= self._next_blink:
            self._blink_tick = self.tick_count
            self._next_blink = self.tick_count + BLINK_EVERY_TICKS + self._rng.randrange(4)
        if self.tick_count >= self._next_glance:
            self._glance_frame = self._rng.choice(("look_left", "look_right"))
            self._glance_until = self.tick_count + 2
            self._next_glance = self.tick_count + GLANCE_EVERY_TICKS + self._rng.randrange(20)
        if not self.sleeping and self.idle_ticks >= SLEEP_AFTER_TICKS and not self.busy:
            self.sleeping = True

    def mark_active(self) -> None:
        """Any user or tool activity: reset the idle clock, wake up."""
        woke = self.sleeping
        self.idle_ticks = 0
        self.sleeping = False
        if woke:
            # Wake/startle: a quick blink on the next frame.
            self._blink_tick = self.tick_count + 1

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.mark_active()

    def set_typing(self, typing: bool) -> None:
        self.typing = typing
        if typing:
            self.mark_active()

    def mark_tool_work(self) -> None:
        """A tool actually ran: qualifies for the success bounce."""
        self.did_tool_work = True
        self.mark_active()

    def mark_error(self) -> None:
        """Something moved in the dark: brief flinch."""
        self.flinch_left = FLINCH_TICKS
        self.mark_active()

    def mark_success(self) -> None:
        """Bounce, but only if tool work happened first."""
        if self.did_tool_work:
            self.bounce_left = BOUNCE_TICKS
        self.did_tool_work = False

    def frame(self) -> str:
        """The sprite frame name to draw this tick."""
        if self.sleeping:
            return "sleep"
        if self.typing:
            return "look_down"
        if self.busy:
            # Busy eye scan: sweep left/right with the tick.
            return "look_left" if (self.tick_count // 2) % 2 == 0 else "look_right"
        if self.tick_count == self._blink_tick:
            return "blink"
        if self.tick_count <= self._glance_until:
            return self._glance_frame
        return "neutral"

    def flinch_dx(self) -> int:
        """Error flinch: shift ±1 cell while flinching."""
        if self.flinch_left:
            return 1 if (self.flinch_left % 2) else -1
        return 0

    def bouncing(self) -> bool:
        return self.bounce_left > 0
