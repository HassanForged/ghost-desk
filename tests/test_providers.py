"""Provider selection, token files, and auth failures. No network and no printed secrets."""

import base64
import json
import time

import pytest

from ghost_desk.agent import DeskSession, run_turn
from ghost_desk.cli import run_smoke
from ghost_desk.client import ChatResponse, ClientError
from ghost_desk.config import Config, setup_interactive
from ghost_desk.memory import Memory
from ghost_desk.oauth import OAuthError, TokenSet, device_login, is_private, load_tokens, save_tokens
from ghost_desk.providers import ProviderClient, TIER_BLOCKED
from ghost_desk.providers.anthropic import NOTE, Anthropic
from ghost_desk.providers.base import Provider
from ghost_desk.providers.ollama import DEFAULT_BASE, Ollama
from ghost_desk.providers.openai_compatible import OpenAICompatible
from ghost_desk.providers.openai_sub import SPEC as OPENAI_SPEC
from ghost_desk.providers.openai_sub import OpenAISub
from ghost_desk.providers.xai import SPEC as XAI_SPEC
from ghost_desk.providers.xai import XAI


class Scripted:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

        class Completions:
            def __init__(self, outer):
                self.outer = outer

            def create(self, **kwargs):
                self.outer.calls.append(kwargs)
                if self.outer.error is not None:
                    raise self.outer.error
                return self.outer.response

        class Chat:
            def __init__(self, outer):
                self.completions = Completions(outer)

        self.chat = Chat(self)


def _reply(text="pong"):
    message = type("M", (), {"content": text, "tool_calls": None})()
    choice = type("C", (), {"message": message})()
    payload = type("R", (), {"choices": [choice], "usage": None})()
    return payload


def _lines():
    found = []

    def output(*args):
        found.append(" ".join(str(part) for part in args))

    return found, output


def _setup(tmp_path, answers, http=None, grok_auth=None, claude_auth=None):
    lines, output = _lines()
    saved = setup_interactive(
        input_fn=lambda _prompt: next(answers),
        output_fn=output,
        cfg=Config(data_dir=str(tmp_path)),
        http=http,
        browser=lambda _url: None,
        sleep=lambda _seconds: None,
        grok_auth_path=grok_auth if grok_auth is not None else tmp_path / "no-grok-auth.json",
        claude_auth_path=claude_auth if claude_auth is not None else tmp_path / "no-claude-auth.json",
    )
    return saved, "\n".join(lines)


def test_setup_menu_lists_five_brains(tmp_path):
    _saved, text = _setup(tmp_path, iter(["6", "", ""]))
    assert "1  ChatGPT or Codex Subscription" in text
    assert "2  OpenAI API key" in text
    assert "3  Claude (API key or Claude Code setup-token)" in text
    assert "4  xAI Grok OAuth (SuperGrok / Premium+)" in text
    assert "5  xAI API key" in text
    assert "6  Ollama" in text
    assert "7  OpenRouter or any OpenAI-compatible URL" in text


def test_local_ollama_needs_no_key_and_keeps_memory(tmp_path):
    (tmp_path / "memory.sqlite").write_text("keep-memory", encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "desk.md").write_text("keep-skill", encoding="utf-8")
    (tmp_path / "handoffs").mkdir()
    (tmp_path / "handoffs" / "old.md").write_text("keep-handoff", encoding="utf-8")
    saved, text = _setup(tmp_path, iter(["6", "", ""]))
    assert saved.provider == "ollama"
    assert saved.auth_mode == "local"
    assert saved.api_key == ""
    assert saved.base_url == DEFAULT_BASE
    assert saved.model == "llama3.2"
    assert "api_key" not in text or "api_key:" not in text
    assert (tmp_path / "memory.sqlite").read_text(encoding="utf-8") == "keep-memory"
    assert (tmp_path / "skills" / "desk.md").read_text(encoding="utf-8") == "keep-skill"
    assert (tmp_path / "handoffs" / "old.md").read_text(encoding="utf-8") == "keep-handoff"
    again, _text = _setup(tmp_path, iter(["6", "http://127.0.0.1:11434/v1", "qwen2.5"]))
    assert again.provider == "ollama"
    assert again.model == "qwen2.5"
    assert (tmp_path / "memory.sqlite").read_text(encoding="utf-8") == "keep-memory"


def test_api_key_choice_picks_provider_from_url(tmp_path):
    saved, text = _setup(
        tmp_path,
        iter(["7", "https://openrouter.ai/api/v1", "sk-or-test", "openrouter/auto"]),
    )
    assert saved.provider == "openrouter"
    assert saved.auth_mode == "api_key"
    assert saved.api_key == "sk-or-test"
    assert "sk-or-test" not in text


def test_claude_choice_is_api_key_not_a_fake_login(tmp_path):
    def http(*_args, **_kwargs):
        raise AssertionError("claude setup must not call a login endpoint")

    saved, text = _setup(tmp_path, iter(["3", "", "sk-ant-test", ""]), http=http)
    assert saved.provider == "anthropic"
    assert saved.auth_mode == "api_key"
    assert saved.base_url == "https://api.anthropic.com"
    assert NOTE in text
    assert "sk-ant-test" not in text
    brain = Anthropic(saved)
    with pytest.raises(OAuthError, match="no public device-code"):
        brain.authenticate(lambda _line: None)


def test_grok_login_saves_token_file_not_config(tmp_path):
    secret = "at-secret-value"

    def http(_method, url, _fields):
        if url.endswith("/device/code"):
            return {
                "device_code": "dev",
                "user_code": "CODE-1",
                "verification_uri": "https://auth.x.ai/device",
                "interval": 5,
            }
        return {"access_token": secret, "refresh_token": "rt-secret-value", "expires_in": 3600}

    saved, text = _setup(tmp_path, iter(["4", ""]), http=http)
    assert saved.provider == "xai-oauth"
    assert saved.auth_mode == "oauth"
    assert saved.api_key == ""
    assert saved.model == "grok-4.6"
    assert saved.base_url == "https://api.x.ai/v1"
    assert secret not in text
    assert secret not in saved.config_file().read_text(encoding="utf-8")
    assert "Code: CODE-1" in text
    assert "URL: https://auth.x.ai/device" in text
    assert "Never share it." in text
    stored = load_tokens(tmp_path / "auth.json", provider="xai-oauth")
    assert stored is not None
    assert stored.access_token == secret


def test_oauth_timeout_falls_back_once(tmp_path):
    calls = {"n": 0}

    def http(_method, url, _fields):
        calls["n"] += 1
        if url.endswith("/device/code"):
            return {
                "device_code": "dev",
                "user_code": "CODE-2",
                "verification_uri": "https://auth.x.ai/device",
                "interval": 1,
            }
        return {"error": "authorization_pending", "status": 400}

    with pytest.raises(OAuthError, match="timed out"):
        device_login(
            XAI_SPEC,
            output=lambda _line: None,
            http=http,
            sleep=lambda _seconds: None,
            max_polls=2,
            browser=lambda _url: None,
        )
    assert calls["n"] == 3
    saved, text = _setup(
        tmp_path,
        iter(["4", "https://api.x.ai/v1", "xai-fallback-key", "grok-4"]),
        http=http,
    )
    assert "login timed out" in text
    assert saved.provider == "xai"
    assert saved.auth_mode == "api_key"
    assert saved.api_key == "xai-fallback-key"
    assert "xai-fallback-key" not in text


def test_token_file_permissions_and_repr(tmp_path):
    path = tmp_path / "auth.json"
    save_tokens(path, TokenSet("sekret-access", "sekret-refresh", time.time() + 100), provider="xai")
    assert path.is_file()
    assert is_private(path)
    loaded = load_tokens(path, provider="xai")
    assert loaded is not None
    assert loaded.access_token == "sekret-access"
    assert "sekret-access" not in repr(loaded)
    assert "sekret-refresh" not in repr(loaded)


def test_refresh_once_on_401_then_stop(tmp_path):
    cfg = Config(data_dir=str(tmp_path), provider="xai", auth_mode="oauth", model="grok-4", api_key="")
    save_tokens(
        cfg.data_path() / "auth.json",
        TokenSet("old-access", "old-refresh", time.time() + 3600),
        provider="xai",
    )
    hits = {"n": 0}

    def http(_method, _url, fields):
        hits["n"] += 1
        assert fields.get("refresh_token") == "old-refresh"
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

    class Once(Provider):
        def __init__(self):
            super().__init__(id="xai", auth_mode="oauth", model="grok-4")
            self.calls = 0

        def authenticate(self, output):
            return None

        def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None):
            self.calls += 1
            if self.calls == 1:
                raise ClientError("API status 401: unauthorized")
            return ChatResponse(text="pong")

    brain = Once()
    client = ProviderClient(cfg, provider=brain, http=http)
    assert client.complete([{"role": "user", "content": "hi"}], stream=False).text == "pong"
    assert brain.calls == 2
    assert hits["n"] == 1
    assert cfg.api_key == ""
    assert load_tokens(cfg.data_path() / "auth.json").access_token == "new-access"

    class Always(Provider):
        def __init__(self):
            super().__init__(id="xai", auth_mode="oauth", model="grok-4")
            self.calls = 0

        def authenticate(self, output):
            return None

        def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None):
            self.calls += 1
            raise ClientError("API status 401: unauthorized")

    save_tokens(
        cfg.data_path() / "auth.json",
        TokenSet("old-access", "old-refresh", time.time() + 3600),
        provider="xai",
    )
    hits["n"] = 0
    stuck = Always()
    again = ProviderClient(cfg, provider=stuck, http=http)
    with pytest.raises(ClientError, match="Run ghost setup again"):
        again.complete([{"role": "user", "content": "hi"}], stream=False)
    assert hits["n"] == 1
    assert stuck.calls == 2


def test_403_tells_user_to_use_api_key(tmp_path):
    cfg = Config(data_dir=str(tmp_path), provider="xai", auth_mode="oauth", model="grok-4", api_key="")
    save_tokens(
        cfg.data_path() / "auth.json",
        TokenSet("access-value", "refresh-value", time.time() + 3600),
        provider="xai",
    )

    class Blocked(Provider):
        def __init__(self):
            super().__init__(id="xai", auth_mode="oauth", model="grok-4")

        def authenticate(self, output):
            return None

        def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None):
            raise ClientError("API status 403: forbidden")

    def http(*_args, **_kwargs):
        raise AssertionError("403 must not refresh or log in again")

    client = ProviderClient(cfg, provider=Blocked(), http=http)
    with pytest.raises(ClientError, match="blocked from inference") as caught:
        client.complete([{"role": "user", "content": "hi"}], stream=False)
    assert str(caught.value) == TIER_BLOCKED
    assert "access-value" not in str(caught.value)


def test_xai_adapter_turns_403_into_tier_block():
    err = Exception("forbidden")
    err.status_code = 403
    cfg = Config(provider="xai", auth_mode="oauth", model="grok-4", api_key="")
    brain = XAI(cfg, sdk=Scripted(error=err))
    brain.access_token = "token-not-printed"
    from ghost_desk.providers.base import TierBlocked

    with pytest.raises(TierBlocked, match="API key"):
        brain.chat([{"role": "user", "content": "hi"}], stream=False)


def test_smoke_answers_and_hides_secrets(tmp_path, capsys):
    cfg = Config(
        api_key="super-secret-key",
        base_url="http://127.0.0.1:9/v1",
        model="smoke-model",
        data_dir=str(tmp_path),
        provider="ollama",
        auth_mode="local",
    )
    from ghost_desk.config import save_config

    save_config(cfg)
    save_tokens(
        cfg.data_path() / "auth.json",
        TokenSet("access-secret-token", "refresh-secret-token", time.time() + 3600),
        provider="xai",
    )

    class Pong(Provider):
        def __init__(self):
            super().__init__(id="ollama", auth_mode="local", model="smoke-model")

        def authenticate(self, output):
            return None

        def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None):
            return ChatResponse(text="pong super-secret-key")

    code = run_smoke(cfg, client=ProviderClient(cfg, provider=Pong()))
    captured = capsys.readouterr()
    assert code == 0
    assert "pong" in captured.out
    assert "smoke ok" in captured.out
    assert "super-secret-key" not in captured.out
    assert "super-secret-key" not in captured.err
    assert "access-secret-token" not in captured.out
    assert "refresh-secret-token" not in captured.out


def test_adapters_expose_the_same_interface():
    cfg = Config(model="m", api_key="k", auth_mode="api_key")
    brains = [
        Ollama(Config(model="llama3.2", auth_mode="local", provider="ollama")),
        XAI(cfg),
        OpenAISub(cfg),
        Anthropic(cfg),
        OpenAICompatible(cfg),
    ]
    for brain in brains:
        for name in ("chat", "stream", "list_models", "authenticate", "health"):
            assert callable(getattr(brain, name))
        assert brain.list_models()


def test_ollama_chat_uses_local_base_without_a_key():
    cfg = Config(provider="ollama", auth_mode="local", api_key="", model="llama3.2", base_url=DEFAULT_BASE)
    sdk = Scripted(response=_reply("pong"))
    reply = Ollama(cfg, sdk=sdk).chat([{"role": "user", "content": "hi"}], stream=False)
    assert reply.text == "pong"
    assert sdk.calls[0]["model"] == "llama3.2"
    assert cfg.api_key == ""


def test_openai_device_login_stores_account_and_hides_token(tmp_path):
    payload = base64.urlsafe_b64encode(
        json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}}).encode()
    ).decode().rstrip("=")
    id_token = f"header.{payload}.signature"
    secret = "at-openai-secret"

    def http(_method, url, _fields):
        if url.endswith("/usercode"):
            return {"device_auth_id": "dev-1", "user_code": "ABCD-EFGH", "interval": "1", "status": 200}
        if url.endswith("/deviceauth/token"):
            return {"authorization_code": "auth-code", "code_verifier": "verifier-value", "status": 200}
        return {
            "access_token": secret,
            "refresh_token": "rt-openai-secret",
            "expires_in": 3600,
            "id_token": id_token,
            "status": 200,
        }

    lines = []
    tokens = device_login(
        OPENAI_SPEC,
        output=lines.append,
        http=http,
        sleep=lambda _seconds: None,
        browser=lambda _url: None,
        max_polls=2,
    )
    assert tokens.account_id == "acct_123"
    assert secret not in "\n".join(lines)
    assert "ABCD-EFGH" in "\n".join(lines)
    save_tokens(tmp_path / "auth.json", tokens, provider="openai")
    raw = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert secret in raw
    assert id_token not in raw
    assert "verifier-value" not in raw


def test_codex_chat_and_anthropic_messages(tmp_path):
    cfg = Config(provider="openai", auth_mode="oauth", model="gpt-4o-mini", api_key="", data_dir=str(tmp_path))
    save_tokens(
        cfg.data_path() / "auth.json",
        TokenSet("codex-access", "codex-refresh", time.time() + 3600, account_id="acct_9"),
        provider="openai",
    )

    def transport(url, payload, headers):
        assert url.endswith("/responses")
        assert headers["ChatGPT-Account-Id"] == "acct_9"
        assert "codex-access" not in json.dumps(payload)
        return 200, {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "pong"}]},
                {"type": "function_call", "call_id": "call_1", "name": "file_read", "arguments": "{\"path\":\"a.txt\"}"},
            ],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }

    reply = OpenAISub(cfg, transport=transport).chat(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "file_read", "parameters": {"type": "object"}}}],
        stream=False,
    )
    assert reply.text == "pong"
    assert reply.tool_calls[0].name == "file_read"
    assert reply.tool_calls[0].arguments["path"] == "a.txt"

    def anthropic_transport(url, payload, headers):
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers["x-api-key"] == "sk-ant-test"
        assert payload["messages"][0]["content"] == "hi"
        return 200, {"content": [{"type": "text", "text": "pong"}], "usage": {"input_tokens": 1, "output_tokens": 1}}

    claude = Anthropic(
        Config(provider="anthropic", auth_mode="api_key", api_key="sk-ant-test", model="claude-sonnet-4-5"),
        transport=anthropic_transport,
    )
    assert claude.chat([{"role": "user", "content": "hi"}], stream=True, on_text=lambda _chunk: None).text == "pong"


def test_grok_cli_import_hides_the_token(tmp_path):
    grok = tmp_path / "grok-auth.json"
    grok.write_text(
        json.dumps(
            {
                "https://auth.x.ai::client": {
                    "key": "grok-cli-access",
                    "refresh_token": "grok-cli-refresh",
                    "expires_at": 9_999_999_999,
                }
            }
        ),
        encoding="utf-8",
    )
    saved, text = _setup(tmp_path, iter(["4", ""]), grok_auth=grok)
    assert saved.provider == "xai-oauth"
    assert saved.api_key == ""
    assert "Imported the Grok CLI login" in text
    assert "grok-cli-access" not in text
    assert "grok-cli-refresh" not in text
    stored = load_tokens(saved.data_path() / "auth.json", provider="xai-oauth")
    assert stored is not None
    assert stored.access_token == "grok-cli-access"


def test_hermes_provider_aliases():
    from ghost_desk.providers import build_provider, canonical

    assert canonical("xai-oauth", "") == ("xai", "oauth")
    assert canonical("grok-oauth", "") == ("xai", "oauth")
    assert canonical("openai-codex", "") == ("openai", "oauth")
    assert canonical("openai-api", "") == ("openai", "api_key")
    brain = build_provider(Config(provider="xai-oauth"))
    assert brain.id == "xai"
    assert brain.auth_mode == "oauth"


def test_agent_uses_the_provider_without_an_api_key(tmp_path, monkeypatch):
    class Pong:
        def complete(self, messages, tools=None, *, model=None, stream=True, on_text=None):
            return ChatResponse(text="pong")

    monkeypatch.setattr(
        "ghost_desk.providers.build_client",
        lambda config, http=None, browser=None: Pong(),
    )
    cfg = Config(
        api_key="",
        provider="ollama",
        auth_mode="local",
        model="llama3.2",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
    )
    memory = Memory(cfg.data_path())
    try:
        result = run_turn("what is 2 plus 2", config=cfg, memory=memory, session=DeskSession(), client=None)
    finally:
        memory.close()
    assert result.text == "pong"
    assert result.stop_reason == "stop"
