"""Pick the saved brain. The agent loop never imports a vendor SDK itself."""

from __future__ import annotations

from dataclasses import replace

from ghost_desk.client import ChatResponse, ClientError, MissingKey
from ghost_desk.config import Config
from ghost_desk.oauth import OAuthError, load_tokens, refresh_token, save_tokens
from ghost_desk.providers.anthropic import NOTE as ANTHROPIC_NOTE
from ghost_desk.providers.anthropic import Anthropic
from ghost_desk.providers.base import Provider, TierBlocked
from ghost_desk.providers.ollama import Ollama
from ghost_desk.providers.openai_compatible import OpenAICompatible
from ghost_desk.providers.openai_sub import SPEC as OPENAI_SPEC
from ghost_desk.providers.openai_sub import OpenAISub
from ghost_desk.providers.xai import SPEC as XAI_SPEC
from ghost_desk.providers.xai import XAI

_SPECS = {"xai": XAI_SPEC, "openai": OPENAI_SPEC}

# Hermes slugs and the aliases those settings screens accept.
_CANONICAL = {
    "xai-oauth": ("xai", "oauth"),
    "grok-oauth": ("xai", "oauth"),
    "x-ai-oauth": ("xai", "oauth"),
    "xai-grok-oauth": ("xai", "oauth"),
    "openai-codex": ("openai", "oauth"),
    "openai-api": ("openai", "api_key"),
    "openrouter": ("openai_compatible", "api_key"),
}


def canonical(provider: str, auth_mode: str = "") -> tuple[str, str]:
    key = (provider or "").strip().lower()
    if key in _CANONICAL:
        return _CANONICAL[key]
    if key == "ollama":
        return "ollama", "local"
    if key in {"xai", "openai", "anthropic", "openai_compatible"}:
        return key, auth_mode or "api_key"
    if not key:
        return "openai_compatible", auth_mode or "api_key"
    return key, auth_mode or "api_key"

TIER_BLOCKED = "This subscription tier is blocked from inference. Run ghost setup and choose API key."
SESSION_EXPIRED = "Session expired. Run ghost setup again."


class ProviderClient:
    """What the agent loop calls. Refreshes once on 401. Says so on 403."""

    def __init__(self, config: Config, provider: Provider | None = None, http=None):
        self.config = config
        self.provider = provider or build_provider(config, http=http)
        self._http = http
        self._refreshed = False
        self._secrets: list[str] = []

    def complete(self, messages, tools=None, *, model=None, stream=True, on_text=None) -> ChatResponse:
        self._refreshed = False
        self._ensure_token()
        try:
            return self._once(messages, tools, model=model, stream=stream, on_text=on_text)
        except TierBlocked as exc:
            raise ClientError(TIER_BLOCKED) from exc
        except MissingKey:
            raise
        except ClientError as exc:
            text = self._redact(str(exc))
            if "401" in text and not self._refreshed and self._mode() == "oauth":
                self._refreshed = True
                if not self._refresh():
                    raise ClientError(SESSION_EXPIRED) from exc
                try:
                    return self._once(messages, tools, model=model, stream=stream, on_text=on_text)
                except ClientError as again:
                    if "401" in self._redact(str(again)):
                        raise ClientError(SESSION_EXPIRED) from again
                    raise ClientError(self._redact(str(again))) from again
            if "403" in text and self._mode() == "oauth":
                raise ClientError(TIER_BLOCKED) from exc
            raise ClientError(text) from exc

    def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None) -> ChatResponse:
        return self.complete(messages, tools, model=model, stream=stream, on_text=on_text)

    def stream(self, messages, tools=None, *, model=None, on_text=None) -> ChatResponse:
        return self.complete(messages, tools, model=model, stream=True, on_text=on_text)

    def list_models(self) -> list[str]:
        return self.provider.list_models()

    def authenticate(self, output) -> None:
        self.provider.authenticate(output)

    def health(self) -> str:
        self._ensure_token()
        return self.provider.health()

    def _once(self, messages, tools, *, model, stream, on_text) -> ChatResponse:
        try:
            return self.provider.chat(messages, tools, model=model, stream=stream, on_text=on_text)
        except TierBlocked:
            raise
        except ClientError:
            raise

    def _ensure_token(self) -> None:
        self._remember(self.config.api_key)
        if self._mode() != "oauth":
            if self._mode() == "api_key" and not self.config.api_key:
                raise MissingKey("No API key. Run ghost setup.")
            return
        path = self.config.data_path() / "auth.json"
        tokens = load_tokens(path, provider=self.config.provider)
        if tokens is None:
            raise MissingKey("Not signed in. Run ghost setup.")
        self._remember(tokens.access_token)
        self._remember(tokens.refresh_token)
        if tokens.expired():
            self._refreshed = True
            if not self._refresh():
                raise ClientError(SESSION_EXPIRED)
            return
        self.provider.access_token = tokens.access_token

    def _refresh(self) -> bool:
        spec = _SPECS.get(canonical(self.config.provider, self.config.auth_mode)[0])
        path = self.config.data_path() / "auth.json"
        current = load_tokens(path, provider=self.config.provider)
        if spec is None or current is None or not current.refresh_token:
            return False
        try:
            fresh = refresh_token(
                spec,
                current.refresh_token,
                http=self._http,
                account_id=current.account_id,
            )
        except OAuthError:
            return False
        save_tokens(path, fresh, provider=self.config.provider)
        self.provider.access_token = fresh.access_token
        self._remember(fresh.access_token)
        self._remember(fresh.refresh_token)
        return True

    def _mode(self) -> str:
        return canonical(self.config.provider, self.config.auth_mode)[1]

    def _remember(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def _redact(self, text: str) -> str:
        cleaned = text
        for secret in self._secrets:
            if secret:
                cleaned = cleaned.replace(secret, "stored")
        return cleaned


def build_provider(config: Config, http=None, browser=None) -> Provider:
    internal, mode = canonical(config.provider, config.auth_mode)
    runtime = config if config.auth_mode == mode else replace(config, auth_mode=mode)
    if internal == "ollama":
        return Ollama(runtime)
    if internal == "xai":
        return XAI(runtime, http=http, browser=browser)
    if internal == "openai":
        return OpenAISub(runtime, http=http, browser=browser)
    if internal == "anthropic":
        return Anthropic(runtime)
    return OpenAICompatible(runtime)


def build_client(config: Config, http=None, browser=None) -> ProviderClient:
    return ProviderClient(config, provider=build_provider(config, http=http, browser=browser), http=http)


__all__ = [
    "ANTHROPIC_NOTE",
    "ProviderClient",
    "SESSION_EXPIRED",
    "TIER_BLOCKED",
    "build_client",
    "build_provider",
    "canonical",
]
