from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.storage.serialization import utc_now_iso


logger = logging.getLogger(__name__)


class SopFailureAlertService:
    """Durably alert actionable third-party SOP send failures."""

    EVENT_TYPE = "sop_failure_alert"
    RETRY_STATUSES = ["accepted", "alert_sending", "alert_retry"]
    SEND_CONFIRMED_STATUSES = {
        "sent",
        "sent_consume_deferred",
        "send_succeeded",
    }
    NON_ALERT_STATUSES = {
        "accepted",
        "platform_delivery_pending",
        "platform_send_uncertain",
    }
    NON_ALERT_REASON_MARKERS = {
        "active_send_timeout_unknown_result",
        "customer_relation_deleted",
        "delivery_not_confirmed",
        "human_takeover",
    }

    def __init__(self, *, settings: Any, repository: Any, client: Any) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client
        self._retry_batch_size = max(
            1,
            min(int(getattr(settings, "sop_failure_alert_retry_batch_size", 20) or 20), 100),
        )

    @property
    def available(self) -> bool:
        return bool(getattr(self.client, "available", False))

    async def notify_result(
        self,
        result: dict[str, Any],
        *,
        tasks: list[dict[str, Any]] | None = None,
        phase: str,
    ) -> int:
        status = str(result.get("status") or "unknown").strip() or "unknown"
        reason = _result_reason(result)
        if (
            not self.available
            or self._result_has_send_evidence(result)
            or self._is_non_alert_outcome(status=status, reason=reason)
        ):
            return 0
        task_map = {
            _task_id(task): task
            for task in tasks or []
            if isinstance(task, dict) and _task_id(task)
        }
        task_ids = _result_task_ids(result)
        if not task_ids:
            task_ids = ["unknown"]
        sent = 0
        for task_id in task_ids:
            task = task_map.get(task_id, {})
            if task_id != "unknown" and await asyncio.to_thread(self._local_task_has_send_evidence, task_id):
                continue
            sent += await self.notify_task_failure(
                task=task,
                task_id=task_id,
                status=status,
                reason=reason,
                phase=phase,
            )
        return sent

    async def notify_task_failure(
        self,
        *,
        task: dict[str, Any] | None = None,
        task_id: str = "",
        status: str,
        reason: str,
        phase: str,
    ) -> int:
        if not self.available:
            return 0
        task = task if isinstance(task, dict) else {}
        clean_task_id = str(task_id or _task_id(task) or "unknown").strip() or "unknown"
        clean_status = _clean_value(status, fallback="unknown")
        clean_reason = _clean_value(reason, fallback=clean_status, limit=500)
        if self._is_non_alert_outcome(status=clean_status, reason=clean_reason):
            return 0
        if (
            clean_task_id != "unknown"
            and not clean_task_id.startswith("system-")
            and await asyncio.to_thread(self._local_task_has_send_evidence, clean_task_id)
        ):
            return 0
        clean_phase = _clean_value(phase, fallback="unknown")
        category = _failure_category(clean_status)
        event_id = _alert_event_id(clean_task_id, category, clean_reason, clean_phase)
        payload = {
            "event_id": event_id,
            "event_type": self.EVENT_TYPE,
            "source": "third_party_sop_worker",
            "request_reply": False,
            "created_at": utc_now_iso(),
            "alert": {
                "alert_id": event_id.rsplit(":", 1)[-1],
                "task_id": clean_task_id,
                "status": clean_status,
                "category": category,
                "phase": clean_phase,
                "reason": clean_reason,
                "customer_id": _first(task, "customerId", "customer_id", "platformCustomerId"),
                "wechat": _first(task, "wechat", "weChat", "userId", "user_id"),
                "scheduled_at": _first(
                    task,
                    "scheduledAt",
                    "scheduled_at",
                    "planTime",
                    "plan_time",
                    "triggerTime",
                ),
                "release_id": str(getattr(self.settings, "release_id", "unknown") or "unknown")[:120],
                "occurred_at": utc_now_iso(),
            },
        }
        event = await asyncio.to_thread(self.repository.create_sop_event, payload)
        if not event.get("created"):
            return 0
        await self._deliver_event(event)
        return 1

    async def notify_system_failure(self, *, phase: str, reason: str) -> int:
        if not self.available:
            return 0
        hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        return await self.notify_task_failure(
            task={},
            task_id=f"system-{hour_bucket}",
            status="pipeline_failed",
            reason=reason,
            phase=phase,
        )

    async def retry_pending(self) -> int:
        if not self.available:
            return 0
        events = await asyncio.to_thread(
            self.repository.list_sop_events_by_statuses,
            self.RETRY_STATUSES,
            limit=self._retry_batch_size,
            event_type=self.EVENT_TYPE,
        )
        delivered = 0
        for event in events:
            if self._event_is_non_alert_outcome(event):
                await asyncio.to_thread(
                    self.repository.update_sop_event_status,
                    str(event.get("event_id") or ""),
                    status="alert_suppressed",
                    error="",
                )
                continue
            delivered += 1 if await self._deliver_event(event) else 0
        return delivered

    async def _deliver_event(self, event: dict[str, Any]) -> bool:
        event_id = str(event.get("event_id") or "").strip()
        payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
        alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
        if not event_id or not alert:
            if event_id:
                await asyncio.to_thread(
                    self.repository.update_sop_event_status,
                    event_id,
                    status="alert_failed",
                    error="invalid_alert_payload",
                )
            return False
        await asyncio.to_thread(
            self.repository.update_sop_event_status,
            event_id,
            status="alert_sending",
            error="",
        )
        try:
            await self.client.send_markdown(
                title="第三方 SOP 发送失败预警",
                text=_render_markdown(alert),
            )
        except Exception as exc:
            delay_seconds = min(900, 30 * (2 ** min(max(0, int(event.get("retry_count") or 0)), 5)))
            next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
            safe_error = f"{type(exc).__name__}:{_clean_value(str(exc), fallback='alert_delivery_failed', limit=300)}"
            await asyncio.to_thread(
                self.repository.schedule_sop_event_retry,
                event_id,
                status="alert_retry",
                error=safe_error,
                next_retry_at=next_retry_at,
            )
            logger.error("Third-party SOP failure alert delivery failed: event_id=%s type=%s", event_id, type(exc).__name__)
            return False
        await asyncio.to_thread(
            self.repository.update_sop_event_status,
            event_id,
            status="alert_sent",
            error="",
        )
        return True

    def _result_has_send_evidence(self, result: dict[str, Any]) -> bool:
        status = str(result.get("status") or "").strip()
        return status in self.SEND_CONFIRMED_STATUSES

    def _is_non_alert_outcome(self, *, status: str, reason: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        normalized_reason = str(reason or "").strip().lower()
        if normalized_status in self.NON_ALERT_STATUSES:
            return True
        return any(marker in normalized_reason for marker in self.NON_ALERT_REASON_MARKERS)

    def _event_is_non_alert_outcome(self, event: dict[str, Any]) -> bool:
        payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
        alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
        return self._is_non_alert_outcome(
            status=str(alert.get("status") or ""),
            reason=str(alert.get("reason") or ""),
        )

    def _local_task_has_send_evidence(self, task_id: str) -> bool:
        local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
        if not isinstance(local_task, dict):
            return False
        status = str(local_task.get("status") or "").strip()
        return bool(status == "sent" and str(local_task.get("sent_at") or "").strip())


def _result_task_ids(result: dict[str, Any]) -> list[str]:
    primary = str(result.get("task_id") or "").strip()
    if primary:
        return [primary]
    raw_ids = result.get("task_ids") if isinstance(result.get("task_ids"), list) else []
    return list(dict.fromkeys(str(value or "").strip() for value in raw_ids if str(value or "").strip()))


def _result_reason(result: dict[str, Any]) -> str:
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    return _clean_value(
        result.get("reason")
        or decision.get("failure_reason")
        or result.get("status"),
        fallback="unknown",
        limit=500,
    )


def _failure_category(status: str) -> str:
    if status in {"accepted", "platform_send_uncertain", "platform_delivery_pending"}:
        return "delivery_unconfirmed"
    if status in {"completed_without_send", "shadow_no_send", "shadow_send"}:
        return "not_sent"
    if "retry" in status or status in {"recovery_waiting", "retry_waiting"}:
        return "send_retry"
    if "failed" in status or status == "send_failed":
        return "send_failed"
    return "processing_failed"


def _alert_event_id(task_id: str, category: str, reason: str, phase: str) -> str:
    dedupe_reason = phase if task_id.startswith("system-") else reason
    digest = hashlib.sha256(f"{task_id}|{category}|{dedupe_reason}".encode("utf-8")).hexdigest()[:24]
    return f"sop_failure_alert:{digest}"


def _task_id(task: dict[str, Any]) -> str:
    return _first(task, "taskId", "task_id", "id")


def _first(task: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = task.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()[:160]
    return ""


def _clean_value(value: Any, *, fallback: str, limit: int = 160) -> str:
    clean = " ".join(str(value or "").replace("`", "'").split())
    return (clean or fallback)[:limit]


def _render_markdown(alert: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            "### 第三方 SOP 发送失败预警",
            f"- 任务 ID：{_clean_value(alert.get('task_id'), fallback='unknown')}",
            f"- 状态：{_clean_value(alert.get('status'), fallback='unknown')}",
            f"- 失败阶段：{_clean_value(alert.get('phase'), fallback='unknown')}",
            f"- 失败原因：{_clean_value(alert.get('reason'), fallback='unknown', limit=500)}",
            f"- 客户 ID：{_clean_value(alert.get('customer_id'), fallback='未提供')}",
            f"- 接待企微：{_clean_value(alert.get('wechat'), fallback='未提供')}",
            f"- 计划时间：{_clean_value(alert.get('scheduled_at'), fallback='未提供')}",
            f"- 发生时间：{_clean_value(alert.get('occurred_at'), fallback='unknown')}",
            f"- 发布版本：{_clean_value(alert.get('release_id'), fallback='unknown')}",
            f"- 告警 ID：{_clean_value(alert.get('alert_id'), fallback='unknown')}",
            "- 判定原则：未取得真实发送成功证据即按失败告警；任务保持未消费并继续恢复。",
        ]
    )
