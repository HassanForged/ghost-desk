"""ChatGPT / Codex subscription via the public device-code endpoints, plus an API key fallback.

The device grant is the Codex CLI flow: usercode, then a server-minted PKCE verifier, then the token endpoint.
Subscription chat goes to the Codex responses endpoint. An API key uses api.openai.com chat completions.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ghost_desk.client import ChatResponse, OpenAIChatClient, ToolCall
from ghost_desk.config import Config
from ghost_desk.oauth import OAuthError, OAuthSpec, device_login, load_tokens, save_tokens
from ghost_desk.providers.base import Provider, post_json, status_error

# Public Codex CLI client id.
SPEC = OAuthSpec(
    provider="openai",
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    scope="openid profile email offline_access",
    device_url="https://auth.openai.com/api/accounts/deviceauth/usercode",
    token_url="https://auth.openai.com/oauth/token",
    flow="openai_device",
    poll_url="https://auth.openai.com/api/accounts/deviceauth/token",
    verification_url="https://auth.openai.com/codex/device",
    redirect_uri="https://auth.openai.com/deviceauth/callback",
)
API_BASE = "https://api.openai.com/v1"
CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODEL = "gpt-4o-mini"
NOTE = "ChatGPT device login can be disabled in account security settings. An API key still works."


class OpenAISub(Provider):
    def __init__(self, config, http=None, browser=None, sdk=None, transport=None, sleep=None):
        super().__init__(
            id="openai",
            auth_mode=config.auth_mode or "oauth",
            model=config.model or DEFAULT_MODEL,
            base_url=config.base_url or API_BASE,
        )
        self._config = config
        self._http = http
        self._browser = browser
        self._sdk = sdk
        self._transport = transport
        self._sleep = sleep

    def authenticate(self, output: Callable[[str], None]) -> None:
        try:
            tokens = device_login(
                SPEC,
                output=output,
                http=self._http,
                browser=self._browser,
                sleep=self._sleep,
            )
        except OAuthError as exc:
            raise OAuthError(f"{exc}. {NOTE}") from exc
        save_tokens(
            self._config.data_path() / "auth.json",
            tokens,
            provider=self._config.provider or "openai-codex",
        )
        output("ChatGPT subscription saved on this machine.")

    def chat(self, messages, tools=None, *, model=None, stream=False, on_text=None) -> ChatResponse:
        if self.auth_mode != "oauth":
            runtime = Config(
                api_key=self._config.api_key,
                base_url=self._config.base_url or API_BASE,
                model=self.model,
                working_directory=self._config.working_directory,
                data_dir=self._config.data_dir,
                provider="openai",
                auth_mode="api_key",
            )
            client = OpenAIChatClient(runtime, sdk=self._sdk, attempts=2)
            return client.complete(messages, tools, model=model or self.model, stream=stream, on_text=on_text)
        return self._codex(messages, tools, model=model or self.model, on_text=on_text if stream else None)

    def list_models(self) -> list[str]:
        return [self.model] if self.model else []

    def _codex(self, messages, tools, *, model: str, on_text) -> ChatResponse:
        tokens = load_tokens(self._config.data_path() / "auth.json", provider=self._config.provider or "openai-codex")
        access = self.access_token or (tokens.access_token if tokens else "")
        account = tokens.account_id if tokens else ""
        if not access:
            from ghost_desk.client import MissingKey

            raise MissingKey("Not signed in. Run ghost setup.")
        if not account:
            from ghost_desk.client import ClientError

            raise ClientError("ChatGPT login has no account id. Run ghost setup again.")
        instructions, items, tool_defs = _to_responses(messages, tools)
        payload: dict = {"model": model, "input": items, "stream": False}
        if instructions:
            payload["instructions"] = instructions
        if tool_defs:
            payload["tools"] = tool_defs
        headers = {
            "Authorization": f"Bearer {access}",
            "ChatGPT-Account-Id": account,
            "OpenAI-Beta": "responses=v1",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ghost-desk",
        }
        status, body = post_json(CODEX_URL, payload, headers, transport=self._transport)
        if status >= 400 or status == 0:
            status_error(status, body)
        reply = _from_responses(body)
        if on_text and reply.text:
            on_text(reply.text)
        return reply


def _to_responses(messages, tools) -> tuple[str, list, list]:
    instructions: list[str] = []
    items: list = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content)
        if role == "system":
            if content:
                instructions.append(content)
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id") or "",
                    "output": content,
                }
            )
            continue
        if role == "assistant" and message.get("tool_calls"):
            if content:
                items.append({"role": "assistant", "content": content})
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "arguments": function.get("arguments") or "{}",
                    }
                )
            continue
        if role in {"user", "assistant"} and content:
            items.append({"role": role, "content": content})
    tool_defs = []
    for tool in tools or []:
        function = tool.get("function") or tool
        tool_defs.append(
            {
                "type": "function",
                "name": function.get("name") or "",
                "description": function.get("description") or "",
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return "\n".join(instructions), items, tool_defs


def _from_responses(body: dict) -> ChatResponse:
    parts: list[str] = []
    calls: list[ToolCall] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    parts.append(str(block.get("text") or ""))
        elif item.get("type") == "function_call":
            raw = item.get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                arguments = {"_raw": raw}
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}
            calls.append(
                ToolCall(
                    id=str(item.get("call_id") or item.get("id") or f"call_{len(calls)}"),
                    name=str(item.get("name") or ""),
                    arguments=arguments,
                )
            )
    if not parts and isinstance(body.get("output_text"), str):
        parts.append(body["output_text"])
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return ChatResponse(
        text="".join(parts),
        tool_calls=calls,
        prompt_tokens=int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    )
