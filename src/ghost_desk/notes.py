"""Notes are written only when a tool result was actually checked."""

from __future__ import annotations

from ghost_desk.memory import Memory
from ghost_desk.verify import VerificationReport


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
    evidence = "\n".join(check.line() for check in good)
    body = good[-1].conclusion
    return memory.add_note(session_id, topic or "desk", body, evidence)
