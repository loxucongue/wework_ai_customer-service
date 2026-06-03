from __future__ import annotations

from typing import Any

from app.services.platform_agent_client import unix_to_text


def empty_appointment(source: str = "none") -> dict[str, Any]:
    return {
        "has_active": False,
        "status": "none",
        "store_id": "",
        "store_name": "",
        "appointment_time": "",
        "summary": "",
        "source": source,
    }


def appointment_from_memory(memory: dict[str, Any]) -> dict[str, Any]:
    basic = memory.get("basic_info", {}) if isinstance(memory, dict) else {}
    if isinstance(basic, dict):
        direct = basic.get("appointment") or basic.get("appointment_info")
        if isinstance(direct, dict):
            return normalize_appointment(direct, source="memory.basic_info")

    events = memory.get("history_events", []) if isinstance(memory, dict) else []
    if not isinstance(events, list):
        events = []

    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        facts = event.get("facts", {})
        if event_type == "appoint_confirm" and isinstance(facts, dict):
            return normalize_appointment(facts, status="confirmed", source="memory.history_events")

    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        facts = event.get("facts", {})
        if event_type == "appoint_intent" and isinstance(facts, dict):
            return normalize_appointment(facts, status="intent_only", source="memory.history_events")

    return empty_appointment()


def appointment_from_request_context(request_context: dict[str, Any]) -> dict[str, Any]:
    store_id = str(request_context.get("confirmed_store_id") or request_context.get("store_id") or "").strip()
    store_name = str(request_context.get("confirmed_store_name") or request_context.get("store_name") or "").strip()
    appointment_id = str(request_context.get("appointment_id") or "").strip()
    appointment_time = str(request_context.get("appointment_time") or "").strip()
    has_active = bool(appointment_id or appointment_time)
    if not (store_id or store_name or has_active):
        return {}
    summary_bits = [store_name, appointment_time]
    return {
        "has_active": has_active,
        "status": "confirmed" if has_active else "context_store_only",
        "appointment_id": appointment_id,
        "store_id": store_id,
        "store_name": store_name,
        "appointment_time": appointment_time,
        "summary": " ".join(bit for bit in summary_bits if bit),
        "source": "request_context",
    }


def normalize_appointment(value: dict[str, Any], *, source: str, status: str | None = None) -> dict[str, Any]:
    store_name = str(value.get("store_name") or value.get("store") or value.get("preferred_store") or "")
    store_id = str(value.get("store_id") or value.get("confirmed_store_id") or "")
    appointment_time = str(
        value.get("appointment_time")
        or value.get("time")
        or value.get("preferred_time")
        or value.get("date")
        or ""
    )
    normalized_status = str(status or value.get("status") or "")
    if not normalized_status:
        normalized_status = "confirmed" if appointment_time or store_name or store_id else "unknown"
    has_active = normalized_status in {"confirmed", "scheduled", "active"} or bool(
        value.get("has_active") or value.get("has_active_appointment")
    )
    summary_bits = []
    if store_name:
        summary_bits.append(store_name)
    if appointment_time:
        summary_bits.append(appointment_time)
    return {
        "has_active": bool(has_active),
        "status": normalized_status,
        "store_id": store_id,
        "store_name": store_name,
        "appointment_time": appointment_time,
        "summary": " ".join(summary_bits),
        "source": source,
    }


def appointment_from_orders(orders: list[dict[str, Any]]) -> dict[str, Any]:
    active_statuses = {1, 2, 3}
    active_orders = []
    for order in orders:
        try:
            status = int(order.get("status", -1))
        except (TypeError, ValueError):
            status = -1
        if status in active_statuses:
            active_orders.append(order)
    if not active_orders:
        return empty_appointment(source="platform_agent.order_index")
    order = sorted(active_orders, key=lambda item: int(item.get("plan_at") or item.get("created_at") or 0), reverse=True)[0]
    appointment_time = unix_to_text(order.get("plan_at")) or unix_to_text(order.get("pre_plan_at"))
    status = order_status_text(order.get("status"))
    store_name = str(order.get("store_name") or "")
    project_names = order_project_names(order)
    summary_bits = [store_name, appointment_time, "、".join(project_names[:2])]
    return {
        "has_active": True,
        "status": status,
        "order_id": str(order.get("id") or ""),
        "order_no": str(order.get("order_no") or ""),
        "store_id": str(order.get("store_id") or ""),
        "store_name": store_name,
        "appointment_time": appointment_time,
        "projects": project_names,
        "summary": " ".join(bit for bit in summary_bits if bit),
        "source": "platform_agent.order_index",
    }


def compact_customer(info: dict[str, Any]) -> dict[str, Any]:
    if not info:
        return {}
    return {
        "id": info.get("id"),
        "name": info.get("name") or info.get("nickname") or "",
        "kind": info.get("kind"),
        "customer_add_wechat_id": info.get("customer_add_wechat_id"),
        "category_id": info.get("category_id"),
    }


def compact_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order.get("id"),
        "order_no": order.get("order_no"),
        "status": order_status_text(order.get("status")),
        "store_id": order.get("store_id"),
        "store_name": order.get("store_name"),
        "appointment_time": unix_to_text(order.get("plan_at")),
        "store_at": unix_to_text(order.get("store_at")),
        "projects": order_project_names(order),
    }


def order_project_names(order: dict[str, Any]) -> list[str]:
    source = order.get("plans") or order.get("buys") or []
    if not isinstance(source, list):
        return []
    result = []
    for item in source:
        if isinstance(item, dict) and item.get("product_name"):
            name = str(item.get("product_name")).strip()
            if name and name not in result:
                result.append(name)
    return result


def order_status_text(value: Any) -> str:
    try:
        status = int(value)
    except (TypeError, ValueError):
        return str(value or "unknown")
    return {
        0: "lost_refunded",
        1: "pending",
        2: "waiting_schedule",
        3: "scheduled",
        4: "timeout",
        5: "visited",
        6: "finished",
        7: "evaluated",
        8: "cancelled",
    }.get(status, "unknown")


def compact_request_context(request_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": request_context.get("user_id"),
        "corp_id": request_context.get("corp_id"),
        "wechat": request_context.get("wechat"),
        "external_userid": request_context.get("external_userid"),
        "customer_add_wechat_id": request_context.get("customer_add_wechat_id"),
        "confirmed_store_id": request_context.get("confirmed_store_id"),
        "confirmed_store_name": request_context.get("confirmed_store_name"),
        "store_id": request_context.get("store_id"),
        "store_name": request_context.get("store_name"),
        "appointment_id": request_context.get("appointment_id"),
    }
