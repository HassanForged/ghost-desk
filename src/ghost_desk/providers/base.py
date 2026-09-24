"""The agent loop talks to this interface. Adapters are interchangeable."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from ghost_desk.client import ChatResponse


GENERIC_BASE = "https://api.openai.com/v1"


def chosen_base(config, fallback: str) -> str:
    """Use a saved URL. The OpenAI default does not leak into other brains."""
    raw = (getattr(config, "base_url", "") or "").strip().rstrip("/")
    if not raw or raw == GENERIC_BASE:
        return fallback
    return raw


class AuthError(RuntimeError):
    pass


class TierBlocked(AuthError):
    pass


@dataclass
class Provider:
    id: str
    auth_mode: str
    model: str
    base_url: str = ""
    access_token: str = ""

    def authenticate(self, output: Callable[[str], None]) -> None:
        raise NotImplementedError

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        model: str | None = None,
        stream: bool = False,
        on_text: Callable[[str], None] | None = None,
    ) -> ChatResponse:
        raise NotImplementedError

    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        model: str | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ChatResponse:
        return self.chat(messages, tools, model=model, stream=True, on_text=on_text)

    def list_models(self) -> list[str]:
        return [self.model] if self.model else []

    def health(self) -> str:
        reply = self.chat(
            [{"role": "user", "content": "Reply with the single word pong."}],
            stream=False,
        )
        return reply.text or ""


def post_json(
    url: str,
    payload: dict,
    headers: dict,
    *,
    transport=None,
    timeout: float = 60,
) -> tuple[int, dict]:
    """POST JSON. `transport` replaces the network in tests. Errors carry a status, not headers."""
    if transport is not None:
        status, body = transport(url, payload, headers)
        return int(status), body if isinstance(body, dict) else {}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _read_json(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, _read_json(exc.read().decode(errors="replace"))
    except urllib.error.URLError:
        return 0, {"error": {"message": "network"}}


def _read_json(raw: str) -> dict:
    try:
        loaded: Any = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def status_error(status: int, body: dict):
    """Raise the client error the loop already understands. 401 and 403 stay visible as numbers."""
    from ghost_desk.client import ClientError

    message = ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("type") or "")
    elif error:
        message = str(error)
    short = message.replace("\n", " ")[:180]
    if status == 401:
        raise ClientError("API status 401: unauthorized")
    if status == 403:
        raise ClientError("API status 403: forbidden")
    if status == 0:
        raise ClientError("API failed after 1 tries: network")
    raise ClientError(f"API status {status}: {short}".rstrip())
