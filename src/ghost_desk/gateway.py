"""Phone door. One allowlisted chat reaches the same desk agent. The token is never printed."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ghost_desk.agent import DeskSession, run_turn
from ghost_desk.config import Config
from ghost_desk.memory import Memory

HELP = """Ghost Desk on your phone.
Say something and the desk answers with the brain you set up.
/new starts a fresh conversation.
/status shows the brain.
/help shows this note."""


@dataclass
class Incoming:
    update_id: int
    chat_id: str
    user_id: str
    text: str
    private: bool


class GatewayError(RuntimeError):
    pass


def redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "stored")
    return text


def _parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def load_gateway_env(data_dir: Path | None = None) -> dict[str, str]:
    """Token and allowlist. Existing grok-gateway file is only a fallback. Nothing is copied into git."""
    merged: dict[str, str] = {}
    home = Path.home()
    for path in (
        home / "grok-gateway" / "gateway.env",
        home / ".ghost-desk" / "gateway.env",
        (data_dir or home / ".ghost-desk") / "gateway.env",
    ):
        merged.update(_parse_env(path))
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        merged["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("GHOST_TELEGRAM_TOKEN"):
        merged["TELEGRAM_BOT_TOKEN"] = os.environ["GHOST_TELEGRAM_TOKEN"]
    if os.environ.get("TELEGRAM_ALLOWED_USERS"):
        merged["TELEGRAM_ALLOWED_USERS"] = os.environ["TELEGRAM_ALLOWED_USERS"]
    if os.environ.get("GHOST_TELEGRAM_ALLOWED_USERS"):
        merged["TELEGRAM_ALLOWED_USERS"] = os.environ["GHOST_TELEGRAM_ALLOWED_USERS"]
    return merged


def allowed_users(env: dict[str, str]) -> set[str]:
    raw = env.get("TELEGRAM_ALLOWED_USERS") or env.get("GHOST_TELEGRAM_ALLOWED_USERS") or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


class TelegramChannel:
    def __init__(self, token: str, http=None, timeout: int = 25):
        self.token = token
        self.http = http
        self.timeout = timeout

    def _call(self, method: str, payload: dict):
        if self.http is not None:
            try:
                return self.http(method, payload)
            except Exception as exc:
                raise GatewayError(redact(str(exc), self.token)) from exc
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout + 15) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise GatewayError(redact(str(exc.reason), self.token)) from exc
        if not isinstance(body, dict) or not body.get("ok"):
            detail = ""
            if isinstance(body, dict):
                detail = str(body.get("description") or "")
            raise GatewayError(redact(detail or "telegram failed", self.token))
        return body.get("result")

    def poll(self, offset: int | None) -> list[Incoming]:
        payload: dict = {"timeout": self.timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload) or []
        incoming: list[Incoming] = []
        for item in result:
            message = item.get("message") or {}
            chat = message.get("chat") or {}
            user = message.get("from") or {}
            incoming.append(
                Incoming(
                    update_id=int(item.get("update_id") or 0),
                    chat_id=str(chat.get("id") or ""),
                    user_id=str(user.get("id") or ""),
                    text=str(message.get("text") or ""),
                    private=chat.get("type") == "private",
                )
            )
        return incoming

    def send(self, chat_id: str, text: str) -> None:
        body = text or "(empty)"
        for start in range(0, len(body), 4000):
            self._call(
                "sendMessage",
                {"chat_id": chat_id, "text": body[start : start + 4000], "disable_web_page_preview": True},
            )


def _offset_path(data_dir: Path) -> Path:
    return data_dir / "telegram-offset.txt"


def _sessions_path(data_dir: Path) -> Path:
    return data_dir / "gateway-sessions.json"


def load_offset(data_dir: Path) -> int | None:
    path = _offset_path(data_dir)
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return int(raw)


def save_offset(data_dir: Path, offset: int) -> None:
    _offset_path(data_dir).write_text(str(offset) + "\n", encoding="utf-8")


def load_session_map(data_dir: Path) -> dict[str, str]:
    path = _sessions_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def save_session_map(data_dir: Path, mapping: dict[str, str]) -> None:
    _sessions_path(data_dir).write_text(json.dumps(mapping) + "\n", encoding="utf-8")


def reply_for(text: str, *, config: Config, memory: Memory, session: DeskSession, client=None) -> str:
    stripped = (text or "").strip()
    if stripped in {"/help", "/start"}:
        return HELP
    if stripped == "/status":
        return f"{config.provider or 'brain'}  {config.model}"
    if stripped == "/new":
        fresh = DeskSession(personality=session.personality)
        session.id = fresh.id
        session.history = []
        session.plan = fresh.plan
        return f"new conversation {session.id}"
    result = run_turn(stripped, config=config, memory=memory, session=session, client=client, depth=0)
    return result.text or "(empty)"


def handle_incoming(
    item: Incoming,
    *,
    allowed: set[str],
    channel: TelegramChannel,
    config: Config,
    memory: Memory,
    sessions: dict[str, str],
    client=None,
) -> str:
    """Return what was sent. Strangers and groups are acked by the caller and get no reply."""
    if not item.private or item.user_id not in allowed or not item.text.strip():
        return ""
    session_id = sessions.get(item.chat_id, "")
    session = DeskSession(id=session_id) if session_id else DeskSession()
    if session_id:
        session.history = memory.load_transcript(session_id)
    answer = reply_for(item.text, config=config, memory=memory, session=session, client=client)
    channel.send(item.chat_id, answer)
    sessions[item.chat_id] = session.id
    return answer


def serve(
    config: Config,
    *,
    channel: TelegramChannel | None = None,
    allowed: set[str] | None = None,
    client=None,
    max_rounds: int | None = None,
    output=print,
) -> int:
    data = config.data_path()
    data.mkdir(parents=True, exist_ok=True)
    env = load_gateway_env(data)
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    people = allowed if allowed is not None else allowed_users(env)
    if channel is None:
        if not token:
            output("Telegram token is missing. Put it in gateway.env. It is not printed.")
            return 2
        if not people:
            output("Allowlist is empty. Set TELEGRAM_ALLOWED_USERS.")
            return 2
        channel = TelegramChannel(token)
    output(f"telegram listening  allowlist={len(people)}")
    memory = Memory(data)
    sessions = load_session_map(data)
    rounds = 0
    try:
        while max_rounds is None or rounds < max_rounds:
            rounds += 1
            offset = load_offset(data)
            try:
                batch = channel.poll(offset)
            except GatewayError as exc:
                output(redact(str(exc), token))
                return 2
            if not batch:
                if max_rounds is not None:
                    break
                continue
            for item in batch:
                try:
                    handle_incoming(
                        item,
                        allowed=people,
                        channel=channel,
                        config=config,
                        memory=memory,
                        sessions=sessions,
                        client=client,
                    )
                except GatewayError as exc:
                    output(redact(str(exc), token))
                    return 2
                save_session_map(data, sessions)
                save_offset(data, item.update_id + 1)
    finally:
        memory.close()
    return 0
