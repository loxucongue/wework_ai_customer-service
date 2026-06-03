from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StorePlatformContext:
    request_context: dict[str, Any]
    customer_id: Any
    customer_add_wechat_id: Any


def store_platform_context(customer_context: dict[str, Any]) -> StorePlatformContext:
    request_context = request_context_from_customer_context(customer_context)
    customer = customer_context.get("customer") if isinstance(customer_context, dict) else {}
    if not isinstance(customer, dict):
        customer = {}
    return StorePlatformContext(
        request_context=request_context,
        customer_id=customer.get("id") or customer_context.get("customer_id") or request_context.get("customer_id"),
        customer_add_wechat_id=(
            customer.get("customer_add_wechat_id")
            or request_context.get("customer_add_wechat_id")
        ),
    )


def request_context_from_customer_context(customer_context: dict[str, Any]) -> dict[str, Any]:
    value = customer_context.get("request_context") if isinstance(customer_context, dict) else {}
    return value if isinstance(value, dict) else {}
