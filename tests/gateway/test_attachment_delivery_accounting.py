"""Tests for attachment delivery accounting in BasePlatformAdapter.

Verifies that failed attachments (images, voice, video, document) are
recorded via _record_delivery() and affect ProcessingOutcome.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
)
from gateway.session import SessionSource, build_session_key


class _AccountableAdapter(BasePlatformAdapter):
    """Adapter that captures processing outcome for assertions."""

    def __init__(self, platform=Platform.FEISHU):
        super().__init__(PlatformConfig(enabled=True, token="test"), platform)
        self.sent_text = []
        self.outcomes = []

    async def connect(self, *, is_reconnect: bool = False):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content=None, **kwargs):
        self.sent_text.append((chat_id, content, kwargs))
        return SendResult(success=True, message_id="text-1")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id, "type": "dm"}

    async def try_send_final_rich_response(self, **kwargs):
        return None  # Never short-circuit in these tests

    async def on_processing_complete(self, event, outcome):
        self.outcomes.append(outcome)


def _event(message_type=MessageType.TEXT):
    source = SessionSource(
        platform=Platform.FEISHU,
        chat_id="chat-1",
        chat_type="dm",
    )
    return MessageEvent(
        text="process this",
        message_type=message_type,
        source=source,
        message_id="msg-1",
    )


def _safe_media_path(tmp_path, monkeypatch, name="file.pdf"):
    """Create a file under a safe root so validate_media_delivery_path accepts it."""
    root = tmp_path / "media-cache"
    media_file = root / name
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"media")
    monkeypatch.setattr("gateway.platforms.base.MEDIA_DELIVERY_SAFE_ROOTS", (root,))
    return media_file.resolve()


def _configure_auto_tts(adapter, audio_path, monkeypatch):
    adapter._should_auto_tts_for_chat = lambda _chat_id: True
    monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)
    monkeypatch.setattr(
        "tools.tts_tool.text_to_speech_tool",
        lambda **_kwargs: json.dumps({"file_path": str(audio_path)}),
    )


# ── auto-TTS delivery accounting ───────────────────────────────────────

@pytest.mark.asyncio
async def test_tts_only_success_marks_processing_success(tmp_path, monkeypatch):
    """A captioned Telegram TTS response is the only delivery and must count."""
    audio_path = tmp_path / "response.ogg"
    audio_path.write_bytes(b"audio")
    adapter = _AccountableAdapter(platform=Platform.TELEGRAM)
    adapter._message_handler = AsyncMock(return_value="Spoken response")
    adapter.play_tts = AsyncMock(
        return_value=SendResult(success=True, message_id="voice-1")
    )
    _configure_auto_tts(adapter, audio_path, monkeypatch)
    event = _event(MessageType.VOICE)

    await adapter._process_message_background(event, build_session_key(event.source))

    assert adapter.play_tts.await_count == 1
    assert adapter.sent_text == []
    assert adapter.outcomes == [ProcessingOutcome.SUCCESS]


@pytest.mark.asyncio
async def test_tts_failure_then_text_success_stays_processing_failure(tmp_path, monkeypatch):
    """Text fallback success must not erase an earlier TTS delivery failure."""
    audio_path = tmp_path / "response.ogg"
    audio_path.write_bytes(b"audio")
    adapter = _AccountableAdapter(platform=Platform.TELEGRAM)
    adapter._message_handler = AsyncMock(return_value="Spoken response")
    adapter.play_tts = AsyncMock(
        return_value=SendResult(success=False, error="voice rejected")
    )
    _configure_auto_tts(adapter, audio_path, monkeypatch)
    event = _event(MessageType.VOICE)

    await adapter._process_message_background(event, build_session_key(event.source))

    assert adapter.play_tts.await_count == 1
    assert len(adapter.sent_text) == 1
    assert adapter.outcomes == [ProcessingOutcome.FAILURE]


# ── send_document failure ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_document_marks_processing_failure(tmp_path, monkeypatch):
    """A document that returns success=False should produce FAILURE."""
    doc_path = _safe_media_path(tmp_path, monkeypatch, "report.pdf")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"Here is the file\nMEDIA:{doc_path}"
    )
    adapter.send_document = AsyncMock(
        return_value=SendResult(success=False, error="Upload rejected")
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert len(adapter.outcomes) == 1, "Expected exactly one on_processing_complete call"
    assert adapter.outcomes[0] == ProcessingOutcome.FAILURE, (
        f"Expected FAILURE, got {adapter.outcomes[0]}"
    )


@pytest.mark.asyncio
async def test_document_exception_marks_processing_failure(tmp_path, monkeypatch):
    """A document that raises should produce FAILURE."""
    doc_path = _safe_media_path(tmp_path, monkeypatch, "doc.pdf")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"Here is the file\nMEDIA:{doc_path}"
    )
    adapter.send_document = AsyncMock(side_effect=RuntimeError("Network timeout"))

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert len(adapter.outcomes) == 1
    assert adapter.outcomes[0] == ProcessingOutcome.FAILURE


# ── send_voice failure ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_voice_marks_processing_failure(tmp_path, monkeypatch):
    """A voice message that returns success=False should produce FAILURE."""
    audio_path = _safe_media_path(tmp_path, monkeypatch, "audio.ogg")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"Voice message\nMEDIA:{audio_path}"
    )
    adapter.send_voice = AsyncMock(
        return_value=SendResult(success=False, error="Voice send failed")
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert len(adapter.outcomes) == 1
    assert adapter.outcomes[0] == ProcessingOutcome.FAILURE


# ── send_video failure ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_video_marks_processing_failure(tmp_path, monkeypatch):
    """A video that returns success=False should produce FAILURE."""
    video_path = _safe_media_path(tmp_path, monkeypatch, "clip.mp4")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"Check this video\nMEDIA:{video_path}"
    )
    adapter.send_video = AsyncMock(
        return_value=SendResult(success=False, error="Video too large")
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert len(adapter.outcomes) == 1
    assert adapter.outcomes[0] == ProcessingOutcome.FAILURE


# ── send_multiple_images failure (exception) ───────────────────────────

@pytest.mark.asyncio
async def test_failed_image_batch_exception_marks_processing_failure(tmp_path, monkeypatch):
    """send_multiple_images that raises should produce FAILURE."""
    img_path = _safe_media_path(tmp_path, monkeypatch, "image.png")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"Images\n![alt]({img_path})"
    )
    adapter.send_multiple_images = AsyncMock(
        side_effect=RuntimeError("Image upload failed")
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert len(adapter.outcomes) == 1
    assert adapter.outcomes[0] == ProcessingOutcome.FAILURE, (
        f"Expected FAILURE for image batch exception, got {adapter.outcomes[0]}"
    )


@pytest.mark.asyncio
async def test_image_only_success_marks_processing_success():
    """A successful image-only response is an accounted delivery."""
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value="![diagram](https://example.com/diagram.png)"
    )
    adapter.send_multiple_images = AsyncMock(
        return_value=SendResult(success=True, message_id="image-1")
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert adapter.sent_text == []
    assert adapter.outcomes == [ProcessingOutcome.SUCCESS]


@pytest.mark.asyncio
async def test_single_image_failure_returns_aggregate_failure_without_raising():
    """The default image helper must expose a swallowed per-image failure."""
    adapter = _AccountableAdapter()
    adapter.send_image = AsyncMock(
        return_value=SendResult(success=False, error="image rejected")
    )

    result = await adapter.send_multiple_images(
        "chat-1", [("https://example.com/image.png", "diagram")]
    )

    assert result.success is False
    assert "image rejected" in (result.error or "")


@pytest.mark.asyncio
async def test_multiple_images_mixed_results_return_aggregate_failure():
    """Any failed item makes the default image batch fail as a whole."""
    adapter = _AccountableAdapter()
    adapter.send_image = AsyncMock(
        side_effect=[
            SendResult(success=True, message_id="image-1"),
            SendResult(success=False, error="second image rejected"),
        ]
    )

    result = await adapter.send_multiple_images(
        "chat-1",
        [
            ("https://example.com/one.png", "one"),
            ("https://example.com/two.png", "two"),
        ],
    )

    assert result.success is False
    assert "second image rejected" in (result.error or "")


# ── sticky delivery aggregation ────────────────────────────────────────

@pytest.mark.asyncio
async def test_attachment_failure_then_success_still_marks_processing_failure(
    tmp_path, monkeypatch
):
    """A later successful attachment must not erase an earlier failure."""
    first_path = _safe_media_path(tmp_path, monkeypatch, "first.pdf")
    second_path = _safe_media_path(tmp_path, monkeypatch, "second.pdf")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"MEDIA:{first_path}\nMEDIA:{second_path}"
    )
    adapter.send_document = AsyncMock(
        side_effect=[
            SendResult(success=False, error="first rejected"),
            SendResult(success=True, message_id="document-2"),
        ]
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert adapter.send_document.await_count == 2
    assert adapter.outcomes == [ProcessingOutcome.FAILURE]


@pytest.mark.asyncio
async def test_attachment_success_then_failure_marks_processing_failure(
    tmp_path, monkeypatch
):
    """A failed later attachment must make the aggregate delivery fail."""
    first_path = _safe_media_path(tmp_path, monkeypatch, "first.pdf")
    second_path = _safe_media_path(tmp_path, monkeypatch, "second.pdf")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"MEDIA:{first_path}\nMEDIA:{second_path}"
    )
    adapter.send_document = AsyncMock(
        side_effect=[
            SendResult(success=True, message_id="document-1"),
            SendResult(success=False, error="second rejected"),
        ]
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert adapter.send_document.await_count == 2
    assert adapter.outcomes == [ProcessingOutcome.FAILURE]


# ── all attachments succeed ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_attachments_success_marks_processing_success(tmp_path, monkeypatch):
    """All attachments succeeding should produce SUCCESS."""
    doc_path = _safe_media_path(tmp_path, monkeypatch, "doc.pdf")
    audio_path = _safe_media_path(tmp_path, monkeypatch, "audio.ogg")
    adapter = _AccountableAdapter()
    adapter._message_handler = AsyncMock(
        return_value=f"Text content\nMEDIA:{doc_path}\nMEDIA:{audio_path}"
    )
    adapter.send_document = AsyncMock(
        return_value=SendResult(success=True, message_id="doc-1")
    )
    adapter.send_voice = AsyncMock(
        return_value=SendResult(success=True, message_id="voice-1")
    )

    await adapter._process_message_background(_event(), build_session_key(_event().source))

    assert len(adapter.outcomes) == 1
    assert adapter.outcomes[0] == ProcessingOutcome.SUCCESS, (
        f"Expected SUCCESS when all attachments deliver, got {adapter.outcomes[0]}"
    )