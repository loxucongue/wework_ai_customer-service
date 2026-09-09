from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import inspect
import logging
from typing import Any, Awaitable, Callable

from app.schemas import ChatRequest, ChatResponse
from app.services.customer_relation import customer_relation_is_deleted, normalize_customer_relation
from app.services.customer_scope import build_customer_scope
from app.services.outreach.first_day import _ai_mode_gate
from app.services.sop_platform_task_policy import personalized_order_eligibility
from app.services.v3_reply_recovery import stable_v3_reply_messages


logger = logging.getLogger(__name__)


_FALLBACK_TEXTS = {"您稍等一下"}
_PENDING_DELIVERY_STATUSES = {
    "submitting",
    "platform_accepted",
    "submission_unknown",
    "sending",
}
_TERMINAL_FAILED_DELIVERY_STATUSES = {"send_failed", "partial_failed"}


Generator = Callable[[ChatRequest, str], Awaitable[ChatResponse]]


class V3ReplyRecoveryWorker:
    """Reliably regenerate and deliver a V3 reply after the synchronous fallback.

    The worker deliberately owns no sales semantics.  It rebuilds the original
    request, asks the injected V3 graph to generate once, and sends only after
    two identical safety/freshness gates.  All state transitions are delegated
    to the durable repository so a process restart cannot duplicate a send.
    """

    def __init__(
        self,
        *,
        repository: Any,
        generator: Generator,
        system_client: Any,
        send_client: Any,
        customer_context_service: Any,
        delivery_finalizer: Any | None = None,
        settings: Any,
    ) -> None:
        self.repository = repository
        self.generator = generator
        self.system_client = system_client
        self.send_client = send_client
        self.customer_context_service = customer_context_service
        self.delivery_finalizer = delivery_finalizer
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._counters: Counter[str] = Counter()
        self._last_error = ""
        self._last_iteration_at = ""

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "v3_reply_recovery_enabled", False))

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        if not self.enabled or self.running:
            return False
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="v3-reply-recovery-worker")
        return True

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "last_iteration_at": self._last_iteration_at,
            "last_error": self._last_error,
            "counters": dict(self._counters),
        }

    async def run_once(self) -> list[dict[str, Any]]:
        """Claim one durable batch and process it serially.

        Serial processing is intentional: recovery is low-volume emergency work
        and must not contend with the live Reply path for model or database
        capacity.
        """

        self._last_iteration_at = _utc_now().isoformat()
        max_attempts = max(1, int(getattr(self.settings, "v3_reply_recovery_max_attempts", 2) or 2))
        batch_size = max(1, min(int(getattr(self.settings, "v3_reply_recovery_batch_size", 5) or 5), 50))
        claims = await _thread_call(
            self.repository.claim_v3_fallback_recoveries,
            limit=batch_size,
            max_attempts=max_attempts,
        )
        results: list[dict[str, Any]] = []
        self._counters["claimed"] += len(claims)
        for claim in claims:
            result = await self.process_claim(claim)
            results.append(result)
            self._counters[str(result.get("status") or "unknown")] += 1
        return results

    async def process_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        request_id = str(claim.get("request_id") or "").strip()
        if not request_id:
            return {"status": "invalid_claim", "reason": "missing_request_id"}

        request_payload = claim.get("request") if isinstance(claim.get("request"), dict) else {}
        if _contains_base64_placeholder(request_payload):
            return await self._manual_review(request_id, "recovery_image_payload_omitted")
        try:
            request = ChatRequest.model_validate(request_payload)
        except Exception as exc:
            return await self._manual_review(
                request_id,
                f"recovery_request_invalid:{type(exc).__name__}",
            )

        identity = _identity_from_request(request)
        if identity.get("missing"):
            return await self._manual_review(
                request_id,
                "recovery_identity_incomplete:" + ",".join(identity["missing"]),
            )
        if not _delivery_tracking_enabled(self.send_client):
            return await self._manual_review(request_id, "recovery_delivery_tracking_unavailable")

        delivery_key = f"v3-recovery:{request_id}"
        existing = await self._delivery_dispatch(delivery_key)
        reconciled = await self._reconcile_existing_dispatch(
            claim=claim,
            request_id=request_id,
            dispatch=existing,
        )
        if reconciled is not None:
            return reconciled

        first_gate = await self._preflight(request_id=request_id, request=request, identity=identity)
        if first_gate["action"] != "allow":
            return await self._apply_gate(request_id, first_gate)

        try:
            response = await self.generator(request, request_id=request_id)
            response_payload = _chat_response_payload(response)
            generated_messages = _reply_messages(response_payload)
            if not _usable_reply_messages(generated_messages):
                raise RuntimeError("recovery_generation_returned_no_customer_reply")
            stable_messages = stable_v3_reply_messages(
                generated_messages,
                response_id=str(claim.get("response_id") or response_payload.get("response_id") or ""),
                recovery_kind=str(claim.get("recovery_kind") or "recovery"),
            )
        except Exception as exc:
            return await self._retry_or_exhaust(
                claim,
                f"recovery_generation_failed:{type(exc).__name__}:{exc}",
            )

        # Generation can take several seconds.  Re-read every mutable guard just
        # before the side effect rather than relying on the earlier snapshot.
        second_gate = await self._preflight(request_id=request_id, request=request, identity=identity)
        if second_gate["action"] != "allow":
            return await self._apply_gate(request_id, second_gate)

        existing = await self._delivery_dispatch(delivery_key)
        reconciled = await self._reconcile_existing_dispatch(
            claim=claim,
            request_id=request_id,
            dispatch=existing,
        )
        if reconciled is not None:
            return reconciled

        request_context = dict(request.request_context or {})
        customer_scope = build_customer_scope(
            corp_id=identity.get("corp_id"),
            wechat=identity.get("wechat"),
            external_userid=identity.get("external_userid"),
            customer_id=identity.get("customer_id"),
            user_id=identity.get("user_id"),
        )
        sales_stage_record = _sales_stage_record_from_response(
            response_payload,
            stable_messages,
        )
        try:
            send_result = await self.send_client.send_reply_messages(
                request_id=request_id,
                request_context=request_context,
                fallback_customer_id=str(request.platform_customer_id or request.customer_id or ""),
                fallback_corp_id=str(request.corp_id or ""),
                fallback_user_id=request.user_id,
                fallback_wechat=request.wechat,
                fallback_external_userid=request.external_userid,
                reply_messages=stable_messages,
                source_channel="v3_reply_recovery",
                source_kind="v3_reply_recovery",
                source_request_id=request_id,
                source_task_id=request_id,
                conversation_id=str(claim.get("conversation_id") or ""),
                source_context={
                    "original_request_id": request_id,
                    "recovery_attempt": int(claim.get("recovery_attempts") or 0),
                    "recovery_kind": str(claim.get("recovery_kind") or ""),
                    "memory_persist_allowed": bool(customer_scope.persistence_allowed),
                    "sales_contact_key": customer_scope.sales_contact_key,
                    "sales_stage_record": sales_stage_record,
                },
                delivery_idempotency_key=delivery_key,
            )
        except Exception as exc:
            return await self._retry_or_exhaust(
                claim,
                f"recovery_send_failed:{type(exc).__name__}:{exc}",
            )

        send_status = str(send_result.get("status") or "").strip().lower()
        delivery_status = str(send_result.get("delivery_status") or "").strip().lower()
        dispatch_id = str(send_result.get("dispatch_id") or "").strip()
        if dispatch_id and delivery_status == "send_succeeded":
            return await self._confirm_delivery(
                request_id=request_id,
                dispatch_id=dispatch_id,
                duplicate=bool(send_result.get("duplicate_dispatch")),
            )
        if dispatch_id and delivery_status in _PENDING_DELIVERY_STATUSES:
            return await self._await_delivery(
                request_id=request_id,
                reply_messages=stable_messages,
                dispatch_id=dispatch_id,
                response_payload=response_payload,
                duplicate=bool(send_result.get("duplicate_dispatch")),
            )
        if send_status == "skipped":
            reason = str(send_result.get("reason") or "unknown")
            if reason == "explicit_stop_contact":
                return await self._apply_gate(request_id, _gate("cancel", reason))
            return await self._manual_review(
                request_id,
                "recovery_send_contract_blocked:" + reason,
            )
        if dispatch_id:
            return await self._manual_review(
                request_id,
                "recovery_delivery_unconfirmed:"
                + str(send_result.get("error") or delivery_status or send_status or "unknown"),
            )
        return await self._retry_or_exhaust(
            claim,
            "recovery_delivery_unconfirmed:"
            + str(send_result.get("error") or delivery_status or send_status or "unknown"),
        )

    async def _run_loop(self) -> None:
        poll_seconds = max(1.0, float(getattr(self.settings, "v3_reply_recovery_poll_seconds", 5.0) or 5.0))
        while not self._stop_event.is_set():
            try:
                await self.run_once()
                self._last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - top-level resilience
                self._last_error = f"{type(exc).__name__}: {exc}"[:1000]
                self._counters["iteration_error"] += 1
                logger.exception("V3 reply recovery iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def _preflight(
        self,
        *,
        request_id: str,
        request: ChatRequest,
        identity: dict[str, Any],
    ) -> dict[str, str]:
        try:
            newer = await _thread_call(
                self.repository.newer_customer_message_for_v3_recovery,
                request_id,
            )
        except Exception as exc:
            return _gate("retry", f"local_newer_message_check_failed:{type(exc).__name__}:{exc}")
        if bool(newer.get("has_newer")):
            return _gate("cancel", str(newer.get("reason") or "customer_sent_newer_message"))

        scope = build_customer_scope(
            corp_id=identity["corp_id"],
            wechat=identity["wechat"],
            external_userid=identity["external_userid"],
            customer_id=identity["customer_id"],
        )
        if not scope.persistence_allowed:
            return _gate("manual", "recovery_customer_scope_invalid")
        try:
            stop_contact = await _thread_call(self.repository.has_stop_contact, scope.sales_contact_key)
        except Exception as exc:
            return _gate("retry", f"stop_contact_check_failed:{type(exc).__name__}:{exc}")
        if stop_contact:
            return _gate("cancel", "explicit_stop_contact")

        if not bool(getattr(self.system_client, "available", True)):
            return _gate("retry", "ai_mode_client_unavailable")
        ai_mode = await _ai_mode_gate(self.system_client, identity)
        if not bool(ai_mode.get("eligible")):
            reason = str(ai_mode.get("reason") or "ai_mode_unknown")
            return _gate("cancel" if ai_mode.get("available") else "retry", reason)

        conversation = await self._load_conversation(identity)
        if str(conversation.get("status") or "").lower() not in {"ok", "success", "200"}:
            return _gate(
                "retry",
                "platform_conversation_unavailable:"
                + str(conversation.get("reason") or conversation.get("error") or "unknown"),
            )
        relation = (
            conversation.get("customer_relation")
            if isinstance(conversation.get("customer_relation"), dict)
            else normalize_customer_relation(conversation.get("response") or conversation)
        )
        if not bool(relation.get("available")):
            return _gate("retry", "customer_relation_unavailable")
        if customer_relation_is_deleted(relation):
            return _gate("cancel", "customer_relation_deleted")
        messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
        if _platform_has_newer_customer_message(
            messages,
            original_msgid=str(request.request_context.get("msgid") or ""),
            request_context=request.request_context,
        ):
            return _gate("cancel", "platform_customer_sent_newer_message")

        try:
            customer_context = await _thread_call(
                self.customer_context_service.load,
                customer_id=identity["customer_id"],
                memory={},
                request_context={**request.request_context, **identity},
            )
        except Exception as exc:
            return _gate("retry", f"order_context_check_failed:{type(exc).__name__}:{exc}")
        eligibility = personalized_order_eligibility(customer_context)
        if not bool(eligibility.get("available")):
            return _gate("retry", str(eligibility.get("reason") or "platform_order_context_unavailable"))
        if bool(eligibility.get("prepay_paid")) or str(
            eligibility.get("deposit_state") or ""
        ).strip().lower() in {"paid", "paid_by_order"}:
            return _gate("cancel", "order_state_changed")
        if not bool(eligibility.get("eligible")):
            return _gate("cancel", str(eligibility.get("reason") or "order_state_changed"))
        if _authoritative_appointment_exists(customer_context):
            return _gate("cancel", "appointment_state_changed")
        return _gate("allow", "all_recovery_guards_passed")

    async def _load_conversation(self, identity: dict[str, Any]) -> dict[str, Any]:
        fetch = getattr(self.send_client, "fetch_conversation", None)
        if callable(fetch):
            try:
                return await _async_call(
                    fetch,
                    corp_id=identity["corp_id"],
                    customer_id=identity["customer_id"],
                    external_userid=identity["external_userid"],
                    user_id=identity["user_id"],
                    wechat=identity["wechat"],
                    limit=50,
                )
            except Exception as exc:
                return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        fetch = getattr(self.system_client, "conversation", None)
        if not callable(fetch):
            return {"status": "failed", "reason": "conversation_client_unavailable"}
        try:
            raw = await _async_call(
                fetch,
                corp_id=identity["corp_id"],
                customer_id=identity["customer_id"],
                external_userid=identity["external_userid"],
                user_id=identity["user_id"],
                wechat=identity["wechat"],
                limit=50,
            )
        except Exception as exc:
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
        data = data if isinstance(data, dict) else {}
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        return {
            "status": "ok",
            "messages": messages,
            "customer_relation": normalize_customer_relation(raw),
            "response": raw,
        }

    async def _delivery_dispatch(self, idempotency_key: str) -> dict[str, Any]:
        loader = getattr(self.system_client, "delivery_dispatch", None)
        if not callable(loader):
            return {}
        try:
            result = await _thread_or_async_call(loader, idempotency_key)
        except Exception:
            return {"_lookup_error": True}
        return result if isinstance(result, dict) else {}

    async def _reconcile_existing_dispatch(
        self,
        *,
        claim: dict[str, Any],
        request_id: str,
        dispatch: dict[str, Any],
    ) -> dict[str, Any] | None:
        if dispatch.get("_lookup_error"):
            return await self._retry_or_exhaust(claim, "recovery_dispatch_lookup_failed")
        if not dispatch:
            return None
        status = str(dispatch.get("status") or "").strip().lower()
        if status in _TERMINAL_FAILED_DELIVERY_STATUSES:
            return await self._finalize_failed_dispatch(
                request_id=request_id,
                dispatch_id=str(dispatch.get("id") or dispatch.get("dispatch_id") or "").strip(),
                status=status,
            )
        dispatch_id = str(dispatch.get("id") or dispatch.get("dispatch_id") or "").strip()
        messages = dispatch.get("reply_messages") if isinstance(dispatch.get("reply_messages"), list) else []
        if not dispatch_id or not messages:
            return await self._manual_review(request_id, "recovery_dispatch_evidence_incomplete")
        if status == "send_succeeded":
            return await self._confirm_delivery(
                request_id=request_id,
                dispatch_id=dispatch_id,
                duplicate=True,
            )
        if status in _PENDING_DELIVERY_STATUSES:
            return await self._await_delivery(
                request_id=request_id,
                reply_messages=messages,
                dispatch_id=dispatch_id,
                response_payload={},
                duplicate=True,
            )
        # ``created`` from a previous process is intentionally ambiguous: old
        # releases did not persist ``submitting`` before the HTTP side effect.
        # Unknown/submission-failed states likewise require reconciliation,
        # never another customer send.
        return await self._manual_review(
            request_id,
            f"recovery_dispatch_ambiguous:{status or 'unknown'}",
        )

    async def _await_delivery(
        self,
        *,
        request_id: str,
        reply_messages: list[dict[str, Any]],
        dispatch_id: str,
        response_payload: dict[str, Any],
        duplicate: bool,
    ) -> dict[str, Any]:
        snapshot = dict(response_payload)
        snapshot.setdefault("request_id", request_id)
        stage = getattr(
            self.repository,
            "stage_v3_fallback_recovery_delivery",
            self.repository.complete_v3_fallback_recovery,
        )
        result = await _thread_call(
            stage,
            request_id=request_id,
            reply_messages=reply_messages,
            dispatch_id=dispatch_id,
            response_snapshot=snapshot,
        )
        status = str(result.get("status") or "")
        if status == "recovered":
            return {
                "status": "recovered",
                "request_id": request_id,
                "dispatch_id": dispatch_id,
                "duplicate_dispatch": duplicate,
                "repository": result,
            }
        if status != "delivery_pending":
            return {
                "status": "state_conflict",
                "request_id": request_id,
                "dispatch_id": dispatch_id,
                "reason": status or "recovery_delivery_stage_failed",
                "repository": result,
            }
        return {
            "status": "delivery_pending",
            "request_id": request_id,
            "dispatch_id": dispatch_id,
            "duplicate_dispatch": duplicate,
            "repository": result,
        }

    async def _confirm_delivery(
        self,
        *,
        request_id: str,
        dispatch_id: str,
        duplicate: bool,
    ) -> dict[str, Any]:
        if self.delivery_finalizer is not None:
            dispatch = await _thread_call(self.repository.get_message_dispatch, dispatch_id)
            if not isinstance(dispatch, dict) or not dispatch:
                dispatch = {
                    "id": dispatch_id,
                    "source_kind": "v3_reply_recovery",
                    "source_request_id": request_id,
                    "status": "send_succeeded",
                }
            else:
                dispatch = {**dispatch, "status": "send_succeeded"}
            result = await _thread_call(self.delivery_finalizer.finalize, dispatch)
        else:
            result = await _thread_call(
                self.repository.finalize_v3_recovery_delivery,
                request_id=request_id,
                dispatch_id=dispatch_id,
                delivery_status="send_succeeded",
                error="",
            )
        status = str(result.get("status") or "")
        return {
            "status": "recovered" if status == "recovered" else "state_conflict",
            "request_id": request_id,
            "dispatch_id": dispatch_id,
            "duplicate_dispatch": duplicate,
            "reason": "" if status == "recovered" else str(result.get("reason") or status),
            "repository": result,
        }

    async def _finalize_failed_dispatch(
        self,
        *,
        request_id: str,
        dispatch_id: str,
        status: str,
    ) -> dict[str, Any]:
        if not dispatch_id:
            return await self._manual_review(request_id, "recovery_dispatch_evidence_incomplete")
        result = await _thread_call(
            self.repository.finalize_v3_recovery_delivery,
            request_id=request_id,
            dispatch_id=dispatch_id,
            delivery_status=status,
            error=f"recovery_dispatch_{status}",
        )
        actual = str(result.get("status") or "")
        return {
            "status": "manual_review" if actual == "manual_review" else "state_conflict",
            "request_id": request_id,
            "dispatch_id": dispatch_id,
            "reason": f"recovery_dispatch_{status}",
            "repository": result,
        }

    async def _apply_gate(self, request_id: str, gate: dict[str, str]) -> dict[str, Any]:
        action = gate["action"]
        reason = gate["reason"]
        if action == "cancel":
            result = await _thread_call(
                self.repository.cancel_v3_fallback_recovery,
                request_id=request_id,
                reason=reason,
            )
            return {"status": "cancelled", "request_id": request_id, "reason": reason, "repository": result}
        if action == "manual":
            return await self._manual_review(request_id, reason)
        claim = await _thread_call(self.repository.get_v3_generation_result, request_id=request_id)
        return await self._retry_or_exhaust(claim, reason)

    async def _manual_review(self, request_id: str, reason: str) -> dict[str, Any]:
        result = await _thread_call(
            self.repository.mark_v3_recovery_manual_review,
            request_id=request_id,
            reason=reason,
        )
        return {"status": "manual_review", "request_id": request_id, "reason": reason, "repository": result}

    async def _retry_or_exhaust(self, claim: dict[str, Any], error: str) -> dict[str, Any]:
        request_id = str(claim.get("request_id") or "").strip()
        max_attempts = max(1, int(getattr(self.settings, "v3_reply_recovery_max_attempts", 2) or 2))
        next_at = _second_attempt_at(claim)
        result = await _thread_call(
            self.repository.retry_v3_fallback_recovery,
            request_id=request_id,
            next_retry_at=next_at,
            error=str(error or "recovery_failed")[:4000],
            max_attempts=max_attempts,
        )
        exhausted = bool(result.get("exhausted")) or str(result.get("status") or "") == "manual_review"
        return {
            "status": "manual_review" if exhausted else "retry_scheduled",
            "request_id": request_id,
            "reason": str(error or "recovery_failed")[:1000],
            "next_retry_at": "" if exhausted else next_at,
            "repository": result,
        }


# Backwards-compatible descriptive alias for dependency assembly code.
V3ReplyRecoveryWorkerService = V3ReplyRecoveryWorker


def _identity_from_request(request: ChatRequest) -> dict[str, Any]:
    context = request.request_context if isinstance(request.request_context, dict) else {}
    identity = {
        "corp_id": str(context.get("corp_id") or request.corp_id or "").strip(),
        "customer_id": str(
            context.get("customer_id") or request.platform_customer_id or request.customer_id or ""
        ).strip(),
        "external_userid": str(context.get("external_userid") or request.external_userid or "").strip(),
        "user_id": str(context.get("user_id") or request.user_id or "").strip(),
        "wechat": str(context.get("wechat") or request.wechat or "").strip(),
    }
    identity["missing"] = [key for key, value in identity.items() if key != "missing" and not value]
    return identity


def _delivery_tracking_enabled(send_client: Any) -> bool:
    explicit = getattr(send_client, "delivery_enabled", None)
    if isinstance(explicit, bool):
        return explicit
    service = getattr(send_client, "_delivery_service", None)
    enabled = getattr(service, "enabled", None)
    callback_required = getattr(service, "callback_required", None)
    return bool(enabled and callback_required)


def _chat_response_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, ChatResponse):
        return response.model_dump(mode="json")
    if isinstance(response, dict):
        return dict(response)
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        value = dump(mode="json")
        return value if isinstance(value, dict) else {}
    return {}


def _reply_messages(response: dict[str, Any]) -> list[dict[str, Any]]:
    messages = response.get("reply_messages") if isinstance(response.get("reply_messages"), list) else []
    return [dict(item) for item in messages if isinstance(item, dict)]


def _usable_reply_messages(messages: list[dict[str, Any]]) -> bool:
    if not messages:
        return False
    visible = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, dict):
            content = content.get("text") or content.get("content") or content
        text = str(content or "").strip()
        if text:
            visible.append(text)
    return bool(visible) and not (len(visible) == 1 and visible[0] in _FALLBACK_TEXTS)


def _sales_stage_record_from_response(
    response: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, str]:
    """Project a delivered text stage from Reply's normalized decision only."""

    if not any(
        str(item.get("type") or "").strip() == "text"
        and str(item.get("content") or "").strip()
        for item in messages
        if isinstance(item, dict)
    ):
        return {}
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    judgment = (
        meta.get("reply_sales_judgment")
        if isinstance(meta.get("reply_sales_judgment"), dict)
        else {}
    )
    next_action = (
        judgment.get("next_sales_action")
        if isinstance(judgment.get("next_sales_action"), dict)
        else {}
    )
    action_type = str(next_action.get("type") or "").strip()
    target_stage = str(next_action.get("target_stage") or "").strip()
    if action_type != "explain_activity" or target_stage != "activity_offer":
        return {}
    return {"stage": "activity_offer", "action_type": "explain_activity"}


def _contains_base64_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("[base64 image omitted:")
    if isinstance(value, dict):
        return any(_contains_base64_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_base64_placeholder(item) for item in value)
    return False


def _authoritative_appointment_exists(customer_context: Any) -> bool:
    if not isinstance(customer_context, dict) or str(customer_context.get("source") or "") != "platform_agent":
        return False
    appointment = customer_context.get("appointment")
    if not isinstance(appointment, dict) or not appointment:
        return False
    status = str(appointment.get("status") or "").strip().lower()
    if status in {"cancelled", "canceled", "expired", "invalid"}:
        return False
    if status in {"scheduled", "visited", "finished"}:
        return True
    if any(
        _meaningful_identifier(appointment.get(key))
        for key in ("id", "appointment_id", "appointmentId")
    ):
        return True
    return any(
        _parse_datetime(appointment.get(key)) is not None
        for key in ("time", "appointment_time", "scheduled_at")
    )


def _meaningful_identifier(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text not in {"", "0", "none", "null", "unknown", "pending", "waiting_schedule"}


def _platform_has_newer_customer_message(
    messages: list[Any],
    *,
    original_msgid: str,
    request_context: dict[str, Any],
) -> bool:
    customer_messages = [item for item in messages if isinstance(item, dict) and _message_party(item) == "customer"]
    if not customer_messages:
        return False
    clean_original = str(original_msgid or "").strip()
    original_time = _first_datetime(
        request_context,
        ("message_created_at", "message_time", "msgtime", "timestamp", "occurred_at", "event_time", "received_at"),
    )
    anchors = [item for item in customer_messages if clean_original and _message_id(item) == clean_original]
    if anchors:
        anchor_time = _message_datetime(anchors[-1]) or original_time
        if anchor_time is not None:
            return any(
                _message_id(item) != clean_original
                and (item_time := _message_datetime(item)) is not None
                and item_time > anchor_time
                for item in customer_messages
            )
        if len(customer_messages) == 1:
            return False
        # The platform normally returns messages chronologically.  Only rely on
        # position when it also returned the exact original message anchor.
        anchor_index = max(index for index, item in enumerate(customer_messages) if _message_id(item) == clean_original)
        return any(_message_id(item) != clean_original for item in customer_messages[anchor_index + 1 :])
    if original_time is not None:
        return any(
            (item_time := _message_datetime(item)) is not None and item_time > original_time
            for item in customer_messages
        )
    # The local durable message check remains authoritative when the upstream
    # response omits message identifiers/timestamps; do not infer ordering from
    # unanchored text alone.
    return False


def _message_party(message: dict[str, Any]) -> str:
    value = str(
        message.get("direction")
        or message.get("sender_type")
        or message.get("role")
        or message.get("from")
        or ""
    ).strip().lower()
    if value in {"customer", "user", "external", "inbound", "receive", "received"}:
        return "customer"
    if value in {"staff", "assistant", "service", "ai", "outbound", "send", "sent"}:
        return "staff"
    return "unknown"


def _message_id(message: dict[str, Any]) -> str:
    return str(
        message.get("msgid")
        or message.get("msg_id")
        or message.get("message_id")
        or message.get("id")
        or ""
    ).strip()


def _message_datetime(message: dict[str, Any]) -> datetime | None:
    return _first_datetime(
        message,
        ("msgtime", "timestamp", "created_at", "send_time", "message_time", "occurred_at"),
    )


def _first_datetime(source: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        parsed = _parse_datetime(source.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_datetime(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _gate(action: str, reason: str) -> dict[str, str]:
    return {"action": action, "reason": str(reason or action)[:1000]}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _second_attempt_at(claim: dict[str, Any]) -> str:
    """Schedule attempt two at original +45s, with a safe fallback.

    Older rows do not expose ``created_at`` in the recovery projection.  For
    those rows attempt one was scheduled at +15s, so waiting another 30s
    preserves the same cadence without guessing an old wall-clock timestamp.
    """

    now = _utc_now()
    created_at = _parse_datetime(claim.get("created_at"))
    desired = created_at + timedelta(seconds=45) if created_at is not None else now + timedelta(seconds=30)
    if desired <= now:
        desired = now + timedelta(seconds=1)
    return desired.isoformat()


async def _thread_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(function, *args, **kwargs)


async def _async_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = function(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


async def _thread_or_async_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    result = await asyncio.to_thread(function, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result
