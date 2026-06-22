from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.graph.nodes.appointment_time_utils import available_time_values
from app.graph.nodes.appointment_utils import extract_date_value
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
        missing = _missing_fields(facts)
        if missing:
            return {"status": "missing_info", "missing": missing, "facts": _public_facts(facts)}
        if not (_customer_confirmed_opening(content) or _contact_info_confirms_recent_booking(content, state)):
            return {"status": "needs_customer_confirmation", "facts": _public_facts(facts)}
        if not self.platform_client or not self.platform_client.available:
            return {
                "status": "platform_unavailable",
                "facts": _public_facts(facts),
                "error": "PLATFORM_AGENT_TOKEN is not configured",
            }

        request_context = _request_context(state)
        facts = _resolve_category_and_prepay(self.platform_client, facts, request_context)
        if _truthy(request_context.get("appointment_opening_dry_run")):
            return _dry_run_result(facts)

        try:
            existing_appointment = _existing_active_appointment(state)
            if existing_appointment:
                return {
                    "status": "already_appointed",
                    "facts": _public_facts(facts),
                    "existing_appointment": existing_appointment,
                    "reason": "客户已有明确预约记录，避免重复创建预约金订单",
                }

            check: dict[str, Any] = {}
            try:
                check = self.platform_client.check_customer(
                    customer_id=facts["customer_id"],
                    kind=facts.get("kind") or None,
                    request_context=request_context,
                )
            except Exception as exc:  # noqa: BLE001
                check = {"error": f"{type(exc).__name__}: {exc}"}
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
            order_id = str(order.get("order_id") or order.get("id") or "").strip()
            if not order_id:
                return {
                    "status": "create_failed",
                    "facts": _public_facts(facts),
                    "create_result": order,
                    "error": "create_work returned no order_id",
                }
            result = _created_result(facts, order_id=order_id, create_result=order, dry_run=False)
            result["check_customer"] = check
            return result
        except Exception as exc:  # noqa: BLE001
            reused = _try_reuse_open_order_after_create_error(
                self.platform_client,
                facts,
                request_context,
                create_error=exc,
            )
            if reused:
                reused["check_customer"] = check if "check" in locals() else {}
                return reused
            return {"status": "error", "facts": _public_facts(facts), "error": f"{type(exc).__name__}: {exc}"}


def appointment_push_message(tool_results: dict[str, Any]) -> dict[str, Any] | None:
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if not isinstance(opening, dict) or opening.get("status") not in {"created", "dry_run_created", "reused_open_order"}:
        return None
    content = opening.get("appointment_push")
    if not isinstance(content, dict) or not content.get("order_id"):
        return None
    return {"type": "appointment_push", "order": 0, "content": content}


def _appointment_facts(
    state: dict[str, Any],
    appointment_query: dict[str, Any],
    available_time: dict[str, Any],
) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    customer = customer_context.get("customer") if isinstance(customer_context.get("customer"), dict) else {}
    customer_orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    request_context = _request_context(state)
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    normalized_content = str(state.get("normalized_content") or "")
    history_text = "\n".join([*_recent_customer_texts(state), *_planner_known_texts(state)])
    preferred_time = str(
        _extract_time(normalized_content)
        or appointment_query.get("time")
        or appointment_cache.get("time")
        or appointment_cache.get("appointment_time")
        or _extract_time(history_text)
        or state.get("appointment_time")
        or request_context.get("appointment_time")
        or ""
    ).strip()
    slots = available_time_values(available_time.get("slots") or {}) if isinstance(available_time, dict) else []
    customer_name = _extract_customer_name(normalized_content) or _extract_customer_name(history_text) or str(
        appointment_query.get("customer_name")
        or appointment_query.get("name")
        or request_context.get("customer_name")
        or request_context.get("name")
        or customer.get("name")
        or ""
    ).strip()
    customer_phone = _extract_phone(normalized_content) or _extract_phone(history_text) or str(
        appointment_query.get("customer_phone")
        or appointment_query.get("phone")
        or request_context.get("customer_phone")
        or request_context.get("phone")
        or customer.get("phone")
        or ""
    ).strip()
    return {
        "customer_id": str(customer.get("id") or customer_context.get("customer_id") or request_context.get("customer_id") or "").strip(),
        "customer_add_wechat_id": str(
            customer.get("customer_add_wechat_id")
            or request_context.get("customer_add_wechat_id")
            or request_context.get("customer_add_id")
            or state.get("customer_add_wechat_id")
            or state.get("customer_add_id")
            or request_context.get("customer_id")
            or ""
        ).strip(),
        "user_id": str(request_context.get("user_id") or state.get("user_id") or "").strip(),
        "store_id": str(appointment_query.get("store_id") or state.get("confirmed_store_id") or state.get("store_id") or "").strip(),
        "store_name": str(appointment_query.get("store_name") or state.get("confirmed_store_name") or state.get("store_name") or "").strip(),
        "date": str(
            appointment_query.get("date")
            or appointment_cache.get("date")
            or appointment_cache.get("appointment_date")
            or extract_date_value(history_text)
            or request_context.get("appointment_date")
            or ""
        ).strip(),
        "time": preferred_time,
        "preferred_time_available": preferred_time in slots if preferred_time and slots else None,
        "available_time_slots": slots[:12],
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "category_id": str(
            request_context.get("category_id")
            or customer.get("category_id")
            or state.get("category_id")
            or ""
        ).strip(),
        "kind": str(
            customer.get("kind")
            or customer_context.get("kind")
            or request_context.get("kind")
            or request_context.get("customer_kind")
            or ""
        ).strip(),
        "prepay": str(request_context.get("prepay") or "10.00").strip(),
        "_customer_orders": [order for order in customer_orders if isinstance(order, dict)],
    }


def _request_context(state: dict[str, Any]) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    context = customer_context.get("request_context") if isinstance(customer_context.get("request_context"), dict) else {}
    merged = dict(context)
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    merged.update({key: value for key, value in request_context.items() if value not in (None, "")})
    return merged


def _existing_active_appointment(state: dict[str, Any]) -> dict[str, Any]:
    """Return a real existing appointment if one is present.

    A pending order or selected store/time is not enough to block appointment
    deposit creation. We only block when there is a clear appointment record.
    """

    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    appointment = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    if _appointment_snapshot_is_active(appointment):
        return appointment

    orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    for order in orders:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status") or order.get("status_text") or "").strip().lower()
        if status in {"cancel", "cancelled", "canceled", "complete", "completed", "finished", "closed", "已取消", "已完成"}:
            continue
        appointment_time = str(
            order.get("appointment_time")
            or order.get("store_at")
            or order.get("plan_at")
            or order.get("pre_plan_at")
            or ""
        ).strip()
        if appointment_time and (order.get("id") or order.get("order_id") or order.get("store_id") or order.get("store_name")):
            return {
                "has_active": True,
                "status": order.get("status") or "active",
                "order_id": str(order.get("id") or order.get("order_id") or ""),
                "store_id": str(order.get("store_id") or ""),
                "store_name": str(order.get("store_name") or ""),
                "appointment_time": appointment_time,
                "source": "customer_context.orders",
            }
    return {}


def _appointment_snapshot_is_active(appointment: dict[str, Any]) -> bool:
    if not isinstance(appointment, dict) or not appointment:
        return False
    appointment_id = str(appointment.get("appointment_id") or appointment.get("order_id") or "").strip()
    appointment_time = str(appointment.get("appointment_time") or "").strip()
    status = str(appointment.get("status") or "").strip().lower()
    if status in {"cancel", "cancelled", "canceled", "complete", "completed", "finished", "closed", "none", "intent_only", "context_store_only", "已取消", "已完成"}:
        return False
    return bool(
        appointment_time
        and (appointment.get("has_active") or status in {"confirmed", "scheduled", "active", "pending", "已预约"})
        and (appointment_id or appointment.get("store_id") or appointment.get("store_name"))
    )


def _try_reuse_open_order_after_create_error(
    platform_client: PlatformAgentClient | None,
    facts: dict[str, Any],
    request_context: dict[str, Any],
    *,
    create_error: Exception,
) -> dict[str, Any]:
    error_text = f"{type(create_error).__name__}: {create_error}"
    if "正在进行的订单" not in error_text:
        return {}
    if not platform_client or not platform_client.available:
        return {}
    order = _reusable_open_order(facts)
    if not order:
        return {}
    order_id = str(order.get("id") or order.get("order_id") or "").strip()
    if not order_id:
        return {}
    try:
        modified = platform_client.modify_work_order(
            order_id=order_id,
            store_id=facts["store_id"],
            user_id=facts["user_id"],
            category_id=facts.get("category_id") or None,
            amount=facts["prepay"],
            request_context=request_context,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "facts": _public_facts(facts),
            "order_id": None,
            "error": f"{type(exc).__name__}: {exc}",
            "create_error": error_text,
            "reuse_order_id": order_id,
        }
    modified_order_id = str(modified.get("order_id") or modified.get("id") or order_id).strip()
    if not modified_order_id:
        return {
            "status": "modify_failed",
            "facts": _public_facts(facts),
            "modify_result": modified,
            "create_error": error_text,
            "reuse_order_id": order_id,
            "error": "order/modify returned no order_id",
        }
    result = _created_result(
        facts,
        order_id=modified_order_id,
        create_result={
            "reused_existing_order": True,
            "source_order": _compact_order_snapshot(order),
            "modify_result": modified,
            "create_error": error_text,
        },
        dry_run=False,
    )
    result["status"] = "reused_open_order"
    result["reused_order_id"] = modified_order_id
    return result


def _reusable_open_order(facts: dict[str, Any]) -> dict[str, Any]:
    orders = facts.get("_customer_orders")
    if not isinstance(orders, list):
        return {}
    for order in orders:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status") or order.get("status_text") or "").strip().lower()
        if status in {"cancel", "cancelled", "canceled", "complete", "completed", "finished", "closed", "已取消", "已完成"}:
            continue
        appointment_time = str(
            order.get("appointment_time")
            or order.get("store_at")
            or order.get("plan_at")
            or order.get("pre_plan_at")
            or ""
        ).strip()
        if appointment_time:
            continue
        order_id = str(order.get("id") or order.get("order_id") or "").strip()
        if not order_id:
            continue
        return order
    return {}


def _compact_order_snapshot(order: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "order_id", "order_no", "status", "store_id", "store_name", "appointment_time", "store_at")
    return {key: order.get(key) for key in keys if order.get(key) not in (None, "")}


def _missing_fields(facts: dict[str, Any]) -> list[str]:
    required = {
        "customer_id": "客户ID",
        "customer_add_wechat_id": "加微记录",
        "user_id": "员工ID",
        "store_id": "门店",
        "customer_name": "姓名",
        "customer_phone": "电话",
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
        "交10",
        "交十",
        "先交",
        "登记",
        "报名",
        "付款入口",
        "付款链接",
        "支付入口",
        "支付链接",
        "发付款",
        "发支付",
        "收款",
        "收款码",
    ]
    negative_terms = ["取消", "不约", "不用", "算了", "退", "投诉", "不要"]
    if any(term in text for term in negative_terms):
        return False
    return any(term in text for term in positive_terms)


def _contact_info_confirms_recent_booking(content: str, state: dict[str, Any]) -> bool:
    if not (_extract_phone(content) and _extract_customer_name(content)):
        return False
    event_texts: list[str] = []
    for event in (state.get("history_events") or [])[-12:]:
        if not isinstance(event, dict):
            continue
        event_texts.append(str(event.get("summary") or ""))
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        event_texts.extend(str(value or "") for value in facts.values())
    dialogue_texts = [str(item or "") for item in (state.get("conversation_history") or [])[-8:]]
    history = "\n".join([*_recent_customer_texts(state)[-8:], *dialogue_texts, *event_texts])
    return any(term in history for term in ("怎么预约", "预约", "报名", "登记", "预约金", "留名额", "锁定名额", "姓名电话"))


def _extract_time(content: str) -> str:
    match = re.search(r"(\d{1,2})[:：](\d{2})", content)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    hour_match = re.search(r"(上午|下午|晚上|中午)?\s*(\d{1,2}|[一二两三四五六七八九十十一十二])\s*点", content)
    if not hour_match:
        return ""
    prefix = hour_match.group(1) or ""
    hour = _hour_number(hour_match.group(2))
    if hour is None:
        return ""
    if prefix in {"下午", "晚上"} and hour < 12:
        hour += 12
    if prefix == "中午" and hour < 11:
        hour += 12
    return f"{hour:02d}:00"


def _hour_number(value: str) -> int | None:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    return mapping.get(text)


def _extract_phone(content: str) -> str:
    match = re.search(r"1[3-9]\d{9}", str(content or ""))
    return match.group(0) if match else ""


def _extract_customer_name(content: str) -> str:
    text = str(content or "").strip()
    patterns = (
        r"(?:我叫|叫我|名字叫|姓名是|姓名|名字是)\s*([\u4e00-\u9fa5A-Za-z]{1,12})",
        r"([\u4e00-\u9fa5]{2,4})\s*(?:电话|手机|手机号)\s*1[3-9]\d{9}",
        r"^\s*([\u4e00-\u9fa5]{2,4})\s+1[3-9]\d{9}\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        name = re.sub(r"(电话|手机|手机号|是|叫)$", "", match.group(1).strip())
        if name and name not in {"电话", "手机", "姓名", "名字"}:
            return name
    return ""


def _recent_customer_texts(state: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in reversed(state.get("conversation_history") or []):
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role and role not in {"user", "customer"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
        else:
            text = str(item or "").strip()
            if text.startswith(("小贝：", "小贝:", "客服：", "客服:", "AI回复：", "AI回复:", "助手：", "助手:")):
                continue
            if text.startswith(("客户：", "客户:", "用户：", "用户:")):
                text = text.split("：", 1)[-1] if "：" in text else text.split(":", 1)[-1]
        if text:
            texts.append(text)
    return texts[:10]


def _planner_known_texts(state: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for task_key in ("primary_task",):
        task = state.get(task_key) if isinstance(state.get(task_key), dict) else {}
        values = task.get("known_info") if isinstance(task, dict) else []
        if isinstance(values, list):
            texts.extend(str(item or "").strip() for item in values if str(item or "").strip())
    secondary = state.get("secondary_tasks") if isinstance(state.get("secondary_tasks"), list) else []
    for task in secondary:
        if not isinstance(task, dict):
            continue
        values = task.get("known_info")
        if isinstance(values, list):
            texts.extend(str(item or "").strip() for item in values if str(item or "").strip())
    return texts[:12]


def _dry_run_result(facts: dict[str, Any]) -> dict[str, Any]:
    return _created_result(facts, order_id="dry_run_order", create_result={"dry_run": True}, dry_run=True)


def _created_result(
    facts: dict[str, Any],
    *,
    order_id: str,
    create_result: dict[str, Any],
    dry_run: bool,
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
        "customer_name": facts.get("customer_name", ""),
        "customer_phone": facts.get("customer_phone", ""),
        "prepay": facts.get("prepay", "10.00"),
        "category_id": facts.get("category_id", ""),
        "remark": _remark_for_order(facts),
    }
    return {
        "status": "dry_run_created" if dry_run else "created",
        "facts": _public_facts(facts),
        "order_id": order_id,
        "create_result": create_result,
        "appointment_push": push,
    }


def _public_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in facts.items() if not str(key).startswith("_")}


def _remark_for_order(facts: dict[str, Any]) -> str:
    date_text = " ".join(part for part in [facts.get("date"), facts.get("time")] if part)
    project_text = f"，意向分类{facts['category_id']}" if facts.get("category_id") else "，项目到店确认"
    return f"AI客服预约开单：{facts.get('store_name') or facts.get('store_id')}，{date_text}{project_text}".strip("，")


def _resolve_category_and_prepay(
    platform_client: PlatformAgentClient | None,
    facts: dict[str, Any],
    request_context: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(facts)
    category_id = str(updated.get("category_id") or "").strip()
    if platform_client and platform_client.available and category_id and not category_id.isdigit():
        resolved = _resolve_category_id(platform_client, category_id, request_context)
        if resolved:
            updated["category_id"] = resolved
            category_id = resolved
    if platform_client and platform_client.available and category_id and category_id.isdigit():
        try:
            data = platform_client.category_prepay(category_id=category_id, request_context=request_context)
            prepay = _select_prepay_amount(data)
            if prepay and not str(request_context.get("prepay") or "").strip():
                updated["prepay"] = prepay
            updated["prepay_options"] = data.get("prepay") if isinstance(data, dict) else []
        except Exception as exc:  # noqa: BLE001
            updated["prepay_error"] = f"{type(exc).__name__}: {exc}"
    return updated


def _resolve_category_id(
    platform_client: PlatformAgentClient,
    category_name: str,
    request_context: dict[str, Any],
) -> str:
    target = str(category_name or "").strip()
    if not target:
        return ""
    try:
        categories = platform_client.list_categories(request_context=request_context)
    except Exception:
        return ""
    for row in categories:
        if not isinstance(row, dict):
            continue
        candidates = [
            str(row.get("name") or "").strip(),
            str(row.get("title") or "").strip(),
            str(row.get("full_name") or "").strip(),
        ]
        if any(candidate and (candidate == target or target in candidate or candidate in target) for candidate in candidates):
            return str(row.get("id") or row.get("value") or "").strip()
    return ""


def _select_prepay_amount(data: dict[str, Any]) -> str:
    rows = data.get("prepay") if isinstance(data, dict) else []
    if isinstance(rows, dict):
        rows = list(rows.values())
    amounts: list[float] = []
    if isinstance(rows, list):
        for row in rows:
            value = row.get("prepay") or row.get("amount") if isinstance(row, dict) else row
            try:
                amount = float(str(value).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            if amount > 0:
                amounts.append(amount)
    if not amounts:
        return ""
    return f"{min(amounts):.2f}"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
