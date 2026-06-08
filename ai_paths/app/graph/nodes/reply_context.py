from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.customer_need_questions import customer_friendly_type_question
from app.graph.nodes.memory_usage_policy import (
    memory_usage_policy_for_reply,
    should_suppress_profile_memory_for_reply,
)
from app.graph.nodes.intent_signals import is_broad_ad_intro
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
    suppress_profile_memory = should_suppress_profile_memory_for_reply(state)
    module_outputs = state.get("module_outputs", [])
    if suspend_active_task:
        module_outputs = [
            item for item in module_outputs if not (isinstance(item, dict) and item.get("skill") == "active_task")
        ]
    if suppress_profile_memory:
        module_outputs = []
    tool_results = {} if suppress_profile_memory else state.get("tool_results", {})
    reply_brief = callbacks.reply_brief(state)
    return {
        "content": state.get("normalized_content"),
        "conversation_history": [] if suppress_profile_memory else state.get("conversation_history", [])[-6:],
        "reply_brief": reply_brief,
        "hard_instruction": _reply_hard_instruction(state, reply_brief),
        "image_info": state.get("image_info", {}),
        "customer_profile": {} if suppress_profile_memory else state.get("customer_profile", {}),
        "customer_basic_info": {} if suppress_profile_memory else state.get("customer_basic_info", {}),
        "history_events": [] if suppress_profile_memory else state.get("history_events", [])[-8:],
        "memory_usage_policy": memory_usage_policy_for_reply(state),
        "recent_assistant_replies": [] if suppress_profile_memory else callbacks.recent_assistant_replies(state, 4),
        "guardrail_result": state.get("guardrail_result", {}),
        "sales_strategy": {} if suppress_profile_memory else sales_strategy_for_reply_model(state),
        "action_plan": {} if suppress_profile_memory else action_plan_for_reply_model(state),
        "active_task": {} if suspend_active_task else state.get("active_task", {}),
        "module_outputs": compact_module_outputs_for_model(module_outputs),
        "tool_results": compact_tool_results_for_model(
            tool_results,
            state,
            callbacks=CompactionCallbacks(
                canonical_price_project=callbacks.canonical_price_project,
                contextual_price_project=callbacks.contextual_price_project,
                is_broad_price_category=callbacks.is_broad_price_category,
            ),
        ),
    }


def _reply_hard_instruction(state: AgentState, reply_brief: dict[str, Any]) -> str:
    content = str(state.get("normalized_content") or "").strip()
    intents = {
        str(item.get("intent") or "")
        for item in (state.get("intents") or [])
        if isinstance(item, dict)
    }
    facts = reply_brief.get("available_facts", {}) if isinstance(reply_brief.get("available_facts"), dict) else {}
    sales_strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    ask_policy = str(sales_strategy.get("ask_policy") or "")
    case_asset_image_url = str(facts.get("case_asset_image_url") or "").strip()
    type_question = str(facts.get("customer_friendly_type_question") or "").strip() or customer_friendly_type_question(
        content,
        visible_concerns=facts.get("visible_concerns") if isinstance(facts.get("visible_concerns"), list) else [],
    )
    if (
        case_asset_image_url
        and ask_policy == "ask_one"
        and (intents & {"project_inquiry", "image_inquiry", "case_request"})
        and (is_broad_ad_intro(content) or any(term in content for term in ["祛斑", "淡斑", "黑色素", "抗衰", "毛孔", "暗沉"]))
    ):
        return (
            "本轮已有真实同类案例图可发送，客户又是在做宽需求了解。"
            "单条文字里必须先短承接“这类大多数都可以做”，再提一句我先给你看个同类参考，最后补一个客户听得懂的类型问题。"
            f"优先使用这个问题：{type_question or '你更像零散小点、成片颜色重一点，还是整体肤色暗沉不均？'}"
            "这个问题只能问类型判断，不能改问城市、门店、价格或项目名。"
            "必须保留问号，不能只发案例说明就收住。"
        )
    return ""


def sales_strategy_for_reply_model(state: AgentState) -> dict[str, Any]:
    strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    if not strategy:
        plan = state.get("action_plan") if isinstance(state.get("action_plan"), dict) else {}
        strategy = plan.get("sales_strategy") if isinstance(plan.get("sales_strategy"), dict) else {}
    if not strategy:
        return {}
    allowed_keys = {
        "sales_stage",
        "stage_label",
        "reason",
        "known_slots",
        "missing_slots",
        "ask_policy",
        "next_best_action",
        "push_goal",
        "reply_rhythm",
    }
    return {key: value for key, value in strategy.items() if key in allowed_keys}


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
