"""One OpenAI-compatible chat function: streaming, tools, and retries."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from ghost_desk.config import Config


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ClientError(RuntimeError):
    pass


class MissingKey(ClientError):
    pass


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return False
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    code = getattr(exc, "status_code", None)
    return isinstance(code, int) and (code == 429 or code >= 500)


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    text = raw or "{}"
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}
    if isinstance(loaded, dict):
        return loaded
    return {"value": loaded}


class OpenAIChatClient:
    def __init__(
        self,
        config: Config,
        sdk: Any | None = None,
        attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
    ):
        if not config.api_key:
            raise MissingKey("No API key. Run ghost setup.")
        self.model = config.model or "gpt-4o-mini"
        self.attempts = max(1, attempts)
        self._sleep = sleep or time.sleep
        self._sdk = sdk or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url or "https://api.openai.com/v1",
            timeout=60.0,
        )

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        model: str | None = None,
        stream: bool = True,
        on_text: Callable[[str], None] | None = None,
    ) -> ChatResponse:
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        last: BaseException | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                kwargs["stream"] = stream
                created = self._sdk.chat.completions.create(**kwargs)
                if stream and not hasattr(created, "choices"):
                    return self._consume_stream(created, on_text)
                return self._consume(created)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                last = exc
                if not _retryable(exc) or attempt == self.attempts:
                    code = getattr(exc, "status_code", None)
                    if code:
                        raise ClientError(f"API status {code}: {exc}") from exc
                    raise ClientError(f"API failed after {attempt} tries: {exc}") from exc
                self._sleep(min(2 ** (attempt - 1), 8))
        raise ClientError(f"API failed: {last}")

    def _consume(self, resp: Any) -> ChatResponse:
        choice = resp.choices[0]
        message = choice.message
        text = message.content or ""
        calls: list[ToolCall] = []
        for index, tool_call in enumerate(message.tool_calls or []):
            calls.append(
                ToolCall(
                    id=tool_call.id or f"call_{index}",
                    name=tool_call.function.name,
                    arguments=_parse_arguments(tool_call.function.arguments),
                )
            )
        usage = getattr(resp, "usage", None)
        return ChatResponse(
            text=text,
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def _consume_stream(self, stream: Iterable[Any], on_text: Callable[[str], None] | None) -> ChatResponse:
        parts: list[str] = []
        slots: dict[int, dict[str, str]] = {}
        prompt = 0
        completion = 0
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt = getattr(usage, "prompt_tokens", 0) or prompt
                completion = getattr(usage, "completion_tokens", 0) or completion
            for choice in getattr(chunk, "choices", None) or []:
                delta = choice.delta
                if delta is None:
                    continue
                if getattr(delta, "content", None):
                    parts.append(delta.content)
                    if on_text:
                        on_text(delta.content)
                for tool_call in getattr(delta, "tool_calls", None) or []:
                    slot = slots.setdefault(tool_call.index, {"id": "", "name": "", "arguments": ""})
                    if getattr(tool_call, "id", None):
                        slot["id"] = tool_call.id
                    function = getattr(tool_call, "function", None)
                    if function is None:
                        continue
                    if getattr(function, "name", None):
                        slot["name"] += function.name
                    if getattr(function, "arguments", None):
                        slot["arguments"] += function.arguments
        calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=_parse_arguments(slot["arguments"]),
            )
            for index, slot in sorted(slots.items())
        ]
        return ChatResponse(
            text="".join(parts),
            tool_calls=calls,
            prompt_tokens=prompt,
            completion_tokens=completion,
        )
