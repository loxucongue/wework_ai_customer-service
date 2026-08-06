from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
MAX_FULL_TIMELINE_MESSAGES = 100
MAX_SHADOW_CONTENT_CHARS = 700


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
            "authoritative_facts": _authoritative_facts(
                state,
                identity=identity,
                customer_result=customer_result,
                store_knowledge=store_knowledge,
                conversation_result=conversation_result,
                memory=memory,
            ),
            "excluded_as_authority": [
                "customer_profile.next_sales_strategy",
                "customer_profile.decision_stage",
                "customer_profile.main_concern_as_current_intent",
                "stale_history_event_psychology",
            ],
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
            "source": "conversation_history",
        }
    )


def _current_message(state: dict[str, Any], *, request_context: dict[str, Any], index: int) -> dict[str, Any]:
    content = _string(state.get("normalized_content") or state.get("content"))
    if not content:
        return {}
    return _drop_empty(
        {
            "message_ref": _string(request_context.get("msgid")) or f"current_{index:03d}",
            "sender": "customer",
            "message_type": _string(request_context.get("msgtype") or "text") or "text",
            "content": content[:MAX_SHADOW_CONTENT_CHARS],
            "sent_at": _message_time_from_context(request_context),
            "source": "current_request",
        }
    )


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
    return _drop_empty(
        {
            "payment": _pick(customer_context, "deposit_state", "payment_state", "paid_protection_status"),
            "orders": {
                "count": len(customer_context.get("orders") or []) if isinstance(customer_context.get("orders"), list) else 0,
                "source": customer_context.get("source") or "platform_order_index",
                "error": customer_dict.get("customer_context_error") or customer_dict.get("orders_error"),
            },
            "registration": _pick(basic, "registration_state", "customer_name", "phone"),
            "visible_store_scope": {
                "store_count": len(store_dict.get("stores") or []) if isinstance(store_dict.get("stores"), list) else 0,
                "source": store_dict.get("source"),
                "error": store_dict.get("error"),
            },
            "sop_delivery": {
                "history_event_count": len(memory_dict.get("history_events") or [])
                if isinstance(memory_dict.get("history_events"), list)
                else 0,
            },
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
