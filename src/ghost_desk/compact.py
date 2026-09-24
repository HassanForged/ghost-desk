"""Rolling summary of old turns. The raw log stays in SQLite. The active tail is never summarized."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

Summarizer = Callable[[list[dict]], str]


def estimate_tokens(messages: list[dict]) -> int:
    chars = 0
    for message in messages:
        chars += len(message.get("content") or "")
        chars += len(str(message.get("tool_calls") or ""))
    return max(1, chars // 4) if chars else 0


def split_old(messages: list[dict], tail_users: int) -> tuple[list[dict], int]:
    """Return the prefix to summarize and the index where the active tail starts."""
    user_at = [index for index, message in enumerate(messages) if message.get("role") == "user"]
    if len(user_at) <= tail_users:
        return [], len(messages)
    cut = user_at[-tail_users]
    if cut <= 0:
        return [], len(messages)
    return messages[:cut], cut


def extractive(messages: list[dict]) -> str:
    lines: list[str] = []
    for message in messages:
        content = (message.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        role = message.get("role", "?")
        lines.append(f"{role}: {content[:180]}")
    return "\n".join(lines[-40:])


class Compactor:
    def __init__(self, window_tokens: int = 32_000, threshold: float = 0.7, tail: int = 6):
        self.window_tokens = window_tokens
        self.threshold = threshold
        self.tail = tail
        self.summary = ""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.compacting = False
        self.started = 0.0
        self._generation = 0
        self._ready = False
        self._cut = 0

    def over_threshold(self, messages: list[dict]) -> bool:
        return estimate_tokens(messages) >= int(self.window_tokens * self.threshold)

    def maybe_start(
        self,
        messages: list[dict],
        summarizer: Summarizer,
        *,
        sync: bool = False,
    ) -> None:
        if self.compacting or not self.over_threshold(messages):
            return
        old, cut = split_old(messages, self.tail)
        if not old:
            return
        with self._lock:
            self._generation += 1
            generation = self._generation
            self.compacting = True
            self.started = time.monotonic()

        def run() -> None:
            try:
                fresh = summarizer(old)
                with self._lock:
                    if generation != self._generation:
                        return
                    combined = (self.summary + "\n" + fresh).strip() if self.summary else fresh
                    if len(combined) > 8000:
                        combined = combined[-8000:]
                    self.summary = combined
                    self._cut = cut
                    self._ready = True
            finally:
                self.compacting = False

        if sync:
            run()
            return
        self._thread = threading.Thread(target=run, name="ghost-compact", daemon=True)
        self._thread.start()

    def apply(self, messages: list[dict]) -> list[dict]:
        """Swap the summary in for the old prefix. The tail stays as it was."""
        with self._lock:
            if not self._ready:
                return messages
            cut = self._cut
            summary = self.summary
            self._ready = False
        if cut <= 0 or cut > len(messages):
            return messages
        head = {
            "role": "system",
            "content": "Rolling summary of earlier turns:\n" + summary,
        }
        return [head, *messages[cut:]]

    def wait_if_needed(self, messages: list[dict], on_status: Callable[[str], None] | None) -> None:
        return
