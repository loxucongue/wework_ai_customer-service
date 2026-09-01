from __future__ import annotations

import asyncio
import time
from typing import Any

from app.schemas import ChatRequest
from app.services.customer_payment_state import is_paid_deposit_state, resolved_payment_fact
from app.services.customer_scope import customer_scope_from_identity
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


def _string(value: Any) -> str:
    return str(value or "").strip()


def _pack_category(pack: dict[str, Any]) -> str:
    return _string(pack.get("sop_category")) or _string(pack.get("id"))


def _send_once_key(identity: dict[str, str], sop_pack_id: str) -> str:
    pack_id = _string(sop_pack_id).lower()
    external_userid = _string(identity.get("external_userid")).lower()
    customer_id = _string(identity.get("customer_id")).lower()
    customer_key = external_userid or customer_id
    wechat = _string(identity.get("wechat")).lower()
    if not pack_id or not customer_key or not wechat:
        return ""
    corp_id = _string(identity.get("corp_id")).lower()
    customer_kind = "external" if external_userid else "customer"
    return f"sop_pack:{pack_id}|corp:{corp_id}|wechat:{wechat}|{customer_kind}:{customer_key}"


def _chat_order_request_context(
    request: ChatRequest,
    request_context: dict[str, Any],
    identity: dict[str, str],
) -> dict[str, Any]:
    output = dict(request.request_context or {})
    output.update(request_context or {})
    request_values = {
        "corp_id": request.corp_id,
        "user_id": request.user_id,
        "wechat": request.wechat,
        "external_userid": request.external_userid,
        "customer_add_wechat_id": request.customer_add_wechat_id,
        "confirmed_store_id": request.confirmed_store_id or request.store_id,
        "confirmed_store_name": request.confirmed_store_name or request.store_name,
    }
    for key, value in request_values.items():
        if output.get(key) in (None, "") and value not in (None, ""):
            output[key] = value
    for key in ("corp_id", "user_id", "wechat", "external_userid", "customer_id"):
        if output.get(key) in (None, "") and identity.get(key) not in (None, ""):
            output[key] = identity[key]
    return output


class SopExecutionCore:
    """Shared safety gates and task lifecycle for SOP execution adapters."""

    def _load_chat_customer_memory(self, identity: dict[str, str]) -> dict[str, Any]:
        if not self.memory_store:
            return {}
        scope = customer_scope_from_identity(identity)
        if not scope.persistence_allowed:
            return {}
        try:
            memory = self.memory_store.load(scope.sales_contact_key)
        except Exception:
            return {}
        return memory if isinstance(memory, dict) else {}

    async def _judge_event_sop_with_retries(
        self,
        selector_input: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        attempts: list[dict[str, Any]] = []
        last_error = ""
        overall_deadline = time.monotonic() + self.event_model_total_timeout_seconds
        for attempt in range(1, self.event_model_retry_attempts + 1):
            remaining_before_ms = max(0, int((overall_deadline - time.monotonic()) * 1000))
            if remaining_before_ms <= 0:
                last_error = "TimeoutError: event model total deadline exhausted"
                break
            started = time.perf_counter()
            deadline = min(overall_deadline, time.monotonic() + self.event_model_attempt_timeout_seconds)
            try:
                async with self._event_model_semaphore:
                    output = await self._judge_event_sop(selector_input, deadline_monotonic=deadline)
                if getattr(self, "event_schema_only_normalizer_enabled", False) and _string(output.get("error")):
                    raise ValueError(_string(output.get("error")))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "error": last_error,
                        "remaining_budget_ms_before": remaining_before_ms,
                        "remaining_budget_ms_after": max(0, int((overall_deadline - time.monotonic()) * 1000)),
                        "total_deadline_seconds": self.event_model_total_timeout_seconds,
                        "model_usage": compact(self.model_client.last_usage or {}, max_chars=1800),
                    }
                )
                if attempt < self.event_model_retry_attempts and self.event_model_retry_delay_seconds:
                    sleep_seconds = min(
                        self.event_model_retry_delay_seconds,
                        max(0.0, overall_deadline - time.monotonic()),
                    )
                    if sleep_seconds > 0:
                        await asyncio.sleep(sleep_seconds)
                continue
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "succeeded",
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "remaining_budget_ms_before": remaining_before_ms,
                    "remaining_budget_ms_after": max(0, int((overall_deadline - time.monotonic()) * 1000)),
                    "total_deadline_seconds": self.event_model_total_timeout_seconds,
                    "model_usage": compact(self.model_client.last_usage or {}, max_chars=1800),
                }
            )
            return output, attempts, ""
        return {}, attempts, last_error or "event model retries exhausted"

    def _load_chat_order_gate(
        self,
        *,
        request: ChatRequest,
        request_context: dict[str, Any],
        identity: dict[str, str],
        customer_memory: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.customer_context_service:
            return {"status": "not_configured", "customer_context": {}, "summary": {"source": "not_configured"}}
        effective_context = _chat_order_request_context(request, request_context, identity)
        try:
            context = self.customer_context_service.load(
                customer_id=identity.get("customer_id", ""),
                memory=customer_memory,
                request_context=effective_context,
            )
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "summary": {"source": "exception"},
            }
        if not isinstance(context, dict) or context.get("source") != "platform_agent" or context.get("orders_error"):
            error = str(
                (context or {}).get("orders_error")
                or (context or {}).get("error")
                or "platform_order_context_unavailable"
            )
            return {
                "status": "failed",
                "error": error,
                "summary": {"source": str((context or {}).get("source") or "unknown")},
            }
        basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
        stored = basic.get("deposit_state")
        stored_fact = stored if isinstance(stored, dict) else {}
        payment = resolved_payment_fact(
            orders=context.get("orders"),
            existing_state=str(stored_fact.get("status") or stored or ""),
            existing_source=str(stored_fact.get("source") or ""),
            existing_fact=stored_fact,
        )
        summary = {
            "source": "platform_agent.order_index",
            "order_count": len(context.get("orders") or []),
            "order_id": str(payment.get("order_id") or ""),
            "store_id": str(payment.get("store_id") or ""),
            "deposit_state": str(payment.get("deposit_state") or "unknown"),
            "prepay_required": payment.get("prepay_required"),
            "prepay_paid": payment.get("prepay_paid"),
        }
        return {
            "status": "paid" if is_paid_deposit_state(payment.get("deposit_state")) else "unpaid",
            "customer_context": context,
            "payment": payment,
            "summary": summary,
        }

    def _record_chat_gate_task(
        self,
        *,
        request: ChatRequest,
        request_id: str,
        request_context: dict[str, Any],
        identity: dict[str, str],
        pack: dict[str, Any],
        reply_messages: list[dict[str, Any]],
        trigger_source: str = "chat_gate",
        mark_sent: bool = True,
    ) -> dict[str, Any]:
        event_id = f"{trigger_source}:{request_id}"
        self.repository.create_sop_event(
            {
                "event_id": event_id,
                "event_type": "chat_gate",
                "source": "ai_paths_platform_reply",
                "request_reply": True,
                "created_at": utc_now_iso(),
                "request_context": request_context,
                "customer": {
                    "customer_id": request.customer_id,
                    "external_userid": request.external_userid,
                },
            }
        )
        sop_pack_id = str(pack.get("id") or "")
        task = self.repository.create_sop_send_task(
            event_id=event_id,
            idempotency_key="|".join(
                ["chat_gate", request_id, identity["external_userid"] or identity["customer_id"], sop_pack_id]
            ),
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            corp_id=identity["corp_id"],
            user_id=identity["user_id"],
            wechat=identity["wechat"],
            sop_pack_id=sop_pack_id,
            sop_pack_name=str(pack.get("name") or ""),
            sop_category=_pack_category(pack),
            trigger_source=trigger_source,
            reply_messages=reply_messages,
            status="pending",
            send_once_key=_send_once_key(identity, str(pack.get("send_once_group") or sop_pack_id)),
        )
        if mark_sent and task.get("id") and task.get("status") == "pending":
            created = bool(task.get("created"))
            task = self.repository.update_sop_send_task(
                str(task["id"]),
                status="sent",
                send_payload={"mode": "sync_http_response", "request_id": request_id, "reply_messages": reply_messages},
                send_response={"accepted": True, "mode": "sync_http_response"},
                sent_at=utc_now_iso(),
            )
            task["created"] = created
        return task

    def confirm_chat_gate_task_sent(
        self,
        task: dict[str, Any],
        *,
        request_id: str,
        reply_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        task_id = _string(task.get("id")) if isinstance(task, dict) else ""
        if not task_id or _string(task.get("status")) != "pending":
            return task
        created = bool(task.get("created"))
        updated = self.repository.update_sop_send_task(
            task_id,
            status="sent",
            send_payload={"mode": "sync_http_response", "request_id": request_id, "reply_messages": reply_messages},
            send_response={"accepted": True, "mode": "sync_http_response"},
            sent_at=utc_now_iso(),
        )
        updated["created"] = created
        return updated

    def fail_chat_gate_task(self, task: dict[str, Any], *, error: str) -> dict[str, Any]:
        task_id = _string(task.get("id")) if isinstance(task, dict) else ""
        if not task_id or _string(task.get("status")) != "pending":
            return task
        created = bool(task.get("created"))
        updated = self.repository.update_sop_send_task(
            task_id,
            status="failed",
            send_response={"accepted": False, "error": str(error or "ai_reply_failed_before_sop_send")[:240]},
        )
        updated["created"] = created
        return updated

    def finalize_message_delivery(self, dispatch: dict[str, Any]) -> None:
        """Apply delivery facts without conflating them with SOP consumption callbacks."""
        context = dispatch.get("source_context") if isinstance(dispatch.get("source_context"), dict) else {}
        task_id = _string(context.get("sop_send_task_id") or dispatch.get("source_task_id"))
        if not task_id:
            raise ValueError("SOP delivery dispatch is missing sop_send_task_id")
        task = self.repository.get_sop_send_task(task_id)
        if not task:
            raise ValueError(f"SOP send task not found: {task_id}")
        send_payload = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
        send_response = task.get("send_response") if isinstance(task.get("send_response"), dict) else {}
        callback_response = {**send_response, "message_delivery": dispatch}
        status = _string(dispatch.get("status"))
        if status == "send_succeeded":
            sent_at = _string(dispatch.get("confirmed_at")) or utc_now_iso()
            task = self.repository.update_sop_send_task(
                task_id,
                status="sent",
                send_payload=send_payload,
                send_response=callback_response,
                sent_at=sent_at,
            )
            self._record_successful_send(task, sent_at=sent_at)
            return
        if status in {"send_failed", "partial_failed"}:
            self.repository.update_sop_send_task(
                task_id,
                status="failed" if status == "send_failed" else "partial_failed",
                send_payload=send_payload,
                send_response=callback_response,
                error=_string(dispatch.get("error_message")) or status,
            )

    def _record_successful_send(self, task: dict[str, Any], *, sent_at: str) -> None:
        customer_id = _string(task.get("customer_id"))
        scope = customer_scope_from_identity(task)
        touch_message_time = getattr(self.repository, "touch_customer_message_time", None)
        if scope.persistence_allowed and callable(touch_message_time):
            try:
                touch_message_time(scope.sales_contact_key, field="last_outreach_at", value=sent_at)
            except Exception:
                pass
        if not customer_id or not self.memory_store or not scope.persistence_allowed:
            return
        messages = task.get("reply_messages") if isinstance(task.get("reply_messages"), list) else []
        message_types = [_string(item.get("type")) for item in messages if isinstance(item, dict) and _string(item.get("type"))]
        send_payload = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
        selected_ids = [_string(item) for item in send_payload.get("selected_sop_pack_ids") or [] if _string(item)]
        selected_categories = [
            _string(item) for item in send_payload.get("selected_sop_categories") or [] if _string(item)
        ]
        if not selected_ids:
            selected_ids = [_string(task.get("sop_pack_id"))]
        for index, pack_id in enumerate(selected_ids):
            if not pack_id:
                continue
            category = selected_categories[index] if index < len(selected_categories) else _string(task.get("sop_category"))
            try:
                self.memory_store.record_sop_pack_sent(
                    scope.sales_contact_key,
                    sop_pack_id=pack_id,
                    sop_category=category,
                    source_event_id=_string(task.get("event_id")),
                    message_types=message_types,
                    sent_at=sent_at,
                    task_id=_string(task.get("id")),
                )
            except Exception:
                pass
