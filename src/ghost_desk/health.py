"""Context health: the twenty-block meter under the header.

Lilac below 60%, amber 60-84%, red at 85%+. No label, no percentage —
just the blocks. The first 85% crossing per session appends a nudge;
past 85% the ghost occasionally glances at the meter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

BLOCKS = 20
WARN_AT = 0.85
AMBER_AT = 0.60

LILAC = "#b8a6d9"
AMBER = "#d9a648"
RED = "#d94f4f"

# Model context limits (tokens). GHOST_DESK_CONTEXT_LIMIT overrides.
MODEL_LIMITS = {
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "grok-3": 131_072,
    "grok-2": 131_072,
}
DEFAULT_LIMIT = 128_000

WARNING_LINE = "getting crowded in here \u2014 /new for a fresh haunting."


def context_limit(model: str | None = None) -> int:
    """Token limit: env override wins, then the model table, then default."""
    env = os.environ.get("GHOST_DESK_CONTEXT_LIMIT", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    if model:
        key = model.lower()
        for name, limit in MODEL_LIMITS.items():
            if name in key or key in name:
                return limit
    return DEFAULT_LIMIT


def estimate_tokens(text: str) -> int:
    """Chars/4 fallback when no reported token count is available."""
    return max(1, len(text) // 4)


@dataclass
class ContextHealth:
    """Tracks context usage across turns. Reset on /new and after compaction."""

    used_tokens: int = 0
    model: str | None = None
    warned: bool = False
    _pending_warning: bool = field(default=False, repr=False)

    @property
    def limit(self) -> int:
        return context_limit(self.model)

    @property
    def ratio(self) -> float:
        limit = self.limit
        if limit <= 0:
            return 0.0
        return min(1.0, max(0.0, self.used_tokens / limit))

    def block_colors(self, n: int = BLOCKS) -> list[str]:
        """Block colors for the meter: lilac/amber/red by threshold."""
        ratio = self.ratio
        if ratio >= WARN_AT:
            color = RED
        elif ratio >= AMBER_AT:
            color = AMBER
        else:
            color = LILAC
        filled = int(ratio * n)
        # Filled blocks get the threshold color; the rest stay dim.
        return [color if i < filled else "#2e2133" for i in range(n)]

    def add_turn(self, reported_tokens: int | None = None, text: str = "") -> None:
        """Record a turn's token use: reported count wins, else chars/4."""
        if reported_tokens is not None and reported_tokens >= 0:
            self.used_tokens += reported_tokens
        else:
            self.used_tokens += estimate_tokens(text)
        if not self.warned and self.ratio >= WARN_AT:
            self.warned = True
            self._pending_warning = True

    def take_warning(self) -> str | None:
        """The one-time nudge line, once per session."""
        if self._pending_warning:
            self._pending_warning = False
            return WARNING_LINE
        return None

    def mark_compacted(self, kept_tokens: int) -> None:
        """Compaction shrinks the context: drop to the kept token count."""
        self.used_tokens = max(0, kept_tokens)

    def reset(self) -> None:
        """/new: fresh haunting, fresh meter."""
        self.used_tokens = 0
        self.warned = False
        self._pending_warning = False

    def glance_at_meter(self) -> bool:
        """Past 85%, the ghost occasionally glances at the meter."""
        return self.ratio >= WARN_AT
