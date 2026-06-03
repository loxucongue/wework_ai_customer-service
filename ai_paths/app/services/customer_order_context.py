from __future__ import annotations

from typing import Any

from app.services.platform_agent_client import unix_to_text


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
        from app.services.customer_context_extractors import empty_appointment

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
