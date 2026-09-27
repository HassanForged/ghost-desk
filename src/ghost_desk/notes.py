"""Notes are written only when a tool result was actually checked."""

from __future__ import annotations

from ghost_desk.memory import Memory
from ghost_desk.verify import VerificationReport

# Actions worth remembering. Reads and no-op turns are noise; recording them
# every turn fills the notes table and drowns the per-turn retrieval.
_KEEP_ACTIONS = {
    "file_write",
    "file_edit",
    "shell",
    "http_fetch",
    "web_search",
    "little_ghost",
    "fact",
    "fact_repair",
    "checklist",
}


def record_verified(
    memory: Memory,
    session_id: str,
    report: VerificationReport,
    topic: str,
) -> int | None:
    good = [
        check
        for check in report.checks
        if check.ok and check.output and check.output != "refused"
    ]
    if not good:
        return None
    if not any(check.action in _KEEP_ACTIONS for check in good):
        return None
    evidence = "\n".join(check.line() for check in good)
    body = good[-1].conclusion
    return memory.add_note(session_id, topic or "desk", body, evidence)
