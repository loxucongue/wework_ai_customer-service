from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.graph.nodes.appointment_time_utils import available_time_values
from app.services.customer_order_context import order_status_text
from app.services.platform_agent_client import PlatformAgentClient


@dataclass(frozen=True)
class AppointmentScheduleService:
    """Apply schedule, reschedule, and cancel actions for existing appointment orders."""

    platform_client: PlatformAgentClient | None = None

    def maybe_apply(
        self,
        *,
        content: str,
        state: dict[str, Any],
        appointment_query: dict[str, Any],
        available_time: dict[str, Any] | None,
    ) -> dict[str, Any]:
        facts = _appointment_action_facts(state, appointment_query, available_time or {}, self.platform_client)
        operation = _detect_operation(content, state)
        if not operation:
            return {"status": "not_applicable", "facts": facts}
        if not self.platform_client or not self.platform_client.available:
            return {"status": "platform_unavailable", "operation": operation, "facts": facts}

        if operation == "schedule":
            missing = _missing_schedule_fields(facts)
            if missing:
                return {"status": "missing_info", "operation": operation, "facts": facts, "missing": missing}
            if facts.get("preferred_time_available") is False:
                return {"status": "preferred_time_unavailable", "operation": operation, "facts": facts}
            if not _customer_confirmed_schedule(content):
                return {"status": "needs_customer_confirmation", "operation": operation, "facts": facts}
            if _truthy(_request_context(state).get("appointment_schedule_dry_run")):
                return {"status": "dry_run_scheduled", "operation": operation, "facts": facts, "result": {"dry_run": True}}
            try:
                result = self.platform_client.create_order_plan(
                    store_id=facts["store_id"],
                    date=facts["planned_at"],
                    order_id=facts["order_id"],
                    user_id=facts["user_id"],
                    note=_schedule_note(facts),
                    request_context=_request_context(state),
                )
            except Exception as exc:
                return {
                    "status": "error",
                    "operation": operation,
                    "facts": facts,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return {"status": "scheduled", "operation": operation, "facts": facts, "result": result}

        if operation == "change":
            missing = _missing_change_fields(facts)
            if missing:
                return {"status": "missing_info", "operation": operation, "facts": facts, "missing": missing}
            if facts.get("preferred_time_available") is False:
                return {"status": "preferred_time_unavailable", "operation": operation, "facts": facts}
            if _truthy(_request_context(state).get("appointment_schedule_dry_run")):
                return {"status": "dry_run_changed", "operation": operation, "facts": facts, "result": {"dry_run": True}}
            try:
                result = self.platform_client.change_order_plan_time(
                    order_id=facts["order_id"],
                    date=facts["planned_at"],
                    user_id=facts["user_id"],
                    request_context=_request_context(state),
                )
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                if _is_change_contract_error(error_text):
                    return {
                        "status": "platform_contract_error",
                        "operation": operation,
                        "facts": facts,
                        "error": error_text,
                        "reason": "改约接口真实参数与当前文档不一致",
                    }
                return {
                    "status": "error",
                    "operation": operation,
                    "facts": facts,
                    "error": error_text,
                }
            return {"status": "changed", "operation": operation, "facts": facts, "result": result}

        if operation == "cancel":
            missing = _missing_cancel_fields(facts)
            if missing:
                return {"status": "missing_info", "operation": operation, "facts": facts, "missing": missing}
            if _truthy(_request_context(state).get("appointment_schedule_dry_run")):
                return {"status": "dry_run_cancelled", "operation": operation, "facts": facts, "result": {"dry_run": True}}
            try:
                result = self.platform_client.cancel_order_plan(
                    order_id=facts["order_id"],
                    user_id=facts["user_id"],
                    request_context=_request_context(state),
                )
            except Exception as exc:
                return {
                    "status": "error",
                    "operation": operation,
                    "facts": facts,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return {"status": "cancelled", "operation": operation, "facts": facts, "result": result}

        return {"status": "not_applicable", "facts": facts}


def _appointment_action_facts(
    state: dict[str, Any],
    appointment_query: dict[str, Any],
    available_time: dict[str, Any],
    platform_client: PlatformAgentClient | None,
) -> dict[str, Any]:
    request_context = _request_context(state)
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    appointment = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    active_task = state.get("active_task") if isinstance(state.get("active_task"), dict) else {}
    known_slots = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
    orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    current_order = _current_order(orders, appointment, request_context)
    user_id = str(request_context.get("user_id") or state.get("user_id") or "").strip()
    if not user_id and platform_client and platform_client.available:
        user_id = str(platform_client.effective_identity(request_context).get("user_id") or "").strip()
    preferred_time = str(known_slots.get("visit_time") or _extract_time(state.get("normalized_content") or "")).strip()
    slots = available_time_values(available_time.get("slots") or {}) if isinstance(available_time, dict) else []
    store_id = str(
        appointment_query.get("store_id")
        or request_context.get("confirmed_store_id")
        or request_context.get("store_id")
        or appointment.get("store_id")
        or current_order.get("store_id")
        or ""
    ).strip()
    store_name = str(
        appointment_query.get("store_name")
        or request_context.get("confirmed_store_name")
        or request_context.get("store_name")
        or appointment.get("store_name")
        or current_order.get("store_name")
        or ""
    ).strip()
    return {
        "order_id": str(
            request_context.get("appointment_id")
            or state.get("appointment_id")
            or appointment.get("order_id")
            or current_order.get("id")
            or ""
        ).strip(),
        "order_status": str(current_order.get("status") or appointment.get("status") or "").strip(),
        "customer_id": str(customer_context.get("customer_id") or request_context.get("customer_id") or "").strip(),
        "customer_add_wechat_id": str(
            request_context.get("customer_add_wechat_id")
            or state.get("customer_add_wechat_id")
            or (customer_context.get("customer") or {}).get("customer_add_wechat_id")
            or ""
        ).strip(),
        "user_id": user_id,
        "store_id": store_id,
        "store_name": store_name,
        "date": str(appointment_query.get("date") or known_slots.get("visit_date_value") or "").strip(),
        "time": preferred_time,
        "planned_at": _planned_at_text(
            str(appointment_query.get("date") or known_slots.get("visit_date_value") or "").strip(),
            preferred_time,
        ),
        "preferred_time_available": preferred_time in slots if preferred_time and slots else None,
        "available_time_slots": slots[:12],
    }


def _request_context(state: dict[str, Any]) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    context = customer_context.get("request_context") if isinstance(customer_context.get("request_context"), dict) else {}
    merged = dict(context)
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    merged.update({key: value for key, value in request_context.items() if value not in (None, "")})
    return merged


def _current_order(
    orders: list[dict[str, Any]],
    appointment: dict[str, Any],
    request_context: dict[str, Any],
) -> dict[str, Any]:
    appointment_order_id = str(appointment.get("order_id") or request_context.get("appointment_id") or "").strip()
    if appointment_order_id:
        for order in orders:
            if isinstance(order, dict) and str(order.get("id") or "").strip() == appointment_order_id:
                return order
    active_statuses = {"pending", "waiting_schedule", "scheduled"}
    for order in orders:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status") or "").strip()
        if status in active_statuses:
            return order
    return orders[0] if orders and isinstance(orders[0], dict) else {}


def _detect_operation(content: str, state: dict[str, Any]) -> str:
    route_result = state.get("route_result") if isinstance(state.get("route_result"), dict) else {}
    intent = str(route_result.get("intent") or "").strip()
    text = str(content or "").strip()
    if intent == "appointment_cancel" or any(term in text for term in ["取消预约", "帮我取消", "不去了", "先取消", "取消一下"]):
        return "cancel"
    if intent == "appointment_change" or any(term in text for term in ["改约", "改时间", "换个时间", "改到", "换到"]):
        return "change"
    if intent not in {"appointment_intent", "appointment_confirm"}:
        return ""
    if not text:
        return ""
    positive_terms = ["可以", "行", "好的", "确认", "就这个", "那就", "安排", "约这个", "这个时间"]
    if any(term in text for term in positive_terms) or _extract_time(text):
        return "schedule"
    return ""


def _customer_confirmed_schedule(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    negative_terms = ["取消", "不去", "不用", "算了", "退款", "投诉"]
    if any(term in text for term in negative_terms):
        return False
    if _extract_time(text):
        return True
    return any(term in text for term in ["可以", "行", "好的", "确认", "就这个", "那就", "安排", "约这个"])


def _missing_schedule_fields(facts: dict[str, Any]) -> list[str]:
    required = {
        "order_id": "订单ID",
        "store_id": "门店",
        "date": "到店日期",
        "time": "到店时间",
        "user_id": "员工ID",
    }
    return [label for key, label in required.items() if not facts.get(key)]


def _missing_change_fields(facts: dict[str, Any]) -> list[str]:
    required = {
        "order_id": "订单ID",
        "date": "到店日期",
        "time": "到店时间",
        "user_id": "员工ID",
    }
    return [label for key, label in required.items() if not facts.get(key)]


def _missing_cancel_fields(facts: dict[str, Any]) -> list[str]:
    required = {
        "order_id": "订单ID",
        "user_id": "员工ID",
    }
    return [label for key, label in required.items() if not facts.get(key)]


def _schedule_note(facts: dict[str, Any]) -> str:
    time_text = str(facts.get("time") or "").strip()
    status_text = order_status_text(facts.get("order_status"))
    suffix = f"，客户偏好{time_text}" if time_text else ""
    prefix = f"当前订单状态{status_text}" if status_text and status_text != "unknown" else "AI客服排客"
    return f"{prefix}{suffix}"


def _planned_at_text(date_text: str, time_text: str) -> str:
    date_value = str(date_text or "").strip()
    time_value = str(time_text or "").strip()
    if not date_value:
        return ""
    if not time_value:
        return date_value
    return f"{date_value} {time_value}"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_change_contract_error(error_text: str) -> bool:
    lowered = str(error_text or "").lower()
    return "changeplantime" in lowered and "array given" in lowered


def _extract_time(content: str) -> str:
    match = re.search(r"(\d{1,2})[:：](\d{2})", str(content or ""))
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    hour_match = re.search(r"(上午|下午|晚上|中午)?\s*(\d{1,2})\s*点", str(content or ""))
    if not hour_match:
        return ""
    prefix = hour_match.group(1) or ""
    hour = int(hour_match.group(2))
    if prefix in {"下午", "晚上"} and hour < 12:
        hour += 12
    if prefix == "中午" and hour < 11:
        hour += 12
    return f"{hour:02d}:00"
