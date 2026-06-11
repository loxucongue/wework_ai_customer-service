from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.appointment_utils import extract_date_value
from app.graph.nodes.intent_signals import has_appointment_change_or_cancel, has_appointment_record_query
from app.graph.nodes.reply_summary_context import (
    asks_price_recap,
    asks_store_or_address_recap,
    has_pre_visit_question,
    is_strong_multi_recap_request,
)
from app.graph.planner_dispute_signals import has_effect_dispute, has_fee_or_refund_dispute
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


def _has_explicit_appointment_request(content: str) -> bool:
    if has_appointment_record_query(content) or has_appointment_change_or_cancel(content):
        return True
    action_terms = [
        "预约",
        "约",
        "定下",
        "定一个",
        "安排",
        "报名",
        "预留",
        "开单",
        "现在来",
        "今天来",
        "下午来",
        "明天来",
        "后天来",
    ]
    if any(term in content for term in action_terms):
        return True
    compact = re.sub(r"\s+", "", content)
    short_confirm = ["可以", "行", "好的", "确认", "就这个", "那就这个", "那行"]
    if len(compact) <= 12 and any(term in compact for term in short_confirm):
        return True
    if len(compact) <= 12 and (extract_date_value(compact) or re.search(r"(上午|下午|晚上)?\d{1,2}[点时]?", compact)):
        return True
    return False


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

    if _has_explicit_appointment_request(content):
        return False
    if is_strong_multi_recap_request(content):
        return True
    if has_fee_or_refund_dispute(content) or has_effect_dispute(content) or _is_service_response_complaint(content):
        return True
    if has_pre_visit_question(content) or asks_store_or_address_recap(content) or asks_price_recap(content):
        return True

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
    }
    appointment_types = {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
    types = _task_types(task_views)
    return bool(types & non_appointment_types and not types & appointment_types)
