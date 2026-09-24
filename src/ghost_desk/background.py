"""User-declared jobs, plus a monthly digest. Repeated topics are listed, not acted on."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from ghost_desk.memory import Memory

_EMOTION = {
    "stuck",
    "frustrated",
    "excited",
    "worried",
    "love",
    "hate",
    "annoyed",
    "happy",
    "blocked",
    "anxious",
}
_ACTION = {"build", "fix", "ship", "write", "call", "email", "deploy", "finish", "debug", "create"}
_STOP = {
    "about",
    "there",
    "their",
    "which",
    "would",
    "could",
    "should",
    "ghost",
    "desk",
    "this",
    "that",
    "with",
    "from",
    "have",
    "will",
    "your",
    "just",
    "into",
    "they",
    "them",
    "been",
    "were",
    "what",
    "when",
    "where",
    "need",
    "want",
}

_ALIASES = {
    "hourly": "0 * * * *",
    "daily": "0 0 * * *",
    "weekly": "0 0 * * 0",
    "monthly": "0 0 1 * *",
}


def parse_schedule(expr: str) -> str:
    text = (expr or "").strip().lower()
    return _ALIASES.get(text, (expr or "").strip())


def _field_ok(field: str, value: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0
    return int(field) == value


def cron_matches(expr: str, now: datetime) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        return False
    cron_dow = (now.weekday() + 1) % 7
    values = [now.minute, now.hour, now.day, now.month, cron_dow]
    try:
        return all(_field_ok(field, value) for field, value in zip(fields, values, strict=True))
    except ValueError:
        return False


def minute_key(now: datetime) -> str:
    return now.replace(second=0, microsecond=0).isoformat()


def observe_message(memory: Memory, text: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text or "")
    lowered = [word.lower() for word in words]
    emotion = next((word for word in lowered if word in _EMOTION), "")
    action = next((word for word in lowered if word in _ACTION), "")
    keywords = []
    for word in lowered:
        if word in _STOP or word in _EMOTION or word in _ACTION:
            continue
        keywords.append(word)
    stamp = now.replace(microsecond=0).isoformat()
    for word in sorted(set(keywords)):
        memory.bump_topic(word, emotion, action, text[:180], stamp)
    memory.flag_recurring(now)
    from ghost_desk.context import remember
    from ghost_desk.plan import facts_in

    for email in facts_in(text):
        remember(memory.data_dir, "memory", f"email: {email}")
    named = re.search(r"(?i)the name is\s+(.+?)(?:\.|,|$)", text or "")
    if named:
        remember(memory.data_dir, "memory", "name: " + named.group(1).strip())


def digest_due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    latest = memory.latest_digest()
    if latest is None:
        return True
    stamp = datetime.fromisoformat(latest["ts"])
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return now - stamp >= timedelta(days=30)


def build_digest(memory: Memory, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        f"# Ghost Desk digest {now.date().isoformat()}",
        "",
        "Review only. Nothing here was acted on.",
        "",
        "## Recurring candidates",
    ]
    memory_file = memory.data_dir / "MEMORY.md"
    if memory_file.is_file():
        for line in memory_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("- ") and not line.lower().startswith("- yeah"):
                lines.append(line)
    topics = [topic for topic in memory.recurring_topics() if topic["name"].lower() not in {"yeah", "maybe"}]
    if not topics:
        lines.append("None.")
    for topic in topics:
        why = topic["action"] or topic["emotion"] or "repeated"
        lines.append(
            f"- {topic['name']} ({topic['mentions']} mentions, {why}): {topic['snippet']}"
        )
    lines.extend(["", "## Research"])
    research = memory.recent_research(limit=10)
    if not research:
        lines.append("None.")
    for item in research:
        lines.append(
            f"- {item['topic']} | source {item['source']} | {item['confidence']} | {item['ts']}"
        )
    lines.extend(["", "## Jobs you created"])
    jobs = memory.list_jobs()
    if not jobs:
        lines.append("None.")
    for job in jobs:
        lines.append(f"- {job['name']} `{job['schedule']}` {job['prompt']}")
    lines.append("")
    return "\n".join(lines)


def write_digest(memory: Memory, now: datetime | None = None) -> str:
    body = build_digest(memory, now)
    memory.add_digest(body)
    path = memory.data_dir / "digests"
    path.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    (path / f"{stamp}.md").write_text(body, encoding="utf-8")
    return body


_LOCKED = re.compile(
    r"(?i)\b(broker|live order|production deploy|kubectl apply|terraform apply)\b"
)


def draft_gaps(roots: list) -> str:
    """Draft next moves. Never a command that spends money or deploys."""
    lines = ["# next moves", "", "Draft only. Nothing here was run.", ""]
    for raw in roots:
        from pathlib import Path

        root = Path(raw)
        if not root.is_dir():
            continue
        hint = "read the project and propose one next move"
        if _LOCKED.search(root.name):
            hint = "locked: do not trade or deploy"
        lines.append(f"- {root.name}: {hint}")
    if len(lines) == 4:
        lines.append("No project folders were visible.")
    lines.append("")
    return "\n".join(lines)


def run_due(
    memory: Memory,
    runner: Callable[[str], str],
    now: datetime | None = None,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    ran: list[str] = []
    current = minute_key(now)
    for job in memory.list_jobs():
        if not job["enabled"]:
            continue
        if not cron_matches(job["schedule"], now):
            continue
        if job["last_run"] and job["last_run"][:16] == current[:16]:
            continue
        result = runner(job["prompt"])
        memory.mark_job(job["name"], current)
        memory.add_research(
            topic=job["name"],
            source=f"bg:{job['name']}",
            confidence="job",
            body=(result or "")[:1000],
        )
        ran.append(job["name"])
    return ran


class BackgroundDesk:
    def __init__(self, memory: Memory, runner: Callable[[str], str], interval: float = 30.0):
        self.memory = memory
        self.runner = runner
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ghost-bg", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                run_due(self.memory, self.runner, datetime.now())
            except Exception:
                continue
