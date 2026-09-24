"""Saved desk settings. Unknown keys in the file, the .env, or the environment are dropped."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from ghost_desk.oauth import (
    OAuthError,
    TokenSet,
    read_claude_setup_token,
    read_external_tokens,
    save_tokens,
    secure_write,
)

KEYS = (
    "api_key",
    "base_url",
    "model",
    "working_directory",
    "data_dir",
    "provider",
    "auth_mode",
    "fallback_provider",
    "fallback_model",
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def default_data_dir() -> Path:
    return Path.home() / ".ghost-desk"


@dataclass
class Config:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    working_directory: str = ""
    data_dir: str = ""
    provider: str = ""
    auth_mode: str = ""
    fallback_provider: str = ""
    fallback_model: str = ""

    def data_path(self) -> Path:
        raw = self.data_dir.strip() or str(default_data_dir())
        return Path(raw).expanduser()

    def workspace(self) -> Path:
        raw = self.working_directory.strip() or os.getcwd()
        return Path(raw).expanduser().resolve()

    def config_file(self) -> Path:
        return self.data_path() / "config.json"


class SetupError(RuntimeError):
    pass


def _pick(mapping: dict | None) -> dict[str, str]:
    if not mapping:
        return {}
    folded = {str(key).strip().lower(): value for key, value in mapping.items() if key}
    chosen: dict[str, str] = {}
    for key in KEYS:
        value = folded.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            chosen[key] = text
    return chosen


def _env_map() -> dict[str, str]:
    raw: dict[str, str] = {}
    for key in KEYS:
        if key in os.environ and os.environ[key].strip():
            raw[key] = os.environ[key]
        prefixed = "GHOST_" + key.upper()
        if prefixed in os.environ and os.environ[prefixed].strip():
            raw[key] = os.environ[prefixed]
    return raw


def _flat_yaml(text: str) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        loaded[key.strip()] = value.strip().strip("\"'")
    return loaded


def load_config(path: Path | None = None, env_file: Path | None = None) -> Config:
    """config.json, config.yaml, .env, then the environment. Later sources win. Only KEYS are kept."""
    env = _pick(_env_map())
    if path is None:
        data_dir = env.get("data_dir") or str(default_data_dir())
        path = Path(data_dir).expanduser() / "config.json"
    file_values: dict[str, str] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            file_values = _pick(loaded)
    yaml_path = path.with_suffix(".yaml")
    if yaml_path.is_file():
        file_values = {**file_values, **_pick(_flat_yaml(yaml_path.read_text(encoding="utf-8")))}
    data_dir = env.get("data_dir") or file_values.get("data_dir") or str(default_data_dir())
    home_env = Path(data_dir).expanduser() / ".env"
    dot_values: dict[str, str] = {}
    if home_env.is_file():
        dot_values.update(_pick(dict(dotenv_values(home_env))))
    dotenv_path = env_file if env_file is not None else Path.cwd() / ".env"
    if dotenv_path.is_file() and dotenv_path.resolve() != home_env.resolve():
        dot_values.update(_pick(dict(dotenv_values(dotenv_path))))
    merged = {**file_values, **dot_values, **env}
    cfg = Config()
    for key, value in merged.items():
        setattr(cfg, key, value)
    if not cfg.data_dir:
        cfg.data_dir = str(default_data_dir())
    return cfg


def save_config(cfg: Config) -> Path:
    data = cfg.data_path()
    data.mkdir(parents=True, exist_ok=True)
    target = data / "config.json"
    payload = {key: getattr(cfg, key) for key in KEYS}
    secure_write(target, json.dumps(payload, indent=2) + "\n")
    return target


def needs_setup(cfg: Config) -> bool:
    return not cfg.config_file().is_file()


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:
        return


def _ask(input_fn, prompt: str, default: str = "") -> str:
    entered = input_fn(prompt).strip()
    return entered or default


def _provider_for_url(base_url: str) -> str:
    host = base_url.lower()
    if "anthropic.com" in host:
        return "anthropic"
    if "api.x.ai" in host:
        return "xai"
    if "api.openai.com" in host:
        return "openai"
    return "openai_compatible"


def _save(cfg: Config, output_fn) -> Config:
    save_config(cfg)
    output_fn(f"saved {cfg.config_file()}")
    return cfg


def _api_key_path(
    cfg: Config,
    input_fn,
    output_fn,
    *,
    provider: str,
    default_base: str,
    default_model: str,
) -> Config:
    base_url = _ask(input_fn, f"base_url [{default_base}]: ", default_base)
    api_key = input_fn("api_key: ").strip()
    model = _ask(input_fn, f"model [{default_model}]: ", default_model)
    if not api_key or not base_url or not model:
        raise SetupError("setup needs base_url, api_key, and model")
    cfg.provider = provider
    cfg.auth_mode = "api_key"
    cfg.base_url = base_url
    cfg.api_key = api_key
    cfg.model = model
    return _save(cfg, output_fn)


def _finish_oauth(cfg: Config, input_fn, output_fn, *, provider: str, base_url: str, default_model: str) -> Config:
    cfg.provider = provider
    cfg.auth_mode = "oauth"
    cfg.api_key = ""
    cfg.base_url = base_url
    output_fn(f"  1  {default_model}")
    cfg.model = _ask(input_fn, f"model [{default_model}]: ", default_model)
    return _save(cfg, output_fn)


def setup_interactive(
    input_fn=input,
    output_fn=print,
    cfg: Config | None = None,
    http=None,
    browser=None,
    sleep=None,
    grok_auth_path: Path | None = None,
    claude_auth_path: Path | None = None,
    boot_choice: str | None = None,
) -> Config:
    """Same provider list Hermes and OpenClaw show. Re-running updates the brain and leaves memory."""
    cfg = cfg or load_config()
    cfg.data_path().mkdir(parents=True, exist_ok=True)
    if grok_auth_path is None:
        grok_auth_path = Path.home() / ".grok" / "auth.json"
    if claude_auth_path is None:
        claude_auth_path = Path.home() / ".claude" / ".credentials.json"
    if boot_choice:
        choice = {
            "1": "chatgpt",
            "2": "claude",
            "3": "grok",
            "4": "api",
            "5": "local",
        }.get(boot_choice.strip(), "")
        if not choice:
            raise SetupError("Choose 1, 2, 3, 4, or 5.")
    else:
        output_fn("Which brain?")
        output_fn("  OpenAI")
        output_fn("  1  ChatGPT or Codex Subscription")
        output_fn("  2  OpenAI API key")
        output_fn("  Anthropic")
        output_fn("  3  Claude (API key or Claude Code setup-token)")
        output_fn("  xAI (Grok)")
        output_fn("  4  xAI Grok OAuth (SuperGrok / Premium+)")
        output_fn("  5  xAI API key")
        output_fn("  Local")
        output_fn("  6  Ollama")
        output_fn("  7  OpenRouter or any OpenAI-compatible URL")
        choice = input_fn("choice: ").strip().lower()
    opener = _open_browser if browser is None else browser

    if choice in {"1", "chatgpt", "openai-codex", "codex"}:
        from ghost_desk.providers.openai_sub import OpenAISub

        cfg.provider = "openai-codex"
        cfg.auth_mode = "oauth"
        try:
            OpenAISub(cfg, http=http, browser=opener, sleep=sleep).authenticate(output_fn)
        except OAuthError as exc:
            output_fn(str(exc).splitlines()[0])
            return _api_key_path(
                cfg,
                input_fn,
                output_fn,
                provider="openai-api",
                default_base=DEFAULT_BASE_URL,
                default_model="gpt-4o-mini",
            )
        return _finish_oauth(
            cfg,
            input_fn,
            output_fn,
            provider="openai-codex",
            base_url=DEFAULT_BASE_URL,
            default_model="gpt-5.4",
        )

    if choice in {"2", "openai-api"}:
        return _api_key_path(
            cfg,
            input_fn,
            output_fn,
            provider="openai-api",
            default_base=DEFAULT_BASE_URL,
            default_model="gpt-4o-mini",
        )

    if choice in {"3", "claude", "anthropic"}:
        from ghost_desk.providers.anthropic import DEFAULT_MODEL as CLAUDE_MODEL
        from ghost_desk.providers.anthropic import NOTE

        output_fn(NOTE)
        existing = read_claude_setup_token(claude_auth_path)
        if existing:
            output_fn("Imported the Claude Code login already on this machine.")
            save_tokens(
                cfg.data_path() / "auth.json",
                TokenSet(existing, "", 0),
                provider="anthropic",
                auth_mode="setup_token",
            )
            cfg.provider = "anthropic"
            cfg.auth_mode = "oauth"
            cfg.api_key = ""
            cfg.base_url = "https://api.anthropic.com"
            cfg.model = _ask(input_fn, f"model [{CLAUDE_MODEL}]: ", CLAUDE_MODEL)
            return _save(cfg, output_fn)
        saved = _api_key_path(
            cfg,
            input_fn,
            output_fn,
            provider="anthropic",
            default_base="https://api.anthropic.com",
            default_model=CLAUDE_MODEL,
        )
        if saved.api_key.startswith("sk-ant-oat"):
            from ghost_desk.oauth import TokenSet

            save_tokens(
                saved.data_path() / "auth.json",
                TokenSet(saved.api_key, "", 0),
                provider="anthropic",
                auth_mode="setup_token",
            )
            saved.auth_mode = "oauth"
            saved.api_key = ""
            save_config(saved)
        return saved

    if choice in {"4", "grok", "xai-oauth", "grok-oauth", "xai-grok-oauth"}:
        from ghost_desk.providers.xai import DEFAULT_MODEL as GROK_MODEL
        from ghost_desk.providers.xai import INFERENCE
        from ghost_desk.providers.xai import XAI

        cfg.provider = "xai-oauth"
        cfg.auth_mode = "oauth"
        imported = read_external_tokens(grok_auth_path)
        if imported is not None:
            save_tokens(cfg.data_path() / "auth.json", imported, provider="xai-oauth")
            output_fn("Imported the Grok CLI login already on this machine.")
            return _finish_oauth(
                cfg,
                input_fn,
                output_fn,
                provider="xai-oauth",
                base_url=INFERENCE,
                default_model=GROK_MODEL,
            )
        try:
            XAI(cfg, http=http, browser=opener, sleep=sleep).authenticate(output_fn)
        except OAuthError as exc:
            output_fn(str(exc).splitlines()[0])
            return _api_key_path(
                cfg,
                input_fn,
                output_fn,
                provider="xai",
                default_base=INFERENCE,
                default_model=GROK_MODEL,
            )
        return _finish_oauth(
            cfg,
            input_fn,
            output_fn,
            provider="xai-oauth",
            base_url=INFERENCE,
            default_model=GROK_MODEL,
        )

    if choice in {"5", "xai"}:
        from ghost_desk.providers.xai import DEFAULT_MODEL as GROK_MODEL
        from ghost_desk.providers.xai import INFERENCE

        return _api_key_path(
            cfg,
            input_fn,
            output_fn,
            provider="xai",
            default_base=INFERENCE,
            default_model=GROK_MODEL,
        )

    if choice in {"6", "local", "ollama"}:
        from ghost_desk.providers.ollama import DEFAULT_BASE, DEFAULT_MODEL as OLLAMA_MODEL

        cfg.provider = "ollama"
        cfg.auth_mode = "local"
        cfg.api_key = ""
        cfg.base_url = _ask(input_fn, f"base_url [{DEFAULT_BASE}]: ", DEFAULT_BASE)
        cfg.model = _ask(input_fn, f"model [{OLLAMA_MODEL}]: ", OLLAMA_MODEL)
        return _save(cfg, output_fn)

    if choice in {"7", "api", "key", "api_key", "openrouter"}:
        base_url = _ask(input_fn, f"base_url [{DEFAULT_BASE_URL}]: ", DEFAULT_BASE_URL)
        api_key = input_fn("api_key: ").strip()
        guessed = _provider_for_url(base_url)
        if "openrouter.ai" in base_url.lower():
            guessed = "openrouter"
        default_model = DEFAULT_MODEL
        if guessed == "anthropic":
            default_model = "claude-sonnet-4-5"
        elif guessed == "xai":
            default_model = "grok-4.6"
        model = _ask(input_fn, f"model [{default_model}]: ", default_model)
        if not api_key or not base_url or not model:
            raise SetupError("setup needs base_url, api_key, and model")
        cfg.provider = guessed
        cfg.auth_mode = "api_key"
        cfg.base_url = base_url
        cfg.api_key = api_key
        cfg.model = model
        return _save(cfg, output_fn)

    raise SetupError("Choose 1, 2, 3, 4, 5, 6, or 7.")
