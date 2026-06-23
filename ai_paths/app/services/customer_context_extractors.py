from __future__ import annotations

from typing import Any

from app.services.customer_appointment_context import (
    appointment_from_memory,
    appointment_from_request_context,
    empty_appointment,
    normalize_appointment,
)
from app.services.customer_order_context import (
    appointment_from_orders,
    compact_order,
    order_project_names,
    order_status_text,
)


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


def compact_request_context(request_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_customer_id": request_context.get("input_customer_id"),
        "platform_customer_id": request_context.get("platform_customer_id"),
        "customer_id": request_context.get("customer_id"),
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
