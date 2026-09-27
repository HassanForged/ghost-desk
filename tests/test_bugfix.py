"""Regression tests for the September 2026 bug-fix pass (critical → low).

Each test pins one numbered fix so it cannot silently regress.
"""

import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from ghost_desk import gateway as gateway_module
from ghost_desk.agent import (
    DeskSession,
    _refuse_done_without_files,
    run_turn,
    verified_notes_block,
)
from ghost_desk.background import observe_message, run_due
from ghost_desk.client import ChatResponse, ToolCall
from ghost_desk.compact import Compactor
from ghost_desk.config import Config, _env_map, _flat_yaml
from ghost_desk.context import make_fallback, remember
from ghost_desk.gateway import GatewayError, TelegramChannel, serve
from ghost_desk.memory import Memory
from ghost_desk.notes import record_verified
from ghost_desk.permissions import PermissionGate, shell_kind
from ghost_desk.providers.anthropic import _split as anthropic_split
from ghost_desk.skills import load_all
from ghost_desk.subagents import Handoff, write_handoff
from ghost_desk.tools import execute
from ghost_desk.tui import _Log, _answer_pending, _slash, _visible_lines
from ghost_desk.verify import Check, VerificationReport


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


class _StubClient:
    """Scripted model: each complete() pops the next canned response."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


# --- 1. repeated file writes ------------------------------------------------

def test_repeated_file_write_does_not_stay_claimed(tmp_path):
    gate = PermissionGate(tmp_path, ask=lambda _q: True)
    first = execute("file_write", {"path": "note.txt", "content": "one"}, gate)
    assert first.ok
    second = execute("file_write", {"path": "note.txt", "content": "two"}, gate)
    assert second.ok, second.check.line() if second.check else "no check"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "two"


def test_failed_write_releases_claim(tmp_path):
    gate = PermissionGate(tmp_path, ask=lambda _q: True)
    (tmp_path / "boom").mkdir()
    with pytest.raises(Exception):
        execute("file_write", {"path": "boom", "content": "x"}, gate)
    # The path must not stay claimed after the crash.
    assert "boom" not in gate.claimed


# --- 2. tool exceptions ------------------------------------------------------

def test_tool_crash_becomes_failed_outcome_not_dead_turn(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    (tmp_path / "boom").mkdir()  # write_text on a directory raises inside execute
    client = _StubClient(
        ChatResponse(text="", tool_calls=[ToolCall(id="1", name="file_write", arguments={"path": "boom", "content": "x"})]),
        ChatResponse(text="recovered"),
    )
    gate = PermissionGate(tmp_path, ask=lambda _q: True)
    result = run_turn("write it", config=cfg, memory=memory, session=DeskSession(), client=client, gate=gate)
    assert "recovered" in result.text, "the turn must continue after a tool crash"
    crashed = [c for c in result.report.checks if not c.ok]
    assert crashed and "tool crashed" in crashed[0].conclusion
    memory.close()


# --- 3. telegram resilience ---------------------------------------------------

def test_poll_gateway_error_retries_then_recovers(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(gateway_module.time, "sleep", sleeps.append)
    attempts = {"n": 0}

    def http(method, payload):
        if method == "getUpdates":
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise GatewayError("network down")
            return []
        raise AssertionError("no sends expected")

    channel = TelegramChannel("test-token", http=http, timeout=0)
    cfg = _cfg(tmp_path)
    lines = []
    code = serve(cfg, channel=channel, allowed={"5"}, client=_StubClient(), max_rounds=3, output=lines.append)
    assert code == 0
    assert attempts["n"] == 3
    assert sleeps == [30.0, 30.0]
    assert "test-token" not in "\n".join(lines)


def test_poll_gives_up_after_ten_failures(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(gateway_module.time, "sleep", sleeps.append)

    def http(method, payload):
        raise GatewayError("still down")

    channel = TelegramChannel("test-token", http=http, timeout=0)
    cfg = _cfg(tmp_path)
    code = serve(cfg, channel=channel, allowed={"5"}, client=_StubClient(), max_rounds=50, output=lambda _s: None)
    assert code == 2
    assert len(sleeps) == 10  # slept after failures 1..10, exit on 11


def test_update_handler_error_does_not_kill_loop(tmp_path):
    def http(method, payload):
        if method == "getUpdates":
            return [{"update_id": 9, "message": {"text": "hi", "from": {"id": 5}, "chat": {"id": 9, "type": "private"}}}]
        if method == "sendMessage":
            return {"message_id": 1}
        raise AssertionError(method)

    class Boom:
        def complete(self, *args, **kwargs):
            raise RuntimeError("kaboom")

    channel = TelegramChannel("test-token", http=http, timeout=0)
    cfg = _cfg(tmp_path)
    lines = []
    code = serve(cfg, channel=channel, allowed={"5"}, client=Boom(), max_rounds=1, output=lines.append)
    assert code == 0
    assert any("kaboom" in line for line in lines)
    assert "test-token" not in "\n".join(lines)


# --- 4. permission shell escapes ----------------------------------------------

def test_shell_kind_traversal_is_outside():
    assert shell_kind("cat ../../secret.txt", Path("/work")) == "outside"


def test_shell_kind_command_substitution_is_write():
    # Substitution without an absolute outside path: at least write/prompt, never readonly.
    assert shell_kind("echo $(whoami)", Path("/work")) == "write"
    assert shell_kind("echo `whoami`", Path("/work")) == "write"
    # With an absolute path the kind is outside, which also prompts.
    assert shell_kind("echo $(cat /etc/passwd)", Path("/work")) == "outside"


def test_traversal_still_prompts_via_gate(tmp_path):
    asked = []
    gate = PermissionGate(tmp_path, ask=lambda q: asked.append(q) or False)
    decision = gate.check_shell("cat ../../secret.txt")
    assert not decision.allowed
    assert asked, "traversal must prompt, not silently pass as readonly"


# --- 5. anthropic consecutive tool results -------------------------------------

def test_anthropic_merges_consecutive_tool_messages():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "file_read", "arguments": "{}"}},
            {"id": "b", "function": {"name": "file_read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "out a"},
        {"role": "tool", "tool_call_id": "b", "content": "out b"},
        {"role": "assistant", "content": "done"},
    ]
    _system, converted = anthropic_split(messages)
    tool_blocks = [m for m in converted if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(tool_blocks) == 1
    assert [b["tool_use_id"] for b in tool_blocks[0]["content"]] == ["a", "b"]
    # Role alternation preserved: no two user messages in a row.
    roles = [m["role"] for m in converted]
    assert all(a != b for a, b in zip(roles, roles[1:]))


# --- 6. streaming usage --------------------------------------------------------

def test_streaming_requests_usage_from_api():
    from ghost_desk.client import OpenAIChatClient

    seen = []

    class FakeSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    seen.append(kwargs)
                    return iter(())

    client = OpenAIChatClient(Config(api_key="k", model="m"), sdk=FakeSDK(), attempts=1)
    client.complete([{"role": "user", "content": "hi"}], stream=True)
    assert seen[0]["stream"] is True
    assert seen[0]["stream_options"] == {"include_usage": True}


# --- 7. compactor wait ----------------------------------------------------------

def test_compactor_wait_joins_background_thread():
    def slow(_messages):
        time.sleep(0.3)
        return "summary"

    compactor = Compactor(window_tokens=20, threshold=0.5, tail=1)
    messages = [
        {"role": "user", "content": "alpha " * 40},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "beta"},
    ]
    compactor.maybe_start(messages, slow, sync=False)
    assert compactor.compacting
    notes = []
    started = time.monotonic()
    compactor.wait_if_needed(messages, notes.append, timeout=5)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.2, "wait_if_needed must block until the summarizer finishes"
    assert notes == ["compacting memory"]


# --- 8. query-relevant notes -----------------------------------------------------

def test_notes_block_empty_for_trivial_text(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    assert verified_notes_block(memory, "") == ""
    assert verified_notes_block(memory, "ok") == ""
    memory.close()


def test_notes_block_matches_current_query(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    memory.add_note("s1", "desk", "the deploy key lives in ~/.ssh/deploy", "checked")
    memory.add_note("s1", "desk", "buy milk tomorrow", "checked")
    block = verified_notes_block(memory, "where is the deploy key stored")
    assert "deploy" in block
    assert "milk" not in block
    memory.close()


# --- 10. fallback base url -------------------------------------------------------

def test_fallback_uses_own_base_url(tmp_path, monkeypatch):
    cfg = _cfg(
        tmp_path,
        provider="xai",
        base_url="https://primary.example/v1",
        fallback_provider="openrouter",
        fallback_model="m2",
    )
    monkeypatch.setenv("GHOST_FALLBACK_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("GHOST_FALLBACK_API_KEY", "fallback-key")
    captured = {}

    def fake_build(alt):
        captured["config"] = alt
        return "client"

    monkeypatch.setattr("ghost_desk.providers.build_client", fake_build)
    assert make_fallback(cfg) == "client"
    alt = captured["config"]
    assert alt.base_url == "https://openrouter.ai/api/v1"
    assert alt.api_key == "fallback-key"
    assert alt.provider == "openrouter"


def test_fallback_does_not_inherit_primary_base_url(tmp_path, monkeypatch):
    cfg = _cfg(
        tmp_path,
        provider="xai",
        base_url="https://primary.example/v1",
        fallback_provider="openrouter",
        fallback_model="m2",
    )
    monkeypatch.delenv("GHOST_FALLBACK_BASE_URL", raising=False)
    captured = {}

    def fake_build(alt):
        captured["config"] = alt
        return "client"

    monkeypatch.setattr("ghost_desk.providers.build_client", fake_build)
    make_fallback(cfg)
    assert captured["config"].base_url is None


# --- 11. background jobs keep their own session ----------------------------------

def test_background_turn_does_not_touch_current_session(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    marker = cfg.data_path() / "current_session.txt"
    marker.write_text("main-session", encoding="utf-8")
    session = DeskSession()
    run_turn("hi", config=cfg, memory=memory, session=session,
             client=_StubClient(ChatResponse(text="ok")), persist_session=False)
    assert marker.read_text(encoding="utf-8") == "main-session"
    assert session.id != "main-session"
    memory.close()


# --- 12. fact repair needs permission ---------------------------------------------

def test_repair_refused_leaves_file_untouched(tmp_path):
    from ghost_desk.verify import repair_near_facts

    target = tmp_path / "note.txt"
    target.write_text("contact me at bob@gmial.com\n", encoding="utf-8")
    gate = PermissionGate(tmp_path, ask=lambda _q: False)
    repaired = repair_near_facts(["bob@gmail.com"], tmp_path, gate=gate)
    assert repaired == []
    assert "gmial" in target.read_text(encoding="utf-8")


def test_repair_allowed_fixes_typo(tmp_path):
    from ghost_desk.verify import repair_near_facts

    target = tmp_path / "note.txt"
    target.write_text("contact me at bob@gmial.com\n", encoding="utf-8")
    gate = PermissionGate(tmp_path, ask=lambda _q: True)
    repaired = repair_near_facts(["bob@gmail.com"], tmp_path, gate=gate)
    assert repaired == [str(target)]
    assert "gmial" not in target.read_text(encoding="utf-8")


# --- 13. lines race -----------------------------------------------------------------

def test_chat_log_survives_concurrent_writes():
    import threading

    lock = threading.Lock()
    lines: list = []
    log = _Log(lines, lambda: None, lock)

    def hammer(n):
        for i in range(200):
            log.print(f"worker {n} line {i}")

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(lines) == 800


def test_chat_log_replace_swaps_transcript():
    lines = [("you", "old"), ("ghost", "old reply")]
    log = _Log(lines, lambda: None, threading.Lock())
    log.replace(_visible_lines([
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]))
    assert lines == [("you", "new question"), ("ghost", "new answer")]


# --- 14. local-time cron ----------------------------------------------------------------

def test_run_due_fires_on_local_midnight(tmp_path, monkeypatch):
    import ghost_desk.background as bg

    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    memory.add_job("daily ping", "0 0 * * *", "ping")
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(bg, "_local_now", lambda: local_midnight)
    ran = run_due(memory, lambda prompt: "pong")
    assert ran == ["daily ping"]
    memory.close()


# --- 15. completion heuristic --------------------------------------------------------------

def test_refuse_done_ignores_user_saying_done(tmp_path):
    reply = _refuse_done_without_files("done", "here is the summary", tmp_path)
    assert reply == "here is the summary"


def test_refuse_done_ignores_unmentioned_files(tmp_path):
    target = tmp_path / "unrelated.txt"
    target.write_text("x", encoding="utf-8")
    reply = _refuse_done_without_files("write report.txt", "done", tmp_path)
    assert reply == "done", "must not refuse when the reply names no missing file"


# --- 16. nudge budget resets per turn -------------------------------------------------------

def test_nudge_counter_resets_each_turn(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    session = DeskSession()
    session.unfinished_nudges = 2
    run_turn("hi", config=cfg, memory=memory, session=session,
             client=_StubClient(ChatResponse(text="ok")))
    assert session.unfinished_nudges == 0
    memory.close()


# --- 17. recurring scan is periodic ----------------------------------------------------------

def test_flag_recurring_runs_every_tenth_message(tmp_path, monkeypatch):
    import ghost_desk.background as bg

    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    calls = []
    monkeypatch.setattr(memory, "flag_recurring", lambda now, **_k: calls.append(now))
    monkeypatch.setattr(bg, "_observe_calls", 0)
    for i in range(20):
        observe_message(memory, f"substantive message number {i} about deployments")
    assert len(calls) == 2
    memory.close()


# --- 18. permission memoization -----------------------------------------------------------------

def test_gate_reused_across_turns_asks_once(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    asked = []
    gate = PermissionGate(tmp_path, ask=lambda q: asked.append(q) or True)
    session = DeskSession()
    script = [
        ChatResponse(text="", tool_calls=[ToolCall(id="1", name="file_write", arguments={"path": "a.txt", "content": "1"})]),
        ChatResponse(text="first done"),
        ChatResponse(text="", tool_calls=[ToolCall(id="2", name="file_write", arguments={"path": "a.txt", "content": "2"})]),
        ChatResponse(text="second done"),
    ]
    run_turn("write a", config=cfg, memory=memory, session=session, client=_StubClient(*script[:2]), gate=gate)
    run_turn("rewrite a", config=cfg, memory=memory, session=session, client=_StubClient(*script[2:]), gate=gate)
    assert session.gate is gate
    assert len(asked) == 1, f"approved path must not re-prompt; asked={asked}"
    memory.close()


# --- 19. handoff filenames ------------------------------------------------------------------------

def test_handoff_filenames_never_collide(tmp_path):
    paths = {write_handoff(Handoff(done=["x"]), tmp_path, "task") for _ in range(5)}
    assert len(paths) == 5


# --- 20. atomic remember -----------------------------------------------------------------------------

def test_remember_survives_concurrent_writes(tmp_path):
    errors = []

    def worker(n):
        try:
            for i in range(20):
                remember(tmp_path, "memory", f"fact {n}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    lines = (tmp_path / "MEMORY.md").read_text(encoding="utf-8").splitlines()
    bullets = [line for line in lines if line.startswith("- ")]
    assert len(bullets) == 80, "no interleaved write may lose a fact"
    assert len(set(bullets)) == 80


# --- 21. env var case safety --------------------------------------------------------------------------

def test_env_map_ignores_wrong_case_bare_names(monkeypatch):
    monkeypatch.setenv("MODEL", "stray-model")
    assert "model" not in _env_map()


def test_env_map_prefers_ghost_prefix(monkeypatch):
    monkeypatch.setenv("model", "bare")
    monkeypatch.setenv("GHOST_MODEL", "prefixed")
    assert _env_map()["model"] == "prefixed"


# --- 22. yaml comments -----------------------------------------------------------------------------------

def test_flat_yaml_keeps_hash_inside_values():
    parsed = _flat_yaml('api_key: "sk-abc#123"\nmodel: grok # the brain\n')
    assert parsed["api_key"] == "sk-abc#123"
    assert parsed["model"] == "grok"


# --- 23. ollama tool degradation -------------------------------------------------------------------------

def test_ollama_retries_without_tools_on_tool_400():
    from ghost_desk.client import OpenAIChatClient
    from ghost_desk.providers.ollama import Ollama

    def ok_payload(text):
        message = type("M", (), {"content": text, "tool_calls": None})()
        choice = type("C", (), {"message": message})()
        payload = type("R", (), {})()
        payload.choices = [choice]
        payload.usage = None
        return payload

    seen = []

    class FakeSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    seen.append(kwargs)
                    if kwargs.get("tools"):
                        raise RuntimeError("400 tools not supported by this model")
                    return ok_payload("plain answer")

    cfg = Config(provider="ollama", model="llama3")
    result = Ollama(cfg, sdk=FakeSDK()).chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "file_read"}}],
        stream=False,
    )
    assert result.text == "plain answer"
    assert len(seen) == 2
    assert "tools" not in seen[1]


def test_ollama_does_not_retry_unrelated_400():
    from ghost_desk.providers.ollama import Ollama
    from ghost_desk.client import ClientError

    class FakeSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("400 bad request: malformed json")

    cfg = Config(provider="ollama", model="llama3")
    with pytest.raises(ClientError):
        Ollama(cfg, sdk=FakeSDK()).chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}], stream=False)


# --- 24. malformed skills ----------------------------------------------------------------------------------

def test_load_all_skips_malformed_skill(capsys, tmp_path):
    (tmp_path / "good.md").write_text("---\nname: good\ndescription: fine\n---\nbody\n", encoding="utf-8")
    (tmp_path / "bad.md").write_text("---\nname: bad\ndescription: never closes\n", encoding="utf-8")
    skills = load_all(tmp_path)
    assert [s.name for s in skills] == ["good"]
    assert "bad.md" in capsys.readouterr().err


# --- 25. notes noise ------------------------------------------------------------------------------------------

def test_record_verified_skips_read_only_turns(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    report = VerificationReport()
    report.add(Check("file_read", "a.txt", "contents", True, "read ok"))
    assert record_verified(memory, "s1", report, "desk") is None
    memory.close()


def test_record_verified_keeps_write_turns(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    report = VerificationReport()
    report.add(Check("file_write", "a.txt", "wrote", True, "wrote a.txt"))
    note_id = record_verified(memory, "s1", report, "desk")
    assert note_id is not None
    memory.close()


# --- 26/27. /new and /resume rebuild the visible chat ------------------------------------------------------------

class _FakeConsole:
    def __init__(self):
        self.printed = []
        self.replaced = None

    def print(self, *args, **_kwargs):
        self.printed.append(" ".join(str(a) for a in args))

    def replace(self, entries):
        self.replaced = list(entries)


def test_slash_new_clears_visible_lines(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    session = DeskSession()
    session.history = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    console = _FakeConsole()
    assert _slash("/new", console=console, config=cfg, memory=memory, session=session, skills_root=tmp_path) == "ok"
    assert session.history == []
    assert console.replaced == []
    memory.close()


def test_slash_resume_rebuilds_visible_lines(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())
    session = DeskSession()
    history = [
        {"role": "user", "content": "resumed question"},
        {"role": "assistant", "content": "resumed answer"},
    ]
    memory.add_message("abc123", "user", "resumed question", {"role": "user", "content": "resumed question"})
    memory.add_message("abc123", "assistant", "resumed answer", {"role": "assistant", "content": "resumed answer"})
    console = _FakeConsole()
    out = _slash("/resume abc123", console=console, config=cfg, memory=memory, session=session, skills_root=tmp_path)
    assert out == "ok"
    assert session.history == history
    assert console.replaced == [("you", "resumed question"), ("ghost", "resumed answer")]
    memory.close()


# --- 28. cancellation ----------------------------------------------------------------------------------------------

def test_cancelled_turn_never_calls_model(tmp_path):
    cfg = _cfg(tmp_path)
    memory = Memory(cfg.data_path())

    class Boom:
        def complete(self, *args, **kwargs):
            raise AssertionError("cancelled turn must not reach the model")

    result = run_turn("hi", config=cfg, memory=memory, session=DeskSession(),
                      client=Boom(), cancelled=lambda: True)
    assert result.stop_reason == "cancelled"
    memory.close()


# --- 29. stale permission events --------------------------------------------------------------------------------------

def test_expired_permission_event_cannot_be_answered_late():
    event = threading.Event()
    pending = {"event": event, "yes": False}
    # The timeout path expires the prompt before the user answers.
    pending["event"] = None
    pending["yes"] = False
    assert not _answer_pending(pending, "y")
    assert pending["yes"] is False
    assert not event.is_set()


def test_live_permission_event_answers_once():
    event = threading.Event()
    pending = {"event": event, "yes": False}
    assert _answer_pending(pending, "yes")
    assert event.is_set() and pending["yes"] is True and pending["event"] is None
    # A second answer after expiry is ignored.
    assert not _answer_pending(pending, "y")
