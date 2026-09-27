"""The seance: merge duplicate wisps, lay the dead to rest, rewrite haunts from their wisps.

Runs by itself at session end; /seance runs it by hand. The janitorial pass is
fast and silent. The synthesis pass asks the bound brain to distill each haunt
whose wisps changed — never the ones that didn't.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from ghost_desk.skills import Skill, load_all, render_skill, write_verified


def _rank(skill: Skill) -> tuple[int, int]:
    # Prefer a parent file, then the longer body.
    return (0 if skill.is_parent else 1, -len(skill.body))


def _state_path(root: Path) -> Path:
    return Path(root) / ".seance.json"


def _read_state(root: Path) -> dict:
    try:
        return json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(root: Path, state: dict) -> None:
    try:
        path = _state_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        print(f"ghost_desk: seance state not saved: {exc}", file=sys.stderr)


def _wisps_hash(skills: list[Skill], haunt_name: str) -> str:
    """Content hash of a haunt's wisps. Unchanged wisps never get re-synthesized."""
    digest = hashlib.sha256()
    for skill in sorted((s for s in skills if s.parent == haunt_name), key=lambda s: s.name):
        digest.update(skill.name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(skill.body.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def llm_synthesizer(config):
    """Build a haunt-body synthesizer from the bound brain.

    Returns None when no provider is configured — the seance stays silent then.
    The synthesizer takes (haunt, wisps) and returns the new body text, or None.
    """
    provider = (getattr(config, "provider", "") or "").strip()
    if not provider:
        return None

    def synthesize(haunt: Skill, wisps: list[Skill]) -> str | None:
        bodies = []
        for wisp in wisps:
            bodies.append(f"Wisp {wisp.name}: {wisp.description}\n{wisp.body[:1500]}")
        prompt = (
            "You maintain a ghost's skill haunt. Below are its wisps (child skills). "
            "Rewrite the haunt's body as a tight distillation of what the wisps know: "
            "dedupe ideas, keep only what matters, lowercase, plainspoken. "
            "Reply with ONLY the new body text — no frontmatter, no commentary.\n\n"
            f"Haunt: {haunt.name}\nCurrent body:\n{haunt.body[:1500]}\n\n" + "\n\n".join(bodies)
        )
        try:
            from ghost_desk.providers import build_client

            client = build_client(config)
            response = client.chat(
                [{"role": "user", "content": prompt}],
                model=getattr(config, "model", None) or None,
                stream=False,
            )
        except Exception:
            return None  # total silence: no provider, no key, no network, no ceremony
        text = (response.text or "").strip()
        # ~500 tokens per haunt, hard cap.
        return text[:2000] if text else None

    return synthesize


def seance(root: Path, synthesizer=None) -> dict[str, int]:
    """Janitorial pass (always) + synthesis for haunts whose wisps changed."""
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
            # A haunt lives through its wisps: manifesting can drain its body
            # to empty, and that must not kill the haunt (or orphan its wisps).
            has_wisps = keeper.is_parent and any(
                s.parent == name for s in skills if s.name != name
            )
            if not has_wisps:
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
        if write_verified(path, render_skill(skill)):
            skill.path = path
            written += 1

    # Fold stray drawer whispers into their wisps. A whisper whose topic
    # already manifested never belongs in the drawer; this tidies leftovers
    # (a crash between the wisp write and the drawer write, or hand edits).
    from ghost_desk.skills import fold_whispers_into_wisp

    for skill in merged.values():
        if not skill.is_parent:
            fold_whispers_into_wisp(root, skill.name)

    # Synthesis: only haunts whose wisps changed since the last seance.
    state = _read_state(root)
    hashes = state.get("haunt_hashes", {})
    synthesized = 0
    if synthesizer is not None:
        all_skills = list(merged.values())
        for skill in all_skills:
            if not skill.is_parent:
                continue
            wisps = [s for s in all_skills if s.parent == skill.name]
            if not wisps:
                continue
            digest = _wisps_hash(all_skills, skill.name)
            if hashes.get(skill.name) == digest:
                continue
            new_body = synthesizer(skill, wisps)
            if not new_body:
                continue
            skill.body = new_body
            path = root / f"{skill.name}.md"
            if write_verified(path, render_skill(skill)):
                hashes[skill.name] = digest
                synthesized += 1
    state["haunt_hashes"] = hashes
    _write_state(root, state)
    return {
        "written": written,
        "removed": removed,
        "parents": len(parent_names),
        "synthesized": synthesized,
    }
