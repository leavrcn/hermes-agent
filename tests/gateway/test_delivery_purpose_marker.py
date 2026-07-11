"""Behavior tests for delivery_purpose → hermes_final_response mapping.

Task 9: external final-response entry points (cron, handoff) set
``delivery_purpose="assistant_final"``.  The shared delivery translation maps
that semantic field to ``hermes_final_response=True`` only for Feishu.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron.scheduler import _deliver_result
from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.delivery import DeliveryRouter, DeliveryTarget
from gateway.platforms.base import SendResult
from gateway.run import GatewayRunner


class RecordingAdapter:
    """Minimal adapter that records the metadata it receives."""

    def __init__(self):
        self.calls = []

    async def send(self, chat_id, content, metadata=None):
        self.calls.append(
            {"chat_id": chat_id, "content": content, "metadata": metadata}
        )
        return SendResult(success=True, message_id="om_test")


def _feishu_gateway_config() -> GatewayConfig:
    return GatewayConfig(
        platforms={
            Platform.FEISHU: PlatformConfig(
                enabled=True,
                home_channel=HomeChannel(
                    platform=Platform.FEISHU,
                    chat_id="oc_test_chat",
                    name="Feishu test home",
                ),
                extra={},
            )
        },
        filter_silence_narration=False,
    )


@pytest.mark.asyncio
async def test_delivery_router_marks_assistant_final_for_feishu(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.delivery.get_hermes_home", lambda: tmp_path)
    adapter = RecordingAdapter()
    router = DeliveryRouter(
        GatewayConfig(), adapters={Platform.FEISHU: adapter}
    )
    target = DeliveryTarget(
        platform=Platform.FEISHU,
        chat_id="oc_test_chat",
        is_explicit=True,
    )

    await router._deliver_to_platform(
        target,
        "Final cron response text",
        metadata={"delivery_purpose": "assistant_final", "job_id": "job1"},
    )

    assert len(adapter.calls) == 1
    sent_meta = adapter.calls[0]["metadata"]
    assert sent_meta is not None
    assert sent_meta.get("hermes_final_response") is True


@pytest.mark.asyncio
async def test_non_feishu_platform_does_not_get_feishu_marker(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.delivery.get_hermes_home", lambda: tmp_path)
    adapter = RecordingAdapter()
    router = DeliveryRouter(
        GatewayConfig(), adapters={Platform.TELEGRAM: adapter}
    )
    target = DeliveryTarget(
        platform=Platform.TELEGRAM,
        chat_id="123456",
        is_explicit=True,
    )

    await router._deliver_to_platform(
        target,
        "Final response",
        metadata={"delivery_purpose": "assistant_final", "job_id": "job2"},
    )

    sent_meta = adapter.calls[0]["metadata"]
    assert sent_meta["delivery_purpose"] == "assistant_final"
    assert "hermes_final_response" not in sent_meta


@pytest.mark.asyncio
async def test_no_delivery_purpose_means_no_final_marker(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.delivery.get_hermes_home", lambda: tmp_path)
    adapter = RecordingAdapter()
    router = DeliveryRouter(
        GatewayConfig(), adapters={Platform.FEISHU: adapter}
    )
    target = DeliveryTarget(
        platform=Platform.FEISHU,
        chat_id="oc_test_chat",
        is_explicit=True,
    )

    await router._deliver_to_platform(
        target,
        "Notice text",
        metadata={"job_id": "job3"},
    )

    assert "hermes_final_response" not in adapter.calls[0]["metadata"]


async def _assert_non_final_purpose_has_no_marker(
    tmp_path,
    monkeypatch,
    purpose,
):
    monkeypatch.setattr("gateway.delivery.get_hermes_home", lambda: tmp_path)
    adapter = RecordingAdapter()
    router = DeliveryRouter(
        GatewayConfig(), adapters={Platform.FEISHU: adapter}
    )
    target = DeliveryTarget(
        platform=Platform.FEISHU,
        chat_id="oc_test_chat",
        is_explicit=True,
    )

    await router._deliver_to_platform(
        target,
        f"{purpose} update",
        metadata={"delivery_purpose": purpose, "job_id": "job4"},
    )

    sent_meta = adapter.calls[0]["metadata"]
    assert sent_meta["delivery_purpose"] == purpose
    assert "hermes_final_response" not in sent_meta


@pytest.mark.asyncio
async def test_notice_purpose_does_not_get_marker(tmp_path, monkeypatch):
    await _assert_non_final_purpose_has_no_marker(tmp_path, monkeypatch, "notice")


@pytest.mark.asyncio
async def test_typing_purpose_does_not_get_marker(tmp_path, monkeypatch):
    await _assert_non_final_purpose_has_no_marker(tmp_path, monkeypatch, "typing")


@pytest.mark.asyncio
async def test_progress_purpose_does_not_get_marker(tmp_path, monkeypatch):
    await _assert_non_final_purpose_has_no_marker(tmp_path, monkeypatch, "progress")


def _run_standalone_feishu_cron(monkeypatch, *, adapters=None, loop=None):
    """Exercise cron -> real _send_to_platform -> standalone Feishu boundary."""
    captured = []

    async def fake_registry_send(
        platform_name,
        pconfig,
        chat_id,
        message,
        thread_id=None,
        metadata=None,
    ):
        captured.append(
            {
                "platform": platform_name,
                "chat_id": chat_id,
                "message": message,
                "thread_id": thread_id,
                "metadata": metadata,
            }
        )
        return {"success": True, "message_id": "om_cron"}

    monkeypatch.setattr(
        "tools.send_message_tool._registry_standalone_send",
        fake_registry_send,
    )
    config = _feishu_gateway_config()
    job = {
        "id": "cron-final",
        "deliver": "origin",
        "origin": {"platform": "feishu", "chat_id": "oc_test_chat"},
    }
    with patch("gateway.config.load_gateway_config", return_value=config), patch(
        "cron.scheduler.load_config",
        return_value={"cron": {"wrap_response": False}},
    ):
        result = _deliver_result(
            job,
            "Standalone cron final",
            adapters=adapters,
            loop=loop,
        )
    return result, captured


def test_cron_standalone_feishu_maps_assistant_final_marker(monkeypatch):
    result, captured = _run_standalone_feishu_cron(monkeypatch)

    assert result is None
    assert len(captured) == 1
    assert captured[0]["metadata"]["delivery_purpose"] == "assistant_final"
    assert captured[0]["metadata"]["hermes_final_response"] is True


def test_cron_live_failure_fallback_keeps_feishu_final_marker(monkeypatch):
    adapter = RecordingAdapter()
    loop = MagicMock()
    loop.is_running.return_value = True

    def fail_live_send(coro, _loop):
        coro.close()
        future = MagicMock()
        future.result.side_effect = RuntimeError("live delivery failed")
        return future

    monkeypatch.setattr(
        "agent.async_utils.safe_schedule_threadsafe",
        fail_live_send,
    )

    result, captured = _run_standalone_feishu_cron(
        monkeypatch,
        adapters={Platform.FEISHU: adapter},
        loop=loop,
    )

    assert result is None
    assert len(captured) == 1
    assert captured[0]["metadata"]["delivery_purpose"] == "assistant_final"
    assert captured[0]["metadata"]["hermes_final_response"] is True


@pytest.mark.asyncio
async def test_feishu_standalone_sender_forwards_mapped_metadata(monkeypatch):
    """The plugin standalone boundary must forward mapped metadata to send()."""
    from plugins.platforms.feishu import adapter as feishu_adapter_module

    captured = []

    class FakeTransientFeishuAdapter:
        _domain_name = "feishu"

        def __init__(self, _config):
            self._client = None

        def _build_lark_client(self, _domain):
            return object()

        async def send(self, chat_id, message, metadata=None):
            captured.append(
                {"chat_id": chat_id, "message": message, "metadata": metadata}
            )
            return SendResult(success=True, message_id="om_standalone")

    monkeypatch.setattr(feishu_adapter_module, "FEISHU_AVAILABLE", True)
    monkeypatch.setattr(
        feishu_adapter_module,
        "FeishuAdapter",
        FakeTransientFeishuAdapter,
    )

    result = await feishu_adapter_module._standalone_send(
        SimpleNamespace(),
        "oc_test_chat",
        "Standalone final",
        metadata={
            "delivery_purpose": "assistant_final",
            "hermes_final_response": True,
        },
    )

    assert result["success"] is True
    assert captured[0]["metadata"] == {
        "delivery_purpose": "assistant_final",
        "hermes_final_response": True,
    }


@pytest.mark.asyncio
async def test_handoff_feishu_final_reaches_adapter_with_final_marker():
    """Execute _process_handoff and verify the adapter-observed final marker."""
    config = _feishu_gateway_config()
    adapter = RecordingAdapter()
    adapter.create_handoff_thread = AsyncMock(return_value=None)

    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = config
    runner.adapters = {Platform.FEISHU: adapter}
    runner.delivery_router = DeliveryRouter(config, runner.adapters)
    runner.session_store = SimpleNamespace()
    runner._async_session_store = SimpleNamespace(
        _store=runner.session_store,
        get_or_create_session=AsyncMock(return_value=SimpleNamespace()),
        switch_session=AsyncMock(return_value=SimpleNamespace()),
    )
    runner._evict_cached_agent = MagicMock()
    runner._release_running_agent_state = MagicMock()
    runner._handle_message = AsyncMock(return_value="Handoff final response")

    await GatewayRunner._process_handoff(
        runner,
        {
            "id": "cli-session",
            "title": "CLI work",
            "handoff_platform": "feishu",
        },
    )

    assert len(adapter.calls) == 1
    sent_meta = adapter.calls[0]["metadata"]
    assert sent_meta["delivery_purpose"] == "assistant_final"
    assert sent_meta["hermes_final_response"] is True
