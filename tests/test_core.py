from pathlib import Path

import pytest

from ghost_desk.agent import DeskSession, run_turn
from ghost_desk.client import ChatResponse, OpenAIChatClient, ToolCall
from ghost_desk.config import Config, load_config, save_config, setup_interactive
from ghost_desk.memory import Memory
from ghost_desk.permissions import PermissionGate
import time

from ghost_desk.background import draft_gaps
from ghost_desk.compact import Compactor
from ghost_desk.plan import build_plan, extract_checklist, is_approval, is_build_request
from ghost_desk.subagents import Handoff, write_handoff
from ghost_desk.verify import verify_facts
from ghost_desk.tools import execute
from ghost_desk.verify import verify_http, verify_shell, verify_write


class FakeSDK:
    def __init__(self, response):
        self.response = response
        self.calls = []

        class Completions:
            def __init__(self, outer):
                self.outer = outer

            def create(self, **kwargs):
                self.outer.calls.append(kwargs)
                return self.outer.response

        class Chat:
            def __init__(self, outer):
                self.completions = Completions(outer)

        self.chat = Chat(self)


def test_config_roundtrip_and_env(tmp_path, monkeypatch):
    data = tmp_path / "desk"
    monkeypatch.setenv("GHOST_API_KEY", "from-env")
    monkeypatch.setenv("GHOST_MODEL", "grok-test")
    monkeypatch.delenv("GHOST_BASE_URL", raising=False)
    cfg = Config(api_key="file", base_url="http://local/v1", model="old", data_dir=str(data))
    save_config(cfg)
    (data / "config.yaml").write_text("base_url: http://yaml/v1\n", encoding="utf-8")
    loaded = load_config(data / "config.json")
    assert loaded.api_key == "from-env"
    assert loaded.model == "grok-test"
    assert loaded.base_url == "http://yaml/v1"
    assert loaded.data_path() == data


def test_setup_asks_three_fields(tmp_path):
    answers = iter(["7", "http://ollama/v1", "local-key", "llama3"])
    cfg = Config(data_dir=str(tmp_path))
    saved = setup_interactive(input_fn=lambda _prompt: next(answers), output_fn=lambda *_a: None, cfg=cfg)
    text = saved.config_file().read_text(encoding="utf-8")
    assert "local-key" in text
    assert "llama3" in text
    assert saved.provider == "openai_compatible"
    assert saved.auth_mode == "api_key"


def test_client_uses_mock_and_parses_tool_call():
    payload = type("R", (), {})()
    message = type("M", (), {"content": "ok", "tool_calls": [
        type("T", (), {"id": "1", "function": type("F", (), {"name": "file_read", "arguments": '{"path":"a.txt"}'})()})
    ]})()
    choice = type("C", (), {"message": message})()
    payload.choices = [choice]
    usage = type("U", (), {"prompt_tokens": 3, "completion_tokens": 1})()
    payload.usage = usage
    cfg = Config(api_key="k", base_url="http://example/v1", model="m")
    sdk = FakeSDK(payload)
    client = OpenAIChatClient(cfg, sdk=sdk, attempts=1)
    result = client.complete([{"role": "user", "content": "hi"}], stream=False)
    assert result.text == "ok"
    assert result.tool_calls[0].name == "file_read"
    assert sdk.calls[0]["model"] == "m"


def test_permissions_read_write_and_secret(tmp_path):
    gate = PermissionGate(tmp_path, ask=lambda _q: False)
    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")
    read = gate.check_read("note.txt")
    assert read.allowed
    write = gate.check_write("out.txt")
    assert not write.allowed
    secret = gate.check_read(".env")
    assert not secret.allowed
    shell = gate.check_shell("rm note.txt")
    assert not shell.allowed


def test_open_chrome_is_allowed_and_does_not_touch_the_workspace(tmp_path):
    from ghost_desk.permissions import is_local_open
    from ghost_desk.tools import launch_open

    gate = PermissionGate(tmp_path, ask=lambda _q: False)
    command = "start chrome https://www.youtube.com"
    assert is_local_open(command)
    assert gate.check_shell(command).allowed
    chrome = r'"C:\Program Files\Google\Chrome\Application\chrome.exe" https://www.youtube.com'
    assert gate.check_shell(chrome).allowed
    assert not gate.check_shell(r"C:\Windows\System32\cmd.exe /c whoami").allowed
    seen = {}

    def runner(args, **_kwargs):
        seen["args"] = args

    shown = launch_open("chrome", "youtube", runner=runner)
    assert shown == "chrome https://www.youtube.com"
    assert seen["args"][-2:] == ["chrome", "https://www.youtube.com"]


def test_edit_search_and_clipboard(tmp_path):
    from ghost_desk.tools import clipboard_run, execute, parse_search_results, web_search

    gate = PermissionGate(tmp_path, ask=lambda _q: True)
    (tmp_path / "note.txt").write_text("hello desk\n", encoding="utf-8")
    edited = execute("file_edit", {"path": "note.txt", "old": "desk", "new": "ghost"}, gate)
    assert edited.ok
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello ghost\n"
    html = '<a class="result__a" href="https://example.com/a">Example Page</a>'
    assert parse_search_results(html)[0]["title"] == "Example Page"
    found = web_search("example", fetch=lambda _url: html)
    assert found[0]["url"] == "https://example.com/a"
    assert clipboard_run("set", "copied-line", runner=lambda action, text: text) == "copied-line"


def test_close_extra_ghost_windows_keeps_this_one_and_the_gateway():
    from ghost_desk.permissions import shell_kind
    from ghost_desk.tools import close_extra_ghosts, extra_ghost_chats

    processes = [
        {"pid": 10, "name": "ghost.exe", "command": "ghost"},
        {"pid": 11, "name": "ghost.exe", "command": "ghost gateway"},
        {"pid": 12, "name": "python.exe", "command": "ghost.exe"},
        {"pid": 99, "name": "powershell.exe", "command": "powershell"},
    ]
    assert extra_ghost_chats(processes, {12}) == [10]
    killed = []
    closed = close_extra_ghosts(list_processes=lambda: processes, killer=killed.append, protected={12})
    assert closed == [10]
    assert killed == [10]
    assert shell_kind(
        'powershell -NoProfile -Command "Get-Process | Select-Object Id"',
        __import__("pathlib").Path("."),
    ) == "read"


def test_shell_read_runs(tmp_path):
    gate = PermissionGate(tmp_path, ask=lambda _q: True)
    outcome = execute("shell", {"command": "echo ghost-desk"}, gate)
    assert outcome.payload["ok"] is True
    assert "ghost-desk" in outcome.payload["stdout"]


def test_plan_extractor_and_reference_prompt():
    assert is_build_request("build a website")
    items = extract_checklist("- home page\n- about page")
    assert items == ["home page", "about page"]
    draft = build_plan("build a website")
    assert draft.status == "awaiting_approval"
    assert draft.needs_reference
    assert "reference" in draft.reference_prompt.lower()


def test_agent_waits_on_build_and_answers_chat(tmp_path):
    cfg = Config(api_key="k", base_url="http://example/v1", model="m", working_directory=str(tmp_path), data_dir=str(tmp_path / "data"))
    memory = Memory(cfg.data_path())
    session = DeskSession()
    held = run_turn("build a website", config=cfg, memory=memory, session=session, client=object())
    assert held.stop_reason == "plan_wait"
    assert "approve" in held.text.lower()

    class Stub:
        def complete(self, messages, tools=None, **kwargs):
            return ChatResponse(text="pong")

    session2 = DeskSession()
    done = run_turn("what is 2 plus 2", config=cfg, memory=memory, session=session2, client=Stub())
    assert done.text == "pong"
    assert done.report.ok
    memory.close()


def test_verify_write_and_http(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("same", encoding="utf-8")
    assert verify_write(path, "same").ok
    assert not verify_write(path, "other").ok
    assert verify_shell("echo", 0, "ok", "").ok
    assert not verify_http("http://x", 500, "no").ok


def test_plan_gate_accepts_go_and_uses_prior_email():
    draft = build_plan("build a one-page site", prior="the email is test@ghostdesk.dev")
    assert "test@ghostdesk.dev" in draft.facts
    assert draft.needs_reference
    assert "reference" in draft.render().lower()
    assert is_approval("go")
    assert is_approval("yes")


def test_email_mismatch_fails(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<p>test@ghostdesk.div</p>", encoding="utf-8")
    checks = verify_facts(["test@ghostdesk.dev"], tmp_path)
    assert checks and not checks[0].ok
    assert "mismatch" in checks[0].conclusion


def test_email_exact_passes_after_go(tmp_path):
    cfg = Config(
        api_key="k",
        base_url="http://example/v1",
        model="m",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
    )
    memory = Memory(cfg.data_path())
    session = DeskSession()
    held = run_turn(
        "build a one-page site, email is test@ghostdesk.dev",
        config=cfg,
        memory=memory,
        session=session,
        client=object(),
    )
    assert held.stop_reason == "plan_wait"
    assert "reference" in held.text.lower()

    class Scripted:
        def __init__(self):
            self.n = 0

        def complete(self, messages, tools=None, **kwargs):
            self.n += 1
            if self.n == 1:
                return ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall(
                            "1",
                            "file_write",
                            {"path": "index.html", "content": "<p>test@ghostdesk.dev</p>"},
                        )
                    ],
                )
            return ChatResponse(text="page written")

    gate = PermissionGate(tmp_path, ask=lambda _q: True)
    done = run_turn("go", config=cfg, memory=memory, session=session, client=Scripted(), gate=gate)
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<p>test@ghostdesk.dev</p>"
    assert any(check.action == "fact" and check.ok for check in done.report.checks)
    memory.close()


def test_handoff_artifact(tmp_path):
    handoff = Handoff(done=["wrote index"], remaining=["check email"], summary="stopped early")
    path = write_handoff(handoff, tmp_path, "build the page")
    text = path.read_text(encoding="utf-8")
    assert "goal: build the page" in text
    assert "remaining" in text
    assert "next command" in text


def test_compaction_does_not_block():
    def slow(_messages):
        time.sleep(0.4)
        return "summary"

    compactor = Compactor(window_tokens=20, threshold=0.5, tail=1)
    messages = [
        {"role": "user", "content": "alpha " * 40},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "beta"},
    ]
    started = time.monotonic()
    compactor.maybe_start(messages, slow, sync=False)
    elapsed = time.monotonic() - started
    assert elapsed < 0.15
    assert compactor.compacting
    compactor._thread.join(timeout=1)


def test_draft_gaps_refuses_live_orders(tmp_path):
    broker = tmp_path / "broker"
    house = tmp_path / "House"
    broker.mkdir()
    house.mkdir()
    text = draft_gaps([broker, house])
    assert "do not trade or deploy" in text
    assert "House" in text


def test_memory_note_and_export(tmp_path):
    memory = Memory(tmp_path)
    memory.add_note("s1", "website", "home page exists", "read home.html")
    found = memory.search_notes(["website"])
    assert found and "home page" in found[0]["body"]
    skills = tmp_path / "skills"
    skills.mkdir()
    out = memory.export_markdown(tmp_path / "export", skills)
    assert (out / "notes").is_dir()
    memory.close()
