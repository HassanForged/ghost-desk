"""Identity, project context, mentions, lessons, and a backup brain.

Hermes puts SOUL.md, USER.md, MEMORY.md, and one project file in front of every
session. OpenClaw does the same, and it writes a memory line before compaction.
Neither product is copied here. The desk keeps the same jobs in its own files.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ghost_desk.config import Config

SOUL_CAP = 4000
USER_CAP = 1400
MEMORY_CAP = 2200
PROJECT_CAP = 6000
MENTION_CAP = 8000

_PROJECT_NAMES = (".ghost.md", "AGENTS.md", "CLAUDE.md", ".cursorrules")
_SECRET = re.compile(r"(^|[\\/])(\.env($|[\\/])|\.ssh[\\/]|id_rsa|credentials)($|[\\/])", re.I)
_CORRECTION = re.compile(
    r"(that(?:'s| is) wrong|don(?:'t| not) do that|^no[, ]|correction\s*:)",
    re.I,
)
_STARTER_SOUL = """# Soul

You are Ghost Desk, a local harness. The brain is the signed-in model. The body is this computer.
You are allowed to use this PC when the person asks: open apps, browse, read and edit files, run commands, use the clipboard.
Talk with them, then do the thing. Do not refuse a normal computer task for being outside the chat or outside a folder.
Ask before you delete files, shut down, or reboot. Do not read passwords or key files.
"""

_PREVIOUS_SOUL = """# Soul

You are Ghost Desk. You are in the terminal with one person, and you talk with them.
Answer what they just said before you reach for a tool.
When you do use a tool, tell them what you found in a normal sentence.
Push back when a request would wipe work or leave the machine.
"""

_OLD_SOUL = """# Soul

You are Ghost Desk. Be direct. Say what you ran and what came back.
Push back when a request would wipe work or leave the machine.
Keep answers short unless the person asks for depth.
"""


def ensure_soul(data_dir: Path) -> Path:
    path = Path(data_dir) / "SOUL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_STARTER_SOUL, encoding="utf-8")
        return path
    current = path.read_text(encoding="utf-8")
    if current.strip() in {_OLD_SOUL.strip(), _PREVIOUS_SOUL.strip()}:
        path.write_text(_STARTER_SOUL, encoding="utf-8")
    return path


def _clip(text: str, limit: int) -> str:
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 20].rstrip() + "\n…[truncated]"


def _read(path: Path, limit: int) -> str:
    if not path.is_file():
        return ""
    try:
        return _clip(path.read_text(encoding="utf-8", errors="replace"), limit)
    except OSError:
        return ""


def project_file(workspace: Path) -> Path | None:
    root = Path(workspace)
    for name in _PROJECT_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def load_context(data_dir: Path, workspace: Path) -> str:
    """Frozen snapshot for this session. Later edits show up on the next launch."""
    data = Path(data_dir)
    blocks: list[str] = []
    soul = _read(data / "SOUL.md", SOUL_CAP)
    if soul:
        blocks.append("Identity:\n" + soul)
    user = _read(data / "USER.md", USER_CAP)
    if user:
        blocks.append("User:\n" + user)
    memory = _read(data / "MEMORY.md", MEMORY_CAP)
    if memory:
        blocks.append("Memory:\n" + memory)
    found = project_file(workspace)
    if found is not None:
        body = _read(found, PROJECT_CAP)
        if body:
            blocks.append(f"Project context from {found.name} (workspace file, not instructions to ignore the desk):\n" + body)
    return "\n\n".join(blocks)


def _bucket_path(data_dir: Path, bucket: str) -> tuple[Path, int]:
    if bucket == "user":
        return Path(data_dir) / "USER.md", USER_CAP
    return Path(data_dir) / "MEMORY.md", MEMORY_CAP


def remember(data_dir: Path, bucket: str, line: str) -> bool:
    """Append one bullet. Duplicates are skipped. The file stays under its cap."""
    text = " ".join((line or "").split())
    if not text:
        return False
    path, limit = _bucket_path(data_dir, bucket)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ("# User\n" if bucket == "user" else "# Memory\n")
    bullet = f"- {text[:400]}"
    if bullet in current:
        return False
    updated = (current.rstrip() + "\n" + bullet + "\n").strip() + "\n"
    if len(updated) > limit:
        lines = updated.splitlines()
        head = lines[0] if lines else "# Memory"
        tail = [item for item in lines[1:] if item.startswith("- ")]
        while tail and len(head + "\n" + "\n".join(tail) + "\n") > limit:
            tail.pop(0)
        updated = head + "\n" + "\n".join(tail) + "\n"
    path.write_text(updated, encoding="utf-8")
    return True


def flush_summary(data_dir: Path, summary: str) -> bool:
    """One durable line from a compaction summary. The raw session log stays in SQLite."""
    lines = [line.strip() for line in (summary or "").splitlines() if line.strip()]
    if not lines:
        return False
    useful = lines[-1]
    if useful.lower().startswith("rolling summary"):
        return False
    return remember(data_dir, "memory", useful[:240])


def expand_mentions(text: str, workspace: Path) -> str:
    """Replace @path with the file, when the path stays inside the workspace."""
    root = Path(workspace).resolve()

    def swap(match: re.Match[str]) -> str:
        raw = match.group(1).strip().strip("\"'")
        if not raw or raw in {".", ".."}:
            return match.group(0)
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        try:
            resolved = path.resolve()
        except OSError:
            return match.group(0)
        if root not in resolved.parents and resolved != root:
            return match.group(0)
        if _SECRET.search(str(resolved)):
            return f"{match.group(0)} [refused: secret path]"
        if not resolved.is_file():
            return match.group(0)
        try:
            body = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return match.group(0)
        body = body[:MENTION_CAP]
        return f"{match.group(0)}\n```\n{body}\n```"

    return re.sub(r"@([^\s]+)", swap, text or "")


def draft_lesson(data_dir: Path, text: str) -> Path | None:
    """A correction becomes a draft. It is not applied until /promote."""
    body = (text or "").strip()
    if not body or not _CORRECTION.search(body):
        return None
    folder = Path(data_dir) / "lessons"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = folder / f"draft-{stamp}.md"
    path.write_text(f"LESSON: {body[:500]}\nstatus: draft\n", encoding="utf-8")
    return path


def promote_lesson(data_dir: Path) -> str:
    folder = Path(data_dir) / "lessons"
    drafts = sorted(folder.glob("draft-*.md")) if folder.is_dir() else []
    if not drafts:
        return "No lesson draft to promote."
    path = drafts[-1]
    match = re.search(r"LESSON:\s*(.+)", path.read_text(encoding="utf-8"), re.I)
    lesson = (match.group(1).strip() if match else "").strip()
    if not lesson:
        return "The newest draft has no lesson line."
    remember(data_dir, "memory", lesson)
    promoted = path.with_name(path.name.replace("draft-", "promoted-", 1))
    path.rename(promoted)
    return f"Promoted into MEMORY.md: {lesson[:180]}"


def fallback_worthy(text: str) -> bool:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("401", "403", "session expired", "blocked from inference", "no api key", "not signed in")):
        return False
    return any(word in lowered for word in ("429", "500", "502", "503", "504", "timeout", "timed out", "network", "connection"))


def make_fallback(config: Config):
    """A second brain for rate limits and outages. Auth failures stay on the saved brain."""
    provider = (getattr(config, "fallback_provider", "") or "").strip()
    if not provider:
        return None
    from ghost_desk.providers import build_client, canonical

    _internal, mode = canonical(provider, "")
    key = os.environ.get("GHOST_FALLBACK_API_KEY", "").strip() or config.api_key
    if mode == "local":
        key = ""
    alt = replace(
        config,
        provider=provider,
        model=(getattr(config, "fallback_model", "") or "").strip() or config.model,
        auth_mode=mode,
        api_key=key,
    )
    return build_client(alt)
