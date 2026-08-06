from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.nodes.store_scope_summary import store_scope_ids
from app.services.risk_hold import health_risk_hold


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
MAX_FULL_TIMELINE_MESSAGES = 100
MAX_SHADOW_CONTENT_CHARS = 700
MAX_FACT_ITEMS = 20
NON_AUTHORITY_PROFILE_FIELDS = (
    "next_sales_strategy",
    "decision_stage",
    "main_concern",
    "main_objection",
    "customer_type",
    "intent_level",
)


def build_reply_chain_shadow_context(
    state: dict[str, Any],
    *,
    identity: Any,
    customer_result: Any,
    store_knowledge: Any,
    conversation_result: Any,
    memory: Any,
) -> dict[str, Any]:
    """Build a read-only future refactor input view without changing model behavior."""
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    timeline = _conversation_timeline(state, conversation_result=conversation_result, request_context=request_context)
    authoritative_facts = _authoritative_facts(
        state,
        identity=identity,
        customer_result=customer_result,
        store_knowledge=store_knowledge,
        conversation_result=conversation_result,
        memory=memory,
    )
    return _drop_empty(
        {
            "schema_version": "reply_chain_shadow_v1",
            "purpose": "shadow_only_no_model_input_no_customer_effect",
            "current_time": _current_time(request_context),
            "customer_scope": state.get("customer_scope") if isinstance(state.get("customer_scope"), dict) else {},
            "conversation": {
                "policy": _conversation_policy(timeline),
                "messages": timeline,
            },
            "authoritative_facts": authoritative_facts,
            "authority_audit": _authority_audit(
                state,
                memory=memory,
                conversation_result=conversation_result,
                timeline=timeline,
                facts=authoritative_facts,
            ),
            "excluded_as_authority": [
                "customer_profile.next_sales_strategy",
                "customer_profile.decision_stage",
                "customer_profile.main_concern_as_current_intent",
                "stale_history_event_psychology",
            ],
        }
    )


def _authority_audit(
    state: dict[str, Any],
    *,
    memory: Any,
    conversation_result: Any,
    timeline: list[dict[str, Any]],
    facts: dict[str, Any],
) -> dict[str, Any]:
    memory_dict = memory if isinstance(memory, dict) else {}
    profile = memory_dict.get("customer_profile")
    if not isinstance(profile, dict):
        profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    seen_soft_fields = [field for field in NON_AUTHORITY_PROFILE_FIELDS if field in profile and _string(profile.get(field))]
    fact_section_status = _fact_section_status(facts)
    current_message_audit = _current_message_audit(state, timeline)
    return _drop_empty(
        {
            "schema_version": "reply_chain_authority_audit_v1",
            "complete_chat_is_primary_authority": True,
            "soft_profile_excluded_from_authority": True,
            "timeline_window_audit": _timeline_window_audit(
                state,
                conversation_result=conversation_result,
                timeline=timeline,
            ),
            "current_message_audit": current_message_audit,
            "soft_profile_fields_seen": seen_soft_fields[:MAX_FACT_ITEMS],
            "timeline_message_count": len(timeline),
            "all_messages_have_sent_at": all(_string(item.get("sent_at")) for item in timeline),
            "fact_snapshot": {
                "schema_version": "reply_chain_fact_snapshot_audit_v1",
                "section_status": fact_section_status,
                "sections_with_error": [
                    section
                    for section, status in fact_section_status.items()
                    if isinstance(status, dict) and status.get("has_error")
                ],
                "empty_or_absent_sections": [
                    section
                    for section, status in fact_section_status.items()
                    if isinstance(status, dict) and not status.get("present")
                ],
            },
            "required_fact_sections": [
                "payment",
                "orders",
                "registration",
                "visible_store_scope",
                "sop_deliveries",
                "structured_messages",
                "risk_holds",
            ],
        }
    )


def _timeline_window_audit(
    state: dict[str, Any],
    *,
    conversation_result: Any,
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    source_name, source_count = _source_message_window(state, conversation_result=conversation_result)
    appended_current_count = sum(1 for item in timeline if _string(item.get("source")) == "current_request")
    available_count = source_count + appended_current_count
    included_count = len(timeline)
    truncated = available_count > included_count
    full_window_required = available_count <= MAX_FULL_TIMELINE_MESSAGES
    source_window_complete = not full_window_required or included_count == available_count
    current_preserved = appended_current_count > 0 or any(
        item.get("source") != "current_request" and _string(item.get("sender")) == "customer"
        for item in timeline[-1:]
    )
    blockers: list[str] = []
    if full_window_required and not source_window_complete:
        blockers.append("source_window_incomplete_under_limit")
    if included_count > MAX_FULL_TIMELINE_MESSAGES:
        blockers.append("timeline_exceeds_max_window")
    if source_count and included_count <= 0:
        blockers.append("timeline_empty_with_source_messages")
    retained_oldest = timeline[0] if timeline else {}
    retained_newest = timeline[-1] if timeline else {}

    return _drop_empty(
        {
            "schema_version": "reply_chain_timeline_window_audit_v1",
            "policy": "full_if_total_available_100_or_less_else_latest_100",
            "source": source_name,
            "source_message_count": source_count,
            "appended_current_request_count": appended_current_count,
            "available_timeline_count": available_count,
            "included_message_count": included_count,
            "max_messages": MAX_FULL_TIMELINE_MESSAGES,
            "truncated": truncated,
            "dropped_message_count": max(0, available_count - included_count),
            "full_window_required": full_window_required,
            "source_window_complete": source_window_complete,
            "current_request_preserved_or_in_source": current_preserved,
            "retained_window": {
                "schema_version": "reply_chain_retained_timeline_window_v1",
                "oldest_message_ref": _string(retained_oldest.get("message_ref")),
                "oldest_sent_at": _string(retained_oldest.get("sent_at")),
                "oldest_source": _string(retained_oldest.get("source")),
                "newest_message_ref": _string(retained_newest.get("message_ref")),
                "newest_sent_at": _string(retained_newest.get("sent_at")),
                "newest_source": _string(retained_newest.get("source")),
                "source_counts": _source_counts(timeline),
                "current_request_message_refs": [
                    _string(item.get("message_ref"))
                    for item in timeline
                    if _string(item.get("source")) == "current_request"
                ][:MAX_FACT_ITEMS],
            },
            "ready_for_authoritative_model_input": not blockers,
            "blockers": blockers,
        }
    )


def _source_message_window(state: dict[str, Any], *, conversation_result: Any) -> tuple[str, int]:
    conversation = conversation_result if isinstance(conversation_result, dict) else {}
    for source_name, value in (
        ("conversation_turns", conversation.get("conversation_turns")),
        ("recent_turns", conversation.get("recent_turns")),
        ("state_conversation_turns", state.get("conversation_turns")),
        ("conversation_history", state.get("conversation_history")),
    ):
        if isinstance(value, list):
            return source_name, len(value)
    return "none", 0


def _current_message_audit(state: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[str, Any]:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    request_content = _request_message_content(state, request_context=request_context)
    request_msgid = _string(request_context.get("msgid"))
    request_msgtype = _string(request_context.get("msgtype"))
    current_required = bool(request_content or request_msgid or request_msgtype)
    match_index = -1
    match: dict[str, Any] = {}
    for index, message in enumerate(timeline):
        message_ref = _string(message.get("message_ref"))
        message_content = _string(message.get("content"))
        sender = _string(message.get("sender"))
        if request_msgid and message_ref == request_msgid:
            match_index = index
            match = message
            break
        if not request_msgid and request_content and message_content == request_content and sender == "customer":
            match_index = index
            match = message
    current_in_timeline = match_index >= 0
    current_is_last = current_in_timeline and match_index == len(timeline) - 1
    matched_content = _string(match.get("content"))
    matched_msgtype = _string(match.get("message_type"))
    content_matches_request = (not request_content) or matched_content == request_content
    msgtype_matches_request = (not request_msgtype) or matched_msgtype == request_msgtype
    blockers: list[str] = []
    if current_required and not current_in_timeline:
        blockers.append("current_message_missing_from_timeline")
    if current_required and current_in_timeline and not current_is_last:
        blockers.append("current_message_not_last_in_timeline")
    if current_required and current_in_timeline and not content_matches_request:
        blockers.append("current_message_content_mismatch")
    if current_required and current_in_timeline and not msgtype_matches_request:
        blockers.append("current_message_type_mismatch")
    if current_required and not _string(match.get("sent_at")):
        blockers.append("current_message_missing_sent_at")

    return _drop_empty(
        {
            "schema_version": "reply_chain_current_message_audit_v1",
            "current_message_required": current_required,
            "request_msgid": request_msgid,
            "request_msgtype": request_msgtype,
            "request_content_present": bool(request_content),
            "request_msgtime": _message_time_from_context(request_context),
            "current_message_in_timeline": current_in_timeline,
            "current_message_is_last": current_is_last,
            "current_message_ref": _string(match.get("message_ref")),
            "current_message_source": _string(match.get("source")),
            "current_message_content_matches_request": content_matches_request,
            "current_message_type_matches_request": msgtype_matches_request,
            "current_message_time_status": _string(match.get("time_status")),
            "ready_for_authoritative_model_input": not blockers,
            "blockers": blockers,
        }
    )


def _fact_section_status(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections = (
        "payment",
        "orders",
        "registration",
        "visible_store_scope",
        "sop_deliveries",
        "structured_messages",
        "risk_holds",
        "conversation_fetch",
        "identity",
    )
    return {
        section: _fact_status(facts.get(section))
        for section in sections
    }


def _fact_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {"present": False}
    error_fields = [
        key
        for key, item in value.items()
        if "error" in str(key).lower() and _string(item)
    ]
    return _drop_empty(
        {
            "present": True,
            "source": value.get("source"),
            "has_error": bool(error_fields),
            "error_fields": error_fields[:MAX_FACT_ITEMS],
        }
    )


def _conversation_timeline(
    state: dict[str, Any],
    *,
    conversation_result: Any,
    request_context: dict[str, Any],
) -> list[dict[str, Any]]:
    conversation = conversation_result if isinstance(conversation_result, dict) else {}
    turns = conversation.get("conversation_turns")
    if not isinstance(turns, list):
        turns = conversation.get("recent_turns")
    if not isinstance(turns, list):
        turns = state.get("conversation_turns") if isinstance(state.get("conversation_turns"), list) else []

    output = [_turn_to_shadow_message(item, index=index) for index, item in enumerate(turns[-MAX_FULL_TIMELINE_MESSAGES:], start=1)]
    output = [item for item in output if item]
    if not output:
        history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
        output = [
            _history_item_to_shadow_message(item, index=index)
            for index, item in enumerate(history[-MAX_FULL_TIMELINE_MESSAGES:], start=1)
        ]
        output = [item for item in output if item]

    current = _current_message(state, request_context=request_context, index=len(output) + 1)
    if current and not _already_contains_current(output, current):
        output.append(current)
    return output[-MAX_FULL_TIMELINE_MESSAGES:]


def _turn_to_shadow_message(item: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    content = _string(item.get("content") or item.get("text") or item.get("message"))
    if not content:
        return {}
    role = _normalize_role(item.get("role") or item.get("direction") or item.get("sender_type"))
    message_type = _string(item.get("message_type") or item.get("msgtype") or item.get("type") or "text") or "text"
    sent_at = _string(item.get("occurred_at") or item.get("sent_at") or item.get("message_time") or item.get("created_at"))
    return _drop_empty(
        {
            "message_ref": _string(item.get("message_ref") or item.get("msgid") or item.get("message_id") or item.get("id"))
            or f"turn_{index:03d}",
            "sender": role,
            "message_type": message_type,
            "content": content[:MAX_SHADOW_CONTENT_CHARS],
            "sent_at": sent_at,
            "time_status": _time_status(sent_at),
            "source": "conversation_turns",
        }
    )


def _history_item_to_shadow_message(item: Any, *, index: int) -> dict[str, Any]:
    text = _string(item)
    if not text:
        return {}
    sender = "unknown"
    content = text
    for prefix, role in (
        ("用户:", "customer"),
        ("小贝:", "assistant"),
        ("客服:", "assistant"),
        ("AI回复:", "assistant"),
        ("user:", "customer"),
        ("assistant:", "assistant"),
    ):
        if text.lower().startswith(prefix.lower()):
            sender = role
            content = text[len(prefix) :].strip()
            break
    return _drop_empty(
        {
            "message_ref": f"history_{index:03d}",
            "sender": sender,
            "message_type": "text",
            "content": content[:MAX_SHADOW_CONTENT_CHARS],
            "time_status": "missing",
            "source": "conversation_history",
        }
    )


def _current_message(state: dict[str, Any], *, request_context: dict[str, Any], index: int) -> dict[str, Any]:
    content = _request_message_content(state, request_context=request_context)
    msgtype = _string(request_context.get("msgtype") or "text") or "text"
    if not content and not (_string(request_context.get("msgid")) or msgtype):
        return {}
    if not content:
        content = f"[non-text current message: {msgtype}]"
    return _drop_empty(
        {
            "message_ref": _string(request_context.get("msgid")) or f"current_{index:03d}",
            "sender": "customer",
            "message_type": msgtype,
            "content": content[:MAX_SHADOW_CONTENT_CHARS],
            "sent_at": _message_time_from_context(request_context),
            "time_status": _time_status(_message_time_from_context(request_context)),
            "source": "current_request",
        }
    )


def _request_message_content(state: dict[str, Any], *, request_context: dict[str, Any]) -> str:
    content = _string(state.get("normalized_content") or state.get("content"))
    if content:
        return content
    raw_payload = request_context.get("raw_workflow_payload")
    if isinstance(raw_payload, dict):
        parameters = raw_payload.get("parameters")
        content_obj = parameters.get("content") if isinstance(parameters, dict) else {}
        if isinstance(content_obj, dict):
            for key in ("content", "text", "location_title", "location_address", "url"):
                value = _string(content_obj.get(key))
                if value:
                    return value
    for key in ("content", "text", "location_title", "location_address", "url"):
        value = _string(request_context.get(key))
        if value:
            return value
    return ""


def _already_contains_current(messages: list[dict[str, Any]], current: dict[str, Any]) -> bool:
    current_ref = _string(current.get("message_ref"))
    if current_ref and any(_string(item.get("message_ref")) == current_ref for item in messages):
        return True
    current_content = _string(current.get("content"))
    return bool(current_content and messages and _string(messages[-1].get("content")) == current_content)


def _authoritative_facts(
    state: dict[str, Any],
    *,
    identity: Any,
    customer_result: Any,
    store_knowledge: Any,
    conversation_result: Any,
    memory: Any,
) -> dict[str, Any]:
    identity_dict = identity if isinstance(identity, dict) else {}
    customer_dict = customer_result if isinstance(customer_result, dict) else {}
    store_dict = store_knowledge if isinstance(store_knowledge, dict) else {}
    conversation_dict = conversation_result if isinstance(conversation_result, dict) else {}
    memory_dict = memory if isinstance(memory, dict) else {}
    customer_context = (
        customer_dict.get("customer_context") if isinstance(customer_dict.get("customer_context"), dict) else {}
    )
    basic = memory_dict.get("customer_basic_info") if isinstance(memory_dict.get("customer_basic_info"), dict) else {}
    history_events = memory_dict.get("history_events") if isinstance(memory_dict.get("history_events"), list) else []
    sent_summary = sent_message_summary_for_model({**state, "history_events": history_events})
    risk_hold = health_risk_hold({**state, "history_events": history_events})
    compact_orders = _compact_orders(customer_context.get("orders"))
    sop_delivery = {
        "history_event_count": len(history_events),
        "source": "history_events",
    }
    return _drop_empty(
        {
            "payment": _payment_fact(state, customer_context=customer_context, sent_summary=sent_summary),
            "orders": {
                "count": len(customer_context.get("orders") or []) if isinstance(customer_context.get("orders"), list) else 0,
                "items": compact_orders,
                "source": customer_context.get("source") or "platform_order_index",
                "error": customer_dict.get("customer_context_error") or customer_dict.get("orders_error"),
            },
            "registration": _registration_fact(basic),
            "visible_store_scope": {
                "store_count": len(store_dict.get("stores") or []) if isinstance(store_dict.get("stores"), list) else 0,
                "store_ids": sorted(store_scope_ids(store_dict))[:MAX_FACT_ITEMS],
                "source": store_dict.get("source"),
                "error": store_dict.get("error"),
                "store_scope_error": store_dict.get("store_scope_error"),
            },
            "sop_delivery": sop_delivery,
            "sop_deliveries": {
                **sop_delivery,
                "recent_event_types": _recent_event_types(history_events),
            },
            "structured_messages": _structured_message_facts(sent_summary),
            "risk_holds": risk_hold,
            "identity": {
                "resolved": bool(identity_dict.get("request_context") or identity_dict.get("identity_context")),
                "error": identity_dict.get("error"),
            },
            "conversation_fetch": conversation_dict.get("conversation_fetch")
            if isinstance(conversation_dict.get("conversation_fetch"), dict)
            else {},
            "request_message": {
                "msgid": (state.get("request_context") or {}).get("msgid") if isinstance(state.get("request_context"), dict) else "",
                "msgtype": (state.get("request_context") or {}).get("msgtype") if isinstance(state.get("request_context"), dict) else "",
            },
        }
    )


def _conversation_policy(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode": "full_if_100_or_less_else_latest_100_shadow",
        "message_count": len(messages),
        "max_messages": MAX_FULL_TIMELINE_MESSAGES,
        "all_messages_have_sent_at": all(_string(item.get("sent_at")) for item in messages),
        "missing_time_message_refs": [
            _string(item.get("message_ref"))
            for item in messages
            if not _string(item.get("sent_at"))
        ][:MAX_FACT_ITEMS],
        "source_counts": _source_counts(messages),
    }


def _current_time(request_context: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(tz=BEIJING_TZ)
    return {
        "timezone": "Asia/Shanghai",
        "now": now.isoformat(timespec="seconds"),
        "request_msgtime": _message_time_from_context(request_context),
    }


def _message_time_from_context(request_context: dict[str, Any]) -> str:
    raw = request_context.get("msgtime")
    timestamp = _parse_timestamp(raw)
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(BEIJING_TZ).isoformat(timespec="seconds")


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    raw = _string(value)
    if not raw:
        return None
    if raw.isdigit():
        number = float(raw)
        return number / 1000.0 if number > 10_000_000_000 else number
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _normalize_role(value: Any) -> str:
    raw = _string(value).lower()
    if raw in {"customer", "user", "external", "incoming", "in", "received", "receive", "contact"}:
        return "customer"
    if raw in {"assistant", "staff", "service", "cs", "internal", "outgoing", "out", "sent", "send", "bot", "agent"}:
        return "assistant"
    return raw or "unknown"


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return _drop_empty({key: source.get(key) for key in keys if key in source})


def _payment_fact(state: dict[str, Any], *, customer_context: dict[str, Any], sent_summary: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            **_pick(customer_context, "deposit_state", "payment_state", "paid_protection_status"),
            "state_payment_state": state.get("payment_state"),
            "state_deposit_state": state.get("deposit_state"),
            "payment_collection": sent_summary.get("payment_collection"),
            "payment_collection_sent": sent_summary.get("payment_collection_sent"),
            "source": "customer_context_and_sent_message_summary",
        }
    )


def _registration_fact(basic: dict[str, Any]) -> dict[str, Any]:
    phone = _string(basic.get("phone"))
    return _drop_empty(
        {
            "registration_state": basic.get("registration_state"),
            "customer_name": basic.get("customer_name"),
            "phone_present": bool(phone),
            "source": "customer_basic_info",
        }
    )


def _compact_orders(value: Any) -> list[dict[str, Any]]:
    orders = value if isinstance(value, list) else []
    output: list[dict[str, Any]] = []
    for order in orders[:MAX_FACT_ITEMS]:
        if not isinstance(order, dict):
            continue
        output.append(
            _drop_empty(
                {
                    "order_id": order.get("order_id") or order.get("id"),
                    "store_id": order.get("store_id"),
                    "store_name": order.get("store_name"),
                    "deposit_state": order.get("deposit_state"),
                    "paid_protection_status": order.get("paid_protection_status"),
                    "status": order.get("status"),
                    "created_at": order.get("created_at") or order.get("create_time"),
                    "time_source": order.get("time_source"),
                }
            )
        )
    return output


def _structured_message_facts(sent_summary: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "payment_collection": sent_summary.get("payment_collection"),
            "case_image_delivery": sent_summary.get("case_image_delivery"),
            "activity_intro_image_sent": sent_summary.get("activity_intro_image_sent"),
            "store_address_delivery": sent_summary.get("store_address_delivery"),
            "store_anchor_fact": sent_summary.get("store_anchor_fact"),
            "source": "sent_message_summary",
        }
    )


def _recent_event_types(history_events: list[Any]) -> list[str]:
    event_types: list[str] = []
    for event in history_events[-MAX_FACT_ITEMS:]:
        if not isinstance(event, dict):
            continue
        event_type = _string(event.get("event_type"))
        if event_type:
            event_types.append(event_type)
    return event_types


def _source_counts(messages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        source = _string(message.get("source") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    return counts


def _time_status(sent_at: Any) -> str:
    return "known" if _string(sent_at) else "missing"


def _string(value: Any) -> str:
    return str(value or "").strip()


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
