from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks

from app.services.outreach_send_client import OutreachSendClient
from app.services.customer_payment_state import is_paid_deposit_state, resolved_payment_fact
from app.services.customer_scope import customer_scope_from_identity
from app.services.customer_relation import customer_relation_is_deleted
from app.services.sop_event_decision import (
    build_event_ai_reply_policy,
    combine_selected_pack_messages,
    selected_candidate_packs,
)
from app.services.sop_execution_service import SopExecutionService, first_add_candidate_packs, is_platform_auto_opening_message
from app.services.sop_message_sanitizer import apply_sop_text_adjustments, sanitize_sop_reply_messages
from app.services.sop_platform_task_policy import classify_platform_task_route
from app.services.sop_reply_pack_service import ALLOWED_MESSAGE_TYPES, SopReplyPackService
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


FIRST_ADD_EVENT_TYPES = {"sop_friend_added_schedule_batch", "sop_friend_added_immediate"}
SOP_QUIET_TIMEZONE = ZoneInfo("Asia/Shanghai")
SOP_QUIET_START_HOUR = 0
SOP_QUIET_END_HOUR = 8
SOP_QUIET_INACTIVITY_MINUTES = 30
SOP_RECENT_ASSISTANT_ACTIVITY_MINUTES = max(
    1,
    int(os.getenv("SOP_RECENT_ASSISTANT_ACTIVITY_MINUTES", "8") or "8"),
)


class SopEventService:
    def __init__(
        self,
        *,
        repository: Any,
        sop_reply_pack_service: SopReplyPackService,
        outreach_send_client: OutreachSendClient,
        sop_execution_service: SopExecutionService | None = None,
        memory_store: Any | None = None,
        customer_context_service: Any | None = None,
        personalized_outreach_service: Any | None = None,
        daily_touch_soft_limit: int = 2,
        default_identity: dict[str, Any] | None = None,
        persistent_retry_attempts: int = 4,
        persistent_retry_base_delay_seconds: float = 30.0,
        persistent_retry_max_delay_seconds: float = 300.0,
        retry_batch_size: int = 5,
    ) -> None:
        self.repository = repository
        self.sop_reply_pack_service = sop_reply_pack_service
        self.outreach_send_client = outreach_send_client
        self.sop_execution_service = sop_execution_service
        self.memory_store = memory_store
        self.customer_context_service = customer_context_service
        self.personalized_outreach_service = personalized_outreach_service
        self.daily_touch_soft_limit = max(1, int(daily_touch_soft_limit or 2))
        self.persistent_retry_attempts = max(0, int(persistent_retry_attempts or 0))
        self.persistent_retry_base_delay_seconds = max(0.0, float(persistent_retry_base_delay_seconds or 0.0))
        self.persistent_retry_max_delay_seconds = max(
            self.persistent_retry_base_delay_seconds,
            float(persistent_retry_max_delay_seconds or 0.0),
        )
        self.retry_batch_size = max(1, int(retry_batch_size or 1))
        self.default_identity = {
            "corp_id": _string((default_identity or {}).get("corp_id")),
            "user_id": _string((default_identity or {}).get("user_id")),
            "wechat": _string((default_identity or {}).get("wechat")),
        }

    async def accept_event(self, payload: dict[str, Any], background_tasks: BackgroundTasks | None = None) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "").strip()
        event_type = str(payload.get("event_type") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        if not event_type:
            raise ValueError("event_type is required")
        event = self.repository.create_sop_event(payload)
        if event.get("created"):
            if background_tasks is not None:
                background_tasks.add_task(self.process_event, event_id)
            else:
                await self.process_event(event_id)
        return {
            "accepted": True,
            "event_id": event_id,
            "duplicate": not bool(event.get("created")),
        }

    async def process_event(self, event_id: str) -> dict[str, Any]:
        event = self.repository.get_sop_event(event_id)
        payload = event.get("raw_payload") if isinstance(event.get("raw_payload"), dict) else {}
        if not payload:
            self.repository.update_sop_event_status(event_id, status="failed", error="empty_event_payload")
            return {"status": "failed", "error": "empty_event_payload"}

        customers = payload.get("customers") if isinstance(payload.get("customers"), list) else []
        if not customers:
            self.repository.update_sop_event_status(event_id, status="skipped_no_customers")
            return {"status": "skipped_no_customers", "tasks": []}

        task_results: list[dict[str, Any]] = []
        for index, customer_item in enumerate(customers):
            customer = customer_item if isinstance(customer_item, dict) else {}
            task = await self._create_customer_task(payload, customer, index=index)
            if not task:
                continue
            if not task.get("created") or task.get("status") != "pending":
                task_results.append(task)
                continue
            task_results.append(await self._send_task(task))

        event_type = _string(payload.get("event_type"))
        retryable_statuses = {
            "failed_model_error",
            "failed_order_fetch",
            "failed_personalized_outreach_not_configured",
            "failed_personalized_outreach_plan",
        }
        if event_type == "sop_platform_task":
            retryable_statuses.add("failed_conversation_fetch")
        retryable_errors = [
            item for item in task_results if _string(item.get("status")) in retryable_statuses
        ]
        if retryable_errors:
            schedule_retry = getattr(self.repository, "schedule_sop_event_model_retry", None)
            if callable(schedule_retry):
                retry_event = schedule_retry(
                    event_id,
                    error=_string(retryable_errors[-1].get("error")) or "event_processing_failed",
                    max_attempts=self.persistent_retry_attempts,
                    base_delay_seconds=self.persistent_retry_base_delay_seconds,
                    max_delay_seconds=self.persistent_retry_max_delay_seconds,
                )
                retry_status = _string(retry_event.get("status")) or "processed_with_errors"
                return {"status": retry_status, "tasks": task_results, "retry": compact(retry_event, max_chars=1200)}

        resolve_retries = getattr(self.repository, "resolve_sop_event_model_retry_tasks", None)
        if callable(resolve_retries):
            resolve_retries(event_id)

        status = "processed"
        if any(_task_has_processing_error(item) for item in task_results):
            status = "processed_with_errors"
        elif not task_results:
            status = "skipped_no_tasks"
        self.repository.update_sop_event_status(event_id, status=status)
        return {"status": status, "tasks": task_results}

    async def process_due_model_retries(self) -> list[dict[str, Any]]:
        claim_due = getattr(self.repository, "claim_due_sop_event_model_retries", None)
        if not callable(claim_due):
            return []
        claimed = claim_due(limit=self.retry_batch_size)

        async def process_claimed(item: dict[str, Any]) -> dict[str, Any]:
            event_id = _string(item.get("event_id"))
            if not event_id:
                return {"event_id": "", "status": "skipped_missing_event_id"}
            try:
                result = await self.process_event(event_id)
            except Exception as exc:
                schedule_retry = getattr(self.repository, "schedule_sop_event_model_retry", None)
                if callable(schedule_retry):
                    retry_event = schedule_retry(
                        event_id,
                        error=f"{type(exc).__name__}: {exc}",
                        max_attempts=self.persistent_retry_attempts,
                        base_delay_seconds=self.persistent_retry_base_delay_seconds,
                        max_delay_seconds=self.persistent_retry_max_delay_seconds,
                    )
                    return {"event_id": event_id, "status": retry_event.get("status"), "error": str(exc)}
                return {"event_id": event_id, "status": "retry_worker_failed", "error": str(exc)}
            return {"event_id": event_id, **result}

        if not claimed:
            return []
        return list(await asyncio.gather(*(process_claimed(item) for item in claimed)))

    async def _create_customer_task(self, payload: dict[str, Any], customer: dict[str, Any], *, index: int) -> dict[str, Any]:
        event_type = _string(payload.get("event_type"))
        identity = self._complete_identity(_customer_identity(payload, customer))
        base_pack_id = "platform_actions" if event_type == "sop_platform_task" else "event_decision"
        base_pack_name = base_pack_id

        missing = [
            key
            for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat")
            if not str(identity.get(key) or "").strip()
        ]
        if missing:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=base_pack_id,
                sop_pack_name=base_pack_name,
                reply_messages=[],
                status="skipped_missing_identity",
                error="missing:" + ",".join(missing),
                send_payload={"identity": identity, "missing": missing},
            )

        conversation_fetch = await self.outreach_send_client.fetch_conversation(
            corp_id=identity["corp_id"],
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            user_id=identity["user_id"],
            wechat=identity["wechat"],
            limit=30,
        )
        if conversation_fetch.get("status") != "ok":
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=base_pack_id,
                sop_pack_name=base_pack_name,
                reply_messages=[],
                status="failed_conversation_fetch",
                error=str(conversation_fetch.get("error") or conversation_fetch.get("reason") or "conversation_fetch_failed"),
                send_payload={"identity": identity, "conversation_fetch": compact(conversation_fetch, max_chars=4000)},
            )

        customer_relation = (
            conversation_fetch.get("customer_relation")
            if isinstance(conversation_fetch.get("customer_relation"), dict)
            else {}
        )
        if customer_relation_is_deleted(customer_relation):
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="customer_deleted",
                sop_pack_name="customer_deleted",
                sop_category="customer_relation",
                reply_messages=[],
                status="skipped_customer_deleted",
                error="",
                send_payload={
                    "identity": identity,
                    "customer_relation": customer_relation,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                },
            )

        conversation_messages = conversation_fetch.get("messages") if isinstance(conversation_fetch.get("messages"), list) else []
        conversation_filter: dict[str, Any] = {}
        if event_type in FIRST_ADD_EVENT_TYPES:
            conversation_messages, conversation_filter = _first_add_conversation_messages(payload, customer, conversation_messages)
        conversation_activity = _conversation_activity(
            conversation_messages,
            first_added_at=_first_added_at(customer) if event_type in FIRST_ADD_EVENT_TYPES else None,
            event_at=_parse_time(payload.get("created_at") or payload.get("upstream_created_at")),
        )
        event_policy_evidence = self._event_policy_evidence(
            payload=payload,
            identity=identity,
            conversation_activity=conversation_activity,
        )
        if event_type in FIRST_ADD_EVENT_TYPES and conversation_activity["uncertain_customer_timing"]:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="first_add_customer_timing_uncertain",
                sop_pack_name="first_add_customer_timing_uncertain",
                reply_messages=[],
                status="skipped_customer_timing_uncertain",
                error="",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "event_policy_evidence": event_policy_evidence,
                },
            )
        if event_type in FIRST_ADD_EVENT_TYPES and conversation_activity["latest_customer_pending_ai_reply"]:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="first_add_customer_pending_ai_reply",
                sop_pack_name="first_add_customer_pending_ai_reply",
                reply_messages=[],
                status="skipped_customer_pending_ai_reply",
                error="",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "event_policy_evidence": event_policy_evidence,
                },
            )
        recent_assistant_activity = _recent_assistant_activity_summary(conversation_activity)
        if event_type in FIRST_ADD_EVENT_TYPES and recent_assistant_activity["skip"]:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="first_add_recent_active_conversation",
                sop_pack_name="first_add_recent_active_conversation",
                reply_messages=[],
                status="skipped_recent_active_conversation",
                error="",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "recent_assistant_activity": recent_assistant_activity,
                    "event_policy_evidence": event_policy_evidence,
                },
            )

        quiet_hours = _quiet_hours_summary(payload, conversation_messages)
        if event_type in FIRST_ADD_EVENT_TYPES and quiet_hours["skip"]:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=base_pack_id,
                sop_pack_name=base_pack_name,
                reply_messages=[],
                status="skipped_quiet_hours_inactive",
                error="",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "quiet_hours": quiet_hours,
                    "event_policy_evidence": event_policy_evidence,
                    "backlog_marker": {
                        "pending": True,
                        "reason": "suppressed_due_to_quiet_hours",
                        "event": _event_backlog_summary(payload, customer),
                    },
                },
            )

        if event_type not in FIRST_ADD_EVENT_TYPES and event_type != "sop_platform_task":
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=base_pack_id,
                sop_pack_name=base_pack_name,
                reply_messages=[],
                status="skipped_unsupported_event_type",
                error=f"unsupported_event_type:{event_type}",
                send_payload={"identity": identity, "conversation_fetch": _conversation_fetch_summary(conversation_fetch)},
            )

        customer_memory = self._load_customer_memory(identity)
        self._record_observed_customer_activity(identity, conversation_activity)
        if (
            not _string(customer_memory.get("last_customer_message_at"))
            and _string(conversation_activity.get("latest_customer_message_at"))
        ):
            customer_memory = {
                **customer_memory,
                "last_customer_message_at": _string(conversation_activity.get("latest_customer_message_at")),
            }
        order_gate = self._load_sop_order_gate(identity, customer_memory)
        customer_context = dict(order_gate.get("customer_context") or {})
        customer_context["_sop_order_gate"] = {
            **dict(order_gate.get("summary") or {}),
            "status": _string(order_gate.get("status")) or "unknown",
            "error": _string(order_gate.get("error")),
        }
        if event_type in FIRST_ADD_EVENT_TYPES and order_gate["status"] == "failed":
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=base_pack_id,
                sop_pack_name=base_pack_name,
                reply_messages=[],
                status="failed_order_fetch",
                error=str(order_gate.get("error") or "order_fetch_failed"),
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "order_gate": order_gate.get("summary", {}),
                },
            )
        if event_type in FIRST_ADD_EVENT_TYPES and order_gate["status"] == "paid":
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=base_pack_id,
                sop_pack_name=base_pack_name,
                reply_messages=[],
                status="skipped_deposit_paid",
                error="",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "order_gate": order_gate.get("summary", {}),
                },
            )
        if event_type in FIRST_ADD_EVENT_TYPES:
            return await self._create_first_add_task(
                payload,
                customer,
                index=index,
                identity=identity,
                conversation_fetch=conversation_fetch,
                conversation_messages=conversation_messages,
                conversation_filter=conversation_filter,
                conversation_activity=conversation_activity,
                customer_memory=customer_memory,
                customer_context=customer_context,
                event_policy_evidence=event_policy_evidence,
            )
        if event_type == "sop_platform_task":
            return await self._create_platform_task(
                payload,
                customer,
                index=index,
                identity=identity,
                conversation_fetch=conversation_fetch,
                conversation_messages=conversation_messages,
                conversation_activity=conversation_activity,
                customer_memory=customer_memory,
                customer_context=customer_context,
                event_policy_evidence=event_policy_evidence,
            )
        raise RuntimeError("unreachable_sop_event_type")

    def _load_customer_memory(self, identity: dict[str, str]) -> dict[str, Any]:
        """Load existing customer memory for model context without mutating it."""
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

    def _record_observed_customer_activity(
        self,
        identity: dict[str, str],
        conversation_activity: dict[str, Any],
    ) -> None:
        """Persist an observed customer reply so later limited history cannot erase that fact."""
        latest_customer_message_at = _string(conversation_activity.get("latest_customer_message_at"))
        if not latest_customer_message_at:
            return
        scope = customer_scope_from_identity(identity)
        if not scope.persistence_allowed:
            return
        touch_message_time = getattr(self.repository, "touch_customer_message_time", None)
        if not callable(touch_message_time):
            return
        try:
            touch_message_time(
                scope.sales_contact_key,
                field="last_customer_message_at",
                value=latest_customer_message_at,
            )
        except Exception:
            return

    def _load_sop_order_gate(self, identity: dict[str, str], customer_memory: dict[str, Any]) -> dict[str, Any]:
        """Freshly load the current order and block unsafe SOP delivery states."""
        if not self.customer_context_service:
            return {"status": "failed", "error": "customer_context_service_not_configured", "summary": {}}
        request_context = {
            "customer_id": identity.get("customer_id", ""),
            "external_userid": identity.get("external_userid", ""),
            "corp_id": identity.get("corp_id", ""),
            "user_id": identity.get("user_id", ""),
            "wechat": identity.get("wechat", ""),
        }
        try:
            context = self.customer_context_service.load(
                customer_id=identity.get("customer_id", ""),
                memory=customer_memory,
                request_context=request_context,
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

    def _event_policy_evidence(
        self,
        *,
        payload: dict[str, Any],
        identity: dict[str, str],
        conversation_activity: dict[str, Any],
    ) -> dict[str, Any]:
        event_at = _parse_time(payload.get("created_at") or payload.get("upstream_created_at")) or datetime.now(timezone.utc)
        list_recent = getattr(self.repository, "list_recent_sop_send_tasks_for_customer", None)
        tasks: list[dict[str, Any]] = []
        if callable(list_recent):
            try:
                tasks = list_recent(
                    customer_id=identity.get("customer_id", ""),
                    external_userid=identity.get("external_userid", ""),
                    corp_id=identity.get("corp_id", ""),
                    wechat=identity.get("wechat", ""),
                    before="",
                    limit=100,
                )
            except Exception:
                tasks = []
        touch_frequency, pending_backlog = _event_touch_evidence(
            tasks,
            event_at=event_at,
            latest_customer_message_at=_parse_time(conversation_activity.get("latest_customer_message_at")),
        )
        delivery_history = _event_delivery_history(tasks, event_at=event_at)
        touch_frequency["daily_soft_limit"] = self.daily_touch_soft_limit
        touch_frequency["daily_soft_limit_reached"] = (
            int(touch_frequency.get("today_count") or 0) >= self.daily_touch_soft_limit
        )
        touch_frequency["silent_soft_limit_reached"] = (
            int(touch_frequency.get("consecutive_silent_touch_count") or 0) >= self.daily_touch_soft_limit
        )
        latest_customer_progress_at = _parse_time(touch_frequency.get("latest_customer_message_at"))
        last_touch_at = _parse_time(touch_frequency.get("last_sent_at"))
        touch_frequency["has_new_customer_progress_since_last_touch"] = bool(
            latest_customer_progress_at
            and (last_touch_at is None or latest_customer_progress_at > last_touch_at)
        )
        return {
            "touch_frequency": touch_frequency,
            "delivery_history": delivery_history,
            "pending_backlog": pending_backlog,
            "ai_reply_policy": build_event_ai_reply_policy(
                conversation_activity,
                runtime_handoff_available=False,
            ),
        }

    def _complete_identity(self, identity: dict[str, str]) -> dict[str, str]:
        lookup = getattr(self.repository, "find_sop_event_identity", None)
        merged = dict(identity)
        found: dict[str, Any] = {}
        lookup_error = ""
        # WeChat is the sales-account boundary and must come from the event itself.
        # Looking it up by customer would risk attaching this event to another account.
        missing = [key for key in ("corp_id", "user_id") if not _string(identity.get(key))]
        if missing and callable(lookup):
            try:
                raw_found = lookup(
                    customer_id=identity.get("customer_id", ""),
                    external_userid=identity.get("external_userid", ""),
                    wechat=identity.get("wechat", ""),
                )
                found = raw_found if isinstance(raw_found, dict) else {}
            except Exception as exc:
                lookup_error = f"{type(exc).__name__}: {exc}"
        for key in ("corp_id", "user_id", "external_userid", "customer_id"):
            if not _string(merged.get(key)) and _string(found.get(key)):
                merged[key] = _string(found.get(key))
        if _string(found.get("identity_source")):
            merged["identity_source"] = _string(found.get("identity_source"))
        filled_from_default: list[str] = []
        for key in ("corp_id", "user_id"):
            if not _string(merged.get(key)) and _string(self.default_identity.get(key)):
                merged[key] = _string(self.default_identity.get(key))
                filled_from_default.append(key)
        if filled_from_default and not _string(merged.get("identity_source")):
            merged["identity_source"] = "default_platform_identity"
        if filled_from_default:
            merged["identity_default_fields"] = ",".join(filled_from_default)
        if lookup_error:
            merged["identity_lookup_error"] = lookup_error
        return merged

    async def _create_first_add_task(
        self,
        payload: dict[str, Any],
        customer: dict[str, Any],
        *,
        index: int,
        identity: dict[str, str],
        conversation_fetch: dict[str, Any],
        conversation_messages: list[dict[str, Any]],
        conversation_filter: dict[str, Any],
        conversation_activity: dict[str, Any],
        customer_memory: dict[str, Any],
        customer_context: dict[str, Any],
        event_policy_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        completed_ids = self.repository.list_sent_sop_pack_ids_for_customer(
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            corp_id=identity.get("corp_id", ""),
            wechat=identity.get("wechat", ""),
        )
        completed_categories = _sent_categories(self.repository, identity)
        match_context = _match_context(payload, customer)
        delay_minutes = match_context["delay_minutes"]
        candidates = first_add_candidate_packs(
            self.sop_reply_pack_service.load(),
            completed_sop_pack_ids=completed_ids,
            completed_sop_categories=completed_categories,
            delay_minutes=delay_minutes,
            event_type=_string(payload.get("event_type")),
            match_context=match_context,
            delivery_evidence=event_policy_evidence.get("delivery_history")
            if isinstance(event_policy_evidence.get("delivery_history"), dict)
            else {},
            payment_state=_event_payment_state(customer_memory, customer_context),
        )
        if not candidates:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="first_add_no_candidate",
                sop_pack_name="first_add_no_candidate",
                reply_messages=[],
                status="skipped_no_candidate_sop",
                error="",
                send_payload={
                    "identity": identity,
                    "completed_sop_pack_ids": completed_ids,
                    "completed_sop_categories": completed_categories,
                    "delay_minutes": delay_minutes,
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                },
            )

        decision = await self._event_decision(
            payload=payload,
            customer=customer,
            identity=identity,
            event_type=_string(payload.get("event_type")),
            conversation_messages=conversation_messages,
            conversation_activity=conversation_activity,
            customer_memory=customer_memory,
            customer_context=customer_context,
            candidate_packs=candidates,
            actions_reply_messages=[],
            event_policy_evidence=event_policy_evidence,
        )
        selected_packs = selected_candidate_packs(decision, candidates)
        decision_name = _string(decision.get("decision"))
        if decision_name in {"send_ai_touch", "handoff_or_safety_notice"}:
            touch_messages = decision.get("ai_touch_messages") if isinstance(decision.get("ai_touch_messages"), list) else []
            messages, sanitize_summary = sanitize_sop_reply_messages(
                touch_messages,
                conversation_messages=conversation_messages,
            )
            status = "pending" if messages else "skipped_empty_ai_touch_messages"
            category = "sop_ai_touch" if decision_name == "send_ai_touch" else "sop_safety_notice"
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=decision_name,
                sop_pack_name=decision_name,
                sop_category=category,
                reply_messages=messages,
                status=status,
                error="" if messages else "ai_touch_messages_empty_after_sanitize",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "message_sanitize": sanitize_summary,
                    "event_policy_evidence": event_policy_evidence,
                    "event_decision_input": decision.get("selector_input", {}),
                    "touch_goal": _string(decision.get("touch_goal")),
                    "touch_decision": decision_name,
                },
                send_response={"event_decision": decision},
            )
        if not decision.get("send_sop") or not selected_packs:
            is_model_error = bool(decision.get("error"))
            rejected_status = (
                "deferred_model"
                if decision_name == "defer"
                else "skipped_handoff_to_ai_reply"
                if decision_name == "handoff_to_ai_reply"
                else "failed_model_error"
                if is_model_error
                else "skipped_model_rejected"
            )
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=str(decision.get("sop_pack_id") or "first_add_model_rejected"),
                sop_pack_name=str(decision.get("sop_pack_name") or "first_add_model_rejected"),
                reply_messages=[],
                status=rejected_status,
                error=str(decision.get("error") or "") if is_model_error else "",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "event_policy_evidence": event_policy_evidence,
                    "event_decision_input": decision.get("selector_input", {}),
                },
                send_response={"event_decision": decision},
            )

        selected_pack_ids = [_string(pack.get("id")) for pack in selected_packs]
        selected_categories = [_pack_category(pack) for pack in selected_packs]
        selected_messages = combine_selected_pack_messages(selected_packs)
        selected_id = selected_pack_ids[0] if len(selected_pack_ids) == 1 else "merge:" + "+".join(selected_pack_ids)
        selected_name = (
            _string(selected_packs[0].get("name"))
            if len(selected_packs) == 1
            else " + ".join(_string(pack.get("name")) for pack in selected_packs)
        )
        selected_category = selected_categories[0] if len(selected_categories) == 1 else "merge:" + "+".join(selected_categories)
        removable_payment_orders = _removable_payment_message_orders_for_selected_packs(
            selected_packs,
            customer_memory=customer_memory,
            customer_context=customer_context,
            completed_sop_pack_ids=completed_ids,
            completed_sop_categories=completed_categories,
        )
        adjusted_messages, adjustment_summary = apply_sop_text_adjustments(
            selected_messages,
            decision.get("text_adjustments"),
            decision.get("message_operations"),
            removable_payment_message_orders=removable_payment_orders,
        )
        messages, sanitize_summary = sanitize_sop_reply_messages(
            adjusted_messages,
            conversation_messages=conversation_messages,
        )
        if not messages:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=selected_id,
                sop_pack_name=selected_name,
                sop_category=selected_category,
                reply_messages=[],
                status="skipped_empty_reply_messages",
                error="selected_sop_messages_empty_after_sanitize",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_filter": conversation_filter,
                    "conversation_activity": conversation_activity,
                    "message_sanitize": sanitize_summary,
                    "message_adjustment": adjustment_summary,
                    "event_policy_evidence": event_policy_evidence,
                    "selected_sop_pack_ids": selected_pack_ids,
                    "selected_sop_categories": selected_categories,
                    "event_decision_input": decision.get("selector_input", {}),
                },
                send_response={"event_decision": decision},
            )
        final_payment_gate = _sop_payment_collection_gate(
            messages,
            customer_memory,
            customer_context,
            completed_sop_pack_ids=completed_ids,
            completed_sop_categories=_effective_event_completed_categories(
                completed_categories,
                decision,
            ),
            require_activity_intro="s10_activity_intro" not in selected_pack_ids,
        )
        if final_payment_gate.get("status") not in {"not_required", "supported"}:
            activity_intro_blocked = final_payment_gate.get("status") == "activity_intro_required"
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=selected_id,
                sop_pack_name=selected_name,
                sop_category=selected_category,
                reply_messages=[],
                status="skipped_payment_collection_blocked",
                error=str(final_payment_gate.get("reason") or "payment_collection_not_supported"),
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "payment_collection_gate": final_payment_gate,
                    "event_policy_evidence": event_policy_evidence,
                    "selected_sop_pack_ids": selected_pack_ids,
                    "selected_sop_categories": selected_categories,
                },
                send_response={"event_decision": decision},
            )
        return self._create_task_record(
            payload,
            customer,
            index=index,
            identity=identity,
            sop_pack_id=selected_id,
            sop_pack_name=selected_name,
            sop_category=selected_category,
            reply_messages=messages,
            status="pending",
            error="",
            send_once_key=_send_once_key(
                identity,
                _selected_send_once_identity(selected_packs, selected_id),
            ),
            send_payload={
                "identity": identity,
                "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                "conversation_filter": conversation_filter,
                "conversation_activity": conversation_activity,
                "message_sanitize": sanitize_summary,
                "message_adjustment": adjustment_summary,
                "payment_collection_gate": final_payment_gate,
                "event_policy_evidence": event_policy_evidence,
                "selected_sop_pack_ids": selected_pack_ids,
                "selected_sop_categories": selected_categories,
                "event_decision_input": decision.get("selector_input", {}),
            },
            send_response={"event_decision": decision},
        )

    async def _create_platform_task(
        self,
        payload: dict[str, Any],
        customer: dict[str, Any],
        *,
        index: int,
        identity: dict[str, str],
        conversation_fetch: dict[str, Any],
        conversation_messages: list[dict[str, Any]],
        conversation_activity: dict[str, Any],
        customer_memory: dict[str, Any],
        customer_context: dict[str, Any],
        event_policy_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        customer_relation = (
            conversation_fetch.get("customer_relation")
            if isinstance(conversation_fetch.get("customer_relation"), dict)
            else {}
        )
        raw_messages = _actions_to_reply_messages(_customer_actions(payload, customer))
        messages, sanitize_summary = sanitize_sop_reply_messages(
            raw_messages,
            conversation_messages=conversation_messages,
        )
        routing = classify_platform_task_route(
            payload=payload,
            customer=customer,
            conversation_activity=conversation_activity,
            customer_context=customer_context,
            customer_memory=customer_memory,
        )
        if routing["route"] == "suppress_day1":
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="platform_actions",
                sop_pack_name="platform_actions",
                sop_category="platform_actions",
                reply_messages=[],
                status="skipped_day1_platform_task",
                error="",
                send_payload={
                    "identity": identity,
                    "routing_mode": "suppress_day1_platform_task",
                    "platform_task_routing": routing,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                },
            )
        if routing["route"] == "defer":
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="platform_actions",
                sop_pack_name="platform_actions",
                sop_category="platform_actions",
                reply_messages=[],
                status="failed_order_fetch",
                error=str((routing.get("order_gate") or {}).get("error") or routing["reason"]),
                send_payload={
                    "identity": identity,
                    "routing_mode": "defer_platform_task_order_unknown",
                    "platform_task_routing": routing,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                },
            )
        if routing["route"] == "personalized":
            if self.personalized_outreach_service is None:
                plan_result = {
                    "created": False,
                    "error": "personalized_outreach_service_not_configured",
                }
                status = "failed_personalized_outreach_not_configured"
                error = "personalized_outreach_service_not_configured"
            else:
                try:
                    plan_result = await self.personalized_outreach_service.ensure_platform_task_plan(
                        identity=identity,
                        conversation_messages=conversation_messages,
                        conversation_activity=conversation_activity,
                        customer_context=customer_context,
                        customer_relation=customer_relation,
                        platform_task={
                            "event_id": str(payload.get("event_id") or ""),
                            "created_at": str(payload.get("created_at") or ""),
                            "messages": messages,
                        },
                    )
                    status = "filtered_personalized_outreach"
                    error = ""
                except Exception as exc:
                    plan_result = {
                        "created": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    status = "failed_personalized_outreach_plan"
                    error = str(plan_result["error"])
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="personalized_outreach",
                sop_pack_name="personalized_outreach",
                sop_category="personalized_outreach",
                reply_messages=[],
                status=status,
                error=error,
                send_payload={
                    "identity": identity,
                    "routing_mode": "personalized_outreach_plan",
                    "platform_task_routing": routing,
                    "suppressed_platform_messages": messages,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "outreach_plan": compact(plan_result, max_chars=12000),
                },
                send_response={"outreach_plan": compact(plan_result, max_chars=12000)},
            )
        if not messages:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="platform_actions",
                sop_pack_name="platform_actions",
                sop_category="platform_actions",
                reply_messages=[],
                status="skipped_empty_reply_messages",
                error="empty_platform_actions",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "message_sanitize": sanitize_summary,
                    "platform_task_routing": routing,
                },
            )

        ai_auto_reply = _conversation_ai_auto_reply(customer)
        if not _sop_payment_collection_supported(messages, customer_memory, customer_context):
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="platform_actions",
                sop_pack_name="platform_actions",
                sop_category="platform_actions",
                reply_messages=[],
                status="skipped_unsafe_payment_collection",
                error="platform_payment_collection_blocked_by_paid_state",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "message_sanitize": sanitize_summary,
                    "ai_auto_reply": ai_auto_reply,
                    "routing_mode": "direct_platform_actions",
                    "platform_task_routing": routing,
                },
            )

        decision = await self._event_decision(
            payload=payload,
            customer=customer,
            identity=identity,
            event_type="sop_platform_task",
            conversation_messages=conversation_messages,
            conversation_activity=conversation_activity,
            customer_memory=customer_memory,
            customer_context=customer_context,
            candidate_packs=[],
            actions_reply_messages=messages,
            event_policy_evidence=event_policy_evidence,
        )
        decision_name = _string(decision.get("decision"))
        if not decision_name:
            selector_output = decision.get("selector_output") if isinstance(decision.get("selector_output"), dict) else {}
            decision_name = _string(selector_output.get("decision"))
        if not decision.get("send_sop"):
            is_model_error = bool(decision.get("error"))
            rejected_status = (
                "deferred_model"
                if decision_name == "defer"
                else "skipped_handoff_to_ai_reply"
                if decision_name == "handoff_to_ai_reply"
                else "failed_model_error"
                if is_model_error
                else "skipped_model_rejected"
            )
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="platform_actions",
                sop_pack_name="platform_actions",
                sop_category="platform_actions",
                reply_messages=[],
                status=rejected_status,
                error=str(decision.get("error") or "") if is_model_error else "",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "message_sanitize": sanitize_summary,
                    "ai_auto_reply": ai_auto_reply,
                    "routing_mode": "event_guarded_platform_actions",
                    "platform_task_routing": routing,
                    "event_policy_evidence": event_policy_evidence,
                    "event_decision_input": decision.get("selector_input", {}),
                },
                send_response={"event_decision": decision},
            )

        selector_output = decision.get("selector_output") if isinstance(decision.get("selector_output"), dict) else {}
        if decision_name in {"send_ai_touch", "handoff_or_safety_notice"}:
            touch_messages = selector_output.get("ai_touch_messages") if isinstance(selector_output.get("ai_touch_messages"), list) else []
            final_messages, final_sanitize_summary = sanitize_sop_reply_messages(
                touch_messages,
                conversation_messages=conversation_messages,
            )
            adjustment_summary: dict[str, Any] = {"mode": "event_ai_touch"}
        else:
            adjusted_messages, adjustment_summary = apply_sop_text_adjustments(
                messages,
                decision.get("text_adjustments"),
                decision.get("message_operations"),
            )
            final_messages, final_sanitize_summary = sanitize_sop_reply_messages(
                adjusted_messages,
                conversation_messages=conversation_messages,
            )
        if not final_messages:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="platform_actions",
                sop_pack_name="platform_actions",
                sop_category="platform_actions",
                reply_messages=[],
                status="skipped_empty_reply_messages",
                error="platform_messages_empty_after_event_decision",
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "conversation_activity": conversation_activity,
                    "message_sanitize": final_sanitize_summary,
                    "message_adjustment": adjustment_summary,
                    "ai_auto_reply": ai_auto_reply,
                    "routing_mode": "event_guarded_platform_actions",
                    "platform_task_routing": routing,
                    "event_policy_evidence": event_policy_evidence,
                    "event_decision_input": decision.get("selector_input", {}),
                },
                send_response={"event_decision": decision},
            )
        return self._create_task_record(
            payload,
            customer,
            index=index,
            identity=identity,
            sop_pack_id="platform_actions",
            sop_pack_name="platform_actions",
            sop_category="platform_actions",
            reply_messages=final_messages,
            status="pending",
            error="",
            send_payload={
                "identity": identity,
                "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                "conversation_activity": conversation_activity,
                "message_sanitize": final_sanitize_summary,
                "message_adjustment": adjustment_summary,
                "ai_auto_reply": ai_auto_reply,
                "routing_mode": "event_guarded_platform_actions",
                "platform_task_routing": routing,
                "event_policy_evidence": event_policy_evidence,
                "event_decision_input": decision.get("selector_input", {}),
            },
            send_response={"event_decision": decision},
        )

    async def _event_decision(
        self,
        *,
        payload: dict[str, Any],
        customer: dict[str, Any],
        identity: dict[str, str],
        event_type: str,
        conversation_messages: list[dict[str, Any]],
        conversation_activity: dict[str, Any],
        customer_memory: dict[str, Any],
        customer_context: dict[str, Any],
        candidate_packs: list[dict[str, Any]],
        actions_reply_messages: list[dict[str, Any]],
        event_policy_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.sop_execution_service:
            if event_type == "sop_platform_task":
                return {"send_sop": True, "reason": "sop_execution_service_not_configured_allow_platform_actions"}
            first = candidate_packs[0] if candidate_packs else {}
            return {
                "send_sop": bool(first),
                "sop_pack_id": str(first.get("id") or ""),
                "sop_pack_name": str(first.get("name") or ""),
                "reason": "sop_execution_service_not_configured_use_first_candidate",
            }
        return await self.sop_execution_service.evaluate_event_suggestion(
            payload=payload,
            customer=customer,
            identity=identity,
            event_type=event_type,
            conversation_messages=conversation_messages,
            conversation_activity=conversation_activity,
            customer_memory=customer_memory,
            customer_context=customer_context,
            candidate_packs=candidate_packs,
            actions_reply_messages=actions_reply_messages,
            event_policy_evidence=event_policy_evidence,
        )

    def _create_task_record(
        self,
        payload: dict[str, Any],
        customer: dict[str, Any],
        *,
        index: int,
        identity: dict[str, str],
        sop_pack_id: str,
        sop_pack_name: str,
        reply_messages: list[dict[str, Any]],
        status: str,
        error: str,
        sop_category: str = "",
        send_once_key: str = "",
        send_payload: dict[str, Any] | None = None,
        send_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.repository.create_sop_send_task(
            event_id=str(payload.get("event_id") or "").strip(),
            idempotency_key=_idempotency_key(payload, customer, sop_pack_id=sop_pack_id, index=index),
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
            corp_id=identity["corp_id"],
            user_id=identity["user_id"],
            wechat=identity["wechat"],
            sop_pack_id=sop_pack_id,
            sop_pack_name=sop_pack_name,
            sop_category=sop_category,
            trigger_source="sop_event",
            reply_messages=reply_messages,
            status=status,
            error=error,
            send_once_key=send_once_key,
        )
        if task.get("id") and task.get("created") and (send_payload or send_response):
            created = bool(task.get("created"))
            task_status = _string(task.get("status")) or status
            task_error = _string(task.get("error")) if task_status != status else error
            task = self.repository.update_sop_send_task(
                str(task["id"]),
                status=task_status,
                send_payload=send_payload or {},
                send_response=send_response or {},
                error=task_error,
            )
            task["created"] = created
        elif (
            task.get("id")
            and not task.get("created")
            and _string(task.get("status"))
            in {
                "failed_model_error",
                "failed_conversation_fetch",
                "failed_order_fetch",
                "failed_personalized_outreach_not_configured",
                "failed_personalized_outreach_plan",
                "model_retry_resolved",
            }
            and status
            in {
                "failed_model_error",
                "failed_conversation_fetch",
                "failed_order_fetch",
                "failed_personalized_outreach_not_configured",
                "failed_personalized_outreach_plan",
                "filtered_personalized_outreach",
                "pending",
                "skipped_model_rejected",
            }
        ):
            task = self.repository.update_sop_send_task(
                str(task["id"]),
                status=status,
                send_payload=send_payload or task.get("send_payload") or {},
                send_response=send_response or task.get("send_response") or {},
                error=error,
            )
            task["created"] = status == "pending"
        return task

    async def _send_task(self, task: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self.outreach_send_client.send_reply_messages(
                request_id=str(task.get("id") or task.get("event_id") or ""),
                request_context={
                    "corp_id": task.get("corp_id") or "",
                    "customer_id": task.get("customer_id") or "",
                    "external_userid": task.get("external_userid") or "",
                    "user_id": task.get("user_id") or "",
                    "wechat": task.get("wechat") or "",
                },
                fallback_customer_id=str(task.get("customer_id") or ""),
                fallback_corp_id=str(task.get("corp_id") or ""),
                fallback_user_id=str(task.get("user_id") or ""),
                fallback_wechat=str(task.get("wechat") or ""),
                fallback_external_userid=str(task.get("external_userid") or ""),
                reply_messages=task.get("reply_messages") if isinstance(task.get("reply_messages"), list) else [],
            )
        except Exception as exc:
            return self.repository.update_sop_send_task(
                str(task.get("id") or ""),
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

        if result.get("status") == "sent":
            sent_at = utc_now_iso()
            sent_task = self.repository.update_sop_send_task(
                str(task.get("id") or ""),
                status="sent",
                send_payload=_merge_send_payload(task, result.get("send_payload") if isinstance(result.get("send_payload"), dict) else {}),
                send_response=_merge_send_response(
                    task,
                    result.get("response") if isinstance(result.get("response"), dict) else result,
                ),
                sent_at=sent_at,
            )
            self._record_successful_send(sent_task, sent_at=sent_at)
            return sent_task
        status = str(result.get("status") or "failed")
        reason = str(result.get("reason") or result.get("error") or "")
        final_status = status if status.startswith("skipped") else "failed"
        return self.repository.update_sop_send_task(
            str(task.get("id") or ""),
            status=final_status,
            send_payload=_merge_send_payload(task, result.get("send_payload") if isinstance(result.get("send_payload"), dict) else {}),
            send_response=_merge_send_response(task, result),
            error=reason,
        )

    def _record_successful_send(self, task: dict[str, Any], *, sent_at: str) -> None:
        """Persist activity timestamps and minimal SOP history after a confirmed send."""
        customer_id = _string(task.get("customer_id"))
        scope = customer_scope_from_identity(task)
        touch_message_time = getattr(self.repository, "touch_customer_message_time", None)
        if scope.persistence_allowed and callable(touch_message_time):
            try:
                touch_message_time(scope.sales_contact_key, field="last_outreach_at", value=sent_at)
            except Exception:
                pass
        if not customer_id or not self.memory_store:
            return
        if not scope.persistence_allowed:
            return
        messages = task.get("reply_messages") if isinstance(task.get("reply_messages"), list) else []
        message_types = [_string(item.get("type")) for item in messages if isinstance(item, dict) and _string(item.get("type"))]
        send_payload = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
        selected_ids = [
            _string(item)
            for item in send_payload.get("selected_sop_pack_ids") or []
            if _string(item)
        ]
        selected_categories = [
            _string(item)
            for item in send_payload.get("selected_sop_categories") or []
            if _string(item)
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


def _customer_identity(payload: dict[str, Any], customer: dict[str, Any]) -> dict[str, str]:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    customer_info = customer.get("customer") if isinstance(customer.get("customer"), dict) else {}
    conversation = customer.get("conversation") if isinstance(customer.get("conversation"), dict) else {}
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}

    external_userid = _first_string(
        customer.get("external_userid"),
        customer_info.get("external_userid"),
        conversation.get("external_userid"),
        customer_sop.get("external_userid"),
        root_sop.get("external_userid"),
        payload.get("external_userid"),
        account.get("external_userid"),
    )
    customer_id = _first_string(
        external_userid,
        customer.get("customer_id"),
        customer_info.get("customer_id"),
        customer_info.get("id"),
        conversation.get("customer_id"),
        customer_sop.get("customer_id"),
        root_sop.get("customer_id"),
        payload.get("customer_id"),
        account.get("customer_id"),
    )
    return {
        "corp_id": _first_string(
            customer.get("corp_id"),
            conversation.get("corp_id"),
            customer_sop.get("corp_id"),
            root_sop.get("corp_id"),
            payload.get("corp_id"),
            account.get("corp_id"),
        ),
        "user_id": _first_string(
            customer.get("user_id"),
            conversation.get("user_id"),
            customer_sop.get("user_id"),
            root_sop.get("user_id"),
            payload.get("user_id"),
            account.get("user_id"),
            account.get("assignee_id"),
        ),
        "wechat": _first_string(
            customer.get("wechat"),
            conversation.get("wechat"),
            conversation.get("wework_user_id"),
            customer_sop.get("wechat"),
            root_sop.get("wechat"),
            payload.get("wechat"),
            account.get("wechat"),
            account.get("wework_user_id"),
        ),
        "external_userid": external_userid,
        "customer_id": customer_id,
    }


def _match_context(payload: dict[str, Any], customer: dict[str, Any]) -> dict[str, Any]:
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    return {
        "event_type": _string(payload.get("event_type")),
        "event_id": _string(payload.get("event_id")),
        "delay_minutes": _int(customer_sop.get("delay_minutes"), _int(root_sop.get("delay_minutes"), 0)),
        "day_stage": _string(customer_sop.get("day_stage")) or _string(root_sop.get("day_stage")),
        "customer_state": _string(customer_sop.get("customer_state")) or _string(root_sop.get("customer_state")),
        "stage_tag": _string(customer_sop.get("stage_tag")) or _string(root_sop.get("stage_tag")),
    }


def _pack_by_id(packs: list[dict[str, Any]], pack_id: str) -> dict[str, Any]:
    for pack in packs:
        if _string(pack.get("id")) == pack_id:
            return pack
    return {}


def _pack_messages(pack: dict[str, Any]) -> list[dict[str, Any]]:
    messages = pack.get("reply_messages") if isinstance(pack.get("reply_messages"), list) else []
    return [message for message in messages if isinstance(message, dict)]


def _pack_category(pack: dict[str, Any]) -> str:
    return _string(pack.get("sop_category")) or _string(pack.get("id"))


def _selected_send_once_identity(packs: list[dict[str, Any]], fallback_id: str) -> str:
    groups = {
        _string(pack.get("send_once_group")).lower()
        for pack in packs
        if _string(pack.get("send_once_group"))
    }
    if len(groups) == 1:
        return next(iter(groups))
    return _string(fallback_id)


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


def _sent_categories(repository: Any, identity: dict[str, str], *, sent_before: str = "") -> list[str]:
    func = getattr(repository, "list_sent_sop_categories_for_customer", None)
    if not callable(func):
        return []
    return list(
        func(
            customer_id=identity.get("customer_id", ""),
            external_userid=identity.get("external_userid", ""),
            corp_id=identity.get("corp_id", ""),
            wechat=identity.get("wechat", ""),
            sent_before=sent_before,
        )
        or []
    )


def _customer_actions(payload: dict[str, Any], customer: dict[str, Any]) -> list[Any]:
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    customer_actions = customer_sop.get("actions")
    if isinstance(customer_actions, list) and customer_actions:
        return customer_actions
    customer_task = customer_sop.get("platform_task") if isinstance(customer_sop.get("platform_task"), dict) else {}
    customer_message_content = customer_task.get("message_content")
    if isinstance(customer_message_content, list) and customer_message_content:
        return customer_message_content
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    root_actions = root_sop.get("actions")
    if isinstance(root_actions, list) and root_actions:
        return root_actions
    root_task = root_sop.get("platform_task") if isinstance(root_sop.get("platform_task"), dict) else {}
    root_message_content = root_task.get("message_content")
    return root_message_content if isinstance(root_message_content, list) else []


def _actions_to_reply_messages(actions: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for index, action_value in enumerate(actions, start=1):
        action = action_value if isinstance(action_value, dict) else {"type": "text", "content": action_value}
        message_type = _string(action.get("type")) or "text"
        if message_type not in ALLOWED_MESSAGE_TYPES:
            message_type = "text"
        if message_type == "human_handoff":
            message_type = "human_handoff_notice"
        raw_content = action.get("content")
        if raw_content in (None, ""):
            raw_content = action.get("message_content")
        content = _message_content(message_type, raw_content)
        if not _content_has_value(content):
            continue
        messages.append({"type": message_type, "order": index, "content": content})
    return messages


def _message_content(message_type: str, value: Any) -> dict[str, Any]:
    value = _decode_embedded_json_string(value)
    content = value if isinstance(value, dict) else {}
    if message_type == "text":
        return {"text": _string(content.get("text")) or _string(value)}
    if message_type in {"image", "video"}:
        return {"url": _string(content.get("url")) or _string(value)}
    if message_type == "payment_collection":
        return {"amount": _int(content.get("amount"), 10), "remark": _string(content.get("remark"))}
    if message_type == "store_address":
        return {"store_id": _string(content.get("store_id")) or _string(content.get("id")) or _string(value)}
    if message_type == "human_handoff_notice":
        return {"handoff_reason": _string(content.get("handoff_reason")) or _string(content.get("text")) or _string(value)}
    return {}


def _decode_embedded_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        return value
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value
    return decoded if isinstance(decoded, str) else value


def _conversation_ai_auto_reply(customer: dict[str, Any]) -> bool | None:
    conversation = customer.get("conversation") if isinstance(customer.get("conversation"), dict) else {}
    value = conversation.get("ai_auto_reply")
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = _string(value).lower()
    if normalized in {"false", "0", "off", "disabled"}:
        return False
    if normalized in {"true", "1", "on", "enabled"}:
        return True
    return None


def _task_has_processing_error(task: dict[str, Any]) -> bool:
    status = _string(task.get("status"))
    if status.startswith("failed"):
        return True
    return status in {
        "skipped_missing_identity",
        "skipped_unsupported_event_type",
    }


def _sop_payment_collection_supported(
    messages: list[dict[str, Any]],
    customer_memory: dict[str, Any],
    customer_context: dict[str, Any],
) -> bool:
    """Allow SOP payment cards after activity intro unless paid/risk gates block them."""
    return _sop_payment_collection_gate(messages, customer_memory, customer_context).get("status") in {
        "not_required",
        "supported",
    }


def _sop_payment_collection_gate(
    messages: list[dict[str, Any]],
    customer_memory: dict[str, Any],
    customer_context: dict[str, Any],
    *,
    completed_sop_pack_ids: list[str] | None = None,
    completed_sop_categories: list[str] | None = None,
    require_activity_intro: bool = False,
) -> dict[str, Any]:
    """Summarize deterministic SOP card eligibility without deciding sales intent."""
    cards = [item for item in messages if isinstance(item, dict) and item.get("type") == "payment_collection"]
    if not cards:
        return {"has_payment_collection": False, "status": "not_required"}
    basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
    payment_fact = resolved_payment_fact(
        orders=customer_context.get("orders") if isinstance(customer_context, dict) else [],
        existing_state=_string(basic.get("deposit_state")),
        existing_source=_string(basic.get("deposit_source")),
        existing_fact=basic.get("deposit_fact"),
    )
    amounts = [_payment_card_amount(card) for card in cards]
    if is_paid_deposit_state(payment_fact.get("deposit_state")) or is_paid_deposit_state(basic.get("deposit_state")):
        return {
            "has_payment_collection": True,
            "status": "paid_skip_card",
            "amounts": amounts,
            "reason": "payment_collection_blocked_by_paid_state",
            "source": payment_fact.get("source") or "customer_memory",
        }
    if require_activity_intro and not _activity_intro_completed(
        completed_sop_pack_ids or [], completed_sop_categories or []
    ):
        return {
            "has_payment_collection": True,
            "status": "activity_intro_required",
            "amounts": amounts,
            "reason": "payment_collection_requires_completed_activity_intro",
            "required_completed_pack_id": "s10_activity_intro",
        }
    return {
        "has_payment_collection": True,
        "status": "supported",
        "amounts": amounts,
        "reason": "activity_intro_completed_or_not_required",
    }


def _removable_payment_message_orders_for_selected_packs(
    packs: list[dict[str, Any]],
    *,
    customer_memory: dict[str, Any],
    customer_context: dict[str, Any],
    completed_sop_pack_ids: list[str],
    completed_sop_categories: list[str],
) -> set[int]:
    output: set[int] = set()
    combined_order = 0
    for pack in sorted(
        (item for item in packs if isinstance(item, dict)),
        key=lambda item: (int(item.get("order") or 0), _string(item.get("id"))),
    ):
        pack_messages = _pack_messages(pack)
        gate = _sop_payment_collection_gate(
            pack_messages,
            customer_memory,
            customer_context,
            completed_sop_pack_ids=completed_sop_pack_ids,
            completed_sop_categories=completed_sop_categories,
            require_activity_intro=_string(pack.get("id")) != "s10_activity_intro",
        )
        gate_status = _string(gate.get("status"))
        for message in sorted(pack_messages, key=lambda item: int(item.get("order") or 0)):
            combined_order += 1
            if gate_status == "paid_skip_card" and _string(message.get("type")) == "payment_collection":
                output.add(combined_order)
    return output


def _payment_card_amount(message: dict[str, Any]) -> int:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    return _int(content.get("amount"), 10)


def _activity_intro_completed(completed_sop_pack_ids: list[str], completed_sop_categories: list[str]) -> bool:
    completed_ids = {_string(item).lower() for item in completed_sop_pack_ids}
    completed_categories = {_string(item).lower() for item in completed_sop_categories}
    return bool(
        completed_ids.intersection({"s10_activity_intro", "event_s10_price_quote_60min"})
        or completed_categories.intersection(
            {"activity_intro", "s10_activity_intro", "price_quote"}
        )
    )


def _effective_event_completed_categories(
    completed_sop_categories: list[str],
    decision: dict[str, Any],
) -> list[str]:
    output = {_string(item) for item in completed_sop_categories if _string(item)}
    raw_evidence = decision.get("stage_skip_evidence")
    if not isinstance(raw_evidence, list):
        return sorted(output)
    for item in raw_evidence:
        if not isinstance(item, dict) or not _string(item.get("evidence")):
            continue
        if _string(item.get("stage_id")) == "activity_and_price" or _string(item.get("pack_id")) in {
            "s10_activity_intro",
            "event_s10_price_quote_60min",
        }:
            output.add("price_quote")
            break
    return sorted(output)


def _content_has_value(content: dict[str, Any]) -> bool:
    return any(str(value or "").strip() for value in content.values())


def _merge_send_payload(task: dict[str, Any], send_payload: dict[str, Any]) -> dict[str, Any]:
    existing = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
    merged = dict(existing)
    if send_payload:
        merged["send_payload"] = send_payload
    return merged


def _merge_send_response(task: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    existing = task.get("send_response") if isinstance(task.get("send_response"), dict) else {}
    if not existing:
        return response
    merged = dict(existing)
    merged["send_response"] = response
    return merged


def _conversation_fetch_summary(conversation_fetch: dict[str, Any]) -> dict[str, Any]:
    response = conversation_fetch.get("response")
    return {
        "status": conversation_fetch.get("status"),
        "error": conversation_fetch.get("error", ""),
        "reason": conversation_fetch.get("reason", ""),
        "request": conversation_fetch.get("request", {}),
        "message_count": conversation_fetch.get("message_count", 0),
        "customer_relation": conversation_fetch.get("customer_relation", {}),
        "response": compact(response, max_chars=2000) if response else {},
    }


def _event_created_at(payload: dict[str, Any]) -> str:
    parsed = _parse_time(payload.get("created_at") or payload.get("upstream_created_at"))
    return parsed.isoformat() if parsed else ""


def _quiet_hours_summary(payload: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    event_at = _parse_time(payload.get("created_at") or payload.get("upstream_created_at")) or datetime.now(timezone.utc)
    local_event_at = event_at.astimezone(SOP_QUIET_TIMEZONE)
    latest_customer_at = _latest_customer_message_at(messages, before=event_at)
    latest_auto_opening_at = _latest_auto_opening_message_at(messages, before=event_at)
    activity_at = latest_customer_at or latest_auto_opening_at
    activity_source = (
        "customer_message"
        if latest_customer_at
        else "platform_auto_opening"
        if latest_auto_opening_at
        else ""
    )
    inactivity_minutes: int | None = None
    if activity_at:
        inactivity_minutes = max(0, int((event_at - activity_at).total_seconds() // 60))
    in_quiet_window = SOP_QUIET_START_HOUR <= local_event_at.hour < SOP_QUIET_END_HOUR
    inactive = activity_at is None or (inactivity_minutes is not None and inactivity_minutes >= SOP_QUIET_INACTIVITY_MINUTES)
    return {
        "timezone": "Asia/Shanghai",
        "event_at": event_at.isoformat(),
        "local_event_at": local_event_at.isoformat(),
        "window": f"{SOP_QUIET_START_HOUR:02d}:00-{SOP_QUIET_END_HOUR:02d}:00",
        "latest_customer_message_at": latest_customer_at.isoformat() if latest_customer_at else "",
        "latest_platform_auto_opening_at": latest_auto_opening_at.isoformat() if latest_auto_opening_at else "",
        "quiet_activity_source": activity_source,
        "inactivity_minutes": inactivity_minutes,
        "skip": bool(in_quiet_window and inactive),
        "reason": "quiet_hours_customer_inactive" if in_quiet_window and inactive else "",
    }


def _event_touch_evidence(
    tasks: list[dict[str, Any]],
    *,
    event_at: datetime,
    latest_customer_message_at: datetime | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sent_tasks = [task for task in tasks if _string(task.get("status")) == "sent"]
    sent_times = [time for time in (_task_time(task) for task in sent_tasks) if time]
    local_event_date = event_at.astimezone(SOP_QUIET_TIMEZONE).date()
    today_count = sum(1 for sent_at in sent_times if sent_at.astimezone(SOP_QUIET_TIMEZONE).date() == local_event_date)
    last_sent_at = max(sent_times) if sent_times else None
    consecutive_silent_touch_count = sum(
        1
        for sent_at in sent_times
        if latest_customer_message_at is None or sent_at > latest_customer_message_at
    )

    quiet_tasks: list[tuple[datetime, dict[str, Any]]] = []
    for task in tasks:
        if _string(task.get("status")) != "skipped_quiet_hours_inactive":
            continue
        task_at = _task_time(task)
        if not task_at:
            continue
        if last_sent_at and task_at <= last_sent_at:
            continue
        quiet_tasks.append((task_at, task))
    quiet_tasks.sort(key=lambda item: item[0])
    backlog_items: list[dict[str, Any]] = []
    for task_at, task in quiet_tasks[-8:]:
        send_payload = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
        marker = send_payload.get("backlog_marker") if isinstance(send_payload.get("backlog_marker"), dict) else {}
        backlog_items.append(
            {
                "event_id": _string(task.get("event_id")),
                "suppressed_at": task_at.isoformat(),
                "reason": _string(marker.get("reason")) or "suppressed_due_to_quiet_hours",
                "event": marker.get("event") if isinstance(marker.get("event"), dict) else {},
            }
        )

    return (
        {
            "timezone": "Asia/Shanghai",
            "today_count": today_count,
            "prior_count": max(0, len(sent_tasks) - today_count),
            "total_count": len(sent_tasks),
            "last_sent_at": last_sent_at.isoformat() if last_sent_at else "",
            "latest_customer_message_at": latest_customer_message_at.isoformat() if latest_customer_message_at else "",
            "consecutive_silent_touch_count": consecutive_silent_touch_count,
            "count_confidence": "authoritative_tasks" if tasks else "no_task_history",
        },
        {
            "count": len(backlog_items),
            "has_pending": bool(backlog_items),
            "items": backlog_items,
            "max_merge_pack_count": 2,
        },
    )


def _event_delivery_history(tasks: list[dict[str, Any]], *, event_at: datetime) -> dict[str, Any]:
    sent_tasks = [task for task in tasks if _string(task.get("status")) == "sent"]
    category_last_sent_at: dict[str, str] = {}
    pack_last_sent_at: dict[str, str] = {}
    payment_card_last_sent_at: datetime | None = None
    for task in sent_tasks:
        sent_at = _task_time(task)
        if not sent_at:
            continue
        send_payload = task.get("send_payload") if isinstance(task.get("send_payload"), dict) else {}
        pack_ids = [
            _string(item)
            for item in send_payload.get("selected_sop_pack_ids") or []
            if _string(item)
        ] or [_string(task.get("sop_pack_id"))]
        categories = [
            _string(item)
            for item in send_payload.get("selected_sop_categories") or []
            if _string(item)
        ] or [_string(task.get("sop_category"))]
        for pack_id in pack_ids:
            if pack_id and (
                not pack_last_sent_at.get(pack_id)
                or sent_at > (_parse_time(pack_last_sent_at[pack_id]) or datetime.min.replace(tzinfo=timezone.utc))
            ):
                pack_last_sent_at[pack_id] = sent_at.isoformat()
        for category in categories:
            if category and (
                not category_last_sent_at.get(category)
                or sent_at > (_parse_time(category_last_sent_at[category]) or datetime.min.replace(tzinfo=timezone.utc))
            ):
                category_last_sent_at[category] = sent_at.isoformat()
        messages = task.get("reply_messages") if isinstance(task.get("reply_messages"), list) else []
        if any(_string(message.get("type")) == "payment_collection" for message in messages if isinstance(message, dict)):
            if payment_card_last_sent_at is None or sent_at > payment_card_last_sent_at:
                payment_card_last_sent_at = sent_at
    local_event_date = event_at.astimezone(SOP_QUIET_TIMEZONE).date()
    today_count = sum(
        1
        for task in sent_tasks
        if (task_at := _task_time(task))
        and task_at.astimezone(SOP_QUIET_TIMEZONE).date() == local_event_date
    )
    return {
        "event_at": event_at.isoformat(),
        "today_count": today_count,
        "category_last_sent_at": category_last_sent_at,
        "pack_last_sent_at": pack_last_sent_at,
        "payment_card_last_sent_at": payment_card_last_sent_at.isoformat()
        if payment_card_last_sent_at
        else "",
    }


def _event_payment_state(customer_memory: dict[str, Any], customer_context: dict[str, Any]) -> str:
    basic = customer_memory.get("basic_info") if isinstance(customer_memory.get("basic_info"), dict) else {}
    payment_fact = resolved_payment_fact(
        orders=customer_context.get("orders") if isinstance(customer_context, dict) else [],
        existing_state=_string(basic.get("deposit_state")),
        existing_source=_string(basic.get("deposit_source")),
        existing_fact=basic.get("deposit_fact"),
    )
    return "paid" if is_paid_deposit_state(payment_fact.get("deposit_state")) else "unpaid"


def _task_time(task: dict[str, Any]) -> datetime | None:
    for key in ("sent_at", "updated_at", "created_at"):
        parsed = _parse_time(task.get(key))
        if parsed:
            return parsed
    return None


def _event_backlog_summary(payload: dict[str, Any], customer: dict[str, Any]) -> dict[str, Any]:
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    return {
        "event_type": _string(payload.get("event_type")),
        "delay_minutes": _int(customer_sop.get("delay_minutes") or root_sop.get("delay_minutes"), 0),
        "day_stage": _string(customer_sop.get("day_stage") or root_sop.get("day_stage")),
        "customer_state": _string(customer_sop.get("customer_state") or root_sop.get("customer_state")),
        "stage_tag": _string(customer_sop.get("stage_tag") or root_sop.get("stage_tag")),
    }


def _recent_assistant_activity_summary(conversation_activity: dict[str, Any]) -> dict[str, Any]:
    """Avoid SOP pushes immediately after an assistant/staff reply in an active chat."""
    silence = conversation_activity.get("silence_after_assistant_minutes")
    assistant_waiting = bool(conversation_activity.get("assistant_waiting_customer"))
    customer_pending = bool(conversation_activity.get("latest_customer_pending_ai_reply"))
    event_at = _parse_time(conversation_activity.get("event_at"))
    latest_assistant_at = _parse_time(conversation_activity.get("latest_assistant_message_at"))
    try:
        silence_minutes = int(silence) if silence is not None else None
    except (TypeError, ValueError):
        silence_minutes = None
    assistant_reply_before_event = bool(
        latest_assistant_at is not None
        and (event_at is None or latest_assistant_at <= event_at)
    )
    within_guard = bool(
        assistant_waiting
        and not customer_pending
        and assistant_reply_before_event
        and silence_minutes is not None
        and silence_minutes < SOP_RECENT_ASSISTANT_ACTIVITY_MINUTES
    )
    return {
        "skip": within_guard,
        "reason": "recent_assistant_activity" if within_guard else "",
        "threshold_minutes": SOP_RECENT_ASSISTANT_ACTIVITY_MINUTES,
        "silence_after_assistant_minutes": silence_minutes,
    }


def _latest_customer_message_at(messages: list[dict[str, Any]], *, before: datetime) -> datetime | None:
    candidates: list[datetime] = []
    for message in messages:
        if not _is_customer_message(message):
            continue
        if is_platform_auto_opening_message(_conversation_message_content(message.get("content"))):
            continue
        message_at = _message_time(message)
        if message_at and message_at <= before:
            candidates.append(message_at)
    return max(candidates) if candidates else None


def _latest_auto_opening_message_at(messages: list[dict[str, Any]], *, before: datetime) -> datetime | None:
    candidates: list[datetime] = []
    for message in messages:
        if not _is_customer_message(message):
            continue
        if not is_platform_auto_opening_message(_conversation_message_content(message.get("content"))):
            continue
        message_at = _message_time(message)
        if message_at and message_at <= before:
            candidates.append(message_at)
    return max(candidates) if candidates else None


def _is_customer_message(message: dict[str, Any]) -> bool:
    direction = _string(message.get("direction") or message.get("from") or message.get("sender_type") or message.get("role")).lower()
    return direction in {"customer", "user", "external"}


def _first_added_at(customer: dict[str, Any]) -> datetime | None:
    """Return the normalized first-added timestamp from the event customer payload."""
    first_added = customer.get("first_added_event") if isinstance(customer.get("first_added_event"), dict) else {}
    return _parse_time(first_added.get("timestamp") or first_added.get("created_at") or first_added.get("time"))


def _conversation_activity(
    messages: list[dict[str, Any]],
    *,
    first_added_at: datetime | None = None,
    event_at: datetime | None = None,
) -> dict[str, Any]:
    """Summarize fresh conversation activity for deterministic first-add safety checks."""
    real_customer_times: list[datetime] = []
    real_customer_count = 0
    unknown_time_customer_count = 0
    ignored_auto_opening_count = 0
    latest_message: tuple[datetime, str, bool] | None = None
    latest_customer_at: datetime | None = None
    latest_assistant_at: datetime | None = None
    fallback_last_direction = ""
    fallback_last_is_real_customer = False

    for message in messages:
        if not isinstance(message, dict):
            continue
        direction = _string(message.get("direction") or message.get("from") or message.get("sender_type") or message.get("role")).lower()
        is_customer = _is_customer_message(message)
        is_auto_opening = is_customer and is_platform_auto_opening_message(_conversation_message_content(message.get("content")))
        is_real_customer = is_customer and not is_auto_opening
        if direction:
            fallback_last_direction = direction
            fallback_last_is_real_customer = is_real_customer
        message_at = _message_time(message)
        if message_at and (latest_message is None or message_at >= latest_message[0]):
            latest_message = (message_at, direction, is_real_customer)
        if message_at and is_real_customer and (latest_customer_at is None or message_at >= latest_customer_at):
            latest_customer_at = message_at
        if message_at and not is_customer and (latest_assistant_at is None or message_at >= latest_assistant_at):
            latest_assistant_at = message_at
        if not is_customer:
            continue
        if is_auto_opening:
            ignored_auto_opening_count += 1
            continue
        real_customer_count += 1
        if message_at:
            real_customer_times.append(message_at)
        else:
            unknown_time_customer_count += 1

    missing_first_added_time = bool(real_customer_count and first_added_at is None)
    uncertain_customer_timing = bool(unknown_time_customer_count or missing_first_added_time)
    customer_replied = real_customer_count > 0
    last_direction = latest_message[1] if latest_message else fallback_last_direction
    last_message_is_customer = latest_message[2] if latest_message else fallback_last_is_real_customer
    latest_customer_pending_ai_reply = bool(
        last_message_is_customer
        or (latest_customer_at and (latest_assistant_at is None or latest_customer_at > latest_assistant_at))
    )
    assistant_waiting_customer = bool(latest_assistant_at and (latest_customer_at is None or latest_assistant_at >= latest_customer_at))
    silence_after_assistant_minutes: int | None = None
    if assistant_waiting_customer and latest_assistant_at:
        reference_time = event_at or datetime.now(timezone.utc)
        silence_after_assistant_minutes = max(0, int((reference_time - latest_assistant_at).total_seconds() // 60))
    if unknown_time_customer_count:
        reason = "customer_message_time_unknown"
    elif missing_first_added_time:
        reason = "first_added_time_unknown"
    elif latest_customer_pending_ai_reply:
        reason = "customer_pending_ai_reply"
    elif assistant_waiting_customer:
        reason = "assistant_waiting_customer"
    elif customer_replied:
        reason = "customer_replied_but_not_pending"
    else:
        reason = ""
    return {
        "message_count": len(messages),
        "customer_replied": customer_replied,
        "real_customer_message_count": real_customer_count,
        "ignored_auto_opening_count": ignored_auto_opening_count,
        "unknown_time_customer_message_count": unknown_time_customer_count,
        "uncertain_customer_timing": uncertain_customer_timing,
        "first_added_at": first_added_at.isoformat() if first_added_at else "",
        "event_at": event_at.isoformat() if event_at else "",
        "latest_customer_message_at": latest_customer_at.isoformat() if latest_customer_at else "",
        "latest_assistant_message_at": latest_assistant_at.isoformat() if latest_assistant_at else "",
        "last_message_direction": last_direction,
        "last_message_is_customer": last_message_is_customer,
        "latest_customer_pending_ai_reply": latest_customer_pending_ai_reply,
        "assistant_waiting_customer": assistant_waiting_customer,
        "silence_after_assistant_minutes": silence_after_assistant_minutes,
        "reason": reason,
    }


def _conversation_message_content(value: Any) -> str:
    """Normalize conversation message content for the platform auto-opening matcher."""
    if isinstance(value, dict):
        for key in ("text", "content", "title", "url"):
            text = _string(value.get(key))
            if text:
                return text
        return ""
    return _string(value)


def _first_add_conversation_messages(
    payload: dict[str, Any],
    customer: dict[str, Any],
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first_added_at = _first_added_at(customer)
    event_created_at = _parse_time(payload.get("created_at") or payload.get("upstream_created_at"))
    summary: dict[str, Any] = {
        "scope": "first_add_event_window",
        "input_count": len(messages),
        "kept_count": len(messages),
        "dropped_before_first_add": 0,
        "dropped_after_event_created": 0,
        "kept_after_event_created": 0,
        "kept_unknown_time": 0,
        "first_added_at": first_added_at.isoformat() if first_added_at else "",
        "event_created_at": event_created_at.isoformat() if event_created_at else "",
    }
    if not first_added_at:
        summary["scope"] = "first_add_no_time_window"
        summary["kept_unknown_time"] = sum(1 for message in messages if not _message_time(message))
        if event_created_at:
            summary["kept_after_event_created"] = sum(
                1 for message in messages if _message_time(message) and _message_time(message) > event_created_at
            )
        return messages, summary

    filtered: list[dict[str, Any]] = []
    dropped = 0
    kept_after_event = 0
    unknown = 0
    for message in messages:
        message_at = _message_time(message)
        if not message_at:
            filtered.append(message)
            unknown += 1
            continue
        if first_added_at and message_at < first_added_at:
            dropped += 1
            continue
        if event_created_at and message_at > event_created_at:
            kept_after_event += 1
        filtered.append(message)
    summary["kept_count"] = len(filtered)
    summary["dropped_before_first_add"] = dropped
    summary["kept_after_event_created"] = kept_after_event
    summary["kept_unknown_time"] = unknown
    return filtered, summary


def _message_time(message: dict[str, Any]) -> datetime | None:
    for key in ("msgtime", "timestamp", "created_at", "time"):
        parsed = _parse_time(message.get(key))
        if parsed:
            return parsed
    return None


def _parse_time(value: Any) -> datetime | None:
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = _string(value)
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        try:
            return _parse_time(float(text))
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _idempotency_key(payload: dict[str, Any], customer: dict[str, Any], *, sop_pack_id: str, index: int) -> str:
    event_id = _string(payload.get("event_id"))
    event_type = _string(payload.get("event_type"))
    identity = _customer_identity(payload, customer)
    customer_key = identity["external_userid"] or identity["customer_id"] or f"customer_{index}"
    account_key = identity.get("wechat") or "missing_wechat"
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    if event_type == "sop_friend_added_schedule_batch":
        first_added = customer.get("first_added_event") if isinstance(customer.get("first_added_event"), dict) else {}
        trace_id = _string(first_added.get("trace_id")) or event_id
        delay = _int(customer_sop.get("delay_minutes"), _int(root_sop.get("delay_minutes"), 0))
        return "|".join([event_type, trace_id, str(delay), account_key, customer_key, sop_pack_id])
    platform_task_id = _string(customer_sop.get("platform_task_id")) or _string(root_sop.get("platform_task_id")) or event_id
    actions_hash = _stable_hash(_customer_actions(payload, customer))[:16]
    return "|".join([event_type or "sop_event", platform_task_id, account_key, customer_key, sop_pack_id, actions_hash])


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _first_string(*values: Any) -> str:
    for value in values:
        text = _string(value)
        if text:
            return text
    return ""


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
