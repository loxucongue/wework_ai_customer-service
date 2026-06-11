from __future__ import annotations

from app.graph.nodes.common import recent_assistant_replies
from app.graph.planner.runtime_plan import planner_task_views
from app.graph.state import AgentState


def appointment_context_sentence(state: AgentState) -> str:
    appointment = state.get("appointment_cache") or {}
    if not isinstance(appointment, dict) or not appointment.get("has_active"):
        return ""

    summary = str(appointment.get("summary") or "").strip()
    store_name = str(appointment.get("store_name") or "").strip()
    appointment_time = str(appointment.get("appointment_time") or "").strip()

    if summary:
        return f"我这边看到您目前有一条预约记录：{summary}"

    bits = [bit for bit in [store_name, appointment_time] if bit]
    if bits:
        return f"我这边看到您目前有一条预约记录：{' '.join(bits)}"

    return "我这边看到您目前有预约记录。如果您是想确认时间、改约或取消，我可以继续帮您核对。"


def should_show_appointment_context(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or "")
    task_views = planner_task_views(state)

    appointment_types = {"appointment", "appointment_status", "appointment_change", "appointment_cancel"}
    if any(
        str(view.get("type") or "") in appointment_types
        or str(view.get("subtype") or "") in appointment_types
        for view in task_views
        if isinstance(view, dict)
    ):
        return True

    explicit_terms = [
        "预约",
        "约了",
        "改约",
        "取消",
        "到店",
        "时间",
        "几点",
        "今天下午",
        "明天",
        "周六",
        "周日",
        "档期",
        "安排",
    ]
    if any(term in content for term in explicit_terms):
        return True

    recent = " ".join(recent_assistant_replies(state, 5))
    if any(term in recent for term in ["预约", "到店", "时间", "档期"]):
        return any(term in content for term in ["可以", "好的", "确认", "改", "取消", "几点", "什么时候"])

    return False
