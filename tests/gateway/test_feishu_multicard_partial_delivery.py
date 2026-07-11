"""
Tests for Feishu multi-card partial delivery.
When a multi-card final response fails after at least one card has been
delivered, the adapter must return PARTIALLY_DELIVERED so the base layer
does not retry or fall back to a legacy text payload.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import FinalDeliveryState, effective_delivery_state
from plugins.platforms.feishu.adapter import FeishuAdapter


class _FakeResponse:
    code = 0
    msg = "ok"
    data = SimpleNamespace(message_id="om_1")

    def success(self):
        return True


class _FakeFailure:
    code = 99992402
    msg = "field validation failed"
    data = None

    def success(self):
        return False


def _make_adapter():
    cfg = PlatformConfig(
        enabled=True,
        token="fake",
        typing_indicator=False,
        extra={"final_response_format": "card"},
    )
    adapter = FeishuAdapter(cfg)
    adapter._client = object()
    return adapter


@pytest.mark.asyncio
async def test_second_card_api_failure_returns_partial_not_success():
    """When send() fails on the 2nd card after 1st succeeded, return partial."""
    adapter = _make_adapter()
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        if kwargs["msg_type"] == "interactive" and len(calls) == 2:
            return _FakeFailure()
        return _FakeResponse()

    adapter._feishu_send_with_retry = fake_send

    # Generate enough text to produce at least 2 card payloads
    long_text = "\n\n".join(f"段 {i}: " + "x" * 500 for i in range(1, 101))

    result = await adapter.send("oc_1", long_text, metadata={"hermes_final_response": True})

    assert result.success is False
    assert result.delivery_state is FinalDeliveryState.PARTIALLY_DELIVERED
    assert result.raw_response["delivered_cards"] == 1


@pytest.mark.asyncio
async def test_second_card_exception_returns_partial_not_legacy():
    """When send() raises on the 2nd card after 1st succeeded, return partial."""
    adapter = _make_adapter()
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        if kwargs["msg_type"] == "interactive" and len(calls) == 2:
            raise RuntimeError("forced")
        return _FakeResponse()

    adapter._feishu_send_with_retry = fake_send

    long_text = "\n\n".join(f"段 {i}: " + "x" * 500 for i in range(1, 101))

    result = await adapter.send("oc_1", long_text, metadata={"hermes_final_response": True})

    assert result.success is False
    assert result.delivery_state is FinalDeliveryState.PARTIALLY_DELIVERED
    # No legacy text sends were attempted — only interactive (card) calls
    assert all(c["msg_type"] == "interactive" for c in calls)


@pytest.mark.asyncio
async def test_first_card_api_failure_still_falls_back():
    """When the very first card fails, no partial — safe to fall back."""
    adapter = _make_adapter()
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return _FakeFailure()

    adapter._feishu_send_with_retry = fake_send

    long_text = "\n\n".join(f"段 {i}: " + "x" * 500 for i in range(1, 101))

    result = await adapter.send("oc_1", long_text, metadata={"hermes_final_response": True})

    # Should NOT be partial (0 cards delivered), should have fallen through
    # to legacy text send.
    assert result.delivery_state is not FinalDeliveryState.PARTIALLY_DELIVERED
    # At least one non-interactive call was made (legacy fallback)
    assert any(c["msg_type"] != "interactive" for c in calls)


@pytest.mark.asyncio
async def test_try_send_final_rich_second_card_failure_returns_partial():
    """try_send_final_rich_response returns partial on second card failure."""
    adapter = _make_adapter()
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        if kwargs["msg_type"] == "interactive" and len(calls) == 2:
            return _FakeFailure()
        return _FakeResponse()

    adapter._feishu_send_with_retry = fake_send

    # Produce enough blocks to trigger multi-card split
    long_text = "\n\n".join(f"段 {i}: " + "x" * 500 for i in range(1, 101))

    # We need a minimal image to enter the rich path
    adapter._download_remote_image = AsyncMock(return_value="/tmp/fake.png")
    adapter._upload_image_for_card = AsyncMock(return_value="img_key")

    result = await adapter.try_send_final_rich_response(
        chat_id="oc_1",
        original_response=long_text,
        text_content=long_text,
        images=[("http://example.com/img.png", "test")],
        media_files=[],
        local_files=[],
        force_document_attachments=False,
        reply_to=None,
        metadata={"hermes_final_response": True},
    )

    assert result is not None
    assert result.success is False
    assert result.delivery_state is FinalDeliveryState.PARTIALLY_DELIVERED
    assert result.raw_response["delivered_cards"] == 1