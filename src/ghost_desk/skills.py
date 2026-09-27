"""Parent skills load at start. Children stay on disk until something asks for them."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    parent: str
    children: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None

    @property
    def is_parent(self) -> bool:
        return not self.parent


def bundled_skills() -> Path:
    return Path(__file__).resolve().parent / "skills"


def parse_skill(text: str, path: Path | None = None) -> Skill:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---"):
        raise ValueError(f"skill missing frontmatter: {path}")
    end = normalized.find("\n---", 3)
    if end < 0:
        raise ValueError(f"skill frontmatter is not closed: {path}")
    raw = normalized[3:end].strip("\n")
    body = normalized[end + 4 :].strip("\n")
    data: dict[str, object] = {
        "name": "",
        "description": "",
        "parent": "",
        "children": [],
    }
    current: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current == "children":
            children = data["children"]
            assert isinstance(children, list)
            children.append(line.split("-", 1)[1].strip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current = key
        if key == "children":
            if value:
                data["children"] = [part.strip() for part in value.split(",") if part.strip()]
            else:
                data["children"] = []
        elif key == "parent":
            data["parent"] = "" if value in {"", "null", "~", "none"} else value
            current = None
        else:
            data[key] = value
    children = data["children"]
    assert isinstance(children, list)
    return Skill(
        name=str(data["name"]),
        description=str(data["description"]),
        parent=str(data["parent"]),
        children=[str(child) for child in children],
        body=body.strip(),
        path=path,
    )


def render_skill(skill: Skill) -> str:
    lines = [
        "---",
        f"name: {skill.name}",
        f"description: {skill.description}",
        f"parent: {skill.parent}",
        "children:",
    ]
    if skill.children:
        lines.extend(f"  - {child}" for child in skill.children)
    lines.append("---")
    lines.append(skill.body)
    lines.append("")
    return "\n".join(lines)


def load_all(root: Path) -> list[Skill]:
    if not root.exists():
        return []
    skills: list[Skill] = []
    for path in sorted(root.rglob("*.md")):
        try:
            skills.append(parse_skill(path.read_text(encoding="utf-8"), path))
        except (ValueError, OSError) as exc:
            # One malformed skill must not crash startup.
            print(f"ghost_desk: skipping malformed skill {path}: {exc}", file=sys.stderr)
    return skills


def load_parents(root: Path) -> list[Skill]:
    return [skill for skill in load_all(root) if skill.is_parent and skill.name]


def load_child(root: Path, name: str) -> Skill | None:
    for skill in load_all(root):
        if skill.name == name and not skill.is_parent:
            return skill
    return None


def render_index(skills: list[Skill]) -> str:
    """Names only. The full skill stays on disk until someone opens it."""
    lines = []
    for skill in skills:
        kids = f" ({', '.join(skill.children)})" if skill.children else ""
        lines.append(f"- {skill.name}: {skill.description}{kids}")
    return "\n".join(lines)


def render_parents(skills: list[Skill]) -> str:
    blocks = []
    for skill in skills:
        child_line = ""
        if skill.children:
            child_line = "\nChildren: " + ", ".join(skill.children)
        blocks.append(f"## {skill.name}\n{skill.description}{child_line}\n{skill.body}")
    return "\n\n".join(blocks)


_BROAD = {
    "pdf": "documents",
    "email": "documents",
    "notes": "documents",
    "calendar": "documents",
    "git": "code",
    "html": "code",
    "search": "code",
    "sql": "data",
    "csv": "data",
    "zip": "data",
    "charts": "media",
    "images": "media",
}


_WHISPER_RE = re.compile(r"^-\s*(\w+)\s*(?::\s*(.*?))?\s*$")


def whisper_topic(line: str) -> str | None:
    """The topic of a haunt-drawer whisper line ("- pdf: use pdftotext" -> "pdf")."""
    match = _WHISPER_RE.match(line.strip())
    return match.group(1).lower() if match else None


def whisper_note(line: str) -> str:
    """The note carried by a whisper line, without the topic prefix."""
    match = _WHISPER_RE.match(line.strip())
    return (match.group(2) or "").strip() if match else ""


def write_verified(path: Path, content: str) -> bool:
    """Write content, re-read it, and confirm the disk copy hashes identically.

    Nothing counts as written until the disk says so: callers use this so a
    failed write can never appear in an upgrade report.
    """
    want = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        got = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    except OSError as exc:
        print(f"ghost_desk: unverified write {path}: {exc}", file=sys.stderr)
        return False
    if got != want:
        print(f"ghost_desk: unverified write {path}: disk copy differs", file=sys.stderr)
        return False
    return True


def file_whisper(skills_root: Path, topic: str, note: str = "") -> dict:
    """File one whisper under its haunt, or fold it into its wisp when manifested.

    Returns {"haunt": str, "wisp": str | None, "filed": bool, "duplicate": bool}.
    Every write is verified against disk before it counts.
    """
    topic = topic.lower().strip()
    note = note.strip()
    skills_root = Path(skills_root)
    wisp = load_child(skills_root, topic)
    if wisp is not None and wisp.path is not None:
        line = f"- {note}" if note else f"- {topic}"
        if line in wisp.body.splitlines():
            return {"haunt": wisp.parent, "wisp": wisp.name, "filed": False, "duplicate": True}
        body = wisp.body.rstrip()
        if "## Field notes" not in body:
            body += "\n\n## Field notes"
        wisp.body = body + "\n" + line + "\n"
        ok = write_verified(wisp.path, render_skill(wisp))
        return {"haunt": wisp.parent, "wisp": wisp.name, "filed": ok, "duplicate": False}
    haunt = _BROAD.get(topic, "notes")
    skills_root.mkdir(parents=True, exist_ok=True)
    path = skills_root / f"{haunt}.md"
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = (
            "---\n"
            f"name: {haunt}\n"
            f"description: broad haunt for {haunt}\n"
            "parent:\n"
            "children:\n"
            "---\n"
        )
    line = f"- {topic}: {note}" if note else f"- {topic}"
    if line in text.splitlines():
        return {"haunt": haunt, "wisp": None, "filed": False, "duplicate": True}
    ok = write_verified(path, text.rstrip() + "\n" + line + "\n")
    return {"haunt": haunt, "wisp": None, "filed": ok, "duplicate": False}


def manifest_threshold() -> int:
    """Whispers per topic before the topic manifests into a wisp."""
    try:
        return max(1, int(os.environ.get("GHOST_DESK_MANIFEST_THRESHOLD", "3")))
    except ValueError:
        return 3


def _distill_description(topic: str, lines: list[str]) -> str:
    for line in lines:
        note = whisper_note(line)
        if note:
            return note[:120]
    return f"what the ghost has learned about {topic}"


def manifest_wisp(skills_root: Path, haunt_name: str, topic: str, threshold: int | None = None) -> Skill | None:
    """Gather a topic's whispers out of its haunt drawer into a real wisp file.

    Only manifests once the topic reaches `threshold` whispers (env-overridable).
    Moves the whisper lines into "## Field notes", removes them from the haunt
    body, and adds the topic to the haunt's children. All writes are verified
    against disk; returns the new wisp, or None when nothing manifested.
    """
    skills_root = Path(skills_root)
    haunt_path = skills_root / f"{haunt_name}.md"
    if not haunt_path.is_file():
        return None
    try:
        haunt = parse_skill(haunt_path.read_text(encoding="utf-8"), haunt_path)
    except (ValueError, OSError):
        return None
    if not haunt.is_parent:
        return None
    lines = haunt.body.splitlines()
    gathered = [line for line in lines if whisper_topic(line) == topic]
    limit = threshold if threshold is not None else manifest_threshold()
    if len(gathered) < limit:
        return None
    notes: list[str] = []
    for line in gathered:
        note = whisper_note(line)
        entry = f"- {note}" if note else f"- {topic}"
        if entry not in notes:
            notes.append(entry)
    wisp = Skill(
        name=topic,
        description=_distill_description(topic, gathered),
        parent=haunt_name,
        body="## Field notes\n" + "\n".join(notes) + "\n",
    )
    wisp_path = skills_root / haunt_name / f"{topic}.md"
    if not write_verified(wisp_path, render_skill(wisp)):
        return None
    wisp.path = wisp_path
    haunt.body = "\n".join(line for line in lines if whisper_topic(line) != topic).strip()
    if topic not in haunt.children:
        haunt.children.append(topic)
    # Best effort: the wisp is already safe on disk, and the next seance
    # relists children from what's actually there.
    write_verified(haunt_path, render_skill(haunt))
    return wisp


def fold_whispers_into_wisp(skills_root: Path, topic: str) -> bool:
    """Move a topic's haunt-drawer whispers into its already-manifested wisp.

    Returns True when anything moved. Verified writes; idempotent.
    """
    skills_root = Path(skills_root)
    wisp = load_child(skills_root, topic)
    if wisp is None or wisp.path is None:
        return False
    haunt_path = skills_root / f"{wisp.parent}.md"
    if not haunt_path.is_file():
        return False
    try:
        haunt = parse_skill(haunt_path.read_text(encoding="utf-8"), haunt_path)
    except (ValueError, OSError):
        return False
    lines = haunt.body.splitlines()
    gathered = [line for line in lines if whisper_topic(line) == topic]
    if not gathered:
        return False
    existing = set(wisp.body.splitlines())
    additions: list[str] = []
    for line in gathered:
        note = whisper_note(line)
        entry = f"- {note}" if note else f"- {topic}"
        if entry not in existing and entry not in additions:
            additions.append(entry)
    moved = False
    if additions:
        body = wisp.body.rstrip()
        if "## Field notes" not in body:
            body += "\n\n## Field notes"
        wisp.body = body + "\n" + "\n".join(additions) + "\n"
        if not write_verified(wisp.path, render_skill(wisp)):
            return False
        moved = True
    haunt.body = "\n".join(line for line in lines if whisper_topic(line) != topic).strip()
    if not write_verified(haunt_path, render_skill(haunt)):
        return moved
    return True


def manifest_check(skills_root: Path, threshold: int | None = None) -> None:
    """Topics with enough whispers manifest into wisps of their own.

    Idempotent: a second run finds no gathered whispers and changes nothing.
    """
    skills_root = Path(skills_root)
    if not skills_root.is_dir():
        return
    limit = threshold if threshold is not None else manifest_threshold()
    for haunt_path in sorted(skills_root.glob("*.md")):
        try:
            haunt = parse_skill(haunt_path.read_text(encoding="utf-8"), haunt_path)
        except (ValueError, OSError):
            continue
        if not haunt.is_parent or not haunt.name:
            continue
        counts: dict[str, int] = {}
        for line in haunt.body.splitlines():
            topic = whisper_topic(line)
            if topic:
                counts[topic] = counts.get(topic, 0) + 1
        for topic in sorted(counts):
            if counts[topic] < limit:
                continue
            if load_child(skills_root, topic) is not None:
                fold_whispers_into_wisp(skills_root, topic)
            else:
                manifest_wisp(skills_root, haunt.name, topic)


def ensure_skills(data_dir: Path) -> Path:
    dest = Path(data_dir) / "skills"
    if not any(dest.rglob("*.md")):
        shutil.copytree(bundled_skills(), dest, dirs_exist_ok=True)
    return dest
