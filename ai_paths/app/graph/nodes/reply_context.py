from __future__ import annotations

from typing import Any

from app.graph.nodes.common import recent_assistant_replies
from app.graph.nodes.memory_usage_policy import (
    memory_usage_policy_for_reply,
    should_suppress_profile_memory_for_reply,
)
from app.graph.nodes.pricing_context import canonical_price_project, is_broad_price_category
from app.graph.runtime_context import contextual_price_project
from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_primary_task,
    planner_reply_strategy,
    planner_required_tools,
    planner_secondary_tasks,
    planner_task_views,
)
from app.graph.state import AgentState
from app.graph.runtime_turn_policy import should_suspend_appointment_context_for_current_turn


def reply_user_payload_for_model(state: AgentState) -> dict[str, Any]:
    planner_views = planner_task_views(state)
    should_show_appointment_context = not should_suspend_appointment_context_for_current_turn(state, planner_views)
    suppress_profile_memory = should_suppress_profile_memory_for_reply(state)
    fact_envelope = {} if suppress_profile_memory else (state.get("fact_envelope") or {})
    primary_task = planner_primary_task(state)
    secondary_tasks = planner_secondary_tasks(state)
    required_tools = planner_required_tools(state)
    reply_strategy = planner_reply_strategy(state)
    handoff = planner_handoff(state)
    appointment_context = _appointment_context_for_model(state) if should_show_appointment_context else {}
    return {
        "content": state.get("normalized_content"),
        "conversation_history": [] if suppress_profile_memory else state.get("conversation_history", [])[-6:],
        "image_info": state.get("image_info", {}),
        "customer_profile": {} if suppress_profile_memory else state.get("customer_profile", {}),
        "customer_basic_info": {} if suppress_profile_memory else state.get("customer_basic_info", {}),
        "history_events": [] if suppress_profile_memory else state.get("history_events", [])[-8:],
        "memory_usage_policy": memory_usage_policy_for_reply(state),
        "recent_assistant_replies": [] if suppress_profile_memory else recent_assistant_replies(state, 4),
        "guardrail_result": state.get("guardrail_result", {}),
        "primary_task": {} if suppress_profile_memory else primary_task,
        "secondary_tasks": [] if suppress_profile_memory else secondary_tasks,
        "required_tools": [] if suppress_profile_memory else required_tools,
        "reply_strategy": {} if suppress_profile_memory else reply_strategy,
        "scene_guidance_context": [] if suppress_profile_memory else state.get("scene_guidance_context", []),
        "handoff": {} if suppress_profile_memory else handoff,
        "appointment_context": {} if suppress_profile_memory else appointment_context,
        "fact_envelope": fact_envelope,
        "fact_notes": _fact_notes_for_model(
            fact_envelope,
            state,
            canonical_price_project=canonical_price_project,
            contextual_price_project=contextual_price_project,
            is_broad_price_category=is_broad_price_category,
        ),
    }


def _fact_notes_for_model(
    fact_envelope: dict[str, Any],
    state: AgentState,
    *,
    canonical_price_project,
    contextual_price_project,
    is_broad_price_category,
) -> list[str]:
    notes: list[str] = []
    project = canonical_price_project(contextual_price_project(state))
    if project and not is_broad_price_category(project):
        notes.append(f"当前推测的价格关联方向：{project}")

    structured_facts = fact_envelope.get("structured_facts") or {}
    if not isinstance(structured_facts, dict):
        structured_facts = {}

    recommended_store = structured_facts.get("recommended_store") or {}
    if isinstance(recommended_store, dict) and recommended_store.get("name"):
        notes.append("已有推荐门店事实，可优先按推荐门店回答。")

    unsupported_claims = {
        str(item).strip().lower()
        for item in (fact_envelope.get("unsupported_claims") or [])
        if str(item).strip()
    }
    if "store_lookup unavailable" in unsupported_claims:
        notes.append("门店事实查询失败，不能编造地址或营业时间。")
    if "available_time unavailable" in unsupported_claims:
        notes.append("档期事实查询失败，不能说预约已成功。")
    if "appointment record unavailable" in unsupported_claims:
        notes.append("预约记录查询失败，不能编造预约状态。")

    appointment_facts = structured_facts.get("appointment_facts") or []
    if isinstance(appointment_facts, list):
        for item in appointment_facts:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "available_time" and item.get("slots"):
                notes.append("已有档期事实，可直接回答可约时间。")
                break

    return notes[:6]


def _appointment_context_for_model(state: AgentState) -> dict[str, Any]:
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    context: dict[str, Any] = {}
    for source_key, target_key in (
        ("store_id", "store_id"),
        ("store_name", "store_name"),
        ("date", "date"),
        ("appointment_date", "date"),
        ("time", "time"),
        ("appointment_time", "time"),
        ("people_count", "people_count"),
    ):
        value = appointment_cache.get(source_key)
        text = str(value or "").strip()
        if text and target_key not in context:
            context[target_key] = text
    return context
