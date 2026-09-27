"""Tests for the self-update flow: detection, the /update command, and the show."""

import json
import subprocess

import pytest

from ghost_desk import palette, update
from ghost_desk.update import (
    DISSOLVE_TICKS,
    REMATERIALIZE_TICKS,
    UpdateResult,
    UpdateSequence,
    install_update,
    installed_commit,
    newer_available,
    remote_head,
    start_update_check,
    update_available,
)

NEW = "a" * 40
OLD = "b" * 40


# ---------------------------------------------------------------------------
# Commit comparison
# ---------------------------------------------------------------------------


def test_newer_available_on_different_commits():
    assert newer_available(OLD, NEW) is True


def test_newer_available_on_older_remote():
    # Hashes carry no ordering; any difference against published HEAD counts.
    assert newer_available(NEW, OLD) is True


def test_newer_available_equal_is_quiet():
    assert newer_available(NEW, NEW) is False


def test_newer_available_silent_when_unparseable():
    assert newer_available(None, NEW) is False
    assert newer_available(OLD, None) is False
    assert newer_available("", "") is False
    assert newer_available("not-a-sha", NEW) is False
    assert newer_available(OLD, "zzz") is False
    assert newer_available("short", "alsoshort") is False


def test_newer_available_case_insensitive():
    assert newer_available(OLD.upper(), OLD) is False


# ---------------------------------------------------------------------------
# installed_commit
# ---------------------------------------------------------------------------


class _FakeDist:
    def __init__(self, payload):
        self._payload = payload

    def read_text(self, name):
        return self._payload


def _patch_distribution(monkeypatch, payload):
    import importlib.metadata

    def fake(name):
        assert name == "ghost-desk"
        return _FakeDist(payload)

    monkeypatch.setattr(importlib.metadata, "distribution", fake)


def test_installed_commit_reads_direct_url(monkeypatch):
    payload = json.dumps({"vcs_info": {"vcs": "git", "commit_id": NEW}})
    _patch_distribution(monkeypatch, payload)
    assert installed_commit() == NEW


def test_installed_commit_missing_or_broken_is_silent(monkeypatch):
    import importlib.metadata

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", missing)
    assert installed_commit() is None

    _patch_distribution(monkeypatch, None)
    assert installed_commit() is None
    _patch_distribution(monkeypatch, "not json{")
    assert installed_commit() is None
    _patch_distribution(monkeypatch, json.dumps({"url": "file:///x"}))
    assert installed_commit() is None
    _patch_distribution(monkeypatch, json.dumps({"vcs_info": {"commit_id": "nope"}}))
    assert installed_commit() is None


# ---------------------------------------------------------------------------
# remote_head
# ---------------------------------------------------------------------------


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_remote_head_parses_ls_remote(monkeypatch):
    monkeypatch.setattr(
        update.subprocess, "run",
        lambda *a, **k: _completed(f"{NEW}\tHEAD\n"),
    )
    assert remote_head(timeout=1) == NEW


def test_remote_head_silent_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no route to host")

    monkeypatch.setattr(update.subprocess, "run", boom)
    assert remote_head(timeout=1) is None


def test_remote_head_silent_on_timeout(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(update.subprocess, "run", slow)
    assert remote_head(timeout=1) is None


def test_remote_head_silent_on_git_failure_or_garbage(monkeypatch):
    monkeypatch.setattr(
        update.subprocess, "run", lambda *a, **k: _completed("", returncode=128, stderr="boom")
    )
    assert remote_head(timeout=1) is None
    monkeypatch.setattr(
        update.subprocess, "run", lambda *a, **k: _completed("garbage\n")
    )
    assert remote_head(timeout=1) is None


# ---------------------------------------------------------------------------
# update_available: the tap on the shoulder
# ---------------------------------------------------------------------------


def test_update_available_returns_remote_when_new(monkeypatch):
    monkeypatch.setattr(update, "remote_head", lambda timeout=10: NEW)
    monkeypatch.setattr(update, "installed_commit", lambda: OLD)
    assert update_available() == NEW


def test_update_available_quiet_when_equal(monkeypatch):
    monkeypatch.setattr(update, "remote_head", lambda timeout=10: NEW)
    monkeypatch.setattr(update, "installed_commit", lambda: NEW)
    assert update_available() is None


def test_update_available_quiet_when_unknown(monkeypatch):
    monkeypatch.setattr(update, "remote_head", lambda timeout=10: NEW)
    monkeypatch.setattr(update, "installed_commit", lambda: None)
    assert update_available() is None


def test_update_available_silent_on_any_error(monkeypatch):
    def boom(timeout=10):
        raise RuntimeError("network is lava")

    monkeypatch.setattr(update, "remote_head", boom)
    assert update_available() is None


def test_checker_stays_silent_on_error(monkeypatch):
    monkeypatch.setattr(update, "update_available", lambda timeout=10: (_ for _ in ()).throw(OSError("down")))
    calls = []
    checker = start_update_check(calls.append, delay=0, timeout=1)
    checker.join(timeout=5)
    assert calls == []


def test_checker_fires_once_on_update(monkeypatch):
    monkeypatch.setattr(update, "update_available", lambda timeout=10: NEW)
    calls = []
    checker = start_update_check(calls.append, delay=0, timeout=1)
    checker.join(timeout=5)
    assert calls == [NEW]


# ---------------------------------------------------------------------------
# install_update: pip runs hidden, failures surface the real error
# ---------------------------------------------------------------------------


def test_install_update_failure_surfaces_pip_error(monkeypatch):
    noise = "\n".join(f"noise line {i}" for i in range(20))
    stderr = noise + "\nERROR: Could not find a version that satisfies the requirement ghost-desk\n"
    monkeypatch.setattr(
        update.subprocess, "run",
        lambda *a, **k: _completed("", returncode=1, stderr=stderr),
    )
    result = install_update(timeout=1)
    assert result.ok is False
    assert "Could not find a version" in result.error
    assert "noise line 0" not in result.error  # only the tail


def test_install_update_failure_when_pip_wont_start(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no pip")

    monkeypatch.setattr(update.subprocess, "run", boom)
    result = install_update(timeout=1)
    assert result.ok is False
    assert result.error


def test_install_update_success_reports_new_commit(monkeypatch):
    monkeypatch.setattr(
        update.subprocess, "run", lambda *a, **k: _completed("ok\n")
    )
    monkeypatch.setattr(update, "installed_commit", lambda: NEW)
    result = install_update(timeout=1)
    assert result.ok is True
    assert result.commit == NEW


# ---------------------------------------------------------------------------
# The /update show: dissolve -> stitch -> re-materialize
# ---------------------------------------------------------------------------


def test_sequence_dissolve_then_stitch():
    seq = UpdateSequence()
    assert seq.phase == "dissolve"
    for _ in range(DISSOLVE_TICKS - 1):
        assert seq.tick() is False
        assert seq.phase == "dissolve"
    assert seq.tick() is False
    assert seq.phase == "stitch"


def test_sequence_waits_for_pip_in_stitch():
    seq = UpdateSequence()
    for _ in range(DISSOLVE_TICKS):
        seq.tick()
    for _ in range(10):
        assert seq.tick() is False
        assert seq.phase == "stitch"
    seq.result = UpdateResult(ok=True, commit=NEW)
    assert seq.tick() is False
    assert seq.phase == "rematerialize"


def test_sequence_finishes_after_rematerialize():
    seq = UpdateSequence()
    seq.result = UpdateResult(ok=True, commit=NEW)
    for _ in range(DISSOLVE_TICKS + 1 + REMATERIALIZE_TICKS - 1):
        assert seq.tick() is False
    assert seq.tick() is True
    assert seq.finished is True
    assert seq.tick() is True  # stays finished


def test_sequence_success_lines_carry_hash_and_restart_note():
    seq = UpdateSequence()
    seq.result = UpdateResult(ok=True, commit=NEW)
    lines = seq.final_lines()
    text = " ".join(t for _, t in lines)
    assert f"({NEW[:7]})" in text
    assert "restart me to wear the new sheets." in text
    assert len(lines) == 2  # no flex, no extra theater


def test_sequence_failure_lines_use_error_path_and_pip_tail():
    seq = UpdateSequence()
    seq.result = UpdateResult(ok=False, error="ERROR: pip exploded")
    lines = seq.final_lines()
    assert len(lines) == 1
    role, text = lines[0]
    assert text.startswith("something moved in the dark: ")
    assert "ERROR: pip exploded" in text


def test_dissolve_frac_ramps():
    seq = UpdateSequence()
    assert seq.dissolve_frac == 1 / DISSOLVE_TICKS
    for _ in range(DISSOLVE_TICKS):
        seq.tick()
    assert seq.phase == "stitch"
    assert seq.dissolve_frac == 1.0
    seq.result = UpdateResult(ok=True, commit=NEW)
    seq.tick()
    assert seq.phase == "rematerialize"
    assert 0.0 < seq.dissolve_frac < 1.0


def test_dither_out_is_seeded_and_bounded():
    from ghost_desk.tui import _dither_out

    rows = [[("s", "█"), ("s", " "), ("s", "█")] for _ in range(4)]
    assert _dither_out(rows, 0.0) == rows
    gone = _dither_out(rows, 1.0)
    assert all(text == " " for row in gone for _, text in row)
    assert _dither_out(rows, 0.5) == _dither_out(rows, 0.5)
    assert _dither_out(rows, 0.5) != _dither_out(rows, 0.5, seed=1234)


def test_stitch_bar_exact_width_block_chars_only():
    from ghost_desk.tui import _stitch_bar

    for frame in range(40):
        bar = _stitch_bar(frame, 22)
        text = "".join(t for _, t in bar)
        assert len(text) == 22
        assert set(text) <= {"█", "░"}
        assert "█" in text  # always some fill


# ---------------------------------------------------------------------------
# /update in the palette registry and _slash
# ---------------------------------------------------------------------------


def test_update_in_registry():
    cmd = palette.canonical("update")
    assert cmd is not None
    assert cmd.description == "fetch the latest haunting"
    assert cmd.needs_arg is False
    assert "update" in palette.command_names()


class _FakeConsole:
    def __init__(self):
        self.printed = []

    def print(self, *args, **_kwargs):
        self.printed.append(" ".join(str(a) for a in args))


def _slash_fixtures(tmp_path):
    from ghost_desk.agent import DeskSession
    from ghost_desk.config import Config
    from ghost_desk.memory import Memory

    cfg = Config(
        provider="p",
        api_key="k",
        base_url="http://example/v1",
        model="m",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
    )
    memory = Memory(cfg.data_path())
    session = DeskSession()
    return cfg, memory, session


def test_slash_update_success_prints_hash_and_restart_note(tmp_path, monkeypatch):
    from ghost_desk import tui

    monkeypatch.setattr(
        tui.update_flow, "install_update",
        lambda timeout=600.0: UpdateResult(ok=True, commit=NEW),
    )
    cfg, memory, session = _slash_fixtures(tmp_path)
    console = _FakeConsole()
    try:
        assert tui._slash("/update", console=console, config=cfg, memory=memory,
                          session=session, skills_root=tmp_path) == "ok"
    finally:
        memory.close()
    text = "\n".join(console.printed)
    assert "hold still…" in text
    assert "stitching new sheets…" in text
    assert f"back. good as new. ({NEW[:7]})" in text
    assert "restart me to wear the new sheets." in text


def test_slash_update_failure_surfaces_pip_error(tmp_path, monkeypatch):
    from ghost_desk import tui

    monkeypatch.setattr(
        tui.update_flow, "install_update",
        lambda timeout=600.0: UpdateResult(ok=False, error="ERROR: pip exploded"),
    )
    cfg, memory, session = _slash_fixtures(tmp_path)
    console = _FakeConsole()
    try:
        assert tui._slash("/update", console=console, config=cfg, memory=memory,
                          session=session, skills_root=tmp_path) == "ok"
    finally:
        memory.close()
    text = "\n".join(console.printed)
    assert "something moved in the dark: " in text
    assert "ERROR: pip exploded" in text


def test_slash_update_uses_animated_ui_when_provided(tmp_path):
    from ghost_desk import tui

    cfg, memory, session = _slash_fixtures(tmp_path)
    try:
        out = tui._slash("/update", console=_FakeConsole(), config=cfg, memory=memory,
                         session=session, skills_root=tmp_path,
                         update_ui=lambda: "animated")
    finally:
        memory.close()
    assert out == "animated"


def test_slash_update_drift_covered():
    # The AST drift test in test_palette.py walks tui._slash for literals;
    # this pins the literal the registry entry must match.
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).parent.parent / "src" / "ghost_desk" / "tui.py").read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "command":
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(comp, ast.Constant):
                    found.add(str(comp.value))
    assert "update" in found
    assert palette.canonical("update") is not None
