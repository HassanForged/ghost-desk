"""The `/` command palette model.

Registry, filtering, and argument completion for slash commands.
Pure logic, no I/O, no terminal code: the floating panel UI and the
`_slash` wiring land in Phase B (tui.py). This module is the single
source of truth for what `/…` means.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """One slash command the palette knows about."""

    name: str
    description: str  # short, ghost-voiced, lowercase
    args_hint: str = ""  # e.g. "<session>"; shown after the name
    needs_arg: bool = False  # Enter completes instead of executing
    aliases: tuple[str, ...] = ()
    order: int = 999  # curated order for an empty query


@dataclass(frozen=True)
class CommandMatch:
    command: Command
    tier: int  # 0 = prefix, 1 = substring, 2 = description, 3 = fuzzy


# ---------------------------------------------------------------------------
# Registry — every slash command, most useful first.
# ---------------------------------------------------------------------------

_COMMANDS: tuple[Command, ...] = (
    Command("new", "start over with a clean slate", order=0),
    Command("retry", "run the last turn again", order=1),
    Command(
        "resume", "pick up where we left off",
        args_hint="<session>", needs_arg=True, order=2,
    ),
    Command(
        "model", "see or swap the brain",
        args_hint="<name>", needs_arg=True, order=3,
    ),
    Command("setup", "pick a brain for the ghost", order=4),
    Command(
        "access", "ask-first or full access",
        args_hint="<ask|full>", needs_arg=True, order=5,
    ),
    Command(
        "personality", "change how i talk",
        args_hint="<tone>", needs_arg=True, order=6,
    ),
    Command(
        "haunts", "list haunts, or peek at a wisp",
        args_hint="<name>", needs_arg=True, order=7,
    ),
    Command("memory", "show what i've remembered", order=8),
    Command(
        "bg", "jobs i run while you're away",
        args_hint="<list|add|run|digest>", needs_arg=True, order=9,
    ),
    Command(
        "recall", "dig through past sessions",
        args_hint="<words>", needs_arg=True, order=10,
    ),
    Command("sessions", "list saved sessions", order=11),
    Command(
        "leaves", "toggle the falling leaves",
        args_hint="<on|off>", needs_arg=True, order=12,
    ),
    Command(
        "picture", "toggle the portrait style",
        args_hint="<kitty|iterm2|off>", needs_arg=True, order=13,
    ),
    Command("help", "list every whisper i know", order=14),
    Command("tools", "list what i can do", order=15),
    Command("plan", "show the current plan", order=16),
    Command("promote", "save a correction to memory", order=17),
    Command(
        "export", "write everything to markdown",
        args_hint="<folder>", needs_arg=True, order=18,
    ),
    Command("seance", "merge wisps and rewrite the haunts", order=19),
    Command("update", "fetch the latest haunting", order=20),
    Command("copy", "copy my last reply", order=21),
    Command("quit", "the ghost fades…", aliases=("exit",), order=22),
)

#: Canonical lookup by name or alias.
_BY_NAME: dict[str, Command] = {}
for _cmd in _COMMANDS:
    _BY_NAME[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        _BY_NAME[_alias] = _cmd

COMMANDS: tuple[Command, ...] = _COMMANDS

#: Commands registered here whose `_slash` wiring lands in Phase B.
#: (access is listed too: its wiring is landing separately.)
STUB_COMMANDS: frozenset[str] = frozenset()


# Phase B: wire into _slash
def run_stub(command_name: str, **kwargs) -> str:
    """Stand-in for palette commands not yet handled by tui._slash."""
    raise NotImplementedError(
        f"/{command_name} is in the palette registry but not wired "
        f"into _slash yet (Phase B)"
    )


def canonical(name: str) -> Command | None:
    """Resolve a name or alias to its canonical command."""
    return _BY_NAME.get((name or "").strip().lstrip("/").lower())


def command_names() -> list[str]:
    return [cmd.name for cmd in _COMMANDS]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

_MAX_RESULTS = 8


def _fuzzy(name: str, query: str) -> bool:
    """True when every char of query appears in name, in order."""
    it = iter(name)
    return all(char in it for char in query)


def _match_tier(cmd: Command, query: str) -> int | None:
    """Best match tier for a command, or None when it doesn't match."""
    best: int | None = None
    for name in (cmd.name, *cmd.aliases):
        if name.startswith(query):
            tier = 0
        elif query in name:
            tier = 1
        elif query in cmd.description:
            tier = 2
        elif _fuzzy(name, query):
            tier = 3
        else:
            continue
        if best is None or tier < best:
            best = tier
            if best == 0:
                break
    return best


def filter_commands(query: str) -> list[CommandMatch]:
    """Fuzzy/substring filter over names and description keywords.

    Pure function, no I/O. Ranking: exact prefix first, then substring,
    then description keyword, then fuzzy. Capped at 8 results. An empty
    query returns the curated most-useful-first order.
    """
    q = (query or "").strip().lower()
    if q.startswith("/"):
        q = q[1:]
    if not q:
        return [CommandMatch(cmd, 0) for cmd in _COMMANDS[:_MAX_RESULTS]]
    scored: list[tuple[int, int, Command]] = []
    for cmd in _COMMANDS:
        tier = _match_tier(cmd, q)
        if tier is not None:
            scored.append((tier, cmd.order, cmd))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [CommandMatch(cmd, tier) for tier, _, cmd in scored[:_MAX_RESULTS]]


# ---------------------------------------------------------------------------
# Second-stage argument completion
# ---------------------------------------------------------------------------

def _prefix(candidates: list[str], partial: str) -> list[str]:
    p = (partial or "").lower()
    return [c for c in candidates if c.lower().startswith(p)]


def _model_provider(partial: str, ctx: dict) -> list[str]:
    models = [str(m) for m in ctx.get("models", [])]
    current = ctx.get("current_model")
    if current:
        current = str(current)
        models = [current] + [m for m in models if m != current]
    return _prefix(models, partial)


def _personality_provider(partial: str, ctx: dict) -> list[str]:
    try:
        from ghost_desk.agent import PERSONALITIES

        tones = list(PERSONALITIES)
    except Exception:
        tones = ["helpful", "concise", "technical"]
    return _prefix(tones, partial)


_ARG_PROVIDERS = {
    "resume": lambda partial, ctx: _prefix(
        [str(s) for s in ctx.get("sessions", [])], partial
    ),
    "haunts": lambda partial, ctx: _prefix(
        [str(s) for s in ctx.get("haunts", [])], partial
    ),
    "model": _model_provider,
    "personality": _personality_provider,
    "bg": lambda partial, ctx: _prefix(
        ["list", "add", "run", "digest"], partial
    ),
    "access": lambda partial, ctx: _prefix(["ask", "full"], partial),
    "leaves": lambda partial, ctx: _prefix(["on", "off"], partial),
    "picture": lambda partial, ctx: _prefix(
        ["kitty", "iterm2", "off"], partial
    ),
}


def complete_arg(
    command_name: str, partial: str = "", ctx: dict | None = None
) -> list[str]:
    """Argument candidates for a command. Pure; missing ctx keys give []."""
    cmd = canonical(command_name)
    if cmd is None:
        return []
    provider = _ARG_PROVIDERS.get(cmd.name)
    if provider is None:
        return []
    try:
        return provider(partial or "", ctx or {})
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Unknown-slash policy
# ---------------------------------------------------------------------------

def is_known_command(text: str) -> bool:
    """True when text is a `/…` command the palette knows.

    Phase B uses this to route unknown `/…` text to the model instead of
    swallowing it with "Unknown command". Mirrors `_slash`'s parsing:
    the head is everything between `/` and the first space, lowercased.
    """
    s = (text or "").strip()
    if not s.startswith("/") or len(s) < 2:
        return False
    head = s[1:].partition(" ")[0].strip().lower()
    return head in _BY_NAME
