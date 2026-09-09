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
        "platform_completed",
        "platform_delivery_pending",
        "platform_send_uncertain",
    }
    NON_ALERT_REASON_MARKERS = {
        "active_send_timeout_unknown_result",
        "customer_already_opened",
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
            if task_id != "unknown" and await asyncio.to_thread(self._local_task_suppresses_alert, task_id):
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
            and await asyncio.to_thread(self._local_task_suppresses_alert, clean_task_id)
        ):
            return 0
        clean_phase = _clean_value(phase, fallback="unknown")
        category = _failure_category(clean_status)
        existing_loader = getattr(self.repository, "find_sop_failure_alert_by_task_id", None)
        if callable(existing_loader) and not clean_task_id.startswith("system-"):
            existing = await asyncio.to_thread(existing_loader, clean_task_id)
            if isinstance(existing, dict) and existing:
                return 0
        event_id = _alert_event_id(clean_task_id, phase=clean_phase)
        responsibility, responsibility_label = _failure_responsibility(
            task_id=clean_task_id,
            reason=clean_reason,
            phase=clean_phase,
        )
        failure_type, failure_type_label = _failure_type(clean_reason)
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
                "failure_type": failure_type,
                "failure_type_label": failure_type_label,
                "responsibility": responsibility,
                "responsibility_label": responsibility_label,
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
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        claimed = await asyncio.to_thread(
            self.repository.claim_sop_failure_alert_delivery,
            event_id,
            stale_before=stale_before,
        )
        if not claimed:
            return False
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

    def _local_task_suppresses_alert(self, task_id: str) -> bool:
        local_task = self.repository.get_sop_send_task_by_idempotency_key(f"platform-sop:{task_id}")
        if not isinstance(local_task, dict):
            return False
        status = str(local_task.get("status") or "").strip()
        sent_at = str(local_task.get("sent_at") or "").strip()
        send_response = local_task.get("send_response") if isinstance(local_task.get("send_response"), dict) else {}
        data = send_response.get("data") if isinstance(send_response.get("data"), dict) else {}
        delivery_status = str(data.get("delivery_status") or "").strip()
        single_id = str(
            data.get("system_msgid")
            or data.get("systemMsgId")
            or data.get("msgid")
            or data.get("msgId")
            or ""
        ).strip()
        multiple_ids = data.get("system_msgids") if isinstance(data.get("system_msgids"), list) else []
        if delivery_status in {"send_succeeded", "delivered"}:
            return True
        if delivery_status == "platform_accepted" and (
            single_id or any(str(value or "").strip() for value in multiple_ids)
        ):
            return True
        if status in self.SEND_CONFIRMED_STATUSES.union({"sent_recovered"}) and sent_at:
            return True
        audit = local_task.get("send_payload") if isinstance(local_task.get("send_payload"), dict) else {}
        decision = audit.get("decision") if isinstance(audit.get("decision"), dict) else {}
        local_reason = " ".join(
            str(value or "")
            for value in (
                local_task.get("error"),
                audit.get("reason"),
                decision.get("reason"),
            )
        )
        return self._is_non_alert_outcome(status=status, reason=local_reason)


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


def _alert_event_id(task_id: str, *, phase: str) -> str:
    lifecycle_key = phase if task_id.startswith("system-") else "sop_execution_failure"
    digest = hashlib.sha256(f"{task_id}|{lifecycle_key}".encode("utf-8")).hexdigest()[:24]
    return f"sop_failure_alert:{digest}"


def _failure_type(reason: str) -> tuple[str, str]:
    normalized = str(reason or "").strip().lower()
    if "timeout" in normalized or "timed out" in normalized:
        return "interface_timeout", "接口超时"
    if normalized.startswith(("missing_identity", "missing_event_log_id")):
        return "missing_required_parameter", "任务缺少必填参数"
    if normalized.startswith("mixed_customer_identity"):
        return "invalid_task_qualification", "任务身份资格不一致"
    if normalized.startswith(
        (
            "missing_ai_auto_reply",
            "missing_customer_relation",
            "missing_conversation_messages",
            "invalid_conversation",
        )
    ):
        return "qualification_data_incomplete", "发送资格数据不足"
    if normalized.startswith(("sop_messages_empty", "invalid_sop_message_group", "missing_sop_message_id")):
        return "invalid_task_content", "第三方任务内容不完整"
    if "failed" in normalized or "error" in normalized or "rejected" in normalized:
        return "interface_or_execution_failure", "接口或执行失败"
    return "processing_failure", "任务处理失败"


def _failure_responsibility(*, task_id: str, reason: str, phase: str) -> tuple[str, str]:
    normalized_reason = str(reason or "").strip().lower()
    normalized_phase = str(phase or "").strip().lower()
    if normalized_reason.startswith(
        (
            "missing_identity",
            "mixed_customer_identity",
            "missing_event_log_id",
            "sop_messages_",
            "invalid_sop_message_group",
            "missing_sop_message_id",
            "invalid_message_content",
        )
    ) or normalized_phase in {"poll_pending_and_content", "platform_consume", "platform_rule_data"}:
        return "third_party_sop_platform", "第三方 SOP 平台"
    if normalized_reason.startswith(
        (
            "customer_gate_query_failed",
            "invalid_conversation",
            "missing_ai_auto_reply",
            "missing_customer_relation",
            "missing_conversation_messages",
        )
    ):
        return "aics_customer_state_interface", "我方客户状态/会话接口"
    if normalized_reason.startswith(("send_interface_", "send_failed", "wecom_")):
        return "aics_proactive_send_interface", "我方主动发送链路"
    if normalized_phase.startswith(("persist_", "queue_process_exception", "recovery_iteration")):
        return "aics_runtime", "我方任务运行服务"
    if task_id.startswith("system-"):
        return "pending_confirmation", "待结合接口阶段确认"
    return "pending_confirmation", "待确认"


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
            f"- 失败类型：{_clean_value(alert.get('failure_type_label'), fallback='任务处理失败')}",
            f"- 责任方向：{_clean_value(alert.get('responsibility_label'), fallback='待确认')}",
            f"- 失败阶段：{_clean_value(alert.get('phase'), fallback='unknown')}",
            f"- 失败原因：{_clean_value(alert.get('reason'), fallback='unknown', limit=500)}",
            f"- 客户 ID：{_clean_value(alert.get('customer_id'), fallback='未提供')}",
            f"- 接待企微：{_clean_value(alert.get('wechat'), fallback='未提供')}",
            f"- 计划时间：{_clean_value(alert.get('scheduled_at'), fallback='未提供')}",
            f"- 发生时间：{_clean_value(alert.get('occurred_at'), fallback='unknown')}",
            f"- 发布版本：{_clean_value(alert.get('release_id'), fallback='unknown')}",
            f"- 告警 ID：{_clean_value(alert.get('alert_id'), fallback='unknown')}",
            "- 处理原则：业务已承接状态不告警；本告警任务保持未消费并继续恢复，后续任务不得越过。",
        ]
    )
