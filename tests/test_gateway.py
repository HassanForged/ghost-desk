"""Telegram door. No network. The token never appears in a reply or an error line."""

from ghost_desk.client import ChatResponse
from ghost_desk.config import Config
from ghost_desk.gateway import Incoming, TelegramChannel, handle_incoming, load_offset, redact, serve


class Pong:
    def complete(self, messages, tools=None, *, model=None, stream=True, on_text=None):
        return ChatResponse(text="pong from the desk")


def test_stranger_is_ignored_and_owner_gets_a_reply(tmp_path):
    sent = []

    def http(method, payload):
        if method == "getUpdates":
            return [
                {"update_id": 7, "message": {"text": "steal", "from": {"id": 1}, "chat": {"id": 1, "type": "private"}}},
                {"update_id": 8, "message": {"text": "hi", "from": {"id": 5}, "chat": {"id": 9, "type": "private"}}},
            ]
        sent.append((method, payload))
        return {"message_id": 1}

    channel = TelegramChannel("test-token", http=http, timeout=0)
    cfg = Config(provider="xai-oauth", model="grok-4.6", data_dir=str(tmp_path), working_directory=str(tmp_path))
    lines = []
    code = serve(cfg, channel=channel, allowed={"5"}, client=Pong(), max_rounds=1, output=lines.append)
    assert code == 0
    assert load_offset(tmp_path) == 9
    assert sent[0][0] == "sendMessage"
    assert sent[0][1]["chat_id"] == "9"
    assert "pong from the desk" in sent[0][1]["text"]
    assert "test-token" not in "\n".join(lines)
    assert all("steal" not in payload.get("text", "") for _method, payload in sent)


def test_offset_waits_until_send_succeeds(tmp_path):
    def http(method, payload):
        if method == "getUpdates":
            return [
                {"update_id": 3, "message": {"text": "hi", "from": {"id": 5}, "chat": {"id": 9, "type": "private"}}},
            ]
        raise RuntimeError("bot test-token failed")

    channel = TelegramChannel("test-token", http=http, timeout=0)
    cfg = Config(model="m", data_dir=str(tmp_path), working_directory=str(tmp_path))
    lines = []
    code = serve(cfg, channel=channel, allowed={"5"}, client=Pong(), max_rounds=1, output=lines.append)
    assert code == 2
    assert load_offset(tmp_path) is None
    assert "test-token" not in "\n".join(lines)
    assert redact("bot test-token failed", "test-token") == "bot stored failed"


def test_help_does_not_call_the_model(tmp_path):
    class Boom:
        def complete(self, *args, **kwargs):
            raise AssertionError("help should not call the model")

    sent = []

    def http(method, payload):
        if method == "sendMessage":
            sent.append(payload["text"])
        return {}

    item = Incoming(update_id=1, chat_id="9", user_id="5", text="/help", private=True)
    from ghost_desk.memory import Memory

    memory = Memory(tmp_path)
    try:
        handle_incoming(
            item,
            allowed={"5"},
            channel=TelegramChannel("test-token", http=http),
            config=Config(provider="xai-oauth", model="grok-4.6", data_dir=str(tmp_path)),
            memory=memory,
            sessions={},
            client=Boom(),
        )
    finally:
        memory.close()
    assert "/new" in sent[0]
    assert "test-token" not in sent[0]
