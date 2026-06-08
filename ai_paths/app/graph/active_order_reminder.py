from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class ActiveOrderReminderCallbacks:
    recent_assistant_replies: Callable[[AgentState, int], list[str]]


def has_active_order(state: AgentState) -> bool:
    appointment = state.get("appointment_cache") or {}
    return isinstance(appointment, dict) and bool(appointment.get("has_active"))


def active_order_reminder_mode(
    state: AgentState,
    callbacks: ActiveOrderReminderCallbacks,
) -> str:
    if not has_active_order(state):
        return ""
    content = str(state.get("normalized_content") or "").strip()
    if not content:
        return ""
    intents = {
        str(item.get("intent") or "")
        for item in (state.get("intents") or [])
        if isinstance(item, dict)
    }
    if intents & {"project_inquiry", "price_inquiry", "campaign_inquiry", "trust_issue", "after_sales", "competitor_compare", "complaint_refund", "human_request"}:
        return ""
    if _already_reminded_recently(state, callbacks):
        return ""
    if _is_direct_order_query(content):
        return "direct_order"
    if _is_arrival_scene(content):
        return "arrival"
    if "store_inquiry" in intents or _is_store_fact_scene(content):
        return "store_related"
    if "appointment_intent" in intents and not any(intent in intents for intent in {"appointment_confirm", "appointment_change", "appointment_cancel"}):
        return "appointment_related"
    return ""


def active_order_reminder_sentence(state: AgentState, mode: str = "") -> str:
    appointment = state.get("appointment_cache") or {}
    if not isinstance(appointment, dict) or not appointment.get("has_active"):
        return ""
    store_name = str(appointment.get("store_name") or "").strip()
    appointment_time = str(appointment.get("appointment_time") or "").strip()
    summary = str(appointment.get("summary") or "").strip()
    status = _status_label(str(appointment.get("status") or "").strip())
    content = str(state.get("normalized_content") or "").strip()
    mode = mode or "store_related"
    # mode here is only used to shape wording; frequency gating is handled before this function is called.
    if mode == "direct_order":
        if summary:
            return f"客户当前有一笔进行中的预约/订单记录：{summary}。这轮如果是在查时间、门店、改约或取消，要优先按这笔记录直接回答。"
        bits = " ".join(bit for bit in [store_name, appointment_time] if bit)
        return f"客户当前有一笔进行中的预约/订单记录：{bits}。这轮如果是在查时间、门店、改约或取消，要优先按这笔记录直接回答。".strip()
    if mode == "arrival":
        bits = " ".join(bit for bit in [store_name, appointment_time] if bit)
        return f"客户现在可能是在去店路上或已到附近。当前活动记录是{bits}。先确认是不是按这家过去，再直接发地址/路线，不要重新聊项目。".strip()
    if mode == "store_related":
        bits = " ".join(bit for bit in [status, store_name, appointment_time] if bit)
        return f"客户当前有一笔活动记录：{bits}。如果这次还是按这家过去，只轻提醒一次，再直接回答地址、路线或停车问题。".strip()
    if mode == "appointment_related":
        bits = " ".join(bit for bit in [status, store_name, appointment_time] if bit)
        return f"客户当前有一笔活动记录：{bits}。如果这次是在继续原来的安排，先顺着这笔记录确认时间或改约，不要当全新预约重新收集。".strip()
    return ""


def _already_reminded_recently(state: AgentState, callbacks: ActiveOrderReminderCallbacks) -> bool:
    appointment = state.get("appointment_cache") or {}
    store_name = str(appointment.get("store_name") or "").strip()
    recent = " ".join(callbacks.recent_assistant_replies(state, 3))
    if not recent:
        return False
    reminder_terms = [
        "已有预约",
        "已有预约记录",
        "有一笔待到店",
        "待到店记录",
        "有一笔待安排",
        "待安排记录",
        "这边现在有一笔",
        "当前活动记录",
    ]
    if not any(term in recent for term in reminder_terms):
        return False
    return not store_name or store_name in recent


def _is_direct_order_query(content: str) -> bool:
    return any(
        term in content
        for term in [
            "我有没有预约",
            "我约的是",
            "约的是几点",
            "预约成功",
            "查一下预约",
            "改约",
            "改时间",
            "取消预约",
            "帮我取消",
            "明天不去了",
            "换个时间",
        ]
    )


def _is_arrival_scene(content: str) -> bool:
    return any(term in content for term in ["我到了", "到了", "在楼下", "到门口了", "快到了", "在路上", "已经到", "到附近了"])


def _is_store_fact_scene(content: str) -> bool:
    return any(
        term in content
        for term in [
            "地址",
            "导航",
            "停车",
            "路线",
            "怎么过去",
            "发我",
            "发给我",
            "哪家近",
            "附近",
            "机场",
            "高铁站",
        ]
    )


def _status_label(status: str) -> str:
    return {
        "pending": "待处理",
        "waiting_schedule": "待安排",
        "scheduled": "待到店",
    }.get(status, "进行中")
