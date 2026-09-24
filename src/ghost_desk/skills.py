"""Parent skills load at start. Children stay on disk until something asks for them."""

from __future__ import annotations

import shutil
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
        skills.append(parse_skill(path.read_text(encoding="utf-8"), path))
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


def file_learned_trick(skills_root: Path, trick: str) -> None:
    """File one trick under a broad parent. Do not open a new top-level skill per trick."""
    broad = _BROAD.get(trick, "notes")
    skills_root.mkdir(parents=True, exist_ok=True)
    path = skills_root / f"{broad}.md"
    if path.is_file():
        body = path.read_text(encoding="utf-8")
    else:
        body = (
            "---\n"
            f"name: {broad}\n"
            f"description: broad skill for {broad}\n"
            "parent:\n"
            "children:\n"
            "---\n"
        )
    line = f"- {trick}"
    if line not in body:
        body = body.rstrip() + "\n" + line + "\n"
        path.write_text(body, encoding="utf-8")


def ensure_skills(data_dir: Path) -> Path:
    dest = Path(data_dir) / "skills"
    if not any(dest.rglob("*.md")):
        shutil.copytree(bundled_skills(), dest, dirs_exist_ok=True)
    return dest
