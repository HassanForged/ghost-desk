"""Core loop: environment, no regex intercepts, one assistant turn, keep working."""

from ghost_desk.agent import DeskSession, run_turn, system_prompt
from ghost_desk.client import ChatResponse, ToolCall
from ghost_desk.config import Config
from ghost_desk.environment import build_environment_hints
from ghost_desk.memory import Memory
from ghost_desk.permissions import PermissionGate
from ghost_desk.plan import PlanDraft


def _desk(tmp_path):
    cfg = Config(
        api_key="k",
        base_url="http://example/v1",
        model="m",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
    )
    memory = Memory(cfg.data_path())
    return cfg, memory


def test_environment_hints_name_the_host_and_forbid_guessing(tmp_path):
    hints = build_environment_hints(workspace=tmp_path, data_dir=tmp_path / "data")
    low = hints.lower()
    assert "workspace" in low
    assert str(tmp_path) in hints
    assert "never answer these from memory" in low
    assert "file_read" in low
    assert "shell" in low
    memory = Memory(tmp_path)
    try:
        text = system_prompt(DeskSession(), memory, "hi", workspace=tmp_path, data_dir=tmp_path / "data")
    finally:
        memory.close()
    assert "never answer these from memory" in text.lower()
    assert str(tmp_path) in text


def test_missing_file_question_reaches_the_model(tmp_path):
    cfg, memory = _desk(tmp_path)
    probe = {"called": False, "messages": None}

    class Probe:
        def complete(self, messages, tools=None, **kwargs):
            probe["called"] = True
            probe["messages"] = messages
            return ChatResponse(text="I will try to read it.")

    try:
        done = run_turn(
            "what is inside no-such-file.txt",
            config=cfg,
            memory=memory,
            session=DeskSession(),
            client=Probe(),
        )
    finally:
        memory.close()
    assert probe["called"] is True
    assert done.text == "I will try to read it."
    assert "is missing." not in done.text


def test_text_stop_does_not_append_a_second_assistant(tmp_path):
    cfg, memory = _desk(tmp_path)
    session = DeskSession()

    class Stub:
        def complete(self, messages, tools=None, **kwargs):
            return ChatResponse(text="pong")

    try:
        done = run_turn("what is 2 plus 2", config=cfg, memory=memory, session=session, client=Stub())
    finally:
        memory.close()
    roles = [m["role"] for m in session.history]
    assert roles == ["user", "assistant"]
    assert session.history[-1]["content"] == "pong"
    assert done.text == "pong"


def test_unverified_done_keeps_the_loop_going(tmp_path):
    cfg, memory = _desk(tmp_path)
    session = DeskSession()
    session.plan.draft = PlanDraft(
        request="build a one-page site as index.html",
        checklist=["index.html"],
        questions=[],
        needs_reference=False,
        reference_prompt="",
        steps=["write index.html"],
        status="approved",
    )
    client = _ScriptedWrite()
    try:
        done = run_turn(
            "write the page",
            config=cfg,
            memory=memory,
            session=session,
            client=client,
            gate=PermissionGate(tmp_path, ask=lambda _q: True),
        )
    finally:
        memory.close()
    assert client.n >= 2
    assert (tmp_path / "index.html").is_file()
    assert "<p>ok</p>" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert done.stop_reason == "stop"


class _ScriptedWrite:
    def __init__(self):
        self.n = 0

    def complete(self, messages, tools=None, **kwargs):
        self.n += 1
        if self.n == 1:
            return ChatResponse(text="done, wrote index.html")
        if self.n == 2:
            return ChatResponse(
                text="",
                tool_calls=[
                    ToolCall("1", "file_write", {"path": "index.html", "content": "<p>ok</p>"}),
                ],
            )
        return ChatResponse(text="page is on disk")


def test_system_prompt_stays_frozen_across_turns(tmp_path):
    cfg, memory = _desk(tmp_path)
    session = DeskSession()
    seen: list[str] = []

    class Probe:
        def complete(self, messages, tools=None, **kwargs):
            seen.append(messages[0]["content"])
            return ChatResponse(text="ok")

    try:
        run_turn("hello", config=cfg, memory=memory, session=session, client=Probe())
        memory.add_note(session.id, "config", "home page exists", "read")
        run_turn("what model is in the config", config=cfg, memory=memory, session=session, client=Probe())
    finally:
        memory.close()
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert "home page exists" not in seen[1]


def test_config_question_does_not_stuff_the_user_message(tmp_path):
    cfg, memory = _desk(tmp_path)
    captured: list[str] = []

    class Probe:
        def complete(self, messages, tools=None, **kwargs):
            captured.append(str(messages[-1].get("content") or ""))
            return ChatResponse(text="I will read the config file.")

    try:
        run_turn(
            "what model is in the config",
            config=cfg,
            memory=memory,
            session=DeskSession(),
            client=Probe(),
        )
    finally:
        memory.close()
    assert captured[0] == "what model is in the config"
