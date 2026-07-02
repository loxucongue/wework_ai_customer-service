from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import BackgroundTasks

from app.services.outreach_send_client import OutreachSendClient
from app.services.sop_execution_service import SopExecutionService, first_add_candidate_packs
from app.services.sop_reply_pack_service import ALLOWED_MESSAGE_TYPES, SopReplyPackService
from app.services.storage.serialization import utc_now_iso
from app.services.trace_logger import compact


class SopEventService:
    def __init__(
        self,
        *,
        repository: Any,
        sop_reply_pack_service: SopReplyPackService,
        outreach_send_client: OutreachSendClient,
        sop_execution_service: SopExecutionService | None = None,
    ) -> None:
        self.repository = repository
        self.sop_reply_pack_service = sop_reply_pack_service
        self.outreach_send_client = outreach_send_client
        self.sop_execution_service = sop_execution_service

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

        status = "processed"
        if any(str(item.get("status") or "").startswith("failed") for item in task_results):
            status = "processed_with_errors"
        elif not task_results:
            status = "skipped_no_tasks"
        self.repository.update_sop_event_status(event_id, status=status)
        return {"status": status, "tasks": task_results}

    async def _create_customer_task(self, payload: dict[str, Any], customer: dict[str, Any], *, index: int) -> dict[str, Any]:
        event_type = _string(payload.get("event_type"))
        identity = _customer_identity(payload, customer)
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

        conversation_messages = conversation_fetch.get("messages") if isinstance(conversation_fetch.get("messages"), list) else []
        if event_type == "sop_friend_added_schedule_batch":
            return await self._create_first_add_task(
                payload,
                customer,
                index=index,
                identity=identity,
                conversation_fetch=conversation_fetch,
                conversation_messages=conversation_messages,
            )
        if event_type == "sop_platform_task":
            return await self._create_platform_task(
                payload,
                customer,
                index=index,
                identity=identity,
                conversation_fetch=conversation_fetch,
                conversation_messages=conversation_messages,
            )

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

    async def _create_first_add_task(
        self,
        payload: dict[str, Any],
        customer: dict[str, Any],
        *,
        index: int,
        identity: dict[str, str],
        conversation_fetch: dict[str, Any],
        conversation_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        completed_ids = self.repository.list_sent_sop_pack_ids_for_customer(
            customer_id=identity["customer_id"],
            external_userid=identity["external_userid"],
        )
        completed_categories = _sent_categories(self.repository, identity)
        delay_minutes = _match_context(payload, customer)["delay_minutes"]
        candidates = first_add_candidate_packs(
            self.sop_reply_pack_service.load(),
            completed_sop_pack_ids=completed_ids,
            completed_sop_categories=completed_categories,
            delay_minutes=delay_minutes,
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
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                },
            )

        decision = await self._event_decision(
            payload=payload,
            customer=customer,
            identity=identity,
            event_type="sop_friend_added_schedule_batch",
            conversation_messages=conversation_messages,
            candidate_packs=candidates,
            actions_reply_messages=[],
        )
        selected = _pack_by_id(candidates, str(decision.get("sop_pack_id") or ""))
        if not decision.get("send_sop") or not selected:
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id=str(decision.get("sop_pack_id") or "first_add_model_rejected"),
                sop_pack_name=str(decision.get("sop_pack_name") or "first_add_model_rejected"),
                reply_messages=[],
                status="skipped_model_error" if decision.get("error") else "skipped_model_rejected",
                error=str(decision.get("error") or decision.get("reason") or "model_rejected_sop_event"),
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
                    "event_decision_input": decision.get("selector_input", {}),
                },
                send_response={"event_decision": decision},
            )

        messages = _pack_messages(selected)
        return self._create_task_record(
            payload,
            customer,
            index=index,
            identity=identity,
            sop_pack_id=str(selected.get("id") or ""),
            sop_pack_name=str(selected.get("name") or ""),
            sop_category=_pack_category(selected),
            reply_messages=messages,
            status="pending",
            error="",
            send_payload={
                "identity": identity,
                "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
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
    ) -> dict[str, Any]:
        messages = _actions_to_reply_messages(_customer_actions(payload, customer))
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
                send_payload={"identity": identity, "conversation_fetch": _conversation_fetch_summary(conversation_fetch)},
            )

        decision = await self._event_decision(
            payload=payload,
            customer=customer,
            identity=identity,
            event_type="sop_platform_task",
            conversation_messages=conversation_messages,
            candidate_packs=[],
            actions_reply_messages=messages,
        )
        if not decision.get("send_sop"):
            return self._create_task_record(
                payload,
                customer,
                index=index,
                identity=identity,
                sop_pack_id="platform_actions",
                sop_pack_name="platform_actions",
                sop_category="platform_actions",
                reply_messages=messages,
                status="skipped_model_error" if decision.get("error") else "skipped_model_rejected",
                error=str(decision.get("error") or decision.get("reason") or "model_rejected_platform_actions"),
                send_payload={
                    "identity": identity,
                    "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
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
            reply_messages=messages,
            status="pending",
            error="",
            send_payload={
                "identity": identity,
                "conversation_fetch": _conversation_fetch_summary(conversation_fetch),
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
        candidate_packs: list[dict[str, Any]],
        actions_reply_messages: list[dict[str, Any]],
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
            candidate_packs=candidate_packs,
            actions_reply_messages=actions_reply_messages,
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
        )
        if task.get("id") and task.get("created") and (send_payload or send_response):
            created = bool(task.get("created"))
            task = self.repository.update_sop_send_task(
                str(task["id"]),
                status=status,
                send_payload=send_payload or {},
                send_response=send_response or {},
                error=error,
            )
            task["created"] = created
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
            return self.repository.update_sop_send_task(
                str(task.get("id") or ""),
                status="sent",
                send_payload=_merge_send_payload(task, result.get("send_payload") if isinstance(result.get("send_payload"), dict) else {}),
                send_response=_merge_send_response(
                    task,
                    result.get("response") if isinstance(result.get("response"), dict) else result,
                ),
                sent_at=utc_now_iso(),
            )
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
        ),
        "wechat": _first_string(
            customer.get("wechat"),
            conversation.get("wechat"),
            customer_sop.get("wechat"),
            root_sop.get("wechat"),
            payload.get("wechat"),
            account.get("wechat"),
        ),
        "external_userid": external_userid,
        "customer_id": customer_id,
    }


def _match_context(payload: dict[str, Any], customer: dict[str, Any]) -> dict[str, Any]:
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    return {
        "event_type": _string(payload.get("event_type")),
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


def _sent_categories(repository: Any, identity: dict[str, str]) -> list[str]:
    func = getattr(repository, "list_sent_sop_categories_for_customer", None)
    if not callable(func):
        return []
    return list(
        func(
            customer_id=identity.get("customer_id", ""),
            external_userid=identity.get("external_userid", ""),
        )
        or []
    )


def _customer_actions(payload: dict[str, Any], customer: dict[str, Any]) -> list[Any]:
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    customer_actions = customer_sop.get("actions")
    if isinstance(customer_actions, list) and customer_actions:
        return customer_actions
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    root_actions = root_sop.get("actions")
    return root_actions if isinstance(root_actions, list) else []


def _actions_to_reply_messages(actions: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for index, action_value in enumerate(actions, start=1):
        action = action_value if isinstance(action_value, dict) else {"type": "text", "content": action_value}
        message_type = _string(action.get("type")) or "text"
        if message_type not in ALLOWED_MESSAGE_TYPES:
            message_type = "text"
        content = _message_content(message_type, action.get("content"))
        if not _content_has_value(content):
            continue
        messages.append({"type": message_type, "order": index, "content": content})
    return messages


def _message_content(message_type: str, value: Any) -> dict[str, Any]:
    content = value if isinstance(value, dict) else {}
    if message_type == "text":
        return {"text": _string(content.get("text")) or _string(value)}
    if message_type in {"image", "video"}:
        return {"url": _string(content.get("url")) or _string(value)}
    if message_type == "payment_collection":
        return {"amount": _int(content.get("amount"), 10), "remark": _string(content.get("remark"))}
    if message_type == "store_address":
        return {"store_id": _string(content.get("store_id")) or _string(content.get("id")) or _string(value)}
    if message_type == "human_handoff":
        return {"handoff_reason": _string(content.get("handoff_reason")) or _string(content.get("text")) or _string(value)}
    return {}


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
        "response": compact(response, max_chars=2000) if response else {},
    }


def _idempotency_key(payload: dict[str, Any], customer: dict[str, Any], *, sop_pack_id: str, index: int) -> str:
    event_id = _string(payload.get("event_id"))
    event_type = _string(payload.get("event_type"))
    identity = _customer_identity(payload, customer)
    customer_key = identity["external_userid"] or identity["customer_id"] or f"customer_{index}"
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    if event_type == "sop_friend_added_schedule_batch":
        first_added = customer.get("first_added_event") if isinstance(customer.get("first_added_event"), dict) else {}
        trace_id = _string(first_added.get("trace_id")) or event_id
        delay = _int(customer_sop.get("delay_minutes"), _int(root_sop.get("delay_minutes"), 0))
        return "|".join([event_type, trace_id, str(delay), customer_key, sop_pack_id])
    platform_task_id = _string(customer_sop.get("platform_task_id")) or _string(root_sop.get("platform_task_id")) or event_id
    actions_hash = _stable_hash(_customer_actions(payload, customer))[:16]
    return "|".join([event_type or "sop_event", platform_task_id, customer_key, sop_pack_id, actions_hash])


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
