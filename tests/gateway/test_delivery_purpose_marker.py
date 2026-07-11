"""TDD tests for delivery_purpose → hermes_final_response marker injection.

Task 9: External final-response entry points (cron, handoff) must set
``delivery_purpose="assistant_final"`` in their send metadata.  The
DeliveryRouter translates this semantic field into the platform-specific
``hermes_final_response=True`` marker when the target is Feishu.

These tests are written FIRST (TDD red phase) and should FAIL until the
production code is modified.
"""

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.delivery import DeliveryRouter, DeliveryTarget


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class RecordingAdapter:
    """Minimal adapter that records the metadata it receives."""

    def __init__(self):
        self.calls = []

    async def send(self, chat_id, content, metadata=None):
        self.calls.append(
            {"chat_id": chat_id, "content": content, "metadata": metadata}
        )
        return {"success": True}


# ---------------------------------------------------------------------------
# 1. DeliveryRouter injects hermes_final_response for Feishu
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_router_marks_assistant_final_for_feishu(tmp_path, monkeypatch):
    """When delivery_purpose=assistant_final and target is Feishu,
    hermes_final_response=True must be injected into send_metadata."""
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


# ---------------------------------------------------------------------------
# 2. Non-Feishu platforms do NOT get the marker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_feishu_platform_does_not_get_feishu_marker(tmp_path, monkeypatch):
    """Even with delivery_purpose=assistant_final, a Telegram target
    should NOT receive hermes_final_response (that's Feishu-specific)."""
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

    assert len(adapter.calls) == 1
    sent_meta = adapter.calls[0]["metadata"]
    # delivery_purpose is preserved (it's just a passthrough key), but
    # hermes_final_response must NOT be injected for non-Feishu platforms
    assert "hermes_final_response" not in (sent_meta or {})


# ---------------------------------------------------------------------------
# 3. Without delivery_purpose, no marker is injected (even on Feishu)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_delivery_purpose_means_no_final_marker(tmp_path, monkeypatch):
    """If delivery_purpose is absent, hermes_final_response must not be
    injected — even on Feishu."""
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
        metadata={"job_id": "job3"},  # no delivery_purpose
    )

    assert len(adapter.calls) == 1
    sent_meta = adapter.calls[0]["metadata"]
    assert "hermes_final_response" not in (sent_meta or {})


# ---------------------------------------------------------------------------
# 4. delivery_purpose != "assistant_final" does NOT trigger the marker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_final_purpose_does_not_get_marker(tmp_path, monkeypatch):
    """delivery_purpose values other than 'assistant_final' (e.g.
    'notice', 'progress') must NOT trigger hermes_final_response."""
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
        "Progress update",
        metadata={"delivery_purpose": "progress", "job_id": "job4"},
    )

    assert len(adapter.calls) == 1
    sent_meta = adapter.calls[0]["metadata"]
    assert "hermes_final_response" not in (sent_meta or {})


# ---------------------------------------------------------------------------
# 5. Cron scheduler sets delivery_purpose on text delivery metadata
# ---------------------------------------------------------------------------

def test_cron_text_delivery_sets_assistant_final_purpose():
    """Cron text delivery metadata must include delivery_purpose='assistant_final'.

    We verify this by inspecting the source code for the route_metadata
    construction in the cron scheduler.  Since we can't easily run the full
    cron pipeline in a unit test, we check the source-level invariant.
    """
    import inspect
    from cron import scheduler as scheduler_mod

    source = inspect.getsource(scheduler_mod)

    # The cron text delivery path should set delivery_purpose on route_metadata
    # that includes "job_id" (the text-delivery metadata, not media).
    # We look for the line that constructs route_metadata with job_id and
    # delivery_purpose.
    assert 'delivery_purpose' in source, (
        "cron/scheduler.py must reference delivery_purpose"
    )

    # More specifically, the route_metadata that includes "job_id" should
    # also include "delivery_purpose": "assistant_final"
    lines = source.splitlines()
    found_purpose_near_job_id = False
    for i, line in enumerate(lines):
        if "delivery_purpose" in line and "assistant_final" in line:
            # Check nearby lines (within 5 lines) for job_id
            context = "\n".join(lines[max(0, i - 5):i + 6])
            if "job_id" in context:
                found_purpose_near_job_id = True
                break

    assert found_purpose_near_job_id, (
        "cron/scheduler.py must set delivery_purpose='assistant_final' "
        "near the route_metadata that includes job_id"
    )


# ---------------------------------------------------------------------------
# 6. Handoff path in gateway/run.py sets delivery_purpose
# ---------------------------------------------------------------------------

def test_handoff_sets_assistant_final_purpose():
    """The handoff send path in gateway/run.py must set
    delivery_purpose='assistant_final' in send_metadata."""
    import inspect
    from gateway import run as run_mod

    source = inspect.getsource(run_mod)

    # Find the handoff send_metadata construction and verify it includes
    # delivery_purpose
    assert 'delivery_purpose' in source, (
        "gateway/run.py must reference delivery_purpose"
    )

    # Check it's near the handoff send path (send_metadata in _process_handoff)
    lines = source.splitlines()
    found = False
    for i, line in enumerate(lines):
        if "delivery_purpose" in line and "assistant_final" in line:
            context = "\n".join(lines[max(0, i - 10):i + 10])
            if "send_metadata" in context or "adapter.send" in context:
                found = True
                break

    assert found, (
        "gateway/run.py must set delivery_purpose='assistant_final' near "
        "the handoff send_metadata / adapter.send path"
    )
