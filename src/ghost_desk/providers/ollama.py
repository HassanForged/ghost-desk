"""Local Ollama. No key."""

from __future__ import annotations

from collections.abc import Callable

from ghost_desk.client import ChatResponse, OpenAIChatClient
from ghost_desk.config import Config
from ghost_desk.providers.base import Provider, chosen_base

DEFAULT_BASE = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "llama3.2"


class Ollama(Provider):
    def __init__(self, config: Config, sdk=None):
        super().__init__(
            id="ollama",
            auth_mode="local",
            model=config.model or DEFAULT_MODEL,
            base_url=chosen_base(config, DEFAULT_BASE),
        )
        self._config = config
        self._sdk = sdk

    def authenticate(self, output: Callable[[str], None]) -> None:
        output(f"Local model at {self.base_url}. No key.")

    def _runtime(self) -> Config:
        return Config(
            api_key=self._config.api_key or "ollama",
            base_url=self.base_url,
            model=self.model,
            working_directory=self._config.working_directory,
            data_dir=self._config.data_dir,
            provider="ollama",
            auth_mode="local",
        )

    def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None) -> ChatResponse:
        client = OpenAIChatClient(self._runtime(), sdk=self._sdk, attempts=1)
        return client.complete(messages, tools, model=model or self.model, stream=stream, on_text=on_text)

    def list_models(self) -> list[str]:
        return [self.model] if self.model else []
