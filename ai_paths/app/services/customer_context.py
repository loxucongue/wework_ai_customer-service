from __future__ import annotations

import re
from typing import Any

from app.config import Settings
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

    def __init__(
        self,
        platform_client: PlatformAgentClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._platform_client = platform_client
        self._test_mode_enabled = bool(settings.platform_agent_test_mode_enabled) if settings else False
        self._test_external_userid = str(settings.platform_agent_test_external_userid or "").strip() if settings else ""
        self._test_order_scene_only = bool(settings.platform_agent_test_order_scene_only) if settings else True

    def load(
        self,
        *,
        customer_id: str,
        memory: dict[str, Any],
        request_context: dict[str, Any],
        current_message: str = "",
    ) -> dict[str, Any]:
        if self._platform_client and self._platform_client.available:
            try:
                platform_context = self._load_from_platform(
                    customer_id=customer_id,
                    request_context=request_context,
                    current_message=current_message,
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

    def _load_from_platform(
        self,
        *,
        customer_id: str,
        request_context: dict[str, Any],
        current_message: str,
    ) -> dict[str, Any]:
        assert self._platform_client is not None
        info: dict[str, Any] = {}
        info_source = ""
        incoming_external_userid = str(request_context.get("external_userid") or "").strip()
        incoming_error = ""
        if incoming_external_userid:
            try:
                info = self._platform_client.get_customer_info(
                    user_id=request_context.get("user_id"),
                    corp_id=request_context.get("corp_id"),
                    wechat=request_context.get("wechat"),
                    external_userid=incoming_external_userid,
                )
                if info.get("id"):
                    info_source = "incoming_external_userid"
            except Exception as exc:
                incoming_error = f"{type(exc).__name__}: {exc}"
        allow_shared_test_customer = self._should_use_shared_test_customer(current_message)
        if (
            self._test_mode_enabled
            and allow_shared_test_customer
            and self._test_external_userid
            and self._test_external_userid != incoming_external_userid
            and not info.get("id")
        ):
            try:
                info = self._platform_client.get_customer_info(
                    user_id=request_context.get("user_id"),
                    corp_id=request_context.get("corp_id"),
                    wechat=request_context.get("wechat"),
                    external_userid=self._test_external_userid,
                )
                if info.get("id"):
                    info_source = "shared_test_external_userid"
            except Exception as exc:
                if not incoming_error:
                    incoming_error = f"{type(exc).__name__}: {exc}"
        if not info and request_context.get("customer_id") and not self._test_mode_enabled:
            info = {
                "id": request_context.get("customer_id"),
                "customer_add_wechat_id": request_context.get("customer_add_wechat_id"),
            }
            info_source = "request_context_fallback"
        if not info.get("id"):
            if incoming_error:
                return {
                    "customer_id": customer_id,
                    "source": "local_memory_fallback",
                    "appointment": appointment_from_request_context(request_context),
                    "request_context": compact_request_context(request_context),
                    "error": incoming_error,
                }
            return {}
        platform_customer_id = str(info.get("id") or customer_id or "")
        orders = self._platform_client.list_orders(customer_id=platform_customer_id, page=1, limit=10, request_context=request_context)
        appointment = appointment_from_request_context(request_context) or appointment_from_orders(orders)
        return {
            "customer_id": platform_customer_id,
            "source": "platform_agent",
            "platform_identity_source": info_source,
            "customer": compact_customer(info),
            "appointment": appointment,
            "orders": [compact_order(order) for order in orders[:5]],
            "request_context": compact_request_context(request_context),
        }

    def _should_use_shared_test_customer(self, current_message: str) -> bool:
        if not self._test_mode_enabled or not self._test_external_userid:
            return False
        if not self._test_order_scene_only:
            return True
        return is_platform_order_scene(current_message)


def is_platform_order_scene(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if re.fullmatch(r"1[3-9]\d{9}", text):
        return True
    if re.fullmatch(r"[\u4e00-\u9fa5]{2,6}", text):
        return True
    if re.search(r"\d{1,2}\s*[:：]\s*\d{2}", text):
        return True
    keywords = [
        "预约",
        "改约",
        "取消",
        "定金",
        "预约金",
        "付款",
        "收款",
        "订单",
        "开单",
        "可约",
        "有时间",
        "几点",
        "明天",
        "后天",
        "今天下午",
        "今天上午",
        "下午",
        "上午",
        "到店",
        "床位",
        "排期",
    ]
    for term in keywords:
        if term in text:
            return True
    return False
