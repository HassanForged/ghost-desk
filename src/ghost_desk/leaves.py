"""Autumn leaves drifting through the ghost pane.

One leaf every few seconds, a slow fall, a gentle sway. Ambient and sparse:
at most five leaves at once, muted fall tones. Turn it off with
GHOST_DESK_LEAVES=off. Leaves are tiny (<=3x3) pixel sprites, not glyphs.
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass, field

from ghost_desk.face import leaf_fragments, leaf_sprites

# Muted fall tones. Tasteful, not neon.
LEAF_COLORS = ("#6f4f2b", "#5d3823", "#755d2b", "#5d3434")

SPAWN_EVERY = 5.0  # seconds between leaves, ±30%
MAX_LEAVES = 5
FALL_SPEED = (1.1, 1.6)  # rows per second


def leaves_enabled() -> bool:
    return os.environ.get("GHOST_DESK_LEAVES", "").strip().lower() not in {
        "off",
        "0",
        "no",
        "false",
    }


@dataclass
class Leaf:
    x: float
    y: float
    speed: float
    sway: float
    phase: float
    color: str
    pattern: list[list[int]]


@dataclass
class LeafField:
    max_leaves: int = MAX_LEAVES
    spawn_every: float = SPAWN_EVERY
    leaves: list[Leaf] = field(default_factory=list)
    _rng: random.Random = field(default_factory=random.Random)
    _next_spawn: float = 0.0
    _last: float | None = None

    def tick(self, now: float, width: int, height: int) -> bool:
        """Advance the field. Returns True when the screen needs repainting."""
        if self._last is None:
            self._last = now
            self._next_spawn = now + 2.5
        dt = min(max(now - self._last, 0.0), 1.0)
        self._last = now
        if now >= self._next_spawn:
            if len(self.leaves) < self.max_leaves:
                self.leaves.append(self._spawn(width))
            self._next_spawn = now + self.spawn_every * (0.7 + 0.6 * self._rng.random())
        if not self.leaves:
            return False
        for leaf in self.leaves:
            leaf.y += leaf.speed * dt
            leaf.x += math.sin(now * 0.9 + leaf.phase) * leaf.sway * dt
            leaf.x = min(max(leaf.x, 0.0), width - 1)
        self.leaves = [leaf for leaf in self.leaves if leaf.y < height]
        return True

    def _spawn(self, width: int) -> Leaf:
        rng = self._rng
        sprites = leaf_sprites()
        pattern, _ = sprites[rng.randrange(len(sprites))]
        return Leaf(
            x=rng.uniform(1, max(2, width - 1)),
            y=-0.5,
            speed=rng.uniform(*FALL_SPEED),
            sway=rng.uniform(0.4, 1.0),
            phase=rng.uniform(0, math.tau),
            color=rng.choice(LEAF_COLORS),
            pattern=pattern,
        )

    def fragments(self) -> list[tuple[int, int, list[list[tuple[str, str]]]]]:
        """(x, y, fragment_rows) for each leaf: a <=3x3 sprite at (x, y)."""
        out = []
        for leaf in self.leaves:
            if leaf.y < 0:
                continue
            out.append((int(leaf.x), int(leaf.y), leaf_fragments(leaf.pattern, leaf.color)))
        return out
