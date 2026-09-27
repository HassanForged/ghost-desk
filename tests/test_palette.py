"""Tests for the `/` palette model (registry, filtering, arg completion)."""

import ast
from pathlib import Path

import pytest

from ghost_desk import palette
from ghost_desk.palette import (
    STUB_COMMANDS,
    CommandMatch,
    canonical,
    command_names,
    complete_arg,
    filter_commands,
    is_known_command,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TUI_PY = REPO_ROOT / "src" / "ghost_desk" / "tui.py"


def _slash_commands_from_source() -> set[str]:
    """Every literal command name tui._slash dispatches on.

    Parsed from source so the test never imports the TUI.
    """
    tree = ast.parse(TUI_PY.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            left_is_command = (
                isinstance(node.left, ast.Name) and node.left.id == "command"
            )
            if not left_is_command:
                continue
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                found.add(str(comparator.value))
            if isinstance(op, ast.In) and isinstance(comparator, ast.Set):
                for elt in comparator.elts:
                    if isinstance(elt, ast.Constant):
                        found.add(str(elt.value))
    return found


def test_registry_covers_every_slash_command():
    """A _slash command without a registry entry fails loudly — no drift."""
    slash_cmds = _slash_commands_from_source()
    assert slash_cmds, "expected to find dispatched commands in tui.py"
    missing = [c for c in sorted(slash_cmds) if canonical(c) is None]
    assert not missing, f"_slash handles {missing} with no palette entry"


def test_wired_registry_commands_exist_in_slash():
    """Non-stub registry entries must actually be handled by _slash."""
    slash_cmds = _slash_commands_from_source()
    for name in command_names():
        if name in STUB_COMMANDS:
            continue
        assert name in slash_cmds, f"/{name} registered but not in _slash"


def test_descriptions_are_ghost_voiced_lowercase():
    for cmd in palette.COMMANDS:
        assert cmd.description, f"/{cmd.name} needs a description"
        assert cmd.description == cmd.description.lower(), (
            f"/{cmd.name} description must be lowercase: {cmd.description!r}"
        )


def test_names_unique_and_aliases_resolve():
    names = command_names()
    assert len(names) == len(set(names)), "duplicate command names"
    assert canonical("exit") is canonical("quit")
    assert canonical("/RESUME") is canonical("resume")


# --- filtering ------------------------------------------------------------


def _names(matches: list[CommandMatch]) -> list[str]:
    return [m.command.name for m in matches]


def test_empty_query_returns_curated_order_capped():
    matches = filter_commands("/")
    assert len(matches) == 8
    assert _names(matches)[0] == "new"
    assert _names(matches) == [
        c.name for c in palette.COMMANDS[:8]
    ]
    assert filter_commands("") == filter_commands("/")


def test_prefix_beats_substring_beats_fuzzy():
    # "res" is a prefix of resume, a substring of personality ("...son")
    # is not involved; check ordering tiers explicitly instead.
    assert _names(filter_commands("res"))[0] == "resume"
    # substring: "son" appears inside "personality", not as a prefix
    assert _names(filter_commands("son"))[0] == "personality"
    # fuzzy: "rty" is not a prefix/substring of anything; retry matches
    # by characters-in-order
    assert _names(filter_commands("rty"))[0] == "retry"


def test_description_keyword_match():
    # "brain" lives in descriptions of model and setup; model is ordered first
    names = _names(filter_commands("brain"))
    assert names[0] == "model"
    assert "setup" in names


def test_case_insensitive_and_slash_prefix():
    assert _names(filter_commands("NEW"))[0] == "new"
    assert _names(filter_commands("/mod"))[0] == "model"


def test_alias_matches():
    assert _names(filter_commands("exi"))[0] == "quit"


def test_cap_of_eight():
    assert len(filter_commands("e")) == 8


def test_no_match_gives_empty():
    assert filter_commands("zzz-no-such-thing") == []


def test_match_tiers_are_sane():
    matches = {m.command.name: m.tier for m in filter_commands("mo")}
    assert matches["model"] == 0  # prefix
    # "mo" is a substring of promote ("proMOte")
    assert matches["promote"] == 1


# --- second-stage arg completion ------------------------------------------


def test_resume_completes_sessions():
    ctx = {"sessions": ["abc123", "def456"]}
    assert complete_arg("resume", "a", ctx) == ["abc123"]
    assert complete_arg("resume", "", ctx) == ["abc123", "def456"]
    assert complete_arg("resume", "z", ctx) == []
    assert complete_arg("resume", "", {}) == []


def test_skills_completes_names():
    ctx = {"skills": ["cook", "code-review"]}
    assert complete_arg("skills", "co", ctx) == ["cook", "code-review"]
    assert complete_arg("skills", "x", {}) == []


def test_model_current_first():
    ctx = {"models": ["grok-4.6", "llama3.2"], "current_model": "llama3.2"}
    assert complete_arg("model", "", ctx) == ["llama3.2", "grok-4.6"]
    assert complete_arg("model", "g", ctx) == ["grok-4.6"]
    assert complete_arg("model", "", {}) == []


def test_personality_lists_tones():
    tones = complete_arg("personality", "", {})
    assert set(tones) == {"helpful", "concise", "technical"}
    assert complete_arg("personality", "c", {}) == ["concise"]


def test_bg_subcommands():
    assert complete_arg("bg", "", {}) == ["list", "add", "run", "digest"]
    assert complete_arg("bg", "r", {}) == ["run"]


def test_access_leaves_picture():
    assert complete_arg("access", "", {}) == ["ask", "full"]
    assert complete_arg("access", "f", {}) == ["full"]
    assert complete_arg("leaves", "", {}) == ["on", "off"]
    assert complete_arg("picture", "k", {}) == ["kitty"]
    assert complete_arg("picture", "", {}) == ["kitty", "iterm2", "off"]


def test_unknown_command_or_no_provider_gives_empty():
    assert complete_arg("nope", "", {}) == []
    assert complete_arg("help", "", {}) == []
    assert complete_arg("/resume", "a", {"sessions": ["abc"]}) == ["abc"]


# --- unknown-slash policy -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["/resume abc123", "/model", "/QUIT", "/exit", "/bg add daily hi", "/setup"],
)
def test_known_commands(text):
    assert is_known_command(text)


@pytest.mark.parametrize(
    "text",
    ["/nope", "/", "hello", "", "/ resume", "   ", "/unknown arg here"],
)
def test_unknown_commands(text):
    assert not is_known_command(text)
