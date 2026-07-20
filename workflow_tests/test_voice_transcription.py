from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.schemas import ChatRequest
from app.services.voice_transcription import VOICE_FALLBACK_TEXT, transcribe_voice_request


class FakeCozeClient:
    def __init__(self, raw: dict | None = None, exc: Exception | None = None) -> None:
        self.settings = SimpleNamespace(audio_to_text_workflow_id="workflow-audio")
        self.raw = raw or {"code": 0, "data": '{"output":"我在厦门湖里区。"}', "execute_id": "exec-1"}
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def run_workflow(self, workflow_id: str, parameters: dict) -> dict:
        self.calls.append((workflow_id, parameters))
        if self.exc:
            raise self.exc
        return self.raw


def test_voice_request_is_transcribed_before_runtime() -> None:
    request = ChatRequest(
        content="https://example.com/a.mp3?token=1",
        customer_id="c1",
        corp_id="corp",
        request_context={"msgtype": "voice"},
    )

    updated = asyncio.run(transcribe_voice_request(request, FakeCozeClient()))

    assert updated.content == "我在厦门湖里区。"
    assert updated.request_context["voice_original_content"] == "https://example.com/a.mp3?token=1"
    assert updated.request_context["voice_transcription"]["status"] == "ok"
    assert updated.request_context["voice_transcription"]["workflow_id"] == "workflow-audio"


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
    client = FakeCozeClient()

    updated = asyncio.run(transcribe_voice_request(request, client))

    assert updated.content == "我在厦门湖里区。"
    assert client.calls == [("workflow-audio", {"input": "https://example.com/raw.mp3"})]


def test_voice_request_does_not_pass_url_when_transcription_fails() -> None:
    request = ChatRequest(
        content="https://example.com/a.mp3",
        customer_id="c1",
        corp_id="corp",
        request_context={"msgtype": "voice"},
    )

    updated = asyncio.run(transcribe_voice_request(request, FakeCozeClient(exc=RuntimeError("boom"))))

    assert updated.content == VOICE_FALLBACK_TEXT
    assert "https://example.com" not in updated.content
    assert updated.request_context["voice_transcription"]["status"] == "failed"
