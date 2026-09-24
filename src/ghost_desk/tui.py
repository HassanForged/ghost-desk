"""Rich banner, a multiline prompt, and slash commands. Status is printed, never left quiet."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ghost_desk.agent import AgentResult, DeskSession, run_turn
from ghost_desk.background import BackgroundDesk, build_digest, digest_due, run_due, write_digest
from ghost_desk.config import Config, SetupError, needs_setup, save_config, setup_interactive
from ghost_desk.providers import build_client
from ghost_desk.curator import curate
from ghost_desk.memory import Memory
from ghost_desk.permissions import PermissionGate
from ghost_desk.agent import PERSONALITIES
from ghost_desk.skills import ensure_skills, load_child, load_parents, render_index
from ghost_desk.subagents import spawn

HELP = """\
/help        show this list
/new         start a fresh conversation
/personality helpful, concise, or technical
/model       show or set the model (/model name)
/tools       list tools
/plan        show the current plan
/skills      list skills, or /skills name for a child
/memory      show recent verified notes
/recall      search past sessions (/recall word)
/sessions    list saved sessions
/resume      continue a session (/resume id, or the latest)
/promote     save the newest correction draft into MEMORY.md
/export      write Markdown folders
/curate      merge duplicate skills and prune dead ones
/bg          list jobs, /bg add <schedule> <prompt>, /bg digest
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
    body.append("local harness   code stays on this machine\n", style="bright_cyan")
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


def _slash(
    text: str,
    *,
    console: Console,
    config: Config,
    memory: Memory,
    session: DeskSession,
    skills_root: Path,
) -> str | None:
    """Return 'quit' to leave, or a string that was handled. None means it is not a slash command."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head, _, rest = stripped[1:].partition(" ")
    command = head.lower().strip()
    rest = rest.strip()
    if command in {"quit", "exit"}:
        return "quit"
    if command == "help":
        console.print(HELP, markup=False)
        return "ok"
    if command == "new":
        fresh = DeskSession(parents_text=session.parents_text, compactor=session.compactor)
        session.id = fresh.id
        session.history = []
        session.plan = fresh.plan
        session.model_override = ""
        console.print(f"new conversation {session.id}")
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
    if command == "tools":
        console.print("shell\nfile_read\nfile_write\nfile_edit\nhttp_fetch\nweb_search\nopen\nclipboard\nclose_ghosts\nlittle_ghost")
        return "ok"
    if command == "plan":
        draft = session.plan.draft
        console.print(draft.render() if draft else "No plan yet.")
        return "ok"
    if command == "skills":
        if rest:
            child = load_child(skills_root, rest)
            console.print(child.body if child else f"No child skill named {rest}.")
            return "ok"
        parents = load_parents(skills_root)
        for skill in parents:
            kids = ", ".join(skill.children) if skill.children else "none"
            console.print(f"{skill.name} — {skill.description} (children: {kids})")
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
    if command == "curate":
        stats = curate(skills_root)
        session.parents_text = render_index(load_parents(skills_root))
        console.print(
            f"curated skills: wrote {stats['written']}, removed {stats['removed']}, parents {stats['parents']}"
        )
        return "ok"
    if command == "bg":
        return _bg(rest, console=console, memory=memory, config=config, session=session, skills_root=skills_root)
    console.print("Unknown command. /help lists them.")
    return "ok"


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
            console.print(f"{job['name']} {job['schedule']} {job['prompt']}", markup=False)
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
        memory.add_job(name, parse_schedule(schedule), prompt)
        console.print(f"added {name}. It will not invent extra work.")
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

    gate = PermissionGate(config.workspace(), ask=_asker(console, prompter))

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
    if setup_first:
        try:
            from ghost_desk.cli import play_boot

            number = play_boot(True)
            config = setup_interactive(cfg=config, boot_choice=number)
        except SetupError as exc:
            console.print(str(exc))
            return 2
        except (EOFError, KeyboardInterrupt):
            console.print("setup did not finish")
            return 2
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

    def unattended(prompt: str) -> str:
        gate = PermissionGate(config.workspace(), ask=lambda _question: False)
        result = run_turn(
            prompt,
            config=config,
            memory=memory,
            session=DeskSession(parents_text=session.parents_text),
            gate=gate,
            depth=0,
            spawn_fn=None,
        )
        return result.text

    desk = BackgroundDesk(memory, unattended)
    desk.start()
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
            handled = _slash(
                str(text),
                console=console,
                config=config,
                memory=memory,
                session=session,
                skills_root=skills_root,
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

            gate = PermissionGate(config.workspace(), ask=_asker(console, ask))

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
            status.set("ready")
    finally:
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
