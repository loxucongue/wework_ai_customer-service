from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def _task_types(task_views: list[dict[str, Any]] | None) -> set[str]:
    output: set[str] = set()
    for item in task_views or []:
        if not isinstance(item, dict):
            continue
        task_type = str(item.get("type") or "").strip()
        if task_type:
            output.add(task_type)
    return output


def _is_service_response_complaint(content: str) -> bool:
    if not content:
        return False
    return any(
        term in content
        for term in [
            "为什么这么慢",
            "怎么这么慢",
            "回消息这么慢",
            "回复这么慢",
            "半天不回",
            "一直没回",
        ]
    )


def should_suspend_appointment_context_for_current_turn(
    state: AgentState,
    task_views: list[dict[str, Any]] | None = None,
) -> bool:
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    has_appointment_context = bool(appointment_cache.get("store_id") or appointment_cache.get("store_name"))
    if not has_appointment_context:
        return False

    content = str(state.get("normalized_content") or "").strip()
    if not content:
        return False

    types = _task_types(task_views)
    non_appointment_types = {
        "project_inquiry",
        "price_inquiry",
        "campaign_inquiry",
        "trust_issue",
        "competitor_compare",
        "after_sales",
        "image_inquiry",
        "ad_price_check",
        "case_request",
        "project_process",
        "store_inquiry",
        "service_complaint",
        "refund_dispute",
        "effect_dispute",
        "complaint_refund",
        "human_request",
    }
    appointment_types = {
        "appointment",
        "appointment_intent",
        "appointment_confirm",
        "appointment_status",
        "appointment_change",
        "appointment_cancel",
    }
    if types:
        return bool(types & non_appointment_types and not types & appointment_types)

    # With no planner view, only suppress stale appointment context for service-delay complaints.
    # Business task routing belongs to Planner Brain, not keyword logic here.
    return _is_service_response_complaint(content)
