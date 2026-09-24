"""Turn a tool result into a sentence grounded in that result."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


def _clip(text: str, limit: int = 400) -> str:
    flat = (text or "").replace("\r\n", "\n").strip()
    if not flat:
        return "(empty)"
    flat = " ".join(flat.splitlines())
    if len(flat) > limit:
        return flat[:limit] + "..."
    return flat


@dataclass
class Check:
    action: str
    detail: str
    output: str
    ok: bool
    conclusion: str

    def line(self) -> str:
        return (
            f"I ran {self.action} {self.detail}; "
            f"output {_clip(self.output)}; "
            f"conclude {self.conclusion}"
        )


@dataclass
class VerificationReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    def text(self) -> str:
        if not self.checks:
            return "No tool calls to verify."
        return "\n".join(check.line() for check in self.checks)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def verify_shell(command: str, code: int, stdout: str, stderr: str) -> Check:
    chunks = [stdout or ""]
    if stderr:
        chunks.append(stderr)
    output = "\n".join(chunks)
    if code == 0:
        return Check("shell", command, output, True, "exit 0")
    return Check("shell", command, output, False, f"exit {code}")


def verify_write(path: Path, expected: str) -> Check:
    if not path.is_file():
        return Check(
            "file_write",
            str(path),
            "missing",
            False,
            "file was not on disk after write",
        )
    actual = path.read_text(encoding="utf-8")
    if actual == expected:
        return Check(
            "file_write",
            str(path),
            f"{len(actual)} bytes match",
            True,
            "re-read matches what was written",
        )
    return Check(
        "file_write",
        str(path),
        f"got {len(actual)} bytes",
        False,
        "re-read does not match what was written",
    )


def verify_read(path: Path, content: str, *, truncated: bool = False) -> Check:
    note = f"read {len(content)} chars"
    if truncated:
        note += ", truncated"
    return Check("file_read", str(path), content, True, note)


def repair_near_facts(facts: list[str], workspace: Path) -> None:
    """Replace a one-letter email miss with the exact address from the user."""
    if not facts or not workspace.exists():
        return
    for path in workspace.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".md", ".txt", ".json"}:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        updated = body
        for fact in facts:
            local = fact.split("@", 1)[0]
            if not local or fact in updated or local not in updated:
                continue
            updated = re.sub(re.escape(local) + r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", fact, updated)
        if updated != body:
            path.write_text(updated, encoding="utf-8")


def verify_facts(facts: list[str], workspace: Path) -> list[Check]:
    """Fail when a fact from the conversation is missing or misspelled on disk."""
    if not facts or not workspace.exists():
        return []
    blob_parts: list[str] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".css", ".js", ".md", ".txt", ".json"}:
            continue
        try:
            blob_parts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    blob = "\n".join(blob_parts)
    checks: list[Check] = []
    for fact in facts:
        if fact in blob:
            checks.append(Check("fact", fact, "present", True, "exact value is on disk"))
            continue
        local = fact.split("@", 1)[0]
        near = local and local in blob and fact not in blob
        conclusion = (
            f"FAIL email mismatch: expected {fact}"
            if near
            else f"FAIL {fact} is not on disk"
        )
        checks.append(Check("fact", fact, "missing or misspelled", False, conclusion))
    return checks


def verify_http(url: str, status: int, body: str) -> Check:
    ok = 200 <= status < 400
    return Check("http_fetch", url, body, ok, f"HTTP {status}")
