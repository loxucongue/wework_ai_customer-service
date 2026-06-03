from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.nodes.result_compaction import (
    CompactionCallbacks,
    compact_module_outputs_for_model,
    compact_tool_results_for_model,
)
from app.graph.state import AgentState


@dataclass(frozen=True)
class ReplyContextCallbacks:
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    is_broad_price_category: Callable[[str], bool]
    recent_assistant_replies: Callable[[AgentState, int], list[str]]
    reply_brief: Callable[[AgentState], dict[str, Any]]
    should_suspend_active_task: Callable[[AgentState, dict[str, Any] | None, list[dict[str, Any]] | None], bool]


def reply_user_payload_for_model(state: AgentState, callbacks: ReplyContextCallbacks) -> dict[str, Any]:
    suspend_active_task = callbacks.should_suspend_active_task(state, state.get("active_task", {}), state.get("intents", []))
    module_outputs = state.get("module_outputs", [])
    if suspend_active_task:
        module_outputs = [item for item in module_outputs if not (isinstance(item, dict) and item.get("skill") == "active_task")]
    return {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-6:],
        "reply_brief": callbacks.reply_brief(state),
        "image_info": state.get("image_info", {}),
        "customer_profile": state.get("customer_profile", {}),
        "customer_basic_info": state.get("customer_basic_info", {}),
        "history_events": state.get("history_events", [])[-8:],
        "recent_assistant_replies": callbacks.recent_assistant_replies(state, 4),
        "guardrail_result": state.get("guardrail_result", {}),
        "action_plan": action_plan_for_reply_model(state),
        "active_task": {} if suspend_active_task else state.get("active_task", {}),
        "module_outputs": compact_module_outputs_for_model(module_outputs),
        "tool_results": compact_tool_results_for_model(
            state.get("tool_results", {}),
            state,
            callbacks=CompactionCallbacks(
                canonical_price_project=callbacks.canonical_price_project,
                contextual_price_project=callbacks.contextual_price_project,
                is_broad_price_category=callbacks.is_broad_price_category,
            ),
        ),
    }


def action_plan_for_reply_model(state: AgentState) -> dict[str, Any]:
    plan = state.get("action_plan") if isinstance(state.get("action_plan"), dict) else {}
    if not plan:
        return {}
    if not store_lookup_missing_city(state.get("tool_results", {}) or {}):
        return plan

    cleaned = dict(plan)
    actions = []
    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        item = dict(action)
        if item.get("name") == "store" or item.get("intent") == "store_inquiry":
            item["known_info"] = []
            item["missing_info"] = ["城市或区域"]
            item["reply_goal"] = "先请客户补充所在城市或区域，再匹配门店。"
            item["should_ask"] = True
            item["tool_plan"] = [{"name": "store_lookup", "query": "", "purpose": "等待客户补充城市或区域"}]
        actions.append(item)
    cleaned["actions"] = actions
    return cleaned


def store_lookup_missing_city(tool_results: dict[str, Any]) -> bool:
    lookup = tool_results.get("store_lookup") if isinstance(tool_results, dict) else {}
    if not isinstance(lookup, dict):
        return False
    stores = lookup.get("stores")
    missing = lookup.get("missing")
    return not (isinstance(stores, list) and stores) and isinstance(missing, list) and "city" in missing
