"""Plan before building. Show the plan, then wait."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ghost_desk.verify import Check

_BUILD = re.compile(
    r"(?i)\b(build|create|implement|scaffold|develop)\b|\bmake\s+(me\s+)?(a|an|the)\b"
)
_BULLET = re.compile(r"(?m)^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$")
_APPROVE = re.compile(
    r"(?i)^\s*(approve|approved|go ahead|go|yes|yep|lgtm|ship it|do it)\s*[.!]?\s*$"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_REJECT = re.compile(r"(?i)^\s*(stop|reject|cancel)\s*[.!]?\s*$")
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".ghost-desk"}
_SKIP_WORDS = {
    "build",
    "make",
    "with",
    "this",
    "that",
    "from",
    "your",
    "have",
    "will",
    "into",
    "about",
    "page",
}


@dataclass
class PlanDraft:
    request: str
    checklist: list[str]
    questions: list[str]
    needs_reference: bool
    reference_prompt: str
    steps: list[str]
    facts: list[str] = field(default_factory=list)
    status: str = "awaiting_approval"

    def render(self) -> str:
        lines = [
            "Plan (waiting for approval). I will not write files until you approve.",
            "",
            "Checklist:",
        ]
        lines.extend(f"- {item}" for item in self.checklist)
        lines.append("")
        lines.append("Steps:")
        lines.extend(f"{index}. {step}" for index, step in enumerate(self.steps, 1))
        if self.questions:
            lines.append("")
            lines.append("Questions:")
            lines.extend(f"- {question}" for question in self.questions)
        if self.needs_reference and self.reference_prompt:
            lines.append("")
            lines.append(self.reference_prompt)
        lines.append("")
        lines.append("Reply go, yes, or approve to start. Reply reject to drop the plan.")
        return "\n".join(lines)


def is_build_request(text: str) -> bool:
    if re.search(r"(?i)\b(do not|don't|not)\s+build\b", text or ""):
        return False
    return bool(_BUILD.search(text or ""))


def needs_a_reference_first(text: str) -> bool:
    return bool(re.search(r"(?i)feel right|can(?:not|'t) explain", text or ""))


def extract_checklist(text: str) -> list[str]:
    items = [match.group(1).strip() for match in _BULLET.finditer(text or "")]
    items = [item for item in items if item]
    if items:
        return items
    for line in (text or "").splitlines():
        if line.strip():
            return [line.strip()]
    stripped = (text or "").strip()
    return [stripped] if stripped else []


def needs_reference(text: str, checklist: list[str]) -> bool:
    if re.search(r"https?://|www\.", text or "", re.I):
        return False
    if len(checklist) >= 2:
        return False
    words = re.findall(r"[A-Za-z0-9_'-]+", text or "")
    return len(words) < 14


def questions_for(vague: bool) -> list[str]:
    questions = [
        "What is in scope, and what should this not do?",
        "What stack or folder should it live in?",
        "What does success look like on day one?",
    ]
    if vague:
        questions.append(
            "If you cannot put it into words, show a reference: a URL, a file, or 'like X but Y'."
        )
    return questions


def facts_in(text: str) -> list[str]:
    return list(dict.fromkeys(_EMAIL.findall(text or "")))


def build_plan(text: str, prior: str = "") -> PlanDraft:
    full = (prior.strip() + "\n" + text.strip()).strip()
    checklist = extract_checklist(full)
    vague = needs_reference(text, checklist)
    return PlanDraft(
        request=full,
        checklist=checklist,
        questions=questions_for(vague),
        needs_reference=vague,
        facts=facts_in(full),
        reference_prompt=(
            "If you can't put it into words, show me a reference: a URL, a screenshot path, or 'like X but Y'."
            if vague
            else ""
        ),
        steps=[
            "Confirm the checklist and any reference.",
            "Sketch the files that satisfy each checklist item.",
            "Write those files inside the workspace.",
            "Re-read each written file and match it to the checklist.",
        ],
    )


def is_approval(text: str) -> bool:
    return bool(_APPROVE.match((text or "").strip()))


def is_rejection(text: str) -> bool:
    return bool(_REJECT.match((text or "").strip()))


@dataclass
class PlanSession:
    draft: PlanDraft | None = None

    def handle(self, text: str, prior: str = "") -> str | None:
        """Return a hold message, or None when the agent may run."""
        if self.draft and self.draft.status == "awaiting_approval":
            if is_approval(text):
                self.draft.status = "approved"
                return None
            if is_rejection(text):
                self.draft = None
                return "Plan stopped. Tell me what to change."
            self.draft = build_plan(text, prior=self.draft.request)
            return self.draft.render()
        if needs_a_reference_first(text):
            self.draft = build_plan(text, prior=prior)
            self.draft.needs_reference = True
            self.draft.reference_prompt = (
                "If you can't put it into words, show me a reference: a URL, a file, or 'like X but Y'."
            )
            self.draft.questions = questions_for(True)
            return self.draft.render()
        if is_build_request(text):
            self.draft = build_plan(text, prior=prior)
            return self.draft.render()
        return None


def _walk_texts(workspace: Path, limit: int = 2000) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    if not workspace.exists():
        return found
    for path in workspace.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".zip", ".pyc", ".exe", ".dll"}:
            continue
        try:
            found.append((path, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
        if len(found) >= limit:
            break
    return found


def _evidence_for(item: str, texts: list[tuple[Path, str]]) -> str:
    low = item.lower()
    if any(word in low for word in ("website", "web page", "webpage", "homepage", "landing")):
        for path, _body in texts:
            if path.suffix.lower() in {".html", ".css", ".js"} or path.name.lower() == "index.html":
                return str(path)
    words = [
        word
        for word in re.findall(r"[a-z0-9]+", low)
        if len(word) > 3 and word not in _SKIP_WORDS
    ]
    if not words:
        return ""
    wanted = words[:4]
    for path, body in texts:
        hay = (path.name + "\n" + body).lower()
        hits = sum(word in hay for word in wanted)
        if hits >= max(1, len(wanted) - 1):
            return str(path)
    return ""


def verify_checklist(checklist: list[str], workspace: Path) -> list[Check]:
    texts = _walk_texts(workspace)
    checks: list[Check] = []
    for item in checklist:
        evidence = _evidence_for(item, texts)
        if evidence:
            checks.append(Check("checklist", item, evidence, True, "found on disk"))
        else:
            checks.append(Check("checklist", item, "no matching file", False, "not found on disk"))
    return checks
