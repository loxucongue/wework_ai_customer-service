from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from app.services.service_rule_data_client import ServiceRuleDataClient
from app.services.storage import AppRepository


logger = logging.getLogger(__name__)
_CUSTOMER_REPLY_TYPES = {"text", "image", "video", "voice", "other"}
_CUSTOMER_OPEN_SCENE_CODE = "customer_opening"
_CUSTOMER_OPEN_SCENE_NAME = "开口场景"


class ServiceRuleDataService:
    """Build and deliver V3 customer-open strategy data without blocking replies."""

    def __init__(
        self,
        *,
        repository: AppRepository,
        client: ServiceRuleDataClient,
        poll_seconds: float = 2.0,
        batch_size: int = 10,
        max_attempts: int = 6,
        retry_base_seconds: float = 10.0,
    ) -> None:
        self.repository = repository
        self.client = client
        self.poll_seconds = max(0.5, float(poll_seconds or 2.0))
        self.batch_size = max(1, min(int(batch_size or 10), 100))
        self.max_attempts = max(1, int(max_attempts or 6))
        self.retry_base_seconds = max(1.0, float(retry_base_seconds or 10.0))
        self._stop = asyncio.Event()

    @property
    def available(self) -> bool:
        return self.client.available

    def enqueue_customer_open(
        self,
        state: dict[str, Any],
        *,
        allow_empty_reply: bool = False,
    ) -> dict[str, Any]:
        if not self.available:
            return {"status": "skipped", "reason": "service_rule_data_not_configured"}
        context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
        if str(context.get("interface_version") or "").lower() != "v3":
            return {"status": "skipped", "reason": "not_v3"}
        if state.get("test_isolated"):
            return {"status": "skipped", "reason": "test_isolated"}
        if not state.get("reply_messages") and not allow_empty_reply:
            return {"status": "skipped", "reason": "no_customer_visible_reply"}

        replied_at_iso, reply_epoch = _reply_times(context)
        task = self.repository.find_latest_platform_task_for_customer_reply(
            customer_id=str(state.get("customer_id") or ""),
            external_userid=str(state.get("external_userid") or ""),
            corp_id=str(state.get("corp_id") or ""),
            wechat=str(state.get("wechat") or ""),
            replied_at=replied_at_iso,
        )
        if not task:
            return {"status": "skipped", "reason": "no_prior_sent_platform_task"}
        task_id = _positive_int(task.get("task_id"))
        if task_id is None:
            return {"status": "skipped", "reason": "invalid_platform_task_id"}

        raw_msgid = str(context.get("msgid") or "").strip()
        if not raw_msgid:
            return {"status": "skipped", "reason": "missing_customer_message_id"}
        reply_msgid = _bounded_message_id(raw_msgid)
        payload: dict[str, Any] = {
            "recordKind": "customer_open",
            "sceneCode": _CUSTOMER_OPEN_SCENE_CODE,
            "sceneName": _CUSTOMER_OPEN_SCENE_NAME,
            "taskId": task_id,
            "sendStatus": 10 if state.get("reply_messages") else 20,
            "customerId": _numeric_or_text(
                state.get("customer_id") or state.get("external_userid") or ""
            ),
            "customerReply": _customer_reply_content(state),
            "customerReplyType": customer_reply_type(
                str(context.get("source_msgtype") or context.get("msgtype") or ""),
                has_image=bool(state.get("file_image") or state.get("image_urls")),
            ),
            "replyMsgId": reply_msgid,
            "replyTime": reply_epoch,
            "triggerType": "customer_open",
            "triggerRef": str(task_id),
        }
        send_content = _sent_content_summary(state.get("reply_messages"))
        if send_content:
            payload["sendContent"] = send_content
        knowledge = state.get("reply_knowledge_use") if isinstance(state.get("reply_knowledge_use"), dict) else {}
        checkpoint = str(
            knowledge.get("checkpoint_code")
            or ((state.get("semantic_route") or {}).get("checkpoint") or {}).get("primary_code")
            or ""
        ).strip()
        if checkpoint:
            payload["checkpointCode"] = checkpoint
        action_code = str(knowledge.get("action_code") or "").strip()
        if action_code:
            payload["actionCode"] = action_code
        sequence_id = str(knowledge.get("sequence_id") or "").strip()
        step_id = str(knowledge.get("step_id") or "").strip()
        numeric_sequence_id = _positive_int(sequence_id)
        numeric_step_id = _positive_int(step_id)
        if numeric_sequence_id is not None:
            payload["followSequenceId"] = numeric_sequence_id
        if numeric_step_id is not None:
            payload["followSequenceStepId"] = numeric_step_id

        selected_scripts = [
            str(item).strip()
            for item in knowledge.get("selected_script_ids") or []
            if str(item).strip()
        ]
        primary_script_id = _primary_script_database_id(state, selected_scripts[0] if selected_scripts else "")
        if primary_script_id:
            payload["followScriptId"] = primary_script_id

        idempotency_key = f"customer_open:{task_id}:{reply_msgid}"
        record = self.repository.enqueue_strategy_data_callback(
            idempotency_key=idempotency_key,
            record_kind="customer_open",
            task_id=str(task_id),
            sales_contact_key=str(state.get("sales_contact_key") or ""),
            customer_id=str(state.get("customer_id") or ""),
            interface_version="v3",
            payload=payload,
        )
        return {
            "status": str(record.get("status") or "pending"),
            "outbox_id": str(record.get("id") or ""),
            "task_id": str(task.get("task_id") or ""),
            "reply_msgid": reply_msgid,
            "customer_reply_type": payload["customerReplyType"],
            "checkpoint_code": checkpoint,
            "action_code": action_code,
            "follow_sequence_id": sequence_id,
            "follow_sequence_step_id": step_id,
            "follow_script_id": primary_script_id or "",
        }

    async def run(self) -> None:
        if not self.available:
            return
        if self._stop.is_set():
            self._stop = asyncio.Event()
        await asyncio.to_thread(self.repository.reset_processing_strategy_data_callbacks)
        while not self._stop.is_set():
            try:
                await self.process_due_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Strategy-data callback worker iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def process_due_once(self) -> int:
        records = await asyncio.to_thread(
            self.repository.claim_due_strategy_data_callbacks,
            limit=self.batch_size,
        )
        for record in records:
            callback_id = str(record.get("id") or "")
            try:
                response = await self.client.send(record.get("payload") or {})
                await asyncio.to_thread(
                    self.repository.complete_strategy_data_callback,
                    callback_id,
                    response=response,
                )
                logger.info(
                    "strategy_data_callback_sent outbox_id=%s task_id=%s record_kind=%s response_code=%s",
                    callback_id,
                    str(record.get("task_id") or ""),
                    str(record.get("record_kind") or ""),
                    str(response.get("code") or ""),
                )
            except Exception as exc:
                failure = await asyncio.to_thread(
                    self.repository.fail_strategy_data_callback,
                    callback_id,
                    error=f"{type(exc).__name__}: {exc}",
                    max_attempts=self.max_attempts,
                    base_delay_seconds=self.retry_base_seconds,
                )
                logger.warning(
                    "strategy_data_callback_failed outbox_id=%s task_id=%s record_kind=%s status=%s retry_count=%s error=%s",
                    callback_id,
                    str(record.get("task_id") or ""),
                    str(record.get("record_kind") or ""),
                    str(failure.get("status") or ""),
                    str(failure.get("retry_count") or ""),
                    f"{type(exc).__name__}: {exc}"[:500],
                )
        return len(records)

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        if not self.available:
            return {"enabled": False, "outbox": {}}
        try:
            counts = self.repository.strategy_data_outbox_status()
        except Exception as exc:
            return {"enabled": True, "error": f"{type(exc).__name__}: {exc}", "outbox": {}}
        return {"enabled": True, "outbox": counts}


def customer_reply_type(raw_type: str, *, has_image: bool = False) -> str:
    value = str(raw_type or "").strip().lower()
    aliases = {
        "audio": "voice",
        "pic": "image",
        "picture": "image",
        "short_video": "video",
        "shortvideo": "video",
        "location": "other",
        "link": "other",
        "miniprogram": "other",
        "unknown": "other",
    }
    normalized = aliases.get(value, value)
    if normalized in _CUSTOMER_REPLY_TYPES:
        return normalized
    return "image" if has_image else "text" if not value else "other"


def _reply_times(context: dict[str, Any]) -> tuple[str, int]:
    raw = str(context.get("msgtime") or "").strip()
    try:
        value = float(raw)
        if value > 10_000_000_000:
            value /= 1000.0
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        parsed = datetime.now(timezone.utc)
    return parsed.isoformat(), int(parsed.timestamp())


def _bounded_message_id(value: str) -> str:
    clean = str(value or "").strip()
    return clean if len(clean) <= 64 else hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _numeric_or_text(value: Any) -> int | str:
    clean = str(value or "").strip()
    return int(clean) if clean.isdigit() else clean


def _positive_int(value: Any) -> int | None:
    clean = str(value or "").strip()
    if not clean.isdigit():
        return None
    parsed = int(clean)
    return parsed if parsed > 0 else None


def _primary_script_database_id(state: dict[str, Any], selected_script_code: str) -> int | None:
    if not selected_script_code:
        return None
    recall = state.get("sales_recall") if isinstance(state.get("sales_recall"), dict) else {}
    for candidate in recall.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        code = str(candidate.get("source_id") or candidate.get("script_code") or "").strip()
        if code != selected_script_code:
            continue
        database_id = str(candidate.get("script_id") or candidate.get("id") or "").strip()
        parsed = _positive_int(database_id)
        if parsed is not None:
            return parsed
    return None


def _sent_content_summary(raw_messages: Any) -> str:
    output: list[str] = []
    for message in raw_messages or []:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "text").strip().lower()
        content = message.get("content")
        if message_type == "text":
            if isinstance(content, dict):
                text = str(content.get("text") or "").strip()
            else:
                text = str(content or "").strip()
            if text:
                output.append(text)
        elif message_type in {"image", "video", "store_address", "payment_collection"}:
            output.append(f"[{message_type}]")
    return "\n".join(output)[:2000]


def _customer_reply_content(state: dict[str, Any]) -> str:
    control = state.get("reply_control") if isinstance(state.get("reply_control"), dict) else {}
    merged = [
        str(item).strip()
        for item in control.get("merged_customer_messages") or []
        if str(item).strip()
    ]
    if merged:
        return "\n".join(merged)[-2000:]
    return str(state.get("content") or "")[:2000]
