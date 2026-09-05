from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.schemas import ChatRequest


@dataclass
class PlatformReplyRecord:
    request_id: str
    customer_key: str
    generation_id: str
    original_content: str
    merged_customer_messages: list[str]
    image_urls: list[str]
    merged_input_events: list[dict[str, Any]]
    started_at: datetime
    message_id: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    status: str = "running"
    superseded_by_request_id: str = ""
    superseded_by_message_id: str = ""


@dataclass
class PlatformReplyDecision:
    mode: str
    request_id: str
    customer_key: str = ""
    generation_id: str = ""
    record: PlatformReplyRecord | None = None
    effective_content: str = ""
    effective_request_context: dict[str, Any] = field(default_factory=dict)
    merged_customer_messages: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    merged_input_events: list[dict[str, Any]] = field(default_factory=list)
    superseded_request_id: str = ""
    superseded_by_message_id: str = ""
    filter_hit: dict[str, Any] = field(default_factory=dict)

    @property
    def should_run_graph(self) -> bool:
        return self.mode in {"normal", "merged_latest"}


class PlatformReplyCoordinator:
    def __init__(self, settings: Settings, *, ttl_minutes: int = 15) -> None:
        self._settings = settings
        self._ttl = timedelta(minutes=ttl_minutes)
        self._inflight: dict[str, PlatformReplyRecord] = {}
        self._lock = asyncio.Lock()

    async def begin(self, request: ChatRequest, *, request_id: str, request_context: dict[str, Any]) -> PlatformReplyDecision:
        customer_key = _customer_key(request, request_context)
        if str(request_context.get("platform_input_batch_role") or "") == "superseded":
            input_events = list(request_context.get("merged_input_events") or [])
            return PlatformReplyDecision(
                mode="input_batch_superseded",
                request_id=request_id,
                customer_key=customer_key,
                generation_id=str(uuid4()),
                effective_content=request.content,
                effective_request_context=dict(request_context),
                merged_customer_messages=list(request_context.get("merged_customer_messages") or []),
                merged_input_events=input_events,
                superseded_by_message_id=str(
                    request_context.get("platform_input_batch_owner_msgid") or ""
                ),
            )
        filter_hit = self._match_filter_word(request.content)
        if filter_hit.get("matched"):
            return PlatformReplyDecision(
                mode="filtered",
                request_id=request_id,
                customer_key=customer_key,
                generation_id=str(uuid4()),
                effective_content=request.content,
                effective_request_context=dict(request_context),
                filter_hit=filter_hit,
            )

        generation_id = str(uuid4())
        original_content = _mergeable_content(request)
        current_image_urls = _request_image_urls(request)
        current_input_events = _request_input_events(request, request_context)
        message_id = _message_id(request, request_context)
        async with self._lock:
            self._cleanup_locked()
            previous = self._inflight.get(customer_key)
            previous_messages: list[str] = []
            previous_image_urls: list[str] = []
            previous_input_events: list[dict[str, Any]] = []
            superseded_request_id = ""
            if previous and previous.status == "running" and message_id:
                previous.status = "superseded"
                previous.superseded_by_request_id = request_id
                previous.superseded_by_message_id = message_id
                previous.cancel_event.set()
                previous_messages = list(previous.merged_customer_messages or [previous.original_content])
                previous_image_urls = list(previous.image_urls)
                previous_input_events = list(previous.merged_input_events)
                superseded_request_id = previous.request_id

            merged_messages = [message for message in [*previous_messages, original_content] if message]
            image_urls = _dedupe_strings([*previous_image_urls, *current_image_urls])[-3:]
            merged_input_events = _dedupe_events([*previous_input_events, *current_input_events])[-10:]
            record = PlatformReplyRecord(
                request_id=request_id,
                customer_key=customer_key,
                generation_id=generation_id,
                original_content=original_content,
                merged_customer_messages=merged_messages,
                image_urls=image_urls,
                merged_input_events=merged_input_events,
                started_at=datetime.now(timezone.utc),
                message_id=message_id,
            )
            self._inflight[customer_key] = record

        mode = "merged_latest" if superseded_request_id else "normal"
        effective_context = dict(request_context)
        if mode == "merged_latest":
            effective_context["merged_customer_messages"] = merged_messages
            effective_context["merged_input_events"] = merged_input_events
            effective_context["superseded_request_id"] = superseded_request_id
        if image_urls:
            effective_context["merged_image_urls"] = image_urls
        return PlatformReplyDecision(
            mode=mode,
            request_id=request_id,
            customer_key=customer_key,
            generation_id=generation_id,
            record=record,
            effective_content=_merged_content(merged_messages) if mode == "merged_latest" else request.content,
            effective_request_context=effective_context,
            merged_customer_messages=merged_messages,
            image_urls=image_urls,
            merged_input_events=merged_input_events,
            superseded_request_id=superseded_request_id,
        )

    async def complete(self, record: PlatformReplyRecord | None) -> None:
        if not record:
            return
        async with self._lock:
            current = self._inflight.get(record.customer_key)
            if current and current.generation_id == record.generation_id:
                record.status = "completed"
                self._inflight.pop(record.customer_key, None)

    async def is_latest(self, record: PlatformReplyRecord | None) -> bool:
        if not record:
            return True
        async with self._lock:
            current = self._inflight.get(record.customer_key)
            return bool(current and current.generation_id == record.generation_id and current.status == "running")

    def control_for_decision(self, decision: PlatformReplyDecision) -> dict[str, Any]:
        return _base_control(
            mode=decision.mode,
            customer_key=decision.customer_key,
            generation_id=decision.generation_id,
            superseded_request_id=decision.superseded_request_id,
            message_id=decision.record.message_id if decision.record else "",
            superseded_by_message_id=decision.superseded_by_message_id,
            merged_customer_messages=decision.merged_customer_messages,
            image_urls=decision.image_urls,
            merged_input_events=decision.merged_input_events,
            filter_hit=decision.filter_hit,
        )

    def control_for_superseded(self, record: PlatformReplyRecord) -> dict[str, Any]:
        return _base_control(
            mode="superseded",
            customer_key=record.customer_key,
            generation_id=record.generation_id,
            superseded_by_request_id=record.superseded_by_request_id,
            message_id=record.message_id,
            superseded_by_message_id=record.superseded_by_message_id,
            merged_customer_messages=record.merged_customer_messages,
            image_urls=record.image_urls,
            merged_input_events=record.merged_input_events,
        )

    async def is_superseded(self, record: PlatformReplyRecord | None) -> bool:
        if not record:
            return False
        async with self._lock:
            return bool(
                record.status == "superseded"
                and record.superseded_by_request_id
                and record.superseded_by_message_id
            )

    def _match_filter_word(self, content: str) -> dict[str, Any]:
        config = self._load_filter_config()
        if not config.get("enabled", True):
            return {"matched": False}
        words = [str(word).strip() for word in config.get("words", []) if str(word or "").strip()]
        regex_patterns = [str(pattern).strip() for pattern in config.get("regex_patterns", []) if str(pattern or "").strip()]
        if not words and not regex_patterns:
            return {"matched": False}
        mode = str(config.get("match_mode") or "contains").strip() or "contains"
        text = str(content or "")
        text_lower = text.lower()
        for word in words:
            if (mode == "exact" and text.strip() == word) or (mode != "exact" and word.lower() in text_lower):
                return {"matched": True, "word": word, "match_mode": mode}
        for pattern in regex_patterns:
            try:
                if re.search(pattern, text):
                    return {"matched": True, "word": pattern, "match_mode": "regex"}
            except re.error:
                continue
        return {"matched": False}

    def _load_filter_config(self) -> dict[str, Any]:
        path = self._settings.platform_filter_words_path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"enabled": True, "match_mode": "contains", "words": []}
        return data if isinstance(data, dict) else {"enabled": True, "match_mode": "contains", "words": []}

    def _cleanup_locked(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._ttl
        stale = [key for key, record in self._inflight.items() if record.started_at < cutoff or record.status != "running"]
        for key in stale:
            self._inflight.pop(key, None)


def _customer_key(request: ChatRequest, request_context: dict[str, Any]) -> str:
    corp_id = str(request_context.get("corp_id") or request.corp_id or "").strip()
    wechat = str(request_context.get("wechat") or request.wechat or "").strip()
    external_userid = str(request_context.get("external_userid") or request.external_userid or "").strip()
    if corp_id and wechat and external_userid:
        return f"{corp_id}:wechat:{wechat}:external:{external_userid}"
    customer_id = str(request_context.get("customer_id") or request.customer_id or "").strip()
    if corp_id and wechat and customer_id:
        return f"{corp_id}:wechat:{wechat}:customer:{customer_id}"
    user_id = str(request_context.get("user_id") or request.user_id or "").strip()
    return f"{corp_id}:fallback:{user_id}:{wechat}:{customer_id}"


def _message_id(request: ChatRequest, request_context: dict[str, Any]) -> str:
    raw = request_context.get("raw_workflow_payload")
    parameters = raw.get("parameters") if isinstance(raw, dict) and isinstance(raw.get("parameters"), dict) else {}
    content = parameters.get("content") if isinstance(parameters.get("content"), dict) else {}
    return str(request_context.get("msgid") or content.get("msgid") or "").strip()


def _merged_content(messages: list[str]) -> str:
    # The model should see the customer's actual consecutive messages, not an
    # internal ticket-style instruction. Ordering and the full event array stay
    # available through reply_control for audit and protocol interpretation.
    return "\n".join(str(message).strip() for message in messages if str(message or "").strip())


def _request_image_urls(request: ChatRequest) -> list[str]:
    image_url = str(request.file_image or "").strip()
    return [image_url] if image_url else []


def _request_input_events(request: ChatRequest, request_context: dict[str, Any]) -> list[dict[str, Any]]:
    existing = request_context.get("merged_input_events")
    if isinstance(existing, list) and existing:
        return [_compact_event(item) for item in existing if isinstance(item, dict)]

    raw = request_context.get("raw_workflow_payload")
    parameters = raw.get("parameters") if isinstance(raw, dict) and isinstance(raw.get("parameters"), dict) else {}
    content_obj = parameters.get("content") if isinstance(parameters.get("content"), dict) else {}
    event: dict[str, Any] = {
        "msgid": _message_id(request, request_context),
        "msgtime": str(request_context.get("msgtime") or content_obj.get("msgtime") or "").strip(),
        "msgtype": str(request_context.get("msgtype") or content_obj.get("msgtype") or "text").strip().lower() or "text",
        "content": _mergeable_content(request),
        "file_image": str(request.file_image or "").strip(),
        "location": str(request_context.get("location") or content_obj.get("location") or "").strip(),
        "location_title": str(request_context.get("location_title") or content_obj.get("location_title") or "").strip(),
        "location_address": str(request_context.get("location_address") or content_obj.get("location_address") or "").strip(),
        "location_zoom": str(request_context.get("location_zoom") or content_obj.get("location_zoom") or "").strip(),
    }
    voice = request_context.get("voice_transcription")
    if isinstance(voice, dict):
        event["voice_transcription"] = {
            key: voice.get(key)
            for key in ("status", "output_preview", "attempt_count", "cache_hit", "error")
            if voice.get(key) not in ("", None)
        }
    return [_compact_event(event)]


def _mergeable_content(request: ChatRequest) -> str:
    content = str(request.content or "").strip()
    image_url = str(request.file_image or "").strip()
    if not image_url:
        return content
    if not content or content == image_url:
        return "[图片]"
    return content.replace(image_url, "[图片]")


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in event.items():
        if value in ("", None, {}, []):
            continue
        if isinstance(value, dict):
            nested = _compact_event(value)
            if nested:
                output[key] = nested
            continue
        output[key] = value
    return output


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        compact = _compact_event(event)
        if not compact:
            continue
        key = str(compact.get("msgid") or "").strip()
        if not key:
            key = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        output.append(compact)
    return output


def _dedupe_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def _base_control(
    *,
    mode: str,
    customer_key: str = "",
    generation_id: str = "",
    superseded_request_id: str = "",
    superseded_by_request_id: str = "",
    message_id: str = "",
    superseded_by_message_id: str = "",
    merged_customer_messages: list[str] | None = None,
    image_urls: list[str] | None = None,
    merged_input_events: list[dict[str, Any]] | None = None,
    filter_hit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "customer_key": customer_key,
        "generation_id": generation_id,
        "superseded_request_id": superseded_request_id,
        "superseded_by_request_id": superseded_by_request_id,
        "message_id": message_id,
        "superseded_by_message_id": superseded_by_message_id,
        "merged_customer_messages": list(merged_customer_messages or []),
        "merged_image_urls": list(image_urls or []),
        "merged_input_events": list(merged_input_events or []),
        "filter_hit": filter_hit or {"matched": False},
        "sync_return": {},
        "async_final": {},
    }
