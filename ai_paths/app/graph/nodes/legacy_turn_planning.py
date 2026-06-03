from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.graph import planner_helpers
from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacyTurnPlanningCallbacks:
    asks_price_recap: Callable[[str], bool]
    asks_store_or_address_recap: Callable[[str], bool]
    extract_date_value: Callable[[str], str]
    has_appointment_change_or_cancel: Callable[[str], bool]
    has_appointment_record_query: Callable[[str], bool]
    has_pre_visit_question: Callable[[str], bool]
    is_strong_multi_recap_request: Callable[[str], bool]


def with_action_planning_notes(output: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output, dict):
        return output
    planned = dict(output)
    facts = list(planned.get("facts") or [])
    reply_points = list(planned.get("reply_points") or [])
    missing_slots = list(planned.get("missing_slots") or [])

    for info in action.get("known_info") or []:
        text = str(info or "").strip()
        if text and text not in facts:
            facts.append(text)
    for slot in action.get("missing_info") or []:
        text = str(slot or "").strip()
        if text and text not in missing_slots:
            missing_slots.append(text)
    reply_goal = str(action.get("reply_goal") or "").strip()
    if reply_goal and reply_goal not in reply_points:
        reply_points.append(f"本轮回复目标：{reply_goal}")
    if action.get("should_ask") is False and missing_slots:
        reply_points.append("缺失信息不是必须前置条件时，先回答当前问题，再只问一个关键问题。")
    planned["facts"] = facts[:12]
    planned["reply_points"] = reply_points[:10]
    planned["missing_slots"] = missing_slots[:8]
    planned["planner_notes"] = {
        "known_info": action.get("known_info") or [],
        "missing_info": action.get("missing_info") or [],
        "reply_goal": reply_goal,
        "should_ask": bool(action.get("should_ask")),
        "tool_plan": action.get("tool_plan") or [],
    }
    return planned


def should_drop_planner_notes_for_skill_output(
    output: dict[str, Any],
    action: dict[str, Any],
    tool_results: dict[str, Any],
) -> bool:
    if not isinstance(output, dict) or output.get("skill") != "store":
        return False
    lookup = tool_results.get("store_lookup") if isinstance(tool_results, dict) else {}
    if not isinstance(lookup, dict):
        return False
    stores = lookup.get("stores")
    missing = lookup.get("missing")
    if isinstance(stores, list) and stores:
        return False
    if not (isinstance(missing, list) and "city" in missing):
        return False
    return bool(action.get("known_info") or action.get("tool_plan"))


def has_explicit_appointment_request(content: str, callbacks: LegacyTurnPlanningCallbacks) -> bool:
    if callbacks.has_appointment_record_query(content) or callbacks.has_appointment_change_or_cancel(content):
        return True
    action_terms = [
        "预约",
        "约",
        "可约",
        "能约",
        "有时间",
        "空位",
        "空档",
        "时间段",
        "几点",
        "直接到店",
        "直接去",
        "直接过去",
        "到店可以",
        "到店就可以",
    ]
    if any(term in content for term in action_terms):
        return True
    compact = re.sub(r"\s+", "", content)
    short_confirm = ["可以吗", "行吗", "就这个", "就这家", "确认", "确定", "肯定今天啊", "今天啊", "就是今天"]
    if len(compact) <= 12 and any(term in compact for term in short_confirm):
        return True
    if len(compact) <= 12 and (
        callbacks.extract_date_value(compact) or re.search(r"(上午|中午|下午|晚上)?\d{1,2}[点:：]", compact)
    ):
        return True
    return False


def should_suspend_active_task_for_current_turn(
    state: AgentState,
    active_task: dict[str, Any] | None = None,
    intents: list[dict[str, Any]] | None = None,
    *,
    callbacks: LegacyTurnPlanningCallbacks,
) -> bool:
    task = active_task if isinstance(active_task, dict) else state.get("active_task", {})
    if not isinstance(task, dict) or task.get("type") != "appointment_visit":
        return False
    content = state.get("normalized_content") or ""
    if not content:
        return False
    if has_explicit_appointment_request(content, callbacks):
        return False
    if callbacks.is_strong_multi_recap_request(content):
        return True
    if planner_helpers._has_fee_or_refund_dispute(content):
        return True
    if planner_helpers._is_service_response_complaint(content) or planner_helpers._has_effect_dispute(content):
        return True
    if callbacks.has_pre_visit_question(content) or callbacks.asks_store_or_address_recap(content) or callbacks.asks_price_recap(content):
        return True

    appointment_intents = {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
    non_appointment_intents = {
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
    }
    intent_set = {
        str(item.get("intent"))
        for item in (intents if isinstance(intents, list) else state.get("intents", []))
        if isinstance(item, dict) and item.get("intent")
    }
    return bool(intent_set & non_appointment_intents and not intent_set & appointment_intents)


def without_appointment_intents(intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    appointment_intents = {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
    filtered = [
        item
        for item in intents
        if isinstance(item, dict) and item.get("skill") != "appointment" and item.get("intent") not in appointment_intents
    ]
    if filtered:
        return filtered[:3]
    return [{"intent": "project_inquiry", "skill": "project_consult", "priority": 4, "reason": "当前问题不是预约确认，按普通咨询承接"}]
