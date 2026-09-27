"""Full access mode: /access show/enable/revoke, persisted level, header indicator.

Pinning the safety contract: enabling requires the exact confirmation phrase,
the gate auto-approves only when full, background jobs keep their stored level.
"""

import io
from pathlib import Path

import pytest
from rich.console import Console

from ghost_desk.agent import DeskSession
from ghost_desk.background import run_due
from ghost_desk.config import Config, access_level, load_config, save_config
from ghost_desk.memory import Memory
from ghost_desk.permissions import PermissionGate
from ghost_desk.tui import (
    ACCESS_PHRASE,
    _confirm_access,
    _slash,
    header_status,
)


def _cfg(tmp_path, **over):
    kwargs = dict(
        api_key="k",
        base_url="http://example/v1",
        model="m",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
    )
    kwargs.update(over)
    return Config(**kwargs)


def _console():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=80), buf


def _slash_call(text, config, session, gate=None, tmp_path=None):
    console, buf = _console()
    memory = Memory(tmp_path / "mem")
    result = _slash(
        text,
        console=console,
        config=config,
        memory=memory,
        session=session,
        skills_root=tmp_path,
        gate=gate,
    )
    return result, buf.getvalue()


def test_default_is_ask(tmp_path):
    assert access_level(_cfg(tmp_path)) == "ask"


def test_access_shows_current_level(tmp_path):
    cfg = _cfg(tmp_path)
    _, out = _slash_call("/access", cfg, DeskSession(), tmp_path=tmp_path)
    assert "ask" in out


def test_full_requires_exact_phrase(tmp_path):
    cfg = _cfg(tmp_path)
    session = DeskSession()
    gate = PermissionGate(Path(tmp_path), full_access=False)
    result, out = _slash_call("/access full", cfg, session, gate=gate, tmp_path=tmp_path)
    assert result == "ok"
    assert session.access_pending is True
    assert access_level(cfg) == "ask"  # not enabled yet
    assert "i trust my ghost" in out

    # A bare "yes" must not enable it.
    consumed = _confirm_access("yes", console=_console()[0], config=cfg, session=session, gate=gate)
    assert consumed is False
    assert access_level(cfg) == "ask"
    assert session.access_pending is False
    assert gate.full_access is False


def test_full_enables_with_phrase_and_persists(tmp_path):
    cfg = _cfg(tmp_path)
    session = DeskSession()
    gate = PermissionGate(Path(tmp_path), full_access=False)
    _slash_call("/access full", cfg, session, gate=gate, tmp_path=tmp_path)
    console, buf = _console()
    consumed = _confirm_access(ACCESS_PHRASE, console=console, config=cfg, session=session, gate=gate)
    assert consumed is True
    assert access_level(cfg) == "full"
    assert gate.full_access is True
    assert "full access is on" in buf.getvalue()

    # Survives a config reload.
    reloaded = load_config(path=cfg.config_file())
    assert access_level(reloaded) == "full"


def test_phrase_is_case_insensitive_but_not_fuzzy(tmp_path):
    cfg = _cfg(tmp_path)
    session = DeskSession()
    _slash_call("/access full", cfg, session, tmp_path=tmp_path)
    assert _confirm_access("I Trust My Ghost", console=_console()[0], config=cfg, session=session, gate=None) is True

    cfg2 = _cfg(tmp_path)
    session2 = DeskSession()
    _slash_call("/access full", cfg2, session2, tmp_path=tmp_path)
    assert _confirm_access("i trust the ghost", console=_console()[0], config=cfg2, session=session2, gate=None) is False
    assert access_level(cfg2) == "ask"


def test_ask_revokes_immediately(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.access = "full"
    save_config(cfg)
    session = DeskSession()
    gate = PermissionGate(Path(tmp_path), full_access=True)
    result, out = _slash_call("/access ask", cfg, session, gate=gate, tmp_path=tmp_path)
    assert result == "ok"
    assert access_level(cfg) == "ask"
    assert gate.full_access is False
    assert "asking first" in out
    # Persisted too.
    assert access_level(load_config(path=cfg.config_file())) == "ask"


def test_gate_auto_approves_when_full(tmp_path):
    def _no_ask(_prompt):
        raise AssertionError("must not prompt in full access")

    gate = PermissionGate(tmp_path, ask=_no_ask, full_access=True)
    assert gate.check_write("brand-new-file.txt").allowed is True
    assert gate.check_read("../outside.txt").allowed is True
    # Even destructive shell is auto-approved; the model still decides.
    decision = gate.check_shell("rm -rf /tmp/something")
    assert decision.allowed is True
    assert decision.reason == "full access"


def test_gate_prompts_when_ask(tmp_path):
    asked = []
    gate = PermissionGate(tmp_path, ask=lambda q: asked.append(q) or False, full_access=False)
    assert gate.check_write("brand-new-file.txt").allowed is False
    assert asked, "ask mode must prompt"
    asked.clear()
    assert gate.check_shell("rm -rf /tmp/something").allowed is False
    assert asked, "destructive shell must prompt in ask mode"


def test_header_shows_full_access_indicator():
    assert header_status(busy=False, full_access=True) == "haunting · full access"
    assert header_status(busy=True, elapsed=4, full_access=True) == "rattling chains…  4s · full access"
    assert "full access" not in header_status(busy=False, full_access=False)
    assert "full access" not in header_status(busy=True, elapsed=4, full_access=False)


def test_job_stores_access_level(tmp_path):
    memory = Memory(tmp_path / "mem")
    memory.add_job("nightly", "0 0 * * *", "summarize", access="full")
    memory.add_job("daily", "0 0 * * *", "review", access="ask")
    rows = {row["name"]: row for row in memory.list_jobs()}
    assert rows["nightly"]["access"] == "full"
    assert rows["daily"]["access"] == "ask"


def test_run_due_passes_access_to_runner(tmp_path):
    memory = Memory(tmp_path / "mem")
    memory.add_job("nightly", "* * * * *", "summarize", access="full")
    seen = []
    ran = run_due(memory, lambda prompt, access: seen.append((prompt, access)) or "done")
    assert ran == ["nightly"]
    assert seen == [("summarize", "full")]


def test_run_due_still_works_with_old_single_arg_runner(tmp_path):
    memory = Memory(tmp_path / "mem")
    memory.add_job("nightly", "* * * * *", "summarize", access="full")
    ran = run_due(memory, lambda prompt: "pong")
    assert ran == ["nightly"]


def test_access_unknown_arg_shows_usage(tmp_path):
    _, out = _slash_call("/access everything", _cfg(tmp_path), DeskSession(), tmp_path=tmp_path)
    assert "usage" in out
