from __future__ import annotations

import asyncio

from app.schemas import ChatRequest
from app.services.voice_transcription import (
    VOICE_FALLBACK_TEXT,
    _audio_format_from_url,
    _doubao_result_text,
    transcribe_voice_request,
)


class FakeVoiceTranscriptionClient:
    provider_name = "doubao_asr"
    resource_id = "volc.seedasr.auc"

    def __init__(self, raw: dict | list[dict] | None = None, exc: Exception | None = None) -> None:
        self.raw = raw or {"text": "我在厦门湖里区。", "task_id": "task-1", "query_attempt_count": 1}
        self.exc = exc
        self.calls: list[tuple[str, str]] = []

    async def transcribe(self, audio_url: str, *, uid: str = "") -> dict:
        self.calls.append((audio_url, uid))
        if self.exc:
            raise self.exc
        if isinstance(self.raw, list):
            index = min(len(self.calls) - 1, len(self.raw) - 1)
            return self.raw[index]
        return self.raw


def test_voice_request_is_transcribed_before_runtime() -> None:
    request = ChatRequest(
        content="https://example.com/a.mp3?token=1",
        customer_id="c1",
        corp_id="corp",
        request_context={"msgtype": "voice"},
    )

    updated = asyncio.run(transcribe_voice_request(request, FakeVoiceTranscriptionClient()))

    assert updated.content == "我在厦门湖里区。"
    assert updated.request_context["voice_original_content"] == "https://example.com/a.mp3?token=1"
    assert updated.request_context["voice_transcription"]["status"] == "ok"
    assert updated.request_context["voice_transcription"]["provider"] == "doubao_asr"


def test_voice_request_uses_raw_workflow_audio_url() -> None:
    request = ChatRequest(
        content="[语音消息]",
        customer_id="c1",
        corp_id="corp",
        request_context={
            "msgtype": "voice",
            "raw_workflow_payload": {
                "parameters": {
                    "content": {
                        "content": "https://example.com/raw.mp3",
                        "msgtype": "voice",
                    }
                }
            },
        },
    )
    client = FakeVoiceTranscriptionClient()

    updated = asyncio.run(transcribe_voice_request(request, client))

    assert updated.content == "我在厦门湖里区。"
    assert client.calls == [("https://example.com/raw.mp3", "c1")]


def test_voice_request_does_not_pass_url_when_transcription_fails() -> None:
    request = ChatRequest(
        content="https://example.com/a.mp3",
        customer_id="c1",
        corp_id="corp",
        request_context={"msgtype": "voice"},
    )

    updated = asyncio.run(
        transcribe_voice_request(request, FakeVoiceTranscriptionClient(exc=RuntimeError("boom")))
    )

    assert updated.content == VOICE_FALLBACK_TEXT
    assert "https://example.com" not in updated.content
    assert updated.request_context["voice_transcription"]["status"] == "failed"


def test_voice_request_retries_empty_transcription_output() -> None:
    request = ChatRequest(
        content="https://example.com/a.mp3",
        customer_id="c1",
        corp_id="corp",
        request_context={"msgtype": "voice"},
    )
    client = FakeVoiceTranscriptionClient(
        raw=[
            {"text": "", "task_id": "task-empty"},
            {"text": "我在厦门湖里区。", "task_id": "task-2", "query_attempt_count": 1},
        ]
    )

    updated = asyncio.run(transcribe_voice_request(request, client))

    assert updated.content == "我在厦门湖里区。"
    assert len(client.calls) == 2
    assert updated.request_context["voice_transcription"]["attempt_count"] == 2


def test_doubao_result_text_accepts_result_object_and_list() -> None:
    assert _doubao_result_text({"result": {"text": "东莞虎门。"}}) == "东莞虎门。"
    assert _doubao_result_text({"result": {"utterances": [{"text": "东莞"}, {"text": "虎门"}]}}) == "东莞虎门"
    assert _doubao_result_text({"result": [{"text": "东莞"}, {"text": "虎门"}]}) == "东莞虎门"


def test_audio_format_from_signed_url() -> None:
    assert _audio_format_from_url("https://example.com/a.mp3?token=1") == "mp3"
    assert _audio_format_from_url("https://example.com/a.wav") == "wav"
    assert _audio_format_from_url("https://example.com/a") == "mp3"
