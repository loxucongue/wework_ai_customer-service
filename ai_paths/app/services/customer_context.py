from __future__ import annotations

from typing import Any

from app.services.customer_context_extractors import (
    appointment_from_memory,
    appointment_from_orders,
    appointment_from_request_context,
    compact_customer,
    compact_order,
    compact_request_context,
)
from app.services.platform_agent_client import PlatformAgentClient


class CustomerContextService:
    """Replace this class with the real customer-system adapter later."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client

    def load(
        self,
        *,
        customer_id: str,
        memory: dict[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._platform_client and self._platform_client.available:
            try:
                platform_context = self._load_from_platform(customer_id=customer_id, request_context=request_context)
                if platform_context:
                    return platform_context
            except Exception as exc:
                appointment = appointment_from_request_context(request_context) or appointment_from_memory(memory)
                return {
                    "customer_id": customer_id,
                    "source": "local_memory_fallback",
                    "appointment": appointment,
                    "request_context": compact_request_context(request_context),
                    "error": f"{type(exc).__name__}: {exc}",
                }

        appointment = appointment_from_request_context(request_context) or appointment_from_memory(memory)
        return {
            "customer_id": customer_id,
            "source": "local_memory_placeholder",
            "appointment": appointment,
            "request_context": compact_request_context(request_context),
        }

    def _load_from_platform(self, *, customer_id: str, request_context: dict[str, Any]) -> dict[str, Any]:
        assert self._platform_client is not None
        info = {}
        if request_context.get("external_userid"):
            info = self._platform_client.get_customer_info(
                user_id=request_context.get("user_id"),
                corp_id=request_context.get("corp_id"),
                wechat=request_context.get("wechat"),
                external_userid=request_context.get("external_userid"),
            )
        if not info and request_context.get("customer_id"):
            info = {
                "id": request_context.get("customer_id"),
                "customer_add_wechat_id": request_context.get("customer_add_wechat_id"),
            }
        if not info.get("id"):
            return {}
        platform_customer_id = str(info.get("id") or customer_id or "")
        orders = self._platform_client.list_orders(customer_id=platform_customer_id, page=1, limit=10, request_context=request_context)
        appointment = appointment_from_request_context(request_context) or appointment_from_orders(orders)
        return {
            "customer_id": platform_customer_id,
            "source": "platform_agent",
            "customer": compact_customer(info),
            "appointment": appointment,
            "orders": [compact_order(order) for order in orders[:5]],
            "request_context": compact_request_context(request_context),
        }
