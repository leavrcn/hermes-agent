"""Runtime tests for Feishu final response card delivery."""

import json
from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.feishu import FeishuAdapter


class _FakeResponse:
    def __init__(self, message_id="om_1"):
        self.code = 0
        self.msg = "ok"
        self.data = SimpleNamespace(message_id=message_id)

    def success(self):
        return True


def _make_adapter(*, final_response_format="card", markdown_tables="table") -> FeishuAdapter:
    cfg = PlatformConfig(
        enabled=True,
        token="fake-token",
        extra={
            "final_response_format": final_response_format,
            "markdown_tables": markdown_tables,
            "card_schema": "2.0",
        },
    )
    adapter = FeishuAdapter(cfg)
    adapter._client = object()
    return adapter


@pytest.mark.asyncio
async def test_feishu_final_response_card_mode_sends_interactive_card(monkeypatch):
    adapter = _make_adapter(final_response_format="card")
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(adapter, "_feishu_send_with_retry", fake_send)

    result = await adapter.send(
        "oc_123",
        "# 标题\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
        metadata={"thread_id": "t1", "hermes_final_response": True},
    )

    assert result.success is True
    assert len(calls) == 1
    assert calls[0]["msg_type"] == "interactive"
    card = json.loads(calls[0]["payload"])
    assert card["schema"] == "2.0"
    assert card["body"]["elements"][0] == {
        "tag": "markdown",
        "content": "标题",
        "text_size": "heading",
    }
    assert any(element.get("tag") == "table" for element in card["body"]["elements"])


@pytest.mark.asyncio
async def test_feishu_non_final_response_uses_legacy_payload(monkeypatch):
    adapter = _make_adapter(final_response_format="card")
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(adapter, "_feishu_send_with_retry", fake_send)

    await adapter.send("oc_123", "# status", metadata={"thread_id": "t1"})

    assert len(calls) == 1
    assert calls[0]["msg_type"] != "interactive"


@pytest.mark.asyncio
async def test_feishu_final_response_auto_mode_keeps_legacy_when_media_tag_present(monkeypatch, tmp_path):
    media = tmp_path / "out.png"
    media.write_bytes(b"fake")
    adapter = _make_adapter(final_response_format="auto")
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(adapter, "_feishu_send_with_retry", fake_send)

    await adapter.send(
        "oc_123",
        f"结果\nMEDIA:{media}",
        metadata={"thread_id": "t1", "hermes_final_response": True},
    )

    assert len(calls) == 1
    assert calls[0]["msg_type"] != "interactive"


@pytest.mark.asyncio
async def test_feishu_final_response_card_failure_falls_back_to_legacy(monkeypatch):
    adapter = _make_adapter(final_response_format="card")
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        if kwargs["msg_type"] == "interactive":
            raise RuntimeError("card rejected")
        return _FakeResponse("fallback")

    monkeypatch.setattr(adapter, "_feishu_send_with_retry", fake_send)

    result = await adapter.send(
        "oc_123",
        "**hello**",
        metadata={"thread_id": "t1", "hermes_final_response": True},
    )

    assert result.success is True
    assert [call["msg_type"] for call in calls] == ["interactive", "post"]
