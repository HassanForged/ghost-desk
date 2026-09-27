"""Ceremony visuals: the upgrade sequence, pixel edition.

Renders haunt-upgrade phases (the ``{kind, text}`` dicts from
``ceremony.phases()``) as a full-screen pixel sequence:

- announce: the ghost shimmers/pulses in, "time for your upgrade."
- count: "whispers gathered: N" ticks up over ~250ms
- wisp: "+ wisp: <name>" materializes pixel-by-pixel
- deepen: "<haunt> — rewritten from its wisps."
- close: "the haunting deepens." with restrained lilac/amber confetti

Any key skips to the close. GHOST_DESK_CEREMONY=off disables entirely.
Test against a stubbed phase list; never import the sibling worktree.
The parent wires this as ``render=`` into ``drain_ceremony`` after merge.
"""

from __future__ import annotations

import os
import select
import sys
import time
from typing import Callable

_ANNOUNCE_S = 0.60
_COUNT_S = 0.25
_WISP_S = 0.50
_DEEPEN_S = 0.35
_CLOSE_S = 1.20


def ceremony_enabled() -> bool:
    return os.environ.get("GHOST_DESK_CEREMONY", "").strip().lower() not in {
        "off",
        "0",
        "no",
        "false",
    }


def _key_pressed() -> bool:
    try:
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (OSError, ValueError):
        return False


def play_ceremony(
    phases: list[dict],
    render: Callable[[list[list[tuple[str, str]]]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    key_pressed: Callable[[], bool] = _key_pressed,
    write: Callable[[str], None] | None = None,
) -> None:
    """Play the ceremony phase list. Any key skips to the close."""
    if not ceremony_enabled():
        return
    if not phases:
        return
    from ghost_desk.face import (
        confetti_fragments,
        dither_shade,
        fragments_from_grid,
        frame_grid,
        materialize_steps,
        pixel_text,
    )
    from ghost_desk.intro import _render_frame

    _write = write or (lambda s: sys.stdout.write(s) or sys.stdout.flush())
    paint = render or (lambda lines: _render_frame(lines, _write))

    ghost = dither_shade(frame_grid("neutral", width=22), seed=7)
    ghost_frags = fragments_from_grid(ghost)
    shown_lines: list[list[tuple[str, str]]] = []

    def skipped() -> bool:
        return key_pressed()

    def _paint() -> None:
        paint(ghost_frags + [[]] + shown_lines)

    # Announce: ghost shimmers in (quick materialize pulse).
    for step in materialize_steps(ghost, steps=6):
        paint(fragments_from_grid(step))
        sleep(_ANNOUNCE_S / 6)
        if skipped():
            break
    for phase in phases:
        kind = phase.get("kind")
        text = phase.get("text", "")
        if skipped():
            break
        if kind == "announce":
            shown_lines.append([("", text)])
            _paint()
            sleep(_ANNOUNCE_S)
        elif kind == "count":
            # Tick the number up over ~250ms.
            import re
            m = re.search(r"(\d+)", text)
            if m:
                total = int(m.group(1))
                prefix = text[: m.start()]
                ticks = max(1, min(total, 8))
                for i in range(1, ticks + 1):
                    n = total * i // ticks
                    shown_lines.append([("", f"{prefix}{n}")])
                    _paint()
                    shown_lines.pop()
                    sleep(_COUNT_S / ticks)
            shown_lines.append([("", text)])
            _paint()
        elif kind == "wisp":
            # "+ wisp: <name>" materializes pixel-by-pixel.
            name = text.partition(":")[2].strip()
            label = text.partition(":")[0] + ": "
            name_grid = pixel_text(name.upper())
            for step in materialize_steps(name_grid, steps=8):
                # Inline: label + first row of the materializing name.
                flat: list[tuple[str, str]] = [("", label)]
                step_frags = fragments_from_grid(step)
                if step_frags:
                    flat.extend(step_frags[0])
                shown_lines.append(flat)
                _paint()
                shown_lines.pop()
                sleep(_WISP_S / 8)
                if skipped():
                    break
            shown_lines.append([("", text)])
            _paint()
        elif kind == "deepen":
            shown_lines.append([("", text)])
            _paint()
            sleep(_DEEPEN_S)
        elif kind == "close":
            shown_lines.append([("", text)])
            # Restrained confetti: lilac/amber dots around the ghost.
            conf = confetti_fragments(seed=7, width=22, height=10)
            base = [list(r) for r in ghost_frags]
            for ox, oy, frag_rows in conf:
                for dy, frow in enumerate(frag_rows):
                    y = oy + dy
                    if 0 <= y < len(base):
                        for dx, cell in enumerate(frow):
                            x = ox + dx
                            if 0 <= x < len(base[y]) and base[y][x][1].strip() == "":
                                base[y][x] = cell
            paint(base + [[]] + shown_lines)
            sleep(_CLOSE_S)
    # Leave the final lockup on screen briefly; the caller clears.
    return
