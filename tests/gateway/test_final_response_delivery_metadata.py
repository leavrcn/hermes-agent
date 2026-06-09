"""Tests for gateway final-response delivery metadata."""

import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource, build_session_key


class DummyFeishuAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="fake-token"), Platform.FEISHU)
        self._busy_text_mode = ""
        self.sent = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append({"chat_id": chat_id, "content": content, "reply_to": reply_to, "metadata": metadata})
        return SendResult(success=True, message_id="m1")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


def _make_event() -> MessageEvent:
    return MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.FEISHU,
            chat_id="oc_123",
            chat_type="group",
            thread_id="thread-1",
        ),
        message_id="msg-1",
    )


@pytest.mark.asyncio
async def test_final_text_response_carries_final_response_metadata_marker():
    adapter = DummyFeishuAdapter()

    async def handler(_event):
        await asyncio.sleep(0)
        return "final answer"

    async def hold_typing(_chat_id, interval=2.0, metadata=None, stop_event=None):
        await asyncio.Event().wait()

    adapter.set_message_handler(handler)
    adapter._keep_typing = hold_typing

    event = _make_event()
    await adapter._process_message_background(event, build_session_key(event.source))

    assert adapter.sent == [
        {
            "chat_id": "oc_123",
            "content": "final answer",
            "reply_to": "msg-1",
            "metadata": {
                "thread_id": "thread-1",
                "notify": True,
                "hermes_final_response": True,
            },
        }
    ]


@pytest.mark.asyncio
async def test_bypass_command_response_does_not_carry_final_response_marker(monkeypatch):
    adapter = DummyFeishuAdapter()

    async def handler(_event):
        await asyncio.sleep(0)
        return "stopped"

    adapter.set_message_handler(handler)
    adapter._active_sessions[build_session_key(_make_event().source)] = asyncio.Event()

    event = _make_event()
    event.text = "/status"

    await adapter.handle_message(event)

    assert adapter.sent == [
        {
            "chat_id": "oc_123",
            "content": "stopped",
            "reply_to": "msg-1",
            "metadata": {"thread_id": "thread-1"},
        }
    ]
