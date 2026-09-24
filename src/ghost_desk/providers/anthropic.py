"""Claude. Official login is a browser PKCE flow inside Claude Code, not a public device code.

Device-code login is not published. This adapter does not pretend it is. Setup uses an API key.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ghost_desk.client import ChatResponse, MissingKey, ToolCall
from ghost_desk.oauth import OAuthError
from ghost_desk.providers.base import Provider, chosen_base, post_json, status_error

NOTE = "Claude has no public device-code login. Use an API key from console.anthropic.com."
DEFAULT_MODEL = "claude-sonnet-4-5"


class Anthropic(Provider):
    def __init__(self, config, transport=None):
        super().__init__(
            id="anthropic",
            auth_mode=config.auth_mode or "api_key",
            model=config.model or DEFAULT_MODEL,
            base_url=chosen_base(config, "https://api.anthropic.com"),
        )
        self._config = config
        self._transport = transport

    def authenticate(self, output: Callable[[str], None]) -> None:
        output(NOTE)
        raise OAuthError(NOTE)

    def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None) -> ChatResponse:
        key = self.access_token or self._config.api_key
        if not key:
            raise MissingKey("No API key. Run ghost setup.")
        oauth = self.auth_mode == "oauth" or key.startswith("sk-ant-oat")
        system, converted = _split(messages)
        payload: dict = {
            "model": model or self.model,
            "max_tokens": 4096,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_tool(tool) for tool in tools]
        root = self.base_url.rstrip("/")
        url = root + "/messages" if root.endswith("/v1") else root + "/v1/messages"
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if oauth:
            headers["Authorization"] = f"Bearer {key}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        else:
            headers["x-api-key"] = key
        status, body = post_json(url, payload, headers, transport=self._transport)
        if status >= 400 or status == 0:
            status_error(status, body)
        reply = _from_messages(body)
        if stream and on_text and reply.text:
            on_text(reply.text)
        return reply

    def list_models(self) -> list[str]:
        return [self.model] if self.model else []


def _tool(tool: dict) -> dict:
    function = tool.get("function") or tool
    return {
        "name": function.get("name") or "",
        "description": function.get("description") or "",
        "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
    }


def _split(messages) -> tuple[str, list]:
    system: list[str] = []
    converted: list = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content)
        if role == "system":
            if content:
                system.append(content)
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.get("tool_call_id") or "",
                            "content": content,
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            blocks: list = []
            if content:
                blocks.append({"type": "text", "text": content})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                raw = function.get("arguments") or "{}"
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    parsed = {"_raw": raw}
                if not isinstance(parsed, dict):
                    parsed = {"value": parsed}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "input": parsed,
                    }
                )
            converted.append({"role": "assistant", "content": blocks or content})
            continue
        if role == "user":
            converted.append({"role": "user", "content": content})
    return "\n".join(system), converted


def _from_messages(body: dict) -> ChatResponse:
    parts: list[str] = []
    calls: list[ToolCall] = []
    for block in body.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
            calls.append(
                ToolCall(
                    id=str(block.get("id") or f"call_{len(calls)}"),
                    name=str(block.get("name") or ""),
                    arguments=arguments,
                )
            )
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return ChatResponse(
        text="".join(parts),
        tool_calls=calls,
        prompt_tokens=int(usage.get("input_tokens") or 0),
        completion_tokens=int(usage.get("output_tokens") or 0),
    )
