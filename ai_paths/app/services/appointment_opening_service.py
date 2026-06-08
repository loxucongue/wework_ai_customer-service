from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.graph.appointment_identity_signals import (
    extract_customer_name_value,
    extract_phone_value,
    recent_assistant_asked_identity_slot,
    recent_history_has_appointment_context,
    recent_identity_values_from_history,
)
from app.graph.nodes.appointment_time_utils import available_time_values
from app.services.platform_agent_client import PlatformAgentClient


@dataclass(frozen=True)
class AppointmentOpeningService:
    """Create appointment deposit orders after customer details are confirmed."""

    platform_client: PlatformAgentClient | None = None

    def maybe_open(
        self,
        *,
        content: str,
        state: dict[str, Any],
        appointment_query: dict[str, Any],
        available_time: dict[str, Any] | None,
    ) -> dict[str, Any]:
        facts = _appointment_facts(state, appointment_query, available_time or {})
        request_context = _request_context(state)
        if not str(facts.get("user_id") or "").strip() and self.platform_client and self.platform_client.available:
            effective_identity = self.platform_client.effective_identity(request_context)
            if effective_identity.get("user_id"):
                facts["user_id"] = effective_identity["user_id"]
        missing = _missing_fields(facts)
        if missing:
            return {"status": "missing_info", "missing": missing, "facts": facts}
        if not _customer_confirmed_opening(content) and not _customer_auto_confirmed(content, state, facts):
            return {"status": "needs_customer_confirmation", "facts": facts}
        if facts.get("preferred_time_available") is False:
            return {"status": "preferred_time_unavailable", "facts": facts}
        if not self.platform_client or not self.platform_client.available:
            return {"status": "platform_unavailable", "facts": facts, "error": "PLATFORM_AGENT_TOKEN is not configured"}
        if _truthy(request_context.get("appointment_opening_dry_run")):
            return _dry_run_result(facts)

        try:
            mobile_sync_result: dict[str, Any] | None = None
            if _should_sync_mobile(facts):
                try:
                    mobile_sync_result = self.platform_client.add_customer_mobile(
                        customer_id=facts["customer_id"],
                        mobile=facts["phone"],
                        request_context=request_context,
                    )
                except Exception as exc:
                    mobile_error = f"{type(exc).__name__}: {exc}"
                    if _is_existing_mobile_error(mobile_error):
                        mobile_sync_result = {"status": "already_exists", "error": mobile_error}
                    else:
                        return {
                            "status": "error",
                            "facts": facts,
                            "error": mobile_error,
                            "reason": "客户手机号同步失败",
                        }
            check: dict[str, Any] = {}
            check_error = ""
            try:
                check = self.platform_client.check_customer(
                    customer_id=facts["customer_id"],
                    request_context=request_context,
                )
            except Exception as exc:
                check_error = f"{type(exc).__name__}: {exc}"
                lowered_error = check_error.lower()
                if "kind不能为空" not in check_error and "kind" not in lowered_error:
                    return {
                        "status": "error",
                        "facts": facts,
                        "error": check_error,
                        "reason": "预约资格校验失败",
                    }
            result_value = str(check.get("result", "")).strip() if isinstance(check, dict) else ""
            if result_value and result_value not in {"1", "true", "True"}:
                return {
                    "status": "cannot_create",
                    "facts": facts,
                    "check_customer": check,
                    "reason": "客户当前存在进行中的订单或暂不可创建预约金订单",
                }
            try:
                order = self.platform_client.create_work_order(
                    customer_id=facts["customer_id"],
                    store_id=facts["store_id"],
                    user_id=facts["user_id"],
                    prepay=facts["prepay"],
                    customer_add_wechat_id=facts["customer_add_wechat_id"],
                    category_id=facts.get("category_id") or None,
                    remark=_remark_for_order(facts),
                    request_context=request_context,
                )
            except Exception as exc:
                create_error = f"{type(exc).__name__}: {exc}"
                if any(term in create_error for term in ["正在进行的订单", "暂不可创建", "请通知门店或财务处理"]):
                    return {
                        "status": "cannot_create",
                        "facts": facts,
                        "check_customer": check,
                        "error": create_error,
                        "reason": "客户当前还有进行中的订单，需门店或财务先处理",
                    }
                return {
                    "status": "create_failed",
                    "facts": facts,
                    "check_customer": check,
                    "error": create_error,
                }
            order_id = str(order.get("order_id") or order.get("id") or "").strip()
            if not order_id:
                return {
                    "status": "create_failed",
                    "facts": facts,
                    "check_customer": check,
                    "create_result": order,
                    "error": "create_work returned no order_id",
                }
            return _created_result(
                facts,
                order_id=order_id,
                create_result=order,
                dry_run=False,
                mobile_sync_result=mobile_sync_result,
            )
        except Exception as exc:
            return {"status": "error", "facts": facts, "error": f"{type(exc).__name__}: {exc}"}


def appointment_push_message(tool_results: dict[str, Any]) -> dict[str, Any] | None:
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if not isinstance(opening, dict) or opening.get("status") not in {"created", "dry_run_created"}:
        return None
    content = opening.get("appointment_push")
    if not isinstance(content, dict) or not content.get("order_id"):
        return None
    return {"type": "appointment_push", "order": 0, "content": content}


def book_order_message(tool_results: dict[str, Any]) -> dict[str, Any] | None:
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if not isinstance(opening, dict) or opening.get("status") not in {"created", "dry_run_created"}:
        return None
    order_id = str(opening.get("order_id") or "").strip()
    if not order_id:
        return None
    return {"type": "book_order", "order": 0, "content": {"order_id": order_id}}


def _appointment_facts(
    state: dict[str, Any],
    appointment_query: dict[str, Any],
    available_time: dict[str, Any],
) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    customer = customer_context.get("customer") if isinstance(customer_context.get("customer"), dict) else {}
    request_context = _request_context(state)
    active_task = state.get("active_task") if isinstance(state.get("active_task"), dict) else {}
    known_slots = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
    current_content = str(state.get("normalized_content") or "")
    current_name = extract_customer_name_value(current_content)
    current_phone = extract_phone_value(current_content)
    recent_identity = recent_identity_values_from_history(state)
    recent_name = str(recent_identity.get("customer_name") or "").strip()
    recent_phone = str(recent_identity.get("phone") or "").strip()
    preferred_time = str(
        known_slots.get("visit_time")
        or request_context.get("appointment_time")
        or request_context.get("visit_time")
        or _extract_time(state.get("normalized_content") or "")
    ).strip()
    slots = available_time_values(available_time.get("slots") or {}) if isinstance(available_time, dict) else []
    profile_phone = str(customer.get("phone") or customer_context.get("phone") or customer_context.get("mobile") or "").strip()
    return {
        "customer_id": str(customer.get("id") or customer_context.get("customer_id") or request_context.get("customer_id") or "").strip(),
        "customer_add_wechat_id": str(
            customer.get("customer_add_wechat_id") or request_context.get("customer_add_wechat_id") or state.get("customer_add_wechat_id") or ""
        ).strip(),
        "user_id": str(request_context.get("user_id") or state.get("user_id") or "").strip(),
        "store_id": str(
            appointment_query.get("store_id")
            or request_context.get("confirmed_store_id")
            or request_context.get("store_id")
            or state.get("confirmed_store_id")
            or state.get("store_id")
            or ""
        ).strip(),
        "store_name": str(
            appointment_query.get("store_name")
            or request_context.get("confirmed_store_name")
            or request_context.get("store_name")
            or state.get("confirmed_store_name")
            or state.get("store_name")
            or ""
        ).strip(),
        "date": str(
            appointment_query.get("date")
            or request_context.get("visit_date")
            or request_context.get("appointment_date")
            or known_slots.get("visit_date_value")
            or ""
        ).strip(),
        "time": preferred_time,
        "customer_name": str(
            current_name
            or known_slots.get("customer_name")
            or recent_name
            or customer.get("name")
            or customer_context.get("name")
            or request_context.get("customer_name")
            or ""
        ).strip(),
        "phone": str(
            current_phone
            or known_slots.get("phone")
            or recent_phone
            or request_context.get("phone")
            or request_context.get("mobile")
            or profile_phone
            or ""
        ).strip(),
        "profile_phone": profile_phone,
        "preferred_time_available": preferred_time in slots if preferred_time and slots else None,
        "available_time_slots": slots[:12],
        "category_id": str(request_context.get("category_id") or "").strip(),
        "prepay": str(request_context.get("prepay") or "10.00").strip(),
    }


def _request_context(state: dict[str, Any]) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    context = customer_context.get("request_context") if isinstance(customer_context.get("request_context"), dict) else {}
    merged = dict(context)
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    merged.update({key: value for key, value in request_context.items() if value not in (None, "")})
    return merged


def _missing_fields(facts: dict[str, Any]) -> list[str]:
    required = {
        "customer_id": "客户ID",
        "customer_add_wechat_id": "加微记录",
        "user_id": "员工ID",
        "store_id": "门店",
        "date": "到店日期",
        "time": "到店时间",
        "customer_name": "姓名",
        "phone": "电话",
        "prepay": "预约金",
    }
    return [label for key, label in required.items() if not facts.get(key)]


def _customer_confirmed_opening(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    positive_terms = [
        "确认",
        "可以",
        "行",
        "好",
        "好的",
        "就这个",
        "就这家",
        "约",
        "预约",
        "帮我定",
        "定吧",
        "安排",
        "开单",
        "付定金",
        "付预约金",
    ]
    negative_terms = ["取消", "不约", "不用", "算了", "退", "投诉", "不要"]
    if any(term in text for term in negative_terms):
        return False
    return any(term in text for term in positive_terms)


def _customer_auto_confirmed(content: str, state: dict[str, Any], facts: dict[str, Any]) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if not recent_history_has_appointment_context(state):
        return False
    if not recent_assistant_asked_identity_slot(state):
        return False
    if not (extract_customer_name_value(text) or extract_phone_value(text)):
        return False
    required_keys = [
        "customer_id",
        "customer_add_wechat_id",
        "user_id",
        "store_id",
        "date",
        "time",
        "customer_name",
        "phone",
        "prepay",
    ]
    if any(not str(facts.get(key) or "").strip() for key in required_keys):
        return False
    return True


def _extract_time(content: str) -> str:
    match = re.search(r"(\d{1,2})[:：](\d{2})", content)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    hour_match = re.search(r"(上午|下午|晚上|中午)?\s*(\d{1,2})\s*点", content)
    if not hour_match:
        return ""
    prefix = hour_match.group(1) or ""
    hour = int(hour_match.group(2))
    if prefix in {"下午", "晚上"} and hour < 12:
        hour += 12
    if prefix == "中午" and hour < 11:
        hour += 12
    return f"{hour:02d}:00"


def _dry_run_result(facts: dict[str, Any]) -> dict[str, Any]:
    return _created_result(facts, order_id="dry_run_order", create_result={"dry_run": True}, dry_run=True)


def _created_result(
    facts: dict[str, Any],
    *,
    order_id: str,
    create_result: dict[str, Any],
    dry_run: bool,
    mobile_sync_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    push = {
        "push_type": "appointment_deposit",
        "order_id": order_id,
        "customer_id": facts.get("customer_id", ""),
        "customer_add_wechat_id": facts.get("customer_add_wechat_id", ""),
        "store_id": facts.get("store_id", ""),
        "store_name": facts.get("store_name", ""),
        "appointment_date": facts.get("date", ""),
        "appointment_time": facts.get("time", ""),
        "prepay": facts.get("prepay", "10.00"),
        "category_id": facts.get("category_id", ""),
        "remark": _remark_for_order(facts),
    }
    return {
        "status": "dry_run_created" if dry_run else "created",
        "facts": facts,
        "order_id": order_id,
        "create_result": create_result,
        "mobile_sync_result": mobile_sync_result or {},
        "appointment_push": push,
    }


def _remark_for_order(facts: dict[str, Any]) -> str:
    date_text = " ".join(part for part in [facts.get("date"), facts.get("time")] if part)
    project_text = f"，意向分类{facts['category_id']}" if facts.get("category_id") else "，项目到店确认"
    return f"AI客服预约开单：{facts.get('store_name') or facts.get('store_id')}，{date_text}{project_text}".strip("，")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _should_sync_mobile(facts: dict[str, Any]) -> bool:
    phone = str(facts.get("phone") or "").strip()
    if not phone:
        return False
    profile_phone = str(facts.get("profile_phone") or "").strip()
    return phone != profile_phone


def _is_existing_mobile_error(error: str) -> bool:
    text = str(error or "")
    return "已有手机号" in text or ("手机号" in text and any(term in text for term in ["已存在", "已经存在", "已有"]))
