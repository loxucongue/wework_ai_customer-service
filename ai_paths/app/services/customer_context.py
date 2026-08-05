from __future__ import annotations

import time
from threading import Lock
from typing import Any

from app.services.customer_context_extractors import (
    appointment_from_memory,
    appointment_from_orders,
    appointment_from_request_context,
    compact_customer,
    compact_order,
    compact_request_context,
)
from app.services.customer_payment_state import normalize_prepay_facts
from app.services.platform_agent_client import PlatformAgentClient


class CustomerContextService:
    """Replace this class with the real customer-system adapter later."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client
        self._identity_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_lock = Lock()
        self._identity_ttl_seconds = 30 * 60

    def load(
        self,
        *,
        customer_id: str,
        memory: dict[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._platform_client and self._platform_client.available:
            try:
                platform_context = self._load_from_platform(
                    customer_id=customer_id,
                    memory=memory,
                    request_context=request_context,
                )
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

    def load_identity(self, *, customer_id: str, request_context: dict[str, Any]) -> dict[str, Any]:
        info, cache_hit, error = self._load_customer_info(request_context)
        if not info and request_context.get("customer_id"):
            info = {
                "id": request_context.get("customer_id"),
                "customer_add_wechat_id": request_context.get("customer_add_wechat_id"),
            }
        platform_customer_id = str(info.get("id") or customer_id or "").strip()
        customer_add_wechat_id = str(info.get("customer_add_wechat_id") or "").strip()
        scoped_context = dict(request_context)
        scoped_context["input_customer_id"] = request_context.get("customer_id") or customer_id
        if platform_customer_id:
            scoped_context["platform_customer_id"] = platform_customer_id
        if customer_add_wechat_id:
            scoped_context["customer_add_wechat_id"] = customer_add_wechat_id
        return {
            "input_customer_id": request_context.get("customer_id") or customer_id,
            "platform_customer_id": platform_customer_id,
            "customer_add_wechat_id": customer_add_wechat_id,
            "external_userid": request_context.get("external_userid"),
            "customer_info": info,
            "request_context": scoped_context,
            "cache_hit": cache_hit,
            "error": error,
        }

    def load_with_identity(
        self,
        *,
        customer_id: str,
        memory: dict[str, Any],
        request_context: dict[str, Any],
        identity: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._platform_client or not self._platform_client.available:
            appointment = appointment_from_request_context(request_context) or appointment_from_memory(memory)
            return {
                "customer_id": customer_id,
                "source": "local_memory_placeholder",
                "appointment": appointment,
                "request_context": compact_request_context(request_context),
            }
        try:
            return self._context_from_identity(customer_id=customer_id, memory=memory, request_context=request_context, identity=identity)
        except Exception as exc:
            appointment = appointment_from_request_context(request_context) or appointment_from_memory(memory)
            return {
                "customer_id": customer_id,
                "source": "local_memory_fallback",
                "appointment": appointment,
                "request_context": compact_request_context(request_context),
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _load_from_platform(
        self,
        *,
        customer_id: str,
        memory: dict[str, Any],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        assert self._platform_client is not None
        identity = self.load_identity(customer_id=customer_id, request_context=request_context)
        return self._context_from_identity(
            customer_id=customer_id,
            memory=memory,
            request_context=request_context,
            identity=identity,
        )

    def _context_from_identity(
        self,
        *,
        customer_id: str,
        memory: dict[str, Any],
        request_context: dict[str, Any],
        identity: dict[str, Any],
    ) -> dict[str, Any]:
        assert self._platform_client is not None
        info = identity.get("customer_info") if isinstance(identity.get("customer_info"), dict) else {}
        if not info.get("id"):
            return {}
        platform_customer_id = str(identity.get("platform_customer_id") or info.get("id") or customer_id or "")
        scoped_context = dict(identity.get("request_context") or request_context)
        orders, orders_error, orders_cache_hit = self._load_orders(platform_customer_id, scoped_context)
        appointment = appointment_from_request_context(request_context) or appointment_from_orders(orders)
        compact_orders = _compact_orders_with_current_scope(
            orders,
            memory=memory,
            request_context=scoped_context,
            customer_info=info,
        )
        context = {
            "customer_id": platform_customer_id,
            "platform_customer_id": platform_customer_id,
            "customer_add_wechat_id": str(info.get("customer_add_wechat_id") or ""),
            "source": "platform_agent",
            "identity": {
                "input_customer_id": identity.get("input_customer_id") or request_context.get("customer_id") or customer_id,
                "platform_customer_id": platform_customer_id,
                "customer_add_wechat_id": info.get("customer_add_wechat_id"),
                "external_userid": request_context.get("external_userid"),
                "customer_info_cache_hit": bool(identity.get("cache_hit")),
            },
            "customer": compact_customer(info),
            "appointment": appointment,
            "orders": compact_orders,
            "request_context": compact_request_context(scoped_context),
            "cache": {
                "customer_info_hit": bool(identity.get("cache_hit")),
                "orders_hit": orders_cache_hit,
            },
        }
        if identity.get("error"):
            context["customer_info_error"] = identity.get("error")
        if orders_error:
            context["orders_error"] = orders_error
        return context

    def _load_customer_info(self, request_context: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
        if not self._platform_client or not self._platform_client.available:
            return {}, False, ""
        if not request_context.get("external_userid"):
            return {}, False, ""
        key = self._identity_cache_key(request_context)
        cached = self._get_cached(self._identity_cache, key, self._identity_ttl_seconds)
        if isinstance(cached, dict):
            return dict(cached), True, ""
        try:
            info = self._platform_client.get_customer_info(
                user_id=request_context.get("user_id"),
                corp_id=request_context.get("corp_id"),
                wechat=request_context.get("wechat"),
                external_userid=request_context.get("external_userid"),
            )
            if info:
                self._set_cached(self._identity_cache, key, dict(info))
            return info, False, ""
        except Exception as exc:
            return {}, False, f"{type(exc).__name__}: {exc}"

    def _load_orders(self, platform_customer_id: str, request_context: dict[str, Any]) -> tuple[list[dict[str, Any]], str, bool]:
        """Load current orders for every turn without reusing a payment-state cache."""
        if not self._platform_client or not self._platform_client.available or not platform_customer_id:
            return [], "", False
        try:
            orders = self._platform_client.list_orders(customer_id=platform_customer_id, page=1, limit=10, request_context=request_context)
            return orders, "", False
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            return [], error, False

    def _get_cached(self, cache: dict[str, tuple[float, Any]], key: str, ttl_seconds: int) -> Any:
        if not key:
            return None
        now = time.monotonic()
        with self._cache_lock:
            item = cache.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at <= now:
                cache.pop(key, None)
                return None
            return value

    def _set_cached(self, cache: dict[str, tuple[float, Any]], key: str, value: Any) -> None:
        if not key:
            return
        with self._cache_lock:
            cache[key] = (time.monotonic() + self._identity_ttl_seconds, value)

    @staticmethod
    def _identity_cache_key(request_context: dict[str, Any]) -> str:
        parts = [
            request_context.get("corp_id"),
            request_context.get("external_userid"),
            request_context.get("user_id"),
            request_context.get("wechat"),
        ]
        return "|".join(str(part or "") for part in parts)


def _compact_orders_with_current_scope(
    orders: list[dict[str, Any]],
    *,
    memory: dict[str, Any],
    request_context: dict[str, Any],
    customer_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Mark and prioritize the one active order that belongs to the current customer flow."""
    active = [order for order in orders if isinstance(order, dict) and _is_active_order(order)]
    current = _select_current_order(
        active,
        memory=memory,
        request_context=request_context,
        customer_info=customer_info,
    )
    ordered = ([current] if current else []) + [order for order in orders if order is not current]
    result: list[dict[str, Any]] = []
    for order in ordered[:5]:
        if not isinstance(order, dict):
            continue
        compact = compact_order(order)
        if order is current:
            compact["is_current_order"] = True
        result.append(compact)
    return result


def _select_current_order(
    orders: list[dict[str, Any]],
    *,
    memory: dict[str, Any],
    request_context: dict[str, Any],
    customer_info: dict[str, Any],
) -> dict[str, Any]:
    """Select the saved order first, then the active order matching confirmed store and category."""
    basic = memory.get("basic_info") if isinstance(memory.get("basic_info"), dict) else {}
    order_state = basic.get("order_state") if isinstance(basic.get("order_state"), dict) else {}
    deposit_state = basic.get("deposit_state") if isinstance(basic.get("deposit_state"), dict) else {}
    target_order_id = str(
        request_context.get("order_id")
        or order_state.get("order_id")
        or deposit_state.get("order_id")
        or ""
    ).strip()
    if target_order_id:
        exact = next((order for order in orders if _order_id(order) == target_order_id), None)
        if exact:
            return exact

    target_store_id = str(
        request_context.get("confirmed_store_id")
        or order_state.get("store_id")
        or basic.get("confirmed_store_id")
        or ""
    ).strip()
    target_category_id = str(
        request_context.get("category_id")
        or order_state.get("category_id")
        or customer_info.get("category_id")
        or ""
    ).strip()
    scoped = [order for order in orders if _order_matches_scope(order, store_id=target_store_id, category_id=target_category_id)]
    if scoped:
        return scoped[0]
    return orders[0] if orders else {}


def _is_active_order(order: dict[str, Any]) -> bool:
    """Return whether a raw platform order can represent the current payment flow."""
    status = str(order.get("status") or "").strip().lower()
    if status not in {"1", "2", "3", "pending", "waiting_schedule", "scheduled"}:
        return False
    return normalize_prepay_facts(order).get("deposit_state") in {
        "required_unpaid",
        "paid_by_order",
    }


def _order_matches_scope(order: dict[str, Any], *, store_id: str, category_id: str) -> bool:
    """Match an active order to the confirmed store and compatible category."""
    if store_id and str(order.get("store_id") or "").strip() != store_id:
        return False
    actual_category = str(order.get("category_id") or "").strip().lower()
    expected_category = str(category_id or "").strip().lower()
    unspecified = {"", "0", "none", "null"}
    return expected_category in unspecified or actual_category in unspecified or expected_category == actual_category


def _order_id(order: dict[str, Any]) -> str:
    """Return a normalized platform order identifier."""
    return str(order.get("id") or order.get("order_id") or "").strip()
