"""Context files, mentions, lessons, session recall, and provider fallback."""

from ghost_desk.agent import DeskSession, run_turn
from ghost_desk.client import ChatResponse, ClientError
from ghost_desk.config import Config
from ghost_desk.context import (
    draft_lesson,
    ensure_soul,
    expand_mentions,
    fallback_worthy,
    load_context,
    promote_lesson,
)
from ghost_desk.memory import Memory


def test_conversation_prompt_talks_to_the_person(tmp_path):
    from ghost_desk.agent import DeskSession, system_prompt

    memory = Memory(tmp_path)
    try:
        session = DeskSession()
        text = system_prompt(session, memory, "hi")
        assert "conversation" in text.lower()
        assert "I ran X" not in text
        session.personality = "concise"
        assert "brief" in system_prompt(session, memory, "hi").lower()
    finally:
        memory.close()


def test_side_portrait_uses_the_photo_and_moves_while_working():
    from ghost_desk.face import HOOD, activity_for, render_blocks

    assert HOOD.is_file()
    still = render_blocks(bob=0)
    moving = render_blocks(bob=1)
    assert still and still[0][0][1] != ""
    assert len(moving) == len(still) + 1
    assert activity_for("tool web_search") == "searching"
    assert activity_for("tool file_read") == "reading"
    assert activity_for("tool file_write") == "working"
    assert activity_for("ready") == "idle"


def test_project_context_prefers_ghost_file_and_soul_is_kept(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("use pytest\n", encoding="utf-8")
    (workspace / ".ghost.md").write_text("desk rules win\n", encoding="utf-8")
    soul = ensure_soul(data)
    soul.write_text("# Soul\nBe brief.\n", encoding="utf-8")
    ensure_soul(data)
    assert soul.read_text(encoding="utf-8") == "# Soul\nBe brief.\n"
    loaded = load_context(data, workspace)
    assert "Be brief." in loaded
    assert "desk rules win" in loaded
    assert "use pytest" not in loaded


def test_mentions_read_workspace_files_and_refuse_env(tmp_path):
    (tmp_path / "note.txt").write_text("hello desk", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    expanded = expand_mentions("see @note.txt and @.env", tmp_path)
    assert "hello desk" in expanded
    assert "SECRET" not in expanded
    assert "refused" in expanded


def test_lesson_stays_a_draft_until_promote(tmp_path):
    assert draft_lesson(tmp_path, "what is 2 plus 2") is None
    path = draft_lesson(tmp_path, "no, that's wrong, the port is 4737")
    assert path is not None
    assert path.is_file()
    assert not (tmp_path / "MEMORY.md").exists()
    message = promote_lesson(tmp_path)
    assert "4737" in message
    assert "4737" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert not path.exists()


def test_session_search_and_resume_transcript(tmp_path):
    memory = Memory(tmp_path)
    try:
        memory.add_message("abc", "user", "the widget port is 4737", {"role": "user", "content": "the widget port is 4737"})
        memory.add_message("abc", "assistant", "noted", {"role": "assistant", "content": "noted"})
        hits = memory.search_messages(["widget"])
        assert hits and "4737" in hits[0]["content"]
        transcript = memory.load_transcript("abc")
        assert [item["role"] for item in transcript] == ["user", "assistant"]
        assert memory.list_sessions()[0]["session_id"] == "abc"
    finally:
        memory.close()


def test_fallback_used_once_for_outage_not_for_auth(tmp_path, monkeypatch):
    assert fallback_worthy("API status 503: down")
    assert not fallback_worthy("API status 401: unauthorized")
    assert not fallback_worthy("This subscription tier is blocked from inference.")

    class Boom:
        def complete(self, messages, tools=None, *, model=None, stream=True, on_text=None):
            raise ClientError("API status 503: down")

    class Pong:
        def complete(self, messages, tools=None, *, model=None, stream=True, on_text=None):
            return ChatResponse(text="pong")

    monkeypatch.setattr("ghost_desk.context.make_fallback", lambda config: Pong())
    cfg = Config(
        api_key="k",
        model="m",
        provider="xai-oauth",
        auth_mode="oauth",
        fallback_provider="ollama",
        fallback_model="llama3.2",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
    )
    memory = Memory(cfg.data_path())
    try:
        done = run_turn("what is 2 plus 2", config=cfg, memory=memory, session=DeskSession(), client=Boom())
    finally:
        memory.close()
    assert done.text == "pong"
    assert done.stop_reason == "stop"

    calls = {"n": 0}

    def count(config):
        calls["n"] += 1
        return Pong()

    monkeypatch.setattr("ghost_desk.context.make_fallback", count)
    memory = Memory(cfg.data_path())
    try:
        denied = run_turn("what is 2 plus 2", config=cfg, memory=memory, session=DeskSession(), client=Boom401())
    finally:
        memory.close()
    assert calls["n"] == 0
    assert denied.stop_reason == "api_error"


class Boom401:
    def complete(self, messages, tools=None, *, model=None, stream=True, on_text=None):
        raise ClientError("API status 401: unauthorized")
