from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def planner_primary_task(state: AgentState) -> dict[str, Any]:
    value = state.get("primary_task")
    return value if isinstance(value, dict) else {}


def planner_secondary_tasks(state: AgentState) -> list[dict[str, Any]]:
    value = state.get("secondary_tasks")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def planner_tasks(state: AgentState) -> list[dict[str, Any]]:
    primary = planner_primary_task(state)
    secondary = planner_secondary_tasks(state)
    return [primary, *secondary] if primary else []


def planner_required_tools(state: AgentState) -> list[dict[str, Any]]:
    value = state.get("required_tools")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def planner_reply_strategy(state: AgentState) -> dict[str, Any]:
    value = state.get("reply_strategy")
    return value if isinstance(value, dict) else {}


def planner_handoff(state: AgentState) -> dict[str, Any]:
    value = state.get("handoff")
    return value if isinstance(value, dict) else {}


def planner_sop_stage(state: AgentState) -> str:
    return str(state.get("planner_stage") or "").strip()


def planner_sop_step(state: AgentState) -> str:
    return str(state.get("planner_sub_rule_id") or "").strip()


def planner_sop_stage_rules(state: AgentState) -> dict[str, Any]:
    del state
    return {}


def planner_task_views(state: AgentState) -> list[dict[str, Any]]:
    decision = str(state.get("planner_decision") or "").strip()
    stage = str(state.get("planner_stage") or "").strip()
    sub_rule_id = str(state.get("planner_sub_rule_id") or "").strip()
    conversion_stage = str(state.get("conversion_stage") or "").strip()
    customer_type = str(state.get("customer_type") or "").strip()
    main_blocker = str(state.get("main_blocker") or "").strip()
    next_step = str(state.get("next_step") or "").strip()
    if not (decision or stage or sub_rule_id):
        return []
    return [
        {
            "intent": sub_rule_id,
            "type": stage,
            "subtype": sub_rule_id,
            "scene": stage,
            "subflow": decision,
            "reason": "",
            "conversion_stage": conversion_stage,
            "customer_type": customer_type,
            "main_blocker": main_blocker,
            "next_step": next_step,
        }
    ]


def planner_project_hints(state: AgentState) -> list[str]:
    del state
    return []


def planner_scene(state: AgentState) -> str:
    return str(state.get("planner_stage") or "").strip()


def planner_public_route(state: AgentState) -> dict[str, Any]:
    handoff = planner_handoff(state)
    decision = str(state.get("planner_decision") or "").strip()
    stage = str(state.get("planner_stage") or "").strip()
    sub_rule_id = str(state.get("planner_sub_rule_id") or "").strip()
    conversion_stage = str(state.get("conversion_stage") or "").strip()
    customer_type = str(state.get("customer_type") or "").strip()
    main_blocker = str(state.get("main_blocker") or "").strip()
    next_step = str(state.get("next_step") or "").strip()
    return {
        "scene": stage,
        "intent": sub_rule_id,
        "subflow": decision,
        "reason": "",
        "conversion_stage": conversion_stage,
        "customer_type": customer_type,
        "main_blocker": main_blocker,
        "next_step": next_step,
        "confidence": 0.0,
        "need_human": bool(handoff.get("needed")),
        "policy_id": "",
        "policy_family_id": "",
        "exact_policy_id": "",
        "policy_match_level": "",
        "sop_stage": stage,
        "sop_step": sub_rule_id,
    }
