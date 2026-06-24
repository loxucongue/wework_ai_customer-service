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
from app.services.platform_agent_client import PlatformAgentClient


class CustomerContextService:
    """Replace this class with the real customer-system adapter later."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client
        self._identity_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._orders_cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = Lock()
        self._identity_ttl_seconds = 30 * 60
        self._orders_ttl_seconds = 30

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

    def _load_from_platform(self, *, customer_id: str, request_context: dict[str, Any]) -> dict[str, Any]:
        assert self._platform_client is not None
        identity = self.load_identity(customer_id=customer_id, request_context=request_context)
        return self._context_from_identity(customer_id=customer_id, memory={}, request_context=request_context, identity=identity)

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
            "orders": [compact_order(order) for order in orders[:5]],
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
        if not self._platform_client or not self._platform_client.available or not platform_customer_id:
            return [], "", False
        bypass_cache = any(request_context.get(key) not in (None, "") for key in ("appointment_id", "store_id", "appointment_time"))
        key = self._orders_cache_key(platform_customer_id, request_context)
        if not bypass_cache:
            cached = self._get_cached(self._orders_cache, key, self._orders_ttl_seconds)
            if isinstance(cached, dict):
                return list(cached.get("orders") or []), str(cached.get("error") or ""), True
        try:
            orders = self._platform_client.list_orders(customer_id=platform_customer_id, page=1, limit=10, request_context=request_context)
            self._set_cached(self._orders_cache, key, {"orders": [dict(order) for order in orders], "error": ""})
            return orders, "", False
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._set_cached(self._orders_cache, key, {"orders": [], "error": error})
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
        ttl = self._identity_ttl_seconds if cache is self._identity_cache else self._orders_ttl_seconds
        with self._cache_lock:
            cache[key] = (time.monotonic() + ttl, value)

    @staticmethod
    def _identity_cache_key(request_context: dict[str, Any]) -> str:
        parts = [
            request_context.get("corp_id"),
            request_context.get("external_userid"),
            request_context.get("user_id"),
            request_context.get("wechat"),
        ]
        return "|".join(str(part or "") for part in parts)

    @staticmethod
    def _orders_cache_key(platform_customer_id: str, request_context: dict[str, Any]) -> str:
        parts = [
            platform_customer_id,
            request_context.get("corp_id"),
            request_context.get("user_id"),
            request_context.get("wechat"),
        ]
        return "|".join(str(part or "") for part in parts)
