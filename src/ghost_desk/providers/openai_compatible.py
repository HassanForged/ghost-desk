"""Any OpenAI-compatible base URL. OpenRouter and the rest land here."""

from __future__ import annotations

from collections.abc import Callable

from ghost_desk.client import ChatResponse, OpenAIChatClient
from ghost_desk.config import Config
from ghost_desk.providers.base import Provider


class OpenAICompatible(Provider):
    def __init__(self, config: Config, sdk=None):
        super().__init__(
            id="openai_compatible",
            auth_mode=config.auth_mode or "api_key",
            model=config.model or "gpt-4o-mini",
            base_url=config.base_url or "https://api.openai.com/v1",
        )
        self._config = config
        self._sdk = sdk

    def authenticate(self, output: Callable[[str], None]) -> None:
        output("Using the saved base URL and key.")

    def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None) -> ChatResponse:
        client = OpenAIChatClient(self._config, sdk=self._sdk, attempts=2)
        return client.complete(messages, tools, model=model or self.model, stream=stream, on_text=on_text)

    def list_models(self) -> list[str]:
        return [self.model] if self.model else []
