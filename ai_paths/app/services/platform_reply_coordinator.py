from __future__ import annotations

import asyncio
import json
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
    started_at: datetime
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    status: str = "running"
    superseded_by_request_id: str = ""


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
    superseded_request_id: str = ""
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
        original_content = str(request.content or "").strip()
        async with self._lock:
            self._cleanup_locked()
            previous = self._inflight.get(customer_key)
            previous_messages: list[str] = []
            superseded_request_id = ""
            if previous and previous.status == "running":
                previous.status = "superseded"
                previous.superseded_by_request_id = request_id
                previous.cancel_event.set()
                previous_messages = list(previous.merged_customer_messages or [previous.original_content])
                superseded_request_id = previous.request_id

            merged_messages = [message for message in [*previous_messages, original_content] if message]
            record = PlatformReplyRecord(
                request_id=request_id,
                customer_key=customer_key,
                generation_id=generation_id,
                original_content=original_content,
                merged_customer_messages=merged_messages,
                started_at=datetime.now(timezone.utc),
            )
            self._inflight[customer_key] = record

        mode = "merged_latest" if superseded_request_id else "normal"
        effective_context = dict(request_context)
        if mode == "merged_latest":
            effective_context["merged_customer_messages"] = merged_messages
            effective_context["superseded_request_id"] = superseded_request_id
        return PlatformReplyDecision(
            mode=mode,
            request_id=request_id,
            customer_key=customer_key,
            generation_id=generation_id,
            record=record,
            effective_content=_merged_content(merged_messages) if mode == "merged_latest" else request.content,
            effective_request_context=effective_context,
            merged_customer_messages=merged_messages,
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
            merged_customer_messages=decision.merged_customer_messages,
            filter_hit=decision.filter_hit,
        )

    def control_for_superseded(self, record: PlatformReplyRecord) -> dict[str, Any]:
        return _base_control(
            mode="superseded",
            customer_key=record.customer_key,
            generation_id=record.generation_id,
            superseded_by_request_id=record.superseded_by_request_id,
            merged_customer_messages=record.merged_customer_messages,
        )

    def _match_filter_word(self, content: str) -> dict[str, Any]:
        config = self._load_filter_config()
        if not config.get("enabled", True):
            return {"matched": False}
        words = [str(word).strip() for word in config.get("words", []) if str(word or "").strip()]
        if not words:
            return {"matched": False}
        mode = str(config.get("match_mode") or "contains").strip() or "contains"
        text = str(content or "")
        text_lower = text.lower()
        for word in words:
            if (mode == "exact" and text.strip() == word) or (mode != "exact" and word.lower() in text_lower):
                return {"matched": True, "word": word, "match_mode": mode}
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
    external_userid = str(request_context.get("external_userid") or request.external_userid or "").strip()
    if corp_id and external_userid:
        return f"{corp_id}:external:{external_userid}"
    customer_id = str(request_context.get("customer_id") or request.customer_id or "").strip()
    if corp_id and customer_id:
        return f"{corp_id}:customer:{customer_id}"
    user_id = str(request_context.get("user_id") or request.user_id or "").strip()
    wechat = str(request_context.get("wechat") or request.wechat or "").strip()
    return f"{corp_id}:fallback:{user_id}:{wechat}:{customer_id}"


def _merged_content(messages: list[str]) -> str:
    lines = ["客户连续发送了多条未回复消息，请作为本轮当前问题整体处理，最新消息优先："]
    lines.extend(f"{index}. {message}" for index, message in enumerate(messages, start=1))
    return "\n".join(lines)


def _base_control(
    *,
    mode: str,
    customer_key: str = "",
    generation_id: str = "",
    superseded_request_id: str = "",
    superseded_by_request_id: str = "",
    merged_customer_messages: list[str] | None = None,
    filter_hit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "customer_key": customer_key,
        "generation_id": generation_id,
        "superseded_request_id": superseded_request_id,
        "superseded_by_request_id": superseded_by_request_id,
        "merged_customer_messages": list(merged_customer_messages or []),
        "filter_hit": filter_hit or {"matched": False},
        "sync_return": {},
        "async_final": {},
    }
