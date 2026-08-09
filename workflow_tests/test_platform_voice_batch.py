from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.config import Settings
from app.schemas import ChatRequest
from app.services.platform_reply_coordinator import PlatformReplyCoordinator
from app.services.platform_voice_batch import PlatformVoiceBatchCoordinator
from app.services.voice_transcription import VOICE_FALLBACK_TEXT


class _VoiceClient:
    provider_name = "doubao_asr"
    resource_id = "volc.seedasr.auc"

    def __init__(
        self,
        *,
        transcripts: dict[str, str],
        delays: dict[str, float] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self.transcripts = transcripts
        self.delays = delays or {}
        self.failures = failures or set()
        self.calls: list[str] = []

    async def transcribe(self, audio_url: str, *, uid: str = "") -> dict:
        self.calls.append(audio_url)
        await asyncio.sleep(self.delays.get(audio_url, 0.0))
        if audio_url in self.failures:
            raise RuntimeError("asr failed")
        return {
            "text": self.transcripts.get(audio_url, ""),
            "task_id": f"task-{len(self.calls)}",
            "query_attempt_count": 1,
        }


def test_multiple_voice_messages_keep_msgtime_order_when_asr_finishes_out_of_order() -> None:
    async def run() -> None:
        coordinator = _coordinator()
        client = _VoiceClient(
            transcripts={"https://audio/1.mp3": "我在嘉兴", "https://audio/2.mp3": "秀洲区有门店吗"},
            delays={"https://audio/1.mp3": 0.08, "https://audio/2.mp3": 0.01},
        )

        first_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/1.mp3", msgid="m1", msgtime=1000), client)
        )
        await asyncio.sleep(0.005)
        second_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/2.mp3", msgid="m2", msgtime=2000), client)
        )
        first, second = await asyncio.gather(first_task, second_task)

        assert first.request_context["platform_input_batch_role"] == "superseded"
        assert second.request_context["platform_input_batch_role"] == "owner"
        assert second.request_context["platform_input_batch"]["ordered_message_ids"] == ["m1", "m2"]
        assert [event["msgid"] for event in second.request_context["merged_input_events"]] == ["m1", "m2"]
        assert second.request_context["merged_input_events"][0]["msgtype"] == "voice"
        assert second.request_context["merged_input_events"][0]["content"] == "我在嘉兴"
        assert second.content.index("语音1转写：我在嘉兴") < second.content.index("语音2转写：秀洲区有门店吗")
        assert client.calls.count("https://audio/1.mp3") == 1
        assert client.calls.count("https://audio/2.mp3") == 1
        await coordinator.aclose()

    asyncio.run(run())


def test_duplicate_msgid_reuses_one_transcription_task() -> None:
    async def run() -> None:
        coordinator = _coordinator()
        client = _VoiceClient(
            transcripts={"https://audio/same.mp3": "同一条语音"},
            delays={"https://audio/same.mp3": 0.03},
        )
        request = _voice_request("https://audio/same.mp3", msgid="same", msgtime=1000)

        first, retry = await asyncio.gather(
            coordinator.prepare(request, client),
            coordinator.prepare(request, client),
        )

        assert first.content == "同一条语音"
        assert retry.content == "同一条语音"
        assert client.calls == ["https://audio/same.mp3"]
        await coordinator.aclose()

    asyncio.run(run())


def test_late_voice_joins_while_first_transcription_is_still_running() -> None:
    async def run() -> None:
        settings = Settings(
            PLATFORM_VOICE_BATCH_ENABLED=True,
            PLATFORM_VOICE_BATCH_SETTLE_SECONDS=0.02,
            PLATFORM_VOICE_BATCH_HARD_WINDOW_SECONDS=0.25,
            PLATFORM_VOICE_BATCH_TIMEOUT_SECONDS=1.0,
            PLATFORM_VOICE_BATCH_MAX_ITEMS=6,
            PLATFORM_VOICE_TRANSCRIPT_CACHE_SECONDS=60.0,
        )
        coordinator = PlatformVoiceBatchCoordinator(settings)
        client = _VoiceClient(
            transcripts={"https://audio/1.mp3": "第一条", "https://audio/2.mp3": "第二条"},
            delays={"https://audio/1.mp3": 0.12, "https://audio/2.mp3": 0.01},
        )

        first_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/1.mp3", msgid="m1", msgtime=1000), client)
        )
        await asyncio.sleep(0.05)
        second_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/2.mp3", msgid="m2", msgtime=2000), client)
        )
        first, owner = await asyncio.gather(first_task, second_task)

        assert first.request_context["platform_input_batch_role"] == "superseded"
        assert owner.request_context["platform_input_batch_role"] == "owner"
        assert owner.request_context["platform_input_batch"]["message_count"] == 2
        assert owner.content == "客户连续发送了多条语音，请按发送顺序整体理解：\n语音1转写：第一条\n语音2转写：第二条"
        await coordinator.aclose()

    asyncio.run(run())


def test_completed_msgid_retry_uses_transcript_cache() -> None:
    async def run() -> None:
        coordinator = _coordinator()
        client = _VoiceClient(transcripts={"https://audio/same.mp3": "缓存语音"})
        request = _voice_request("https://audio/same.mp3", msgid="same", msgtime=1000)

        first = await coordinator.prepare(request, client)
        retry = await coordinator.prepare(request, client)

        assert first.content == "缓存语音"
        assert retry.content == "缓存语音"
        assert retry.request_context["voice_transcription"]["cache_hit"] is True
        assert client.calls == ["https://audio/same.mp3"]
        await coordinator.aclose()

    asyncio.run(run())


def test_failed_msgid_retry_runs_transcription_again() -> None:
    async def run() -> None:
        coordinator = _coordinator()
        client = _VoiceClient(transcripts={}, failures={"https://audio/retry.mp3"})
        request = _voice_request("https://audio/retry.mp3", msgid="retry", msgtime=1000)

        with patch("app.services.voice_transcription.VOICE_TRANSCRIPTION_ATTEMPTS", 1):
            first = await coordinator.prepare(request, client)
            client.failures.clear()
            client.transcripts["https://audio/retry.mp3"] = "重试成功"
            retry = await coordinator.prepare(request, client)

        assert first.content == VOICE_FALLBACK_TEXT
        assert retry.content == "重试成功"
        assert client.calls == ["https://audio/retry.mp3", "https://audio/retry.mp3"]
        await coordinator.aclose()

    asyncio.run(run())


def test_failed_voice_is_not_mixed_into_successful_transcript() -> None:
    async def run() -> None:
        coordinator = _coordinator()
        client = _VoiceClient(
            transcripts={"https://audio/good.mp3": "我想问一下价格"},
            failures={"https://audio/bad.mp3"},
        )

        failed_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/bad.mp3", msgid="m1", msgtime=1000), client)
        )
        await asyncio.sleep(0.005)
        good_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/good.mp3", msgid="m2", msgtime=2000), client)
        )
        failed, owner = await asyncio.gather(failed_task, good_task)

        assert failed.request_context["platform_input_batch_role"] == "superseded"
        assert owner.content == "我想问一下价格"
        assert VOICE_FALLBACK_TEXT not in owner.content
        assert owner.request_context["platform_input_batch"]["transcription_failed"] == 1
        await coordinator.aclose()

    with patch("app.services.voice_transcription.VOICE_TRANSCRIPTION_ATTEMPTS", 1):
        asyncio.run(run())


def test_latest_failed_voice_still_owns_batch_result() -> None:
    async def run() -> None:
        coordinator = _coordinator()
        client = _VoiceClient(
            transcripts={"https://audio/good.mp3": "前一条转写成功"},
            failures={"https://audio/bad.mp3"},
        )

        first_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/good.mp3", msgid="m1", msgtime=1000), client)
        )
        await asyncio.sleep(0.005)
        latest_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/bad.mp3", msgid="m2", msgtime=2000), client)
        )
        first, latest = await asyncio.gather(first_task, latest_task)

        assert first.request_context["platform_input_batch_role"] == "superseded"
        assert latest.request_context["platform_input_batch_role"] == "owner"
        assert latest.request_context["platform_input_batch_owner_msgid"] == "m2"
        assert latest.content == "前一条转写成功"
        assert latest.request_context["platform_input_batch"]["transcription_failed"] == 1
        await coordinator.aclose()

    with patch("app.services.voice_transcription.VOICE_TRANSCRIPTION_ATTEMPTS", 1):
        asyncio.run(run())


def test_all_failed_voice_messages_produce_one_owner_fallback() -> None:
    async def run() -> None:
        coordinator = _coordinator()
        client = _VoiceClient(transcripts={}, failures={"https://audio/1.mp3", "https://audio/2.mp3"})

        first_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/1.mp3", msgid="m1", msgtime=1000), client)
        )
        await asyncio.sleep(0.005)
        second_task = asyncio.create_task(
            coordinator.prepare(_voice_request("https://audio/2.mp3", msgid="m2", msgtime=2000), client)
        )
        first, owner = await asyncio.gather(first_task, second_task)

        assert first.request_context["platform_input_batch_role"] == "superseded"
        assert owner.request_context["platform_input_batch_role"] == "owner"
        assert owner.content == VOICE_FALLBACK_TEXT
        assert owner.request_context["platform_input_batch"]["transcription_failed"] == 2
        await coordinator.aclose()

    with patch("app.services.voice_transcription.VOICE_TRANSCRIPTION_ATTEMPTS", 1):
        asyncio.run(run())


def test_reply_coordinator_skips_non_owner_voice_batch_member() -> None:
    async def run() -> None:
        settings = _settings()
        coordinator = PlatformReplyCoordinator(settings)
        request = _voice_request("第一条", msgid="m1", msgtime=1000).model_copy(
            update={
                "request_context": {
                    **_voice_request("第一条", msgid="m1", msgtime=1000).request_context,
                    "platform_input_batch_role": "superseded",
                    "platform_input_batch_owner_msgid": "m2",
                }
            }
        )

        decision = await coordinator.begin(
            request,
            request_id="request-1",
            request_context=request.request_context,
        )

        assert decision.mode == "input_batch_superseded"
        assert decision.should_run_graph is False
        assert decision.superseded_by_message_id == "m2"
        assert coordinator.control_for_decision(decision)["superseded_by_message_id"] == "m2"

    asyncio.run(run())


def _coordinator() -> PlatformVoiceBatchCoordinator:
    return PlatformVoiceBatchCoordinator(_settings())


def _settings() -> Settings:
    return Settings(
        PLATFORM_VOICE_BATCH_ENABLED=True,
        PLATFORM_VOICE_BATCH_SETTLE_SECONDS=0.03,
        PLATFORM_VOICE_BATCH_HARD_WINDOW_SECONDS=0.2,
        PLATFORM_VOICE_BATCH_TIMEOUT_SECONDS=1.0,
        PLATFORM_VOICE_BATCH_MAX_ITEMS=6,
        PLATFORM_VOICE_TRANSCRIPT_CACHE_SECONDS=60.0,
    )


def _voice_request(audio_url: str, *, msgid: str, msgtime: int) -> ChatRequest:
    return ChatRequest(
        content=audio_url,
        customer_id="customer-1",
        corp_id="corp-1",
        wechat="DY258",
        external_userid="external-1",
        request_context={
            "msgtype": "voice",
            "msgid": msgid,
            "msgtime": str(msgtime),
            "corp_id": "corp-1",
            "wechat": "DY258",
            "external_userid": "external-1",
            "customer_id": "customer-1",
        },
    )
