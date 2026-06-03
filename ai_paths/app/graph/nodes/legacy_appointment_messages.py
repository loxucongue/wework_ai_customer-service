from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacyAppointmentMessageCallbacks:
    recent_assistant_replies: Callable[[AgentState, int], list[str]]


def appointment_context_sentence(state: AgentState) -> str:
    appointment = state.get("appointment_cache") or {}
    if not isinstance(appointment, dict) or not appointment.get("has_active"):
        return ""
    summary = str(appointment.get("summary") or "").strip()
    store_name = str(appointment.get("store_name") or "").strip()
    appointment_time = str(appointment.get("appointment_time") or "").strip()
    if summary:
        return f"另外小贝也看到你这边已有预约记录：{summary}。如果这次要约新的门店或项目，我会按新的需求单独帮你确认。"
    if store_name or appointment_time:
        bits = " ".join(bit for bit in [store_name, appointment_time] if bit)
        return f"另外小贝也看到你这边已有预约记录：{bits}。"
    return "另外小贝也看到你这边已有预约记录，后面涉及改约、取消或查时间时会一起帮你对照。"


def should_show_appointment_context(state: AgentState, callbacks: LegacyAppointmentMessageCallbacks) -> bool:
    intents = {item.get("intent") for item in state.get("intents", [])}
    content = state.get("normalized_content") or ""
    if intents & {"appointment_confirm", "appointment_change", "appointment_cancel"}:
        return True
    if "appointment_intent" in intents:
        recent = " ".join(callbacks.recent_assistant_replies(state, 5))
        if any(term in recent for term in ["已有预约记录", "已有预约", "约的是", "预约记录"]):
            return any(term in content for term in ["我有没有预约", "我约的是", "预约成功", "改约", "取消预约", "帮我取消", "换个时间"])
        return any(term in content for term in ["我有没有预约", "我约的是", "预约成功", "改约", "取消预约", "帮我取消", "换个时间", "再约", "重新约"])
    explicit_terms = [
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
    return any(term in content for term in explicit_terms)
