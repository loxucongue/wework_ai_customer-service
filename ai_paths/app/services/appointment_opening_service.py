from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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
        missing = _missing_fields(facts)
        if missing:
            return {"status": "missing_info", "missing": missing, "facts": facts}
        if not _customer_confirmed_opening(content):
            return {"status": "needs_customer_confirmation", "facts": facts}
        if facts.get("preferred_time_available") is False:
            return {"status": "preferred_time_unavailable", "facts": facts}
        if not self.platform_client or not self.platform_client.available:
            return {
                "status": "platform_unavailable",
                "facts": facts,
                "error": "PLATFORM_AGENT_TOKEN is not configured",
            }

        request_context = _request_context(state)
        facts = _resolve_category_and_prepay(self.platform_client, facts, request_context)
        if _truthy(request_context.get("appointment_opening_dry_run")):
            return _dry_run_result(facts)

        try:
            check = self.platform_client.check_customer(
                customer_id=facts["customer_id"],
                request_context=request_context,
            )
            if str(check.get("result", "")).strip() not in {"1", "true", "True"}:
                return {
                    "status": "cannot_create",
                    "facts": facts,
                    "check_customer": check,
                    "reason": "客户当前存在进行中的订单或暂不可创建预约金订单",
                }
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
                    "facts": facts,
                    "create_result": order,
                    "error": "create_work returned no order_id",
                }
            return _created_result(facts, order_id=order_id, create_result=order, dry_run=False)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "facts": facts, "error": f"{type(exc).__name__}: {exc}"}


def appointment_push_message(tool_results: dict[str, Any]) -> dict[str, Any] | None:
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if not isinstance(opening, dict) or opening.get("status") not in {"created", "dry_run_created"}:
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
    request_context = _request_context(state)
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    normalized_content = str(state.get("normalized_content") or "")
    history_text = "\n".join(_recent_customer_texts(state))
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
        request_context.get("customer_name") or request_context.get("name") or customer.get("name") or ""
    ).strip()
    customer_phone = _extract_phone(normalized_content) or _extract_phone(history_text) or str(
        request_context.get("customer_phone") or request_context.get("phone") or customer.get("phone") or ""
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
        "customer_phone": "电话",
        "prepay": "预约金",
    }
    missing = [label for key, label in required.items() if not facts.get(key)]
    if facts.get("date") and facts.get("time") and facts.get("preferred_time_available") is not True:
        missing.append("真实档期确认")
    return missing


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
        "facts": facts,
        "order_id": order_id,
        "create_result": create_result,
        "appointment_push": push,
    }


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
