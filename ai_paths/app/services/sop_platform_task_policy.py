from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.customer_order_context import order_status_text


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PERSONALIZED_ORDER_STATUSES = {"pending"}


def personalized_order_eligibility(customer_context: dict[str, Any]) -> dict[str, Any]:
    """Return the factual order gate used before an automatic personalized send."""
    if not isinstance(customer_context, dict):
        return {
            "available": False,
            "eligible": False,
            "reason": "customer_context_missing",
            "order_status": "unknown",
        }
    error = str(customer_context.get("orders_error") or customer_context.get("error") or "").strip()
    if error:
        return {
            "available": False,
            "eligible": False,
            "reason": "platform_order_context_unavailable",
            "order_status": "unknown",
            "error": error,
        }
    if str(customer_context.get("source") or "") != "platform_agent":
        return {
            "available": False,
            "eligible": False,
            "reason": "platform_order_context_unavailable",
            "order_status": "unknown",
        }
    order = _current_order(customer_context)
    order_status = order_status_text(order.get("status")) if order else "no_order"
    return {
        "available": True,
        "eligible": order_status == "no_order" or order_status in PERSONALIZED_ORDER_STATUSES,
        "reason": (
            "still_spoken_without_booked_order"
            if order_status == "no_order" or order_status in PERSONALIZED_ORDER_STATUSES
            else "order_state_changed"
        ),
        "order_status": order_status,
        "order_id": str(order.get("id") or order.get("order_id") or "") if order else "",
    }


def classify_platform_task_route(
    *,
    payload: dict[str, Any],
    customer: dict[str, Any],
    conversation_activity: dict[str, Any],
    customer_context: dict[str, Any],
    customer_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify platform tasks using only time, conversation and order facts."""
    day = _event_day(payload, customer)
    memory = customer_memory if isinstance(customer_memory, dict) else {}
    recent_customer_count = int(conversation_activity.get("real_customer_message_count") or 0)
    remembered_customer_message_at = str(memory.get("last_customer_message_at") or "").strip()
    has_spoken = recent_customer_count > 0 or bool(remembered_customer_message_at)
    order_gate = (
        customer_context.get("_sop_order_gate")
        if isinstance(customer_context.get("_sop_order_gate"), dict)
        else {}
    )
    if str(order_gate.get("status") or "").strip().lower() == "failed":
        return {
            "route": "defer",
            "reason": "platform_order_context_unavailable",
            "day": day,
            "has_spoken": has_spoken,
            "order": {},
            "order_gate": order_gate,
        }

    order = _current_order(customer_context)
    order_status = order_status_text(order.get("status")) if order else "no_order"
    evidence = {
        "day": day,
        "has_spoken": has_spoken,
        "has_spoken_sources": {
            "recent_conversation": recent_customer_count > 0,
            "persisted_customer_message_time": bool(remembered_customer_message_at),
        },
        "real_customer_message_count": recent_customer_count,
        "latest_customer_message_at": str(conversation_activity.get("latest_customer_message_at") or ""),
        "remembered_customer_message_at": remembered_customer_message_at,
        "order": {
            "order_id": str(order.get("id") or order.get("order_id") or ""),
            "status": order_status,
            "is_current_order": bool(order.get("is_current_order")),
        }
        if order
        else {"status": "no_order"},
        "order_gate": order_gate,
    }

    if day == "day1":
        return {
            "route": "suppress_day1",
            "reason": "day1_uses_ai_reply_and_friend_added_events",
            **evidence,
        }
    if day != "day2_plus":
        return {
            "route": "direct",
            "reason": "day_stage_unconfirmed_keep_platform_delivery",
            **evidence,
        }
    if not has_spoken:
        return {
            "route": "direct",
            "reason": "day2_plus_never_spoke_uses_platform_sop",
            **evidence,
        }
    if order_status == "no_order" or order_status in PERSONALIZED_ORDER_STATUSES:
        return {
            "route": "personalized",
            "reason": "day2_plus_spoken_without_booked_order",
            **evidence,
        }
    return {
        "route": "direct",
        "reason": "day2_plus_existing_order_uses_platform_sop",
        **evidence,
    }


def _event_day(payload: dict[str, Any], customer: dict[str, Any]) -> str:
    customer_sop = customer.get("sop") if isinstance(customer.get("sop"), dict) else {}
    root_sop = payload.get("sop") if isinstance(payload.get("sop"), dict) else {}
    stage = str(customer_sop.get("day_stage") or root_sop.get("day_stage") or "").strip().lower()
    if stage in {"day1", "d1", "1", "当天", "第一天"}:
        return "day1"
    if stage and stage not in {"unknown", "none", "null"}:
        return "day2_plus"

    first_added = customer.get("first_added_event") if isinstance(customer.get("first_added_event"), dict) else {}
    first_added_at = _parse_time(
        first_added.get("timestamp") or first_added.get("created_at") or first_added.get("time")
    )
    event_at = _parse_time(payload.get("created_at") or payload.get("upstream_created_at"))
    if not first_added_at or not event_at:
        return "unknown"
    first_date = first_added_at.astimezone(BEIJING_TZ).date()
    event_date = event_at.astimezone(BEIJING_TZ).date()
    return "day1" if event_date <= first_date else "day2_plus"


def _current_order(customer_context: dict[str, Any]) -> dict[str, Any]:
    orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    records = [item for item in orders if isinstance(item, dict)]
    return next((item for item in records if item.get("is_current_order")), records[0] if records else {})


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=BEIJING_TZ)
