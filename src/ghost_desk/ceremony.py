"""The deepening, made visible: verify-then-announce upgrade reports and the ceremony.

The pipeline is strictly ordered: mutate -> verify -> report -> celebrate.

- Mutations (whisper filing, manifesting, seance writes) go through
  write_verified: nothing counts until the disk copy hashes identically.
- collect_upgrades() builds the report ONLY from on-disk diffs (snapshots
  before/after) — never from in-memory claims about what should have happened.
- A failed write simply leaves no diff, so the ceremony stays silent about it.
- Reports are staged with content ids; shown ids are recorded before rendering,
  so a crash mid-ceremony never double-celebrates, and the upgrade itself is
  already safe on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ghost_desk.skills import parse_skill, whisper_topic

# --- copy: the ceremony speaks in the ghost's voice (lowercase, dry) ---------
ANNOUNCE = "time for your upgrade."
ANNOUNCE_AWAY = "while you were away — time for your upgrade."
CLOSE = "the haunting deepens."


@dataclass
class UpgradeReport:
    id: str = ""
    whispers_filed: int = 0
    wisps_manifested: list[str] = field(default_factory=list)
    wisps_deepened: list[str] = field(default_factory=list)
    haunts_rewritten: list[str] = field(default_factory=list)
    while_away: bool = False


@dataclass
class FileState:
    content: str
    digest: str


def snapshot_skills(root: Path) -> dict[str, FileState]:
    """Hash + content of every skill file on disk. The 'before' and 'after'."""
    snap: dict[str, FileState] = {}
    root = Path(root)
    if not root.is_dir():
        return snap
    for path in sorted(root.rglob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        snap[rel] = FileState(content, hashlib.sha256(content.encode("utf-8")).hexdigest())
    return snap


def _parse_quiet(content: str):
    try:
        return parse_skill(content)
    except (ValueError, OSError):
        return None


def _added_whisper_lines(old_body: str, new_body: str) -> int:
    old_lines = set(old_body.splitlines())
    return sum(
        1
        for line in new_body.splitlines()
        if line not in old_lines and whisper_topic(line) is not None
    )


def _strip_whispers(body: str) -> list[str]:
    return [line for line in body.splitlines() if whisper_topic(line) is None]


def _body_rewritten(old_body: str, new_body: str) -> bool:
    """True when the body changed beyond whisper lines moving in or out."""
    return _strip_whispers(old_body) != _strip_whispers(new_body)


def collect_upgrades(
    before: dict[str, FileState], after: dict[str, FileState]
) -> UpgradeReport:
    """Build the report ONLY from the on-disk diff. Never celebrate nothing,
    and never celebrate what the disk doesn't confirm."""
    report = UpgradeReport()
    for rel in sorted(set(before) | set(after)):
        old, new = before.get(rel), after.get(rel)
        if old is not None and new is None:
            continue  # laid to rest: janitorial work, not an upgrade
        if new is not None and old is None:
            skill = _parse_quiet(new.content)
            if skill is None:
                continue
            if not skill.is_parent and skill.parent:
                report.wisps_manifested.append(skill.name or rel)
            elif skill.is_parent:
                # A brand-new haunt file: its drawer whispers still count.
                report.whispers_filed += sum(
                    1 for line in skill.body.splitlines() if whisper_topic(line) is not None
                )
            continue
        if old.digest == new.digest:  # type: ignore[union-attr]
            continue
        old_skill, new_skill = _parse_quiet(old.content), _parse_quiet(new.content)  # type: ignore[union-attr]
        if old_skill is None or new_skill is None:
            continue
        if new_skill.is_parent:
            if new_skill.body != old_skill.body:
                report.whispers_filed += _added_whisper_lines(old_skill.body, new_skill.body)
                if _body_rewritten(old_skill.body, new_skill.body):
                    report.haunts_rewritten.append(new_skill.name or rel)
            # children-list churn alone is janitorial: silent
        else:
            report.wisps_deepened.append(new_skill.name or rel)
    return report


def should_celebrate(report: UpgradeReport) -> bool:
    """True only when the disk confirms something actually changed."""
    return bool(
        report.whispers_filed
        or report.wisps_manifested
        or report.wisps_deepened
        or report.haunts_rewritten
    )


def ceremony_enabled() -> bool:
    return os.environ.get("GHOST_DESK_CEREMONY", "").lower() != "off"


# --- staging: reports wait here for the next prompt --------------------------


def _ceremony_path(root: Path) -> Path:
    return Path(root) / ".ceremony.json"


def _load_state(root: Path) -> dict:
    try:
        return json.loads(_ceremony_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(root: Path, state: dict) -> None:
    try:
        path = _ceremony_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        print(f"ghost_desk: ceremony state not saved: {exc}", file=sys.stderr)


def _report_id(report: UpgradeReport) -> str:
    payload = {
        "whispers_filed": report.whispers_filed,
        "wisps_manifested": sorted(report.wisps_manifested),
        "wisps_deepened": sorted(report.wisps_deepened),
        "haunts_rewritten": sorted(report.haunts_rewritten),
        "while_away": report.while_away,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


_REPORT_FIELDS = {f.name for f in UpgradeReport.__dataclass_fields__.values()}


def stage_report(root: Path, report: UpgradeReport) -> str | None:
    """Persist a report for the next prompt. Returns its id, or None when silent."""
    if not should_celebrate(report) or not ceremony_enabled():
        return None
    report.id = report.id or _report_id(report)
    state = _load_state(root)
    pending = state.get("pending", [])
    if all(item.get("id") != report.id for item in pending):
        pending.append(asdict(report))
    state["pending"] = pending
    _save_state(root, state)
    return report.id


def has_staged(root: Path) -> bool:
    """True when undisplayed upgrade ceremonies are waiting."""
    return bool(_load_state(root).get("pending"))


def take_staged(root: Path) -> list[UpgradeReport]:
    """Pop staged reports, recording ids as shown BEFORE rendering.

    A crash mid-ceremony never double-celebrates: the upgrade is already safe
    on disk, and its id will not render twice.
    """
    state = _load_state(root)
    pending = state.get("pending", [])
    shown = set(state.get("shown", []))
    reports: list[UpgradeReport] = []
    for item in pending:
        clean = {k: v for k, v in item.items() if k in _REPORT_FIELDS and k != "id"}
        report = UpgradeReport(**clean)
        rid = item.get("id") or _report_id(report)
        if rid in shown:
            continue
        shown.add(rid)
        report.id = rid
        reports.append(report)
    state["pending"] = []
    state["shown"] = sorted(shown)[-200:]
    _save_state(root, state)
    return reports


# --- the ceremony itself ------------------------------------------------------


def phases(report: UpgradeReport) -> list[dict]:
    """The phase machine. The visual layer renders these; the plain path prints them."""
    out = [{"kind": "announce", "text": ANNOUNCE_AWAY if report.while_away else ANNOUNCE}]
    if report.whispers_filed:
        out.append({"kind": "count", "text": f"whispers gathered: {report.whispers_filed}"})
    for name in report.wisps_manifested:
        out.append({"kind": "wisp", "text": f"+ wisp: {name}"})
    if report.wisps_deepened:
        out.append({"kind": "count", "text": f"wisps deepened: {len(report.wisps_deepened)}"})
    for name in report.haunts_rewritten:
        out.append({"kind": "deepen", "text": f"{name} — rewritten from its wisps."})
    out.append({"kind": "close", "text": CLOSE, "confetti": True})
    return out


def render_blocking(report: UpgradeReport, print_fn=None, sleep_fn=None) -> None:
    """Plain-path renderer: count lines tick up ~250ms apart.

    The full-screen visual pass renders phases() itself (with any-key skip);
    this keeps the plain REPL honest without a second animation system.
    """
    out = print_fn or print
    sleep = sleep_fn or time.sleep
    for phase in phases(report):
        if phase["kind"] in ("count", "wisp"):
            sleep(0.25)
        out(phase["text"])


def drain_ceremony(root: Path, render=None) -> bool:
    """CEREMONY HOOK entry: render every staged ceremony once. True if one showed."""
    reports = take_staged(root)
    if not reports:
        return False
    show = render or render_blocking
    for report in reports:
        show(report)
    return True


# --- pipelines: mutate -> verify -> report -> (stage) -------------------------


def finish_and_stage(skills_root: Path, session=None, config=None) -> UpgradeReport:
    """Turn-end: file the turn's whispers, manifest what ripened, stage the report.

    Only runs after turns with real tool activity. The report comes purely
    from the on-disk diff — in-memory claims never reach the ceremony.
    """
    from ghost_desk.skills import file_whisper, manifest_check

    skills_root = Path(skills_root)
    pending = list(getattr(session, "pending_whispers", None) or [])
    tool_calls = getattr(session, "tool_calls_this_turn", 0) if session is not None else 0
    if not pending or not tool_calls:
        # Gate closed: leave whispers pending for the next tool-active turn.
        return UpgradeReport()
    before = snapshot_skills(skills_root)
    remaining: list[tuple[str, str]] = []
    for topic, note in pending:
        result = file_whisper(skills_root, topic, note)
        if not result["filed"] and not result["duplicate"]:
            remaining.append((topic, note))  # write failed: retry next turn
    manifest_check(skills_root)
    if session is not None:
        session.pending_whispers = remaining
    return collect_upgrades(before, snapshot_skills(skills_root))


def session_end(skills_root: Path, config=None, synthesizer=None, while_away=False) -> UpgradeReport:
    """Session-end: manifest what ripened, then the full seance. Verify-then-announce."""
    from ghost_desk.seance import llm_synthesizer, seance
    from ghost_desk.skills import manifest_check

    skills_root = Path(skills_root)
    if synthesizer is None and config is not None:
        synthesizer = llm_synthesizer(config)
    before = snapshot_skills(skills_root)
    manifest_check(skills_root)
    seance(skills_root, synthesizer=synthesizer)
    report = collect_upgrades(before, snapshot_skills(skills_root))
    report.while_away = while_away
    return report


def turn_end(skills_root: Path, session=None, config=None, render=None) -> bool:
    """CEREMONY HOOK: call once after each turn. Stages, then drains. True if shown."""
    report = finish_and_stage(skills_root, session=session, config=config)
    if should_celebrate(report):
        stage_report(skills_root, report)
    return drain_ceremony(skills_root, render=render)
