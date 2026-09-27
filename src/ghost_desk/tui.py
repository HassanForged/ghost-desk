"""Rich banner, a multiline prompt, and slash commands. Status is printed, never left quiet."""

from __future__ import annotations

import sys
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ghost_desk.agent import AgentResult, DeskSession, run_turn
from ghost_desk.background import BackgroundDesk, build_digest, digest_due, run_due, write_digest
from ghost_desk.config import Config, SetupError, access_level, needs_setup, save_config, setup_interactive
from ghost_desk.providers import build_client
from ghost_desk.seance import seance
from ghost_desk.memory import Memory
from ghost_desk.permissions import PermissionGate
from ghost_desk.agent import PERSONALITIES
from ghost_desk.skills import ensure_skills, load_child, load_parents, render_index
from ghost_desk.subagents import spawn
from ghost_desk.tools import schemas
from ghost_desk import update as update_flow
from ghost_desk.update import UpdateSequence

def header_status(*, busy: bool, phase: str = "thinking", elapsed: int = 0, full_access: bool = False) -> str:
    """Right side of the session header. The ghost is the identity here; the brain stays under /model."""
    suffix = " · full access" if full_access else ""
    if busy:
        return f"rattling chains…  {elapsed}s{suffix}"
    return "haunting" + suffix


ACCESS_PHRASE = "i trust my ghost"
ACCESS_WARNING = (
    "full access means i won't ask before reading, writing, editing, or running commands "
    "on this machine. everything still gets logged. you can take it back anytime with /access ask.\n"
    'type "i trust my ghost" to turn it on — anything else cancels.'
)
ACCESS_ON = (
    "full access is on. i won't ask before reading, writing, or running commands. "
    "everything is still logged. /access ask takes it back."
)


def header_line(
    *,
    busy: bool,
    phase: str = "thinking",
    elapsed: int = 0,
    full_access: bool = False,
    meter_colors: tuple = (),
    model: str = "",
    width: int = 80,
) -> list[tuple[str, str]]:
    """Row-0 header text cells: `ghost desk · <status>` left, meter + model right.

    The buddy occupies the first 16 columns plus a 2-column gutter. When the
    terminal is too narrow for the right side, the meter is dropped rather
    than wrapping.
    """
    status_txt = header_status(busy=busy, phase=phase, elapsed=elapsed, full_access=full_access)
    left_len = len("ghost desk · " + status_txt)
    right_len = len(meter_colors) + 2 + len(model)
    cells: list[tuple[str, str]] = [("class:brand", "ghost desk"), ("class:muted", f" · {status_txt}")]
    gap = width - 18 - left_len - right_len
    if meter_colors and gap >= 1:
        cells.append(("", " " * gap))
        cells += [(f"fg:{color}", "█") for color in meter_colors]
        cells.append(("class:muted", f"  {model}"))
    return cells


def session_chrome() -> dict:
    # Full-width terminal-native layout: a compact buddy docked top-left,
    # chat and input spanning the terminal. No centered column, no margins.
    return {
        "fullwidth": True,
        "buddy_width": 16,
        "buddy_rows": 6,
        "ghost_width": 16,  # picture-protocol region follows the buddy
        "ghost_height": 6,
        "header": True,
    }


CHIPS = ["plan my day", "explain something", "draft a message"]


def _greeting() -> str:
    import datetime

    hour = datetime.datetime.now().hour
    if hour < 12:
        return "Good morning."
    if hour < 17:
        return "Good afternoon."
    return "Good evening."


def _you_fragments(text: str, width: int) -> list[tuple[str, str]]:
    """The user's message as plain full-width lines: `› ` prefix, wrapped."""
    import textwrap

    wrapped = textwrap.wrap(text, max(20, width - 4)) or [""]
    fragments: list[tuple[str, str]] = []
    for i, line in enumerate(wrapped):
        fragments.append(("class:prompt", "› " if i == 0 else "  "))
        fragments.append(("", line + "\n"))
    fragments.append(("", "\n"))
    return fragments


def _dwidth(text: str) -> int:
    """Cell width, counting wide characters as two."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


HELP = """\
/help        show this list
/new         start a fresh conversation
/personality helpful, concise, or technical
/model       show or set the model (/model name)
/tools       list tools
/plan        show the current plan
/haunts      list haunts, or /haunts name to read a wisp
/memory      show recent verified notes
/recall      search past sessions (/recall word)
/sessions    list saved sessions
/resume      continue a session (/resume id, or the latest)
/promote     save the newest correction draft into MEMORY.md
/export      write Markdown folders
/seance      merge wisps, lay the dead to rest, rewrite haunts
/bg          list jobs, /bg add <schedule> <prompt>, /bg digest
/update      fetch and install the latest haunting
/access      show or change access level (/access full, /access ask)
/quit        leave
Enter sends. Alt-Enter inserts a newline. Ctrl+C cancels the current turn.
"""


class Status:
    def __init__(self) -> None:
        self.text = "ready"

    def set(self, text: str, console: Console | None = None) -> None:
        self.text = text
        if console is not None:
            console.print(f"[dim]{text}[/dim]")


def banner() -> Panel:
    body = Text()
    body.append("GHOST\n", style="bold magenta")
    body.append("your ghost   code stays on this machine\n", style="bright_cyan")
    body.append("only the chat API leaves the machine", style="bright_cyan")
    return Panel(body, title="Ghost Desk", border_style="bright_magenta", padding=(1, 2))


def default_prompter(status: Status, data_dir: Path) -> Callable[[str], str]:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    history = FileHistory(str(data_dir / "history.txt"))
    session = PromptSession(
        multiline=True,
        key_bindings=bindings,
        history=history,
        bottom_toolbar=lambda: [("class:toolbar", f" {status.text}  Enter sends  Alt-Enter newline")],
    )

    def ask(message: str = "❯ ") -> str:
        return session.prompt(message)

    return ask


def show_transcript(console: Console, history: list[dict], limit: int = 8) -> None:
    """Print the recent thread so opening the desk feels like walking back into a chat."""
    visible = [
        message
        for message in history
        if message.get("role") in {"user", "assistant"} and (message.get("content") or "").strip()
    ]
    for message in visible[-limit:]:
        speaker = "you" if message.get("role") == "user" else "ghost"
        console.print(f"{speaker}", style="bold", markup=False)
        console.print(str(message.get("content") or "").strip(), markup=False, highlight=False)
        console.print()


def _print_result(console: Console, result: AgentResult, streamed: bool) -> None:
    failed = [check for check in result.report.checks if not check.ok]
    if streamed:
        console.print()
    else:
        console.print("ghost", style="bold magenta", markup=False)
        console.print(result.text, markup=False, highlight=False)
        console.print()
    for check in failed:
        console.print(f"  ✗ {check.line()}", markup=False, highlight=False)


def _answer_pending(pending: dict, text: str) -> bool:
    """Answer a live permission prompt. Returns False when the prompt already
    expired (timeout) or was answered: late input is chat, never permission."""
    event = pending.get("event")
    if event is None:
        return False
    pending["yes"] = text.strip().lower() in {"y", "yes"}
    event.set()
    pending["event"] = None
    return True


def _visible_lines(history: list[dict]) -> list[tuple[str, str]]:
    """Flatten a stored session history into the chat pane's (role, text) rows."""
    shown: list[tuple[str, str]] = []
    for message in history:
        content = message.get("content")
        if not content or not isinstance(content, str):
            continue
        if message.get("role") == "user":
            shown.append(("you", content.strip()))
        elif message.get("role") == "assistant":
            shown.append(("ghost", content.strip()))
    return shown


def _slash(
    text: str,
    *,
    console: Console,
    config: Config,
    memory: Memory,
    session: DeskSession,
    skills_root: Path,
    gate: PermissionGate | None = None,
    update_ui: Callable[[], str] | None = None,
    health: object | None = None,
    hooks: dict | None = None,
) -> str | None:
    """Return 'quit' to leave, or a string that was handled. None means it is not a slash command."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped[1:].partition(" ")
    command = head.lower().strip()
    rest = rest.strip()
    if command in {"quit", "exit"}:
        console.print("the ghost fades…")
        return "quit"
    if command == "help":
        console.print(HELP, markup=False)
        return "ok"
    if command == "new":
        # SEANCE HOOK — the old session gets its seance before the fresh one
        # starts; any upgrades show as a ceremony below.
        from ghost_desk import ceremony

        ceremony.stage_report(skills_root, ceremony.session_end(skills_root, config=config))
        fresh = DeskSession(parents_text=session.parents_text, compactor=session.compactor)
        session.id = fresh.id
        session.history = []
        session.plan = fresh.plan
        session.model_override = ""
        if health is not None and hasattr(health, "reset"):
            health.reset()
        replace = getattr(console, "replace", None)
        if callable(replace):
            # Full-screen mode: rebuild the visible chat instead of leaving
            # the old transcript on screen.
            replace([])
        console.print("a fresh haunting.")
        # CEREMONY HOOK — drain anything the session-end seance staged.
        # The visual pass replaces the default renderer with its own phases.
        ceremony.drain_ceremony(skills_root)
        return "ok"
    if command == "personality":
        name = rest.lower()
        if not name:
            console.print(session.personality)
            console.print("helpful, concise, technical")
            return "ok"
        if name not in PERSONALITIES:
            console.print("helpful, concise, or technical")
            return "ok"
        session.personality = name
        console.print(f"personality {name}")
        return "ok"
    if command == "model":
        if not rest:
            console.print(session.model_override or config.model)
            return "ok"
        session.model_override = rest
        config.model = rest
        if config.config_file().is_file():
            save_config(config)
        console.print(f"model {rest}")
        return "ok"
    if command == "access":
        level = rest.lower()
        if not level:
            current = access_level(config)
            if current == "full":
                console.print("access: full — i don't ask before reading, writing, or running commands.")
            else:
                console.print("access: ask — i check before writes and shell.")
            return "ok"
        if level == "ask":
            config.access = "ask"
            save_config(config)
            if gate is not None:
                gate.full_access = False
            session.access_pending = False
            console.print("back to asking first.")
            return "ok"
        if level == "full":
            if access_level(config) == "full":
                console.print("full access is already on.")
                return "ok"
            console.print(ACCESS_WARNING, markup=False)
            session.access_pending = True
            return "ok"
        console.print("usage: /access [full|ask]")
        return "ok"
    if command == "tools":
        console.print("shell\nfile_read\nfile_write\nfile_edit\nhttp_fetch\nweb_search\nopen\nclipboard\nclose_ghosts\nlittle_ghost")
        return "ok"
    if command == "plan":
        draft = session.plan.draft
        console.print(draft.render() if draft else "No plan yet.")
        return "ok"
    if command == "haunts":
        if rest:
            child = load_child(skills_root, rest)
            console.print(child.body if child else f"No wisp named {rest}.")
            return "ok"
        parents = load_parents(skills_root)
        for skill in parents:
            kids = ", ".join(skill.children) if skill.children else "none"
            console.print(f"{skill.name} — {skill.description} (wisps: {kids})")
        return "ok"
    if command == "memory":
        notes = memory.recent_notes(limit=8)
        if not notes:
            console.print("No verified notes yet.")
            return "ok"
        for note in notes:
            console.print(f"{note['ts']} {note['topic']}: {note['body']}", markup=False)
        return "ok"
    if command == "recall":
        hits = memory.search_messages(rest.split(), limit=8)
        if not hits:
            console.print("Nothing in past sessions.")
            return "ok"
        for hit in hits:
            console.print(f"{hit['ts']} {hit['session_id'][:8]} {hit['role']}: {hit['content'][:160]}", markup=False)
        return "ok"
    if command == "sessions":
        rows = memory.list_sessions()
        if not rows:
            console.print("No sessions yet.")
            return "ok"
        for row in rows:
            console.print(f"{row['session_id']}  {row['ts']}  {row['messages']} messages")
        return "ok"
    if command == "resume":
        target = rest or _latest_session(memory)
        if not target:
            console.print("No session to resume.")
            return "ok"
        history = memory.load_transcript(target)
        if not history:
            console.print(f"No session {target}.")
            return "ok"
        session.id = target
        session.history = history
        session.plan = type(session.plan)()
        (config.data_path() / "current_session.txt").write_text(target, encoding="utf-8")
        replace = getattr(console, "replace", None)
        if callable(replace):
            # Full-screen mode: show the resumed transcript, not the old one.
            replace(_visible_lines(history))
        console.print(f"resumed {target} ({len(history)} messages)")
        return "ok"
    if command == "promote":
        from ghost_desk.context import promote_lesson

        console.print(promote_lesson(config.data_path()), markup=False)
        return "ok"
    if command == "export":
        target = Path(rest).expanduser() if rest else config.data_path() / "export"
        memory.export_markdown(target, skills_root)
        console.print(f"exported {target}")
        return "ok"
    if command == "seance":
        from ghost_desk.seance import llm_synthesizer

        stats = seance(skills_root, synthesizer=llm_synthesizer(config))
        session.parents_text = render_index(load_parents(skills_root))
        console.print(
            f"seance done: {stats['synthesized']} haunts rewritten from their wisps, "
            f"{stats['removed']} laid to rest, {stats['written']} kept."
        )
        return "ok"
    if command == "bg":
        return _bg(rest, console=console, memory=memory, config=config, session=session, skills_root=skills_root)
    if command == "update":
        if update_ui is not None:
            # Full-screen TUI: the animated dissolve/stitch/re-materialize show.
            return update_ui()
        return _update_sync(console)
    if command == "setup":
        # Re-pick the brain: show the menu, /model does the swap.
        console.print("pick a brain for the ghost — /model <name> to swap.")
        console.print(f"now: {config.provider or 'none'} / {config.model or 'none'}")
        return "ok"
    if command == "leaves":
        if hooks is None or "toggle_leaves" not in hooks:
            console.print("leaves can't be toggled here.")
            return "ok"
        mode = rest.lower()
        if mode not in ("on", "off"):
            console.print("usage: /leaves <on|off>")
            return "ok"
        hooks["toggle_leaves"](mode == "on")
        console.print("leaves on." if mode == "on" else "leaves off.")
        return "ok"
    if command == "picture":
        if hooks is None or "set_picture" not in hooks:
            console.print("picture can't be toggled here.")
            return "ok"
        mode = rest.lower()
        if mode not in ("kitty", "iterm2", "off"):
            console.print("usage: /picture <kitty|iterm2|off>")
            return "ok"
        hooks["set_picture"](None if mode == "off" else mode)
        console.print(f"picture: {mode}.")
        return "ok"
    if command == "retry":
        # Re-run the last user turn.
        last_user = None
        for msg in reversed(session.history):
            if msg.get("role") == "user" and msg.get("content", "").strip():
                content = msg["content"].strip()
                if not content.startswith("/"):
                    last_user = content
                    break
        if not last_user:
            console.print("nothing to retry.")
            return "ok"
        if hooks is not None and "resubmit" in hooks:
            hooks["resubmit"](last_user)
        else:
            console.print("(retry needs the live ui)")
        return "ok"
    if command == "copy":
        # Copy the last ghost reply to the terminal clipboard (OSC 52).
        last_reply = None
        for msg in reversed(session.history):
            if msg.get("role") == "assistant" and msg.get("content", "").strip():
                last_reply = msg["content"].strip()
                break
        if not last_reply:
            console.print("nothing to copy.")
            return "ok"
        import base64
        import sys
        b64 = base64.b64encode(last_reply.encode("utf-8")).decode("ascii")
        sys.stdout.write(f"\033]52;c;{b64}\033\\")
        sys.stdout.flush()
        console.print("copied.")
        return "ok"
    # Unknown /… goes to the model, not swallowed.
    from ghost_desk.palette import is_known_command
    if not is_known_command(text):
        return None
    console.print("Unknown command. /help lists them.")
    return "ok"


def _update_sync(console) -> str:
    """The /update run for the plain REPL: blocking, no pixel show."""
    console.print(update_flow.HOLD_STILL)
    console.print(update_flow.STITCHING)
    seq = UpdateSequence()
    seq.result = update_flow.install_update()
    for _role, text in seq.final_lines():
        console.print(text)
    return "ok"


def _dither_out(rows: list[list[tuple[str, str]]], frac: float, seed: int = 0x6A05):
    """Blank a seeded-random fraction of the non-blank cells: a dither dissolve."""
    import random

    rng = random.Random(seed)
    cells = [
        (y, x)
        for y, row in enumerate(rows)
        for x, (_style, text) in enumerate(row)
        if text.strip()
    ]
    rng.shuffle(cells)
    hide = set(cells[: int(len(cells) * max(0.0, min(1.0, frac)))])
    return [
        [("", " ") if (y, x) in hide else cell for x, cell in enumerate(row)]
        for y, row in enumerate(rows)
    ]


def _stitch_bar(frame: int, width: int) -> list[tuple[str, str]]:
    """One shimmering indeterminate pixel bar row, `width` cells wide."""
    span = max(4, width - 6)
    pos = frame % (2 * span)
    filled = 6 + (pos if pos <= span else 2 * span - pos)
    filled = max(0, min(width, filled))
    return [("class:upbar", "█" * filled), ("class:muted", "░" * (width - filled))]


def _update_buddy_rows(seq: UpdateSequence) -> list[list[tuple[str, str]]]:
    """Buddy-sized frames for the /update show: dissolve, stitch, re-materialize.

    Deterministic materialization steps on the buddy grid: the dissolve runs
    the steps backwards, the rematerialize runs them forwards. During the
    stitch phase the bottom row becomes the shimmering pixel bar; the header
    text shows the stitching label.
    """
    from ghost_desk.face import (
        BUDDY_ROWS,
        BUDDY_W,
        buddy_grid,
        dither_shade,
        fragments_from_grid,
        materialize_steps,
    )

    grid = dither_shade(buddy_grid())
    steps = materialize_steps(grid, seed=0x6A05, steps=12)
    frac = seq.dissolve_frac
    # frac 0->1 during dissolve, 1->0 during rematerialize; map to a step.
    idx = int((1.0 - frac) * (len(steps) - 1))
    idx = max(0, min(len(steps) - 1, idx))
    rows = [list(row) for row in fragments_from_grid(steps[idx])]
    blank = ("", " ")
    norm = []
    for row in rows[:BUDDY_ROWS]:
        row = list(row)[:BUDDY_W] + [blank] * max(0, BUDDY_W - len(row))
        norm.append(row)
    while len(norm) < BUDDY_ROWS:
        norm.append([blank] * BUDDY_W)
    if seq.phase == "stitch":
        norm[-1] = _stitch_bar(seq.frame, BUDDY_W)
    return norm


def _confirm_access(
    text: str,
    *,
    console: Console,
    config: Config,
    session: DeskSession,
    gate: PermissionGate | None,
) -> bool:
    """Handle a pending `/access full` confirmation. True means the input was consumed."""
    if not session.access_pending:
        return False
    if text.strip().lower() == ACCESS_PHRASE:
        config.access = "full"
        save_config(config)
        if gate is not None:
            gate.full_access = True
        session.access_pending = False
        console.print(ACCESS_ON, markup=False)
        return True
    session.access_pending = False
    console.print("full access stays off.", markup=False)
    return False


def _latest_session(memory: Memory) -> str:
    rows = memory.list_sessions(limit=1)
    if not rows:
        return ""
    return str(rows[0]["session_id"])


def _bg(rest: str, *, console: Console, memory: Memory, config: Config, session: DeskSession, skills_root: Path) -> str:
    if not rest or rest == "list":
        jobs = memory.list_jobs()
        if not jobs:
            console.print("No background jobs. /bg add daily Review the notes")
            return "ok"
        for job in jobs:
            level = job["access"] if "access" in job.keys() else "ask"
            console.print(f"{job['name']} {job['schedule']} [{level}] {job['prompt']}", markup=False)
        return "ok"
    if rest == "digest":
        console.print(write_digest(memory), markup=False)
        return "ok"
    if rest.startswith("add "):
        body = rest[4:].strip()
        schedule, _, prompt = body.partition(" ")
        if not schedule or not prompt:
            console.print("Usage: /bg add <hourly|daily|weekly|cron> <prompt>")
            return "ok"
        from ghost_desk.background import parse_schedule

        name = "job-" + schedule.replace(" ", "-")
        memory.add_job(name, parse_schedule(schedule), prompt, access=access_level(config))
        console.print(f"added {name} [{access_level(config)} access]. It will not invent extra work.")
        return "ok"
    if rest.startswith("run "):
        name = rest[4:].strip()
        jobs = [job for job in memory.list_jobs() if job["name"] == name]
        if not jobs:
            console.print(f"No job {name}.")
            return "ok"
        result = _one_turn(jobs[0]["prompt"], config=config, memory=memory, session=session, skills_root=skills_root)
        console.print(result.text, markup=False, highlight=False)
        return "ok"
    console.print("Usage: /bg list | /bg add <schedule> <prompt> | /bg digest")
    return "ok"


def _one_turn(text, *, config, memory, session, skills_root, console=None, prompter=None) -> AgentResult:
    status = Status()

    def on_status(note: str) -> None:
        status.set(note, console)

    gate = PermissionGate(config.workspace(), ask=_asker(console, prompter), full_access=access_level(config) == "full")

    def spawn_fn(**kwargs):
        return spawn(client_factory=build_client, **kwargs)

    return run_turn(
        text,
        config=config,
        memory=memory,
        session=session,
        gate=gate,
        spawn_fn=spawn_fn,
        on_text=None if console is None else _streamer(console),
        on_status=on_status,
    )


def _streamer(console: Console):
    def on_text(delta: str) -> None:
        console.print(delta, end="", markup=False, highlight=False)

    return on_text


def _asker(console: Console | None, prompter: Callable[[str], str] | None):
    def ask(question: str) -> bool:
        if console is not None:
            console.print(question, markup=False)
        if prompter is None:
            return False
        try:
            answer = prompter("allow? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in {"y", "yes"}

    return ask


class _Log:
    """Slash commands write here. The chat pane reads it."""

    def __init__(self, lines: list[tuple[str, str]], refresh: Callable[[], None], lock=None) -> None:
        self.lines = lines
        self.refresh = refresh
        self._lock = lock

    def print(self, *args, **_kwargs) -> None:
        text = " ".join(str(part) for part in args)
        if self._lock is not None:
            with self._lock:
                self.lines.append(("note", text))
        else:
            self.lines.append(("note", text))
        self.refresh()

    def replace(self, entries) -> None:
        """Swap the visible chat (for /new and /resume)."""
        if self._lock is not None:
            with self._lock:
                self.lines[:] = list(entries)
        else:
            self.lines[:] = list(entries)
        self.refresh()


def _run_chat(config: Config, console: Console, memory: Memory, session: DeskSession, skills_root: Path, status: Status) -> int:
    import asyncio

    from ghost_desk.intro import play_intro

    # Fresh-boot intro: materialize, wordmark, tagline. Skippable, off via
    # GHOST_DESK_INTRO=off. Never plays on /new or /resume (those stay
    # inside the running UI).
    play_intro()

    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import HSplit, VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.styles import Style

    from ghost_desk.face import activity_for

    import threading

    lines: list[tuple[str, str]] = _visible_lines(session.history)
    pending: dict = {"event": None, "yes": False}
    # The worker thread appends to lines while the render thread iterates
    # them. Every mutation goes through add_line / bump_tool under this lock.
    lines_lock = threading.Lock()

    def add_line(role: str, text: str) -> None:
        with lines_lock:
            lines.append((role, text))

    def bump_tool(name: str) -> None:
        with lines_lock:
            if lines and lines[-1][0] == "tool" and lines[-1][1].startswith(name):
                prev = lines[-1][1]
                if " ×" in prev:
                    base, _, count = prev.rpartition(" ×")
                    try:
                        lines[-1] = ("tool", f"{base} ×{int(count) + 1}")
                        return
                    except ValueError:
                        pass
                else:
                    lines[-1] = ("tool", f"{name} ×2")
                    return
            lines.append(("tool", name))

    def snapshot_lines() -> list[tuple[str, str]]:
        with lines_lock:
            return list(lines)

    state = {"activity": "idle", "tick": 0, "stream": "", "busy": False, "started": 0.0, "cancel": False,
             "update_seq": None, "update_nagged": False}

    # 90s pixel pass: the ghost's little life, context meter, and crew.
    from ghost_desk.life import GhostLife
    from ghost_desk.health import ContextHealth
    from ghost_desk.crew import CrewState

    life = GhostLife()
    health = ContextHealth(model=config.model)
    crew = CrewState()

    def refresh() -> None:
        if app.is_running:
            app.invalidate()

    log = _Log(lines, refresh, lines_lock)

    def unattended(prompt: str, access: str = "ask") -> str:
        gate = PermissionGate(
            config.workspace(),
            ask=lambda _question: False,
            full_access=(access == "full"),
        )
        result = run_turn(
            prompt,
            config=config,
            memory=memory,
            session=DeskSession(parents_text=session.parents_text),
            gate=gate,
            depth=0,
            spawn_fn=None,
            persist_session=False,
        )
        return result.text

    desk = BackgroundDesk(memory, unattended)
    desk.start()

    def term_width() -> int:
        """Live terminal width for full-width layout. Falls back to 80."""
        try:
            return max(40, app.output.get_size().columns)
        except Exception:
            return 80

    def chat_fragments():
        fragments: list[tuple[str, str]] = []
        if not lines and not state["stream"] and not state["busy"]:
            fragments.append(("class:greet", _greeting() + "\n"))
            fragments.append(("class:muted", "What do you want to do?\n"))
            return fragments
        w = term_width()
        for role, text in snapshot_lines():
            if role == "you":
                fragments.extend(_you_fragments(text, w))
            elif role == "ghost":
                fragments.append(("class:reply", text + "\n\n"))
            elif role == "tool":
                fragments.append(("class:tool", "· " + text + "\n"))
            elif role == "ask":
                fragments.append(("class:ask", "? " + text + "\n"))
            else:
                fragments.append(("class:muted", text + "\n"))
        if state["stream"]:
            fragments.append(("class:reply", state["stream"]))
        elif state["busy"]:
            elapsed = max(0, int(time.monotonic() - state["started"]))
            label = {"searching": "searching", "reading": "reading", "working": "working"}.get(
                state["activity"], "thinking"
            )
            fragments.append(("class:status", f"  {label}  {elapsed}s\n"))
        return fragments

    chrome = session_chrome()

    from ghost_desk.images import detect_protocol, install_picture

    # Mutable UI toggles (/leaves, /picture): stored in a dict so the
    # _slash hooks can flip them at runtime.
    ui_toggles = {
        "picture_protocol": detect_protocol(),
        "leaf_field": None,
    }

    from ghost_desk.leaves import LeafField, leaves_enabled

    if leaves_enabled():
        ui_toggles["leaf_field"] = LeafField()

    # Hooks for the /… command handlers that need live UI state.
    resubmit_queue: list[str] = []

    def _toggle_leaves(on: bool) -> None:
        ui_toggles["leaf_field"] = LeafField() if on else None

    def _set_picture(mode: str | None) -> None:
        ui_toggles["picture_protocol"] = mode

    hooks = {
        "toggle_leaves": _toggle_leaves,
        "set_picture": _set_picture,
        "resubmit": resubmit_queue.append,
    }

    def header_fragments():
        """Full-width header: the buddy docked top-left, status and meter beside it.

        Row 0: `ghost desk · <status>` left, context meter + model right.
        Row 1: crew activity, if any. Rows 2-5 are empty air the leaves drift through.
        """
        from ghost_desk.face import (
            BUDDY_ROWS,
            BUDDY_W,
            buddy_frame,
            confetti_fragments,
            dither_shade,
            fragments_from_grid,
            sleep_z_fragments,
        )

        w = term_width()
        blank = ("", " ")
        seq = state.get("update_seq")
        if seq is not None:
            # The /update show takes over the buddy: dissolve, stitch, re-materialize.
            buddy_rows = _update_buddy_rows(seq)
        elif ui_toggles["picture_protocol"]:
            # The real picture paints over this blank space after each flush.
            buddy_rows = [[blank] * BUDDY_W for _ in range(BUDDY_ROWS)]
        else:
            # The buddy: GhostLife picks the frame (blink, glance, sleep,
            # busy scan). No bob — it sits docked in the header.
            frame_name = life.frame()
            # Past 85% context, occasionally glance at the meter.
            if frame_name == "neutral" and health.glance_at_meter() and life.tick_count % 10 == 0:
                frame_name = "glance_meter"
            buddy_rows = [list(row) for row in fragments_from_grid(dither_shade(buddy_frame(frame_name)))]
            # Error flinch: shift ±1 cell.
            dx = life.flinch_dx()
            if dx:
                for i, row in enumerate(buddy_rows):
                    buddy_rows[i] = ([blank] * dx + row[:-dx]) if dx > 0 else (row[-dx:] + [blank] * (-dx))
        # Normalize to BUDDY_W x BUDDY_ROWS.
        norm = []
        for row in buddy_rows[:BUDDY_ROWS]:
            row = list(row)[:BUDDY_W] + [blank] * max(0, BUDDY_W - len(row))
            norm.append(row)
        while len(norm) < BUDDY_ROWS:
            norm.append([blank] * BUDDY_W)
        buddy_rows = norm

        # Full-width rows: buddy art at cols 0..15, text from col 18.
        rows: list[list[tuple[str, str]]] = [row + [blank, blank] for row in buddy_rows]

        # Row 0: brand + status left, meter + model right.
        full = access_level(config) == "full"
        if seq is not None and seq.phase == "stitch":
            rows[0].append(("class:muted", update_flow.STITCHING))
        else:
            if state["busy"]:
                elapsed = max(0, int(time.monotonic() - state["started"]))
                phase = {"searching": "searching", "reading": "reading", "working": "working"}.get(
                    state["activity"], "thinking"
                )
                line = header_line(
                    busy=True, phase=phase, elapsed=elapsed, full_access=full,
                    meter_colors=tuple(health.block_colors(10)),
                    model=config.model or "", width=w,
                )
            else:
                line = header_line(
                    busy=False, full_access=full,
                    meter_colors=tuple(health.block_colors(10)),
                    model=config.model or "", width=w,
                )
            rows[0].extend(line)

        # Row 1: crew, compact.
        visible = crew.visible
        if visible:
            from ghost_desk.face import CREW_LABELS

            labels = " · ".join(CREW_LABELS.get(m.activity, m.activity) for m in visible)
            if crew.overflow:
                labels += f" · +{crew.overflow} more"
            rows[1].append(("class:muted", labels))

        # Pad every row to the full width, then stamp overlays on top.
        for row in rows:
            vis = sum(len(ch) for _, ch in row)
            row.extend([blank] * max(0, w - vis))
        overlays: list[tuple[int, int, list[list[tuple[str, str]]]]] = []
        if life.sleeping:
            overlays += sleep_z_fragments(life.tick_count, BUDDY_W, BUDDY_ROWS)
        if life.bouncing():
            overlays += confetti_fragments(life.tick_count, BUDDY_W, BUDDY_ROWS)
        leaf_field = ui_toggles["leaf_field"]
        if leaf_field is not None:
            overlays += [(lx, ly, frag) for lx, ly, frag in leaf_field.fragments()]
        for ox, oy, frag_rows in overlays:
            for dy, frow in enumerate(frag_rows):
                y = oy + dy
                if not 0 <= y < BUDDY_ROWS:
                    continue
                for dxx, cell in enumerate(frow):
                    x = ox + dxx
                    if 0 <= x < w:
                        rows[y][x] = cell
        fragments: list[tuple[str, str]] = []
        for row in rows:
            fragments.extend(row)
            fragments.append(("", "\n"))
        return fragments

    def chips_fragments():
        if lines or state["busy"] or state["stream"]:
            return []
        row = "   ".join(f"{i} · {chip}" for i, chip in enumerate(CHIPS, 1))
        return [("class:chip", row + "\n")]

    buffer = Buffer(multiline=True)

    # Command palette (Phase B): floating panel above the input pill.
    # Opens when the input starts with `/`. ↑/↓ navigate, Tab completes,
    # Enter runs (or enters arg completion), Esc dismisses / goes back.
    from ghost_desk.palette import (
        complete_arg,
        filter_commands,
    )

    pal: dict = {
        "matches": [],
        "selected": 0,
        "arg_mode": False,
        "arg_candidates": [],
        "arg_selected": 0,
        "arg_command": None,
    }

    def _pal_ctx() -> dict:
        """Context for arg completion: sessions, skills, etc."""
        ctx: dict = {"sessions": [], "skills": []}
        try:
            if hasattr(memory, "list_sessions"):
                sessions = memory.list_sessions()
                ctx["sessions"] = [
                    s.get("id", s) if isinstance(s, dict) else str(s) for s in sessions
                ]
        except Exception:
            pass
        return ctx

    def _pal_update() -> None:
        """Refresh palette matches from the buffer text."""
        text = buffer.text
        if state["busy"] or not text.startswith("/") or len(text) < 2:
            pal["matches"] = []
            pal["selected"] = 0
            pal["arg_mode"] = False
            return
        if pal["arg_mode"]:
            # In arg mode, filter candidates by what's after the command.
            cmd = pal["arg_command"]
            partial = text.partition(" ")[2] if " " in text else ""
            cands = complete_arg(cmd.name, partial, _pal_ctx())
            pal["arg_candidates"] = cands
            pal["arg_selected"] = min(pal["arg_selected"], max(0, len(cands) - 1))
            return
        # Command mode: filter on the head (up to first space).
        query = text[1:].partition(" ")[0]
        pal["matches"] = filter_commands(query)[:8]
        pal["selected"] = min(pal["selected"], max(0, len(pal["matches"]) - 1))

    def _pal_visible() -> bool:
        if state["busy"]:
            return False
        if pal["arg_mode"]:
            return bool(pal["arg_candidates"])
        return bool(buffer.text.startswith("/") and len(buffer.text) >= 2 and pal["matches"])

    buffer.on_text_changed.add_handler(lambda _: _pal_update())

    def palette_fragments():
        """The floating command palette panel, above the input line."""
        if not _pal_visible():
            return []
        w = term_width()
        lines: list[list[tuple[str, str]]] = []

        def _row(cells: list[tuple[str, str]]) -> None:
            lines.append(cells + [("", "\n")])

        # Top border.
        _row([("class:pill", "╭" + "─" * (w - 2) + "╮")])
        if pal["arg_mode"]:
            cmd = pal["arg_command"]
            # Header: /command <arg> — description.
            header = [
                ("class:ask", f"/{cmd.name} "),
                ("class:muted", f"{cmd.args_hint} — {cmd.description}"),
            ]
            _row([("class:pill", "│ ")] + header + [("class:pill", " │")])
            # Arg candidates.
            for i, cand in enumerate(pal["arg_candidates"][:8]):
                sel = i == pal["arg_selected"]
                style = "class:bubble" if sel else ""
                _row([("class:pill", "│ "), (style, f"{cand}".ljust(w - 4)), ("class:pill", " │")])
            footer = "↑↓ pick · tab fill · enter run · esc back"
        else:
            # Header: selected command with description.
            if pal["matches"]:
                m = pal["matches"][pal["selected"]]
                cmd = m.command
                header = [
                    ("class:ask", f"/{cmd.name} "),
                ]
                if cmd.args_hint:
                    header.append(("class:muted", f"{cmd.args_hint} "))
                header.append(("class:muted", f"— {cmd.description}"))
                _row([("class:pill", "│ ")] + header + [("class:pill", " │")])
            # Command rows.
            for i, m in enumerate(pal["matches"][:8]):
                cmd = m.command
                sel = i == pal["selected"]
                style = "class:bubble" if sel else ""
                name = f"/{cmd.name}"
                row_cells = [("class:pill", "│ "), ("class:ask" if not sel else style, name.ljust(12))]
                row_cells.append((style, f" {cmd.description}".ljust(w - 16)))
                row_cells.append(("class:pill", " │"))
                _row(row_cells)
            footer = "↑↓ pick · tab fill · enter run · esc dismiss"
        _row([("class:pill", "│ "), ("class:muted", footer.ljust(w - 4)), ("class:pill", " │")])
        _row([("class:pill", "╰" + "─" * (w - 2) + "╯")])
        # Flatten.
        frags: list[tuple[str, str]] = []
        for row in lines:
            frags.extend(row)
        return frags

    def _pal_height() -> int:
        if not _pal_visible():
            return 0
        if pal["arg_mode"]:
            return min(len(pal["arg_candidates"]), 8) + 4
        return min(len(pal["matches"]), 8) + 4

    def ask_allow(question: str) -> bool:
        event = threading.Event()
        pending["event"] = event
        pending["yes"] = False
        add_line("ask", question + "  y/n")
        app.invalidate()
        answered = event.wait(timeout=180)
        yes = answered and bool(pending["yes"])
        # Expire the prompt: anything typed after the timeout is a new
        # message, never an answer to this question.
        pending["event"] = None
        pending["yes"] = False
        return yes

    # One gate per session: an approved path stays approved across turns.
    gate = PermissionGate(config.workspace(), ask=ask_allow, full_access=access_level(config) == "full")

    def submit() -> None:
        text = buffer.text
        buffer.reset()
        if _confirm_access(text, console=log, config=config, session=session, gate=gate):
            refresh()
            return
        if _answer_pending(pending, text):
            return
        if not text.strip() or state["busy"]:
            return
        add_line("you", text.strip())
        handled = _slash(text, console=log, config=config, memory=memory, session=session, skills_root=skills_root, gate=gate, update_ui=_begin_update, health=health, hooks=hooks)
        if handled == "quit":
            app.exit(result=0)
            return
        if handled == "ok" and not resubmit_queue:
            refresh()
            return
        if resubmit_queue:
            # /retry: run the last user turn again.
            text = resubmit_queue.pop(0)
            add_line("you", text.strip())
        state["busy"] = True
        state["cancel"] = False
        state["activity"] = "working"
        state["stream"] = ""
        state["started"] = time.monotonic()

        def work() -> None:
            def on_text(delta: str) -> None:
                state["stream"] += delta
                life.set_typing(False)  # streaming, not typing
                app.invalidate()

            def on_status(note: str) -> None:
                state["activity"] = activity_for(note)
                if note.startswith("tool "):
                    bump_tool(note[5:].strip())
                    life.mark_tool_work()
                    # Real crew worker: add it.
                    from ghost_desk.face import classify_activity

                    crew.add(note[5:].strip(), classify_activity(note[5:].strip()), note[5:].strip())
                app.invalidate()

            def on_tokens(tokens: int) -> None:
                health.add_turn(reported_tokens=tokens)

            def spawn_fn(**kwargs):
                return spawn(client_factory=build_client, **kwargs)

            try:
                result = run_turn(
                    text,
                    config=config,
                    memory=memory,
                    session=session,
                    gate=gate,
                    spawn_fn=spawn_fn,
                    on_text=on_text,
                    on_status=on_status,
                    on_tokens=on_tokens,
                    cancelled=lambda: state["cancel"],
                )
                # Fallback if no tokens reported: estimate from text.
                if result.total_tokens == 0:
                    health.add_turn(text=text + result.text)
                warning = health.take_warning()
                if warning:
                    add_line("ghost", warning)
                if result.stop_reason == "cancelled":
                    # The user moved on: drop any late streamed text.
                    state["stream"] = ""
                    add_line("note", "cancelled")
                else:
                    add_line("ghost", state["stream"] or result.text)
                    for check in result.report.checks:
                        if not check.ok:
                            add_line("note", "✗ " + check.line())
                    # Success bounce, but only after tool work.
                    life.mark_success()
            except Exception as exc:
                add_line("note", "something moved in the dark: " + str(exc))
                life.mark_error()
            # CEREMONY HOOK — file the turn's whispers, manifest what ripened,
            # stage any upgrade. Staged ceremonies drain with the pixel
            # renderer when the next prompt paints; until then the report waits
            # in the state file (never celebrated twice).
            from ghost_desk import ceremony

            _report = ceremony.finish_and_stage(skills_root, session=session, config=config)
            if ceremony.stage_report(skills_root, _report):
                # Something real upgraded: the animate tick plays the pixel
                # ceremony (run_in_terminal) when the prompt repaints.
                state["ceremony_due"] = True
            state["stream"] = ""
            state["cancel"] = False
            state["activity"] = "idle"
            state["busy"] = False
            status.set("ready")
            app.invalidate()

        asyncio.get_running_loop().run_in_executor(None, work)

    def _begin_update() -> str:
        """Start the /update show: `hold still…`, pip in a thread, frames on the tick."""
        if state.get("update_seq") is not None or state["busy"]:
            add_line("note", "already stitching — hold still a little longer.")
            return "ok"
        seq = UpdateSequence()
        state["update_seq"] = seq
        add_line("ghost", update_flow.HOLD_STILL)

        def _worker() -> None:
            seq.result = update_flow.install_update()
            app.invalidate()

        threading.Thread(target=_worker, daemon=True, name="ghost-update").start()
        app.invalidate()
        return "ok"

    bindings = KeyBindings()

    @bindings.add("enter")
    def _enter(event) -> None:
        # Palette: Enter runs the command, or enters arg completion.
        if _pal_visible() and not pal["arg_mode"] and pal["matches"]:
            cmd = pal["matches"][pal["selected"]].command
            if cmd.needs_arg:
                # Enter arg mode: keep the command, complete the arg.
                pal["arg_mode"] = True
                pal["arg_command"] = cmd
                pal["arg_selected"] = 0
                buf = event.current_buffer
                buf.text = f"/{cmd.name} "
                buf.cursor_position = len(buf.text)
                _pal_update()
                return
        if _pal_visible() and pal["arg_mode"] and pal["arg_candidates"]:
            # Fill the selected arg and submit.
            cand = pal["arg_candidates"][pal["arg_selected"]]
            buf = event.current_buffer
            cmd = pal["arg_command"]
            buf.text = f"/{cmd.name} {cand}"
            pal["arg_mode"] = False
        submit()

    @bindings.add("up")
    def _pal_up(event) -> None:
        if not _pal_visible():
            return
        if pal["arg_mode"]:
            pal["arg_selected"] = (pal["arg_selected"] - 1) % max(1, len(pal["arg_candidates"]))
        else:
            pal["selected"] = (pal["selected"] - 1) % max(1, len(pal["matches"]))

    @bindings.add("down")
    def _pal_down(event) -> None:
        if not _pal_visible():
            return
        if pal["arg_mode"]:
            pal["arg_selected"] = (pal["arg_selected"] + 1) % max(1, len(pal["arg_candidates"]))
        else:
            pal["selected"] = (pal["selected"] + 1) % max(1, len(pal["matches"]))

    @bindings.add("tab")
    def _pal_tab(event) -> None:
        if not _pal_visible():
            return
        buf = event.current_buffer
        if pal["arg_mode"] and pal["arg_candidates"]:
            cand = pal["arg_candidates"][pal["arg_selected"]]
            cmd = pal["arg_command"]
            buf.text = f"/{cmd.name} {cand}"
        elif pal["matches"]:
            cmd = pal["matches"][pal["selected"]].command
            buf.text = f"/{cmd.name} " if cmd.needs_arg else f"/{cmd.name}"
        buf.cursor_position = len(buf.text)
        _pal_update()

    @bindings.add("escape")
    def _pal_esc(event) -> None:
        if pal["arg_mode"]:
            # Back to the command list.
            pal["arg_mode"] = False
            buf = event.current_buffer
            cmd = pal["arg_command"]
            buf.text = f"/{cmd.name}"
            buf.cursor_position = len(buf.text)
            _pal_update()
            return
        if _pal_visible():
            # Dismiss: clear the slash input.
            event.current_buffer.reset()

    @bindings.add("escape", "enter")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-c")
    def _cancel(event) -> None:
        if state["busy"]:
            state["cancel"] = True
            return
        app.exit(result=0)

    @bindings.add("c-d")
    def _quit(event) -> None:
        app.exit(result=0)

    for _key, _chip in zip("123", CHIPS):

        @bindings.add(_key)
        def _pick_chip(event, _chip=_chip, _key=_key) -> None:
            if not lines and not event.current_buffer.text and not state["busy"]:
                event.current_buffer.insert_text(_chip)
            else:
                event.current_buffer.insert_text(_key)

    async def animate() -> None:
        while True:
            await asyncio.sleep(0.5)
            ticked = False
            # The ghost's little life ticks every 0.5s: blink, glance, sleep.
            life.tick()
            crew.tick()
            ticked = True
            if state["busy"]:
                state["tick"] += 1
            # Sync busy/typing into the life state.
            life.set_busy(state["busy"])
            seq = state.get("update_seq")
            if seq is not None:
                if seq.tick():
                    for role, text in seq.final_lines():
                        add_line(role, text)
                    state["update_seq"] = None
                ticked = True
            lf = ui_toggles["leaf_field"]
            if lf is not None and lf.tick(time.monotonic(), term_width(), BUDDY_ROWS):
                ticked = True
            # Upgrade ceremony: staged at turn end, or while away. Played here
            # in the app thread via run_in_terminal — the full-screen UI is
            # suspended, the pixel ceremony paints raw, any key skips, and
            # the UI repaints cleanly when it returns.
            if state.pop("ceremony_due", False):
                from prompt_toolkit.application.run_in_terminal import (
                    run_in_terminal,
                )

                from ghost_desk import ceremony as _ceremony
                from ghost_desk.ceremony_fx import play_ceremony

                def _play() -> None:
                    _ceremony.drain_ceremony(
                        skills_root,
                        render=lambda report: play_ceremony(_ceremony.phases(report)),
                    )

                async with run_in_terminal():
                    _play()
                ticked = True
            if ticked:
                app.invalidate()

    from ghost_desk.face import BUDDY_ROWS

    header_win = Window(
        height=BUDDY_ROWS,
        content=FormattedTextControl(header_fragments),
        style="class:header",
    )
    chat = Window(
        content=FormattedTextControl(chat_fragments),
        wrap_lines=True,
        style="class:chat",
    )
    chips = Window(
        height=1,
        content=FormattedTextControl(chips_fragments),
        style="class:chips",
    )
    prompt_win = Window(
        width=2,
        height=1,
        content=FormattedTextControl(lambda: [("class:prompt", "\u203a ")]),
        style="class:input",
    )
    typed = Window(
        content=BufferControl(buffer=buffer),
        height=1,
        style="class:input",
    )
    input_row = VSplit([prompt_win, typed], height=1)
    hint = Window(
        height=1,
        content=FormattedTextControl(
            lambda: [("class:muted", "Enter send  \u00b7  Alt-Enter newline  \u00b7  /help  \u00b7  /new")]
        ),
        style="class:footer",
    )
    palette_win = Window(
        height=_pal_height,
        content=FormattedTextControl(palette_fragments),
        style="class:palette",
    )
    # Full-width terminal-native layout: header, chat, palette, input, hint.
    # No centered column, no side margins — the app fills the terminal.
    body = HSplit([header_win, chat, chips, palette_win, input_row, hint])
    layout = Layout(body)
    app = Application(
        layout=layout,
        key_bindings=bindings,
        style=Style.from_dict(
            {
                "chat": "bg:#090909 #d4d4d4",
                "side": "bg:#090909",
                "header": "bg:#090909",
                "footer": "bg:#090909",
                "meter": "bg:#090909",
                "rule": "bg:#090909 #333333",
                "composer": "bg:#161616 #f0f0f0",
                "input": "#f0f0f0",
                "prompt": "#9a9a9a",
                "brand": "bold #f0abfc",
                "bubble": "bg:#1e1e1e #f4f4f4",
                "greet": "#ededed",
                "chip": "#8a8a8a",
                "pill": "#3d3d3d",
                "palette": "bg:#090909",
                "chips": "bg:#090909",
                "caption": "#5a5a5a",
                "divider": "#2e2e2e bg:#090909",
                "user": "bold #f4f4f4",
                "reply": "#d4d4d4",
                "tool": "#8a8a8a",
                "ask": "#f0abfc",
                "muted": "#7a7a7a",
                "upbar": "#d8a7e0 bg:#090909",
                "status": "#9a9a9a italic",
            }
        ),
        full_screen=True,
        mouse_support=True,
    )

    def startup() -> None:
        app.create_background_task(animate())
        # CEREMONY HOOK — a previous session's seance may have staged an
        # upgrade ceremony ("while you were away — time for your upgrade.").
        # The animate tick plays it via run_in_terminal: safe raw painting,
        # any key skips, the UI repaints cleanly afterwards.
        from ghost_desk import ceremony as _ceremony

        if _ceremony.has_staged(skills_root):
            state["ceremony_due"] = True

        def _nag(_remote: str) -> None:
            # One dim line per session, never a modal, never blocking.
            if state.get("update_nagged"):
                return
            state["update_nagged"] = True
            add_line("note", update_flow.TAP_LINE)
            app.invalidate()

        update_flow.start_update_check(_nag)

    app.pre_run_callables.append(startup)
    app.layout.focus(typed)
    cleanup_picture = install_picture(
        app.output, ui_toggles["picture_protocol"], chrome, lambda: (state["busy"], state["tick"])
    )
    try:
        app.run()
    finally:
        # SEANCE HOOK — session end: janitorial pass + synthesis for changed
        # haunts, then verify-then-announce. Upgrades stage a ceremony that the
        # next session drains ("while you were away — time for your upgrade.").
        # Safe to run twice: an empty disk diff stages nothing.
        from ghost_desk import ceremony

        ceremony.stage_report(
            skills_root, ceremony.session_end(skills_root, config=config, while_away=True)
        )
        cleanup_picture()
        desk.stop()
        memory.close()
    return 0


def run_tui(
    config: Config,
    *,
    prompter: Callable[[str], str] | None = None,
    console: Console | None = None,
    setup_first: bool | None = None,
) -> int:
    console = console or Console()
    config.data_path().mkdir(parents=True, exist_ok=True)
    interactive = prompter is None and sys.stdin.isatty()
    if setup_first is None:
        setup_first = needs_setup(config) and interactive
    if interactive:
        screen = None
        try:
            from ghost_desk.boot import BootScreen

            screen = BootScreen()
            screen.enter()
            signed = bool(config.provider or config.model) and not needs_setup(config)
            screen.play_checks(signed=signed)
            if setup_first or needs_setup(config):
                number = screen.choose()
                config = setup_interactive(
                    cfg=config,
                    boot_choice=number,
                    input_fn=screen.ask,
                    output_fn=screen.log,
                )
                screen.log("brain bound")
                screen.log("ready")
            else:
                screen.log((config.provider or "brain") + "  " + (config.model or ""))
                screen.log("session open")
            time.sleep(0.08)
        except SetupError as exc:
            console.print(str(exc))
            return 2
        except (EOFError, KeyboardInterrupt):
            console.print("setup did not finish")
            return 2
        finally:
            if screen is not None:
                screen.leave()
    from ghost_desk.context import ensure_soul

    ensure_soul(config.data_path())
    skills_root = ensure_skills(config.data_path())
    parents = load_parents(skills_root)
    memory = Memory(config.data_path())
    session = DeskSession(parents_text=render_index(parents))
    marker = config.data_path() / "current_session.txt"
    if marker.is_file():
        previous = marker.read_text(encoding="utf-8").strip()
        history = memory.load_transcript(previous) if previous else []
        if history:
            session.id = previous
            session.history = history
    status = Status()
    if access_level(config) == "full":
        console.print("full access is on — /access ask to take it back.")
    if prompter is None and sys.stdin.isatty():
        return _run_chat(config, console, memory, session, skills_root, status)
    ask = prompter or default_prompter(status, config.data_path())
    if config.workspace() == Path.home():
        console.print("Workspace is your home directory. Start Ghost Desk inside a project folder.")
    console.print(banner())
    console.print(f"{config.provider or 'brain'}  ·  {config.model}  ·  {session.personality}", style="dim")
    if session.history:
        console.print()
        show_transcript(console, session.history)
    if digest_due(memory):
        console.print("[dim]Monthly digest is ready for review. /bg digest writes it. Nothing runs by itself.[/dim]")
    console.print("Talk here.  /help for commands", style="dim")
    # CEREMONY HOOK — a session-end seance may have staged an upgrade ceremony
    # ("while you were away — time for your upgrade."). Drain it before input.
    from functools import partial

    from ghost_desk import ceremony as _ceremony

    _ceremony.drain_ceremony(
        skills_root,
        render=partial(
            _ceremony.render_blocking,
            print_fn=lambda text: console.print(text, markup=False),
        ),
    )

    def unattended(prompt: str, access: str = "ask") -> str:
        gate = PermissionGate(
            config.workspace(),
            ask=lambda _question: False,
            full_access=(access == "full"),
        )
        result = run_turn(
            prompt,
            config=config,
            memory=memory,
            session=DeskSession(parents_text=session.parents_text),
            gate=gate,
            depth=0,
            spawn_fn=None,
            persist_session=False,
        )
        return result.text

    desk = BackgroundDesk(memory, unattended)
    desk.start()
    # The tap on the shoulder: one background check per boot, silent on failure.
    from ghost_desk import update as update_flow

    update_flow.start_update_check(
        lambda _remote: console.print(update_flow.TAP_LINE, style="dim")
    )
    # One gate for the whole session: approved paths stay approved across turns.
    gate = PermissionGate(config.workspace(), ask=_asker(console, ask), full_access=access_level(config) == "full")
    try:
        while True:
            try:
                text = ask("❯ ")
            except KeyboardInterrupt:
                console.print("cancelled")
                status.set("cancelled")
                continue
            except EOFError:
                break
            if text is None:
                break
            if not str(text).strip():
                continue
            if _confirm_access(str(text), console=console, config=config, session=session, gate=gate):
                continue
            handled = _slash(
                str(text),
                console=console,
                config=config,
                memory=memory,
                session=session,
                skills_root=skills_root,
                gate=gate,
            )
            if handled == "quit":
                break
            if handled == "ok":
                continue
            streamed = {"on": False, "opened": False}

            def on_text(delta: str) -> None:
                if not streamed["opened"]:
                    console.print("ghost", style="bold magenta")
                    streamed["opened"] = True
                streamed["on"] = True
                console.print(delta, end="", markup=False, highlight=False)

            def on_status(note: str) -> None:
                if note.startswith("tool "):
                    console.print(f"  ● {note[5:]}", style="dim", markup=False)
                    return
                if note == "fallback provider":
                    console.print("  ● switching brain", style="dim", markup=False)

            def spawn_fn(**kwargs):
                return spawn(client_factory=build_client, **kwargs)

            try:
                result = run_turn(
                    str(text),
                    config=config,
                    memory=memory,
                    session=session,
                    gate=gate,
                    spawn_fn=spawn_fn,
                    on_text=on_text,
                    on_status=on_status,
                )
            except KeyboardInterrupt:
                console.print("\ncancelled")
                status.set("cancelled")
                continue
            _print_result(console, result, streamed["on"])
            # CEREMONY HOOK — file the turn's whispers, manifest what ripened,
            # stage and drain any upgrade ceremony before the next prompt.
            # The visual pass replaces render= with its own phase renderer.
            _ceremony.turn_end(
                skills_root,
                session=session,
                config=config,
                render=partial(
                    _ceremony.render_blocking,
                    print_fn=lambda text: console.print(text, markup=False),
                ),
            )
            status.set("ready")
    finally:
        # SEANCE HOOK — clean shutdown: same session-end seance as /quit.
        # Safe to run twice: an empty disk diff stages nothing.
        _ceremony.stage_report(
            skills_root, _ceremony.session_end(skills_root, config=config, while_away=True)
        )
        desk.stop()
        memory.close()
    return 0


def export_now(config: Config, out: Path | None = None) -> Path:
    skills_root = ensure_skills(config.data_path())
    memory = Memory(config.data_path())
    try:
        return memory.export_markdown(out or (config.data_path() / "export"), skills_root)
    finally:
        memory.close()
