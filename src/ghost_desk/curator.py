"""Off-session skill cleanup: merge duplicates, drop dead files, rewrite parent lists."""

from __future__ import annotations

from pathlib import Path

from ghost_desk.skills import Skill, load_all, render_skill


def _rank(skill: Skill) -> tuple[int, int]:
    # Prefer a parent file, then the longer body.
    return (0 if skill.is_parent else 1, -len(skill.body))


def curate(root: Path) -> dict[str, int]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    skills = [skill for skill in load_all(root) if skill.name]
    grouped: dict[str, list[Skill]] = {}
    for skill in skills:
        grouped.setdefault(skill.name, []).append(skill)

    merged: dict[str, Skill] = {}
    removed = 0
    for name, group in grouped.items():
        group.sort(key=_rank)
        keeper = group[0]
        children: list[str] = []
        bodies = [keeper.body]
        for other in group[1:]:
            for child in other.children:
                if child not in children and child not in keeper.children:
                    children.append(child)
            if other.body and other.body not in bodies:
                bodies.append(other.body)
            if other.path and other.path != keeper.path and other.path.exists():
                other.path.unlink()
                removed += 1
        keeper.children = list(dict.fromkeys([*keeper.children, *children]))
        if len(bodies) > 1:
            keeper.body = "\n\n".join(body for body in bodies if body)
        if not keeper.body.strip():
            if keeper.path and keeper.path.exists():
                keeper.path.unlink()
                removed += 1
            continue
        merged[name] = keeper

    parent_names = {name for name, skill in merged.items() if skill.is_parent}
    for name, skill in list(merged.items()):
        if skill.parent and skill.parent not in parent_names:
            if skill.path and skill.path.exists():
                skill.path.unlink()
                removed += 1
            del merged[name]

    for skill in merged.values():
        if skill.is_parent:
            skill.children = sorted(
                child.name for child in merged.values() if child.parent == skill.name
            )

    written = 0
    for skill in merged.values():
        if skill.is_parent:
            path = root / f"{skill.name}.md"
        else:
            folder = root / skill.parent
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{skill.name}.md"
        if skill.path and skill.path.resolve() != path.resolve() and skill.path.exists():
            skill.path.unlink()
        path.write_text(render_skill(skill), encoding="utf-8")
        written += 1
    return {"written": written, "removed": removed, "parents": len(parent_names)}
