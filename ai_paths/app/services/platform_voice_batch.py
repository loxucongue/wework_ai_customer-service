from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.schemas import ChatRequest
from app.services.voice_transcription import (
    VOICE_FALLBACK_TEXT,
    VoiceTranscriptionClient,
    transcribe_voice_request,
)


@dataclass
class _CachedTranscript:
    content: str
    voice_transcription: dict[str, Any]
    voice_original_content: str
    cached_at: float


@dataclass
class _VoiceBatchItem:
    ticket_id: str
    message_id: str
    message_time: int
    arrival_sequence: int
    registered_at: float
    request: ChatRequest
    normalization_task: asyncio.Task[ChatRequest] | None = None
    normalized_request: ChatRequest | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def order_key(self) -> tuple[int, int]:
        fallback_time = int(self.registered_at * 1000)
        return (self.message_time or fallback_time, self.arrival_sequence)


@dataclass
class _VoiceBatchResult:
    batch_id: str
    owner_message_id: str
    owner_ticket_id: str
    owner_request: ChatRequest
    ordered_message_ids: list[str]
    message_count: int
    success_count: int
    failure_count: int


@dataclass
class _VoiceBatch:
    batch_id: str
    customer_key: str
    created_at: float
    last_arrival_at: float
    items: list[_VoiceBatchItem] = field(default_factory=list)
    item_by_message_id: dict[str, _VoiceBatchItem] = field(default_factory=dict)
    finalize_task: asyncio.Task[None] | None = None
    result: asyncio.Future[_VoiceBatchResult] | None = None
    closed: bool = False


class PlatformVoiceBatchCoordinator:
    """Orders and deduplicates platform voice inputs before business models run."""

    def __init__(self, settings: Settings) -> None:
        self._enabled = bool(settings.platform_voice_batch_enabled)
        self._settle_seconds = max(0.0, float(settings.platform_voice_batch_settle_seconds))
        self._hard_window_seconds = max(self._settle_seconds, float(settings.platform_voice_batch_hard_window_seconds))
        self._timeout_seconds = max(1.0, float(settings.platform_voice_batch_timeout_seconds))
        self._max_items = max(1, int(settings.platform_voice_batch_max_items))
        self._cache_seconds = max(1.0, float(settings.platform_voice_transcript_cache_seconds))
        self._open_batches: dict[str, _VoiceBatch] = {}
        self._cache: dict[str, _CachedTranscript] = {}
        self._lock = asyncio.Lock()
        self._arrival_sequence = 0

    async def prepare(
        self,
        request: ChatRequest,
        transcription_client: VoiceTranscriptionClient,
    ) -> ChatRequest:
        if not self._enabled or not _is_voice_request(request):
            return await transcribe_voice_request(request, transcription_client)

        customer_key = _customer_key(request)
        message_id = _message_id(request)
        if not customer_key or not message_id:
            return await transcribe_voice_request(request, transcription_client)

        item, batch = await self._register(request, customer_key=customer_key, message_id=message_id)
        await self._ensure_normalized(item, customer_key=customer_key, transcription_client=transcription_client)
        result = await asyncio.shield(batch.result)
        if item.ticket_id == result.owner_ticket_id:
            return result.owner_request
        return _superseded_request(
            item.normalized_request or request,
            batch_result=result,
        )

    async def aclose(self) -> None:
        async with self._lock:
            tasks = [
                batch.finalize_task
                for batch in self._open_batches.values()
                if batch.finalize_task and not batch.finalize_task.done()
            ]
            self._open_batches.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _register(
        self,
        request: ChatRequest,
        *,
        customer_key: str,
        message_id: str,
    ) -> tuple[_VoiceBatchItem, _VoiceBatch]:
        now = time.monotonic()
        async with self._lock:
            self._cleanup_cache_locked(now)
            batch = self._open_batches.get(customer_key)
            if (
                batch is None
                or batch.closed
                or len(batch.items) >= self._max_items
                or now - batch.created_at >= self._hard_window_seconds
            ):
                batch = _VoiceBatch(
                    batch_id=str(uuid4()),
                    customer_key=customer_key,
                    created_at=now,
                    last_arrival_at=now,
                    result=asyncio.get_running_loop().create_future(),
                )
                self._open_batches[customer_key] = batch

            existing = batch.item_by_message_id.get(message_id)
            if existing is not None:
                return existing, batch

            self._arrival_sequence += 1
            item = _VoiceBatchItem(
                ticket_id=str(uuid4()),
                message_id=message_id,
                message_time=_message_time(request),
                arrival_sequence=self._arrival_sequence,
                registered_at=now,
                request=request,
            )
            batch.items.append(item)
            batch.item_by_message_id[message_id] = item
            batch.last_arrival_at = now
            self._schedule_finalize_locked(batch)
            return item, batch

    async def _ensure_normalized(
        self,
        item: _VoiceBatchItem,
        *,
        customer_key: str,
        transcription_client: VoiceTranscriptionClient,
    ) -> None:
        cache_key = f"{customer_key}:msgid:{item.message_id}"
        async with self._lock:
            if item.normalization_task is None:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    item.normalization_task = asyncio.create_task(
                        _request_from_cache(item.request, cached)
                    )
                else:
                    item.normalization_task = asyncio.create_task(
                        transcribe_voice_request(item.request, transcription_client)
                    )
            task = item.normalization_task

        try:
            normalized = await asyncio.shield(task)
        except Exception as exc:
            normalized = _failed_transcription_request(item.request, exc)

        async with self._lock:
            if item.normalized_request is None:
                item.normalized_request = normalized
                item.ready.set()
                context = normalized.request_context if isinstance(normalized.request_context, dict) else {}
                voice = context.get("voice_transcription") if isinstance(context.get("voice_transcription"), dict) else {}
                if str(voice.get("status") or "") == "ok":
                    self._cache[cache_key] = _CachedTranscript(
                        content=str(normalized.content or ""),
                        voice_transcription=dict(voice),
                        voice_original_content=str(context.get("voice_original_content") or ""),
                        cached_at=time.monotonic(),
                    )

    def _schedule_finalize_locked(self, batch: _VoiceBatch) -> None:
        if batch.finalize_task and not batch.finalize_task.done():
            batch.finalize_task.cancel()
        batch.finalize_task = asyncio.create_task(self._finalize_when_settled(batch))

    async def _finalize_when_settled(self, batch: _VoiceBatch) -> None:
        try:
            while True:
                async with self._lock:
                    if batch.closed:
                        return
                    now = time.monotonic()
                    quiet_remaining = self._settle_seconds - (now - batch.last_arrival_at)
                    hard_remaining = self._hard_window_seconds - (now - batch.created_at)
                    all_ready = bool(batch.items) and all(item.ready.is_set() for item in batch.items)
                    if (
                        hard_remaining <= 0
                        or len(batch.items) >= self._max_items
                        or (quiet_remaining <= 0 and all_ready)
                    ):
                        batch.closed = True
                        if self._open_batches.get(batch.customer_key) is batch:
                            self._open_batches.pop(batch.customer_key, None)
                        items = list(batch.items)
                        break
                    delay = (
                        min(max(0.0, quiet_remaining), max(0.0, hard_remaining))
                        if quiet_remaining > 0
                        else min(0.05, max(0.0, hard_remaining))
                    )
                await asyncio.sleep(delay)

            await _wait_for_items(items, timeout_seconds=self._timeout_seconds)
            result = _build_batch_result(batch, items)
            if batch.result and not batch.result.done():
                batch.result.set_result(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if batch.result and not batch.result.done():
                batch.result.set_exception(exc)

    def _cleanup_cache_locked(self, now: float) -> None:
        cutoff = now - self._cache_seconds
        self._cache = {
            key: value
            for key, value in self._cache.items()
            if value.cached_at >= cutoff
        }


async def _request_from_cache(request: ChatRequest, cached: _CachedTranscript) -> ChatRequest:
    context = dict(request.request_context or {})
    context["voice_transcription"] = dict(cached.voice_transcription)
    context["voice_transcription"]["cache_hit"] = True
    context["voice_original_content"] = cached.voice_original_content
    return request.model_copy(update={"content": cached.content, "request_context": context})


async def _wait_for_items(items: list[_VoiceBatchItem], *, timeout_seconds: float) -> None:
    waiters = [asyncio.create_task(item.ready.wait()) for item in items]
    try:
        await asyncio.wait(waiters, timeout=timeout_seconds)
    finally:
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)


def _build_batch_result(batch: _VoiceBatch, items: list[_VoiceBatchItem]) -> _VoiceBatchResult:
    ordered = sorted(items, key=lambda item: item.order_key)
    ready_items = [item for item in ordered if item.normalized_request is not None]
    owner = ordered[-1]
    successful = [item for item in ready_items if _transcription_succeeded(item.normalized_request)]
    failed_count = len(ordered) - len(successful)

    owner_request = owner.normalized_request or _failed_transcription_request(
        owner.request,
        TimeoutError("voice_batch_normalization_timeout"),
    )
    transcripts = [str(item.normalized_request.content or "").strip() for item in successful]
    transcripts = [text for text in transcripts if text]
    content = (
        transcripts[0]
        if len(transcripts) == 1
        else _merged_voice_content(transcripts)
        if transcripts
        else VOICE_FALLBACK_TEXT
    )
    context = dict(owner_request.request_context or {})
    if successful:
        latest_success_context = successful[-1].normalized_request.request_context or {}
        latest_success_voice = latest_success_context.get("voice_transcription")
        if isinstance(latest_success_voice, dict):
            context["voice_transcription"] = {
                **latest_success_voice,
                "batch_aggregated": len(ordered) > 1,
                "batch_success_count": len(successful),
                "batch_failure_count": failed_count,
            }
    context["platform_input_batch_role"] = "owner"
    context["platform_input_batch_id"] = batch.batch_id
    context["platform_input_batch_owner_msgid"] = owner.message_id
    context["merged_customer_messages"] = transcripts
    context["voice_transcriptions"] = [
        _transcription_summary(item)
        for item in ordered
    ]
    context["merged_input_events"] = [
        _voice_input_event(item)
        for item in ordered
    ]
    context["platform_input_batch"] = {
        "batch_id": batch.batch_id,
        "message_count": len(ordered),
        "voice_count": len(ordered),
        "ordered_by": "msgtime",
        "ordered_message_ids": [item.message_id for item in ordered],
        "transcription_succeeded": len(successful),
        "transcription_failed": failed_count,
    }
    owner_request = owner_request.model_copy(update={"content": content, "request_context": context})
    return _VoiceBatchResult(
        batch_id=batch.batch_id,
        owner_message_id=owner.message_id,
        owner_ticket_id=owner.ticket_id,
        owner_request=owner_request,
        ordered_message_ids=[item.message_id for item in ordered],
        message_count=len(ordered),
        success_count=len(successful),
        failure_count=failed_count,
    )


def _voice_input_event(item: _VoiceBatchItem) -> dict[str, Any]:
    request = item.normalized_request or item.request
    context = request.request_context if isinstance(request.request_context, dict) else {}
    voice = context.get("voice_transcription") if isinstance(context.get("voice_transcription"), dict) else {}
    output: dict[str, Any] = {
        "msgid": item.message_id,
        "msgtime": str(item.message_time or ""),
        "msgtype": "voice",
        "content": str(request.content or "").strip(),
    }
    if voice:
        output["voice_transcription"] = {
            key: voice.get(key)
            for key in ("status", "output_preview", "attempt_count", "cache_hit", "error")
            if voice.get(key) not in ("", None)
        }
    return {key: value for key, value in output.items() if value not in ("", None, {}, [])}


def _superseded_request(request: ChatRequest, *, batch_result: _VoiceBatchResult) -> ChatRequest:
    context = dict(request.request_context or {})
    context["platform_input_batch_role"] = "superseded"
    context["platform_input_batch_id"] = batch_result.batch_id
    context["platform_input_batch_owner_msgid"] = batch_result.owner_message_id
    context["platform_input_batch"] = {
        "batch_id": batch_result.batch_id,
        "message_count": batch_result.message_count,
        "voice_count": batch_result.message_count,
        "ordered_by": "msgtime",
        "ordered_message_ids": batch_result.ordered_message_ids,
        "transcription_succeeded": batch_result.success_count,
        "transcription_failed": batch_result.failure_count,
    }
    return request.model_copy(update={"request_context": context})


def _transcription_succeeded(request: ChatRequest | None) -> bool:
    if request is None:
        return False
    context = request.request_context if isinstance(request.request_context, dict) else {}
    voice = context.get("voice_transcription") if isinstance(context.get("voice_transcription"), dict) else {}
    return str(voice.get("status") or "") == "ok" and bool(str(request.content or "").strip())


def _transcription_summary(item: _VoiceBatchItem) -> dict[str, Any]:
    request = item.normalized_request
    context = request.request_context if request and isinstance(request.request_context, dict) else {}
    voice = context.get("voice_transcription") if isinstance(context.get("voice_transcription"), dict) else {}
    return {
        "msgid": item.message_id,
        "msgtime": item.message_time,
        "status": str(voice.get("status") or "timeout"),
        "text": str(request.content or "") if request and str(voice.get("status") or "") == "ok" else "",
        "attempt_count": int(voice.get("attempt_count") or 0),
        "cache_hit": bool(voice.get("cache_hit")),
        "error": str(voice.get("error") or "")[:200],
    }


def _failed_transcription_request(request: ChatRequest, exc: Exception) -> ChatRequest:
    context = dict(request.request_context or {})
    context["voice_original_content"] = str(request.content or "")
    context["voice_transcription"] = {
        "status": "failed",
        "source_msgtype": "voice",
        "error": f"{type(exc).__name__}: {exc}"[:200],
    }
    return request.model_copy(update={"content": VOICE_FALLBACK_TEXT, "request_context": context})


def _merged_voice_content(transcripts: list[str]) -> str:
    lines = ["客户连续发送了多条语音，请按发送顺序整体理解："]
    lines.extend(f"语音{index}转写：{text}" for index, text in enumerate(transcripts, start=1))
    return "\n".join(lines)


def _is_voice_request(request: ChatRequest) -> bool:
    context = request.request_context if isinstance(request.request_context, dict) else {}
    return str(context.get("msgtype") or "").strip().lower() == "voice"


def _customer_key(request: ChatRequest) -> str:
    context = request.request_context if isinstance(request.request_context, dict) else {}
    corp_id = str(context.get("corp_id") or request.corp_id or "").strip()
    wechat = str(context.get("wechat") or request.wechat or "").strip()
    external_userid = str(context.get("external_userid") or request.external_userid or "").strip()
    customer_id = str(context.get("customer_id") or request.customer_id or "").strip()
    identity = external_userid or customer_id
    return f"{corp_id}:wechat:{wechat}:customer:{identity}" if corp_id and wechat and identity else ""


def _message_id(request: ChatRequest) -> str:
    context = request.request_context if isinstance(request.request_context, dict) else {}
    raw = context.get("raw_workflow_payload")
    parameters = raw.get("parameters") if isinstance(raw, dict) and isinstance(raw.get("parameters"), dict) else {}
    content = parameters.get("content") if isinstance(parameters.get("content"), dict) else {}
    return str(context.get("msgid") or content.get("msgid") or "").strip()


def _message_time(request: ChatRequest) -> int:
    context = request.request_context if isinstance(request.request_context, dict) else {}
    raw = context.get("raw_workflow_payload")
    parameters = raw.get("parameters") if isinstance(raw, dict) and isinstance(raw.get("parameters"), dict) else {}
    content = parameters.get("content") if isinstance(parameters.get("content"), dict) else {}
    raw_value = context.get("msgtime") or content.get("msgtime") or 0
    try:
        return int(float(str(raw_value)))
    except (TypeError, ValueError):
        return 0
