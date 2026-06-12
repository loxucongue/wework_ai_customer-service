from __future__ import annotations

import json
from typing import Any

from app.graph.nodes.common import model_usage_snapshot
from app.graph.planner.planner_contract import SHORT_GREETING_TOKENS
from app.graph.planner.brain_v2_prompts import PLANNER_REPAIR_PROMPT, PLANNER_RISK_PATCH_PROMPT, PLANNER_SYSTEM_PROMPT
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2, safety_fallback_plan
from app.graph.state import AgentState
from app.prompts.business_strategy import BUSINESS_STRATEGY_PROMPT
from app.services.model_client import ModelClient

def planner_v2_model_tier(state: AgentState) -> str:
    content = str(state.get("normalized_content") or "").strip()
    has_image = bool((state.get("image_info") or {}).get("has_image"))
    if not has_image and content in SHORT_GREETING_TOKENS:
        return "fast"
    return "balanced"


def planner_v2_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    payload = {
        "current_message": state.get("normalized_content") or "",
        "message_type": _message_type(state),
        "conversation_history": (state.get("conversation_history") or [])[-10:],
        "image_info": state.get("image_info") or {},
        "category_id": str(((state.get("request_context") or {}).get("category_id") or "")).strip(),
        "request_context": _compact_request_context(state.get("request_context") or {}),
        "customer_profile": state.get("customer_profile") or {},
        "customer_basic_info": state.get("customer_basic_info") or {},
        "history_events": (state.get("history_events") or [])[-8:],
        "appointment_cache": state.get("appointment_cache") or {},
        "customer_context": _compact_customer_context(state.get("customer_context") or {}),
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": BUSINESS_STRATEGY_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def planner_v2_repair_messages_for_model(
    state: AgentState,
    *,
    original_plan: dict[str, Any],
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payload = {
        "current_message": state.get("normalized_content") or "",
        "message_type": _message_type(state),
        "conversation_history": (state.get("conversation_history") or [])[-10:],
        "image_info": state.get("image_info") or {},
        "category_id": str(((state.get("request_context") or {}).get("category_id") or "")).strip(),
        "request_context": _compact_request_context(state.get("request_context") or {}),
        "customer_profile": state.get("customer_profile") or {},
        "customer_basic_info": state.get("customer_basic_info") or {},
        "history_events": (state.get("history_events") or [])[-8:],
        "appointment_cache": state.get("appointment_cache") or {},
        "customer_context": _compact_customer_context(state.get("customer_context") or {}),
        "original_plan": original_plan,
        "tool_policy_violations": violations,
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": BUSINESS_STRATEGY_PROMPT},
        {"role": "system", "content": PLANNER_REPAIR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


async def run_planner_brain_v2(
    state: AgentState,
    model_client: ModelClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tier = planner_v2_model_tier(state)
    payload = await model_client.chat_json(planner_v2_messages_for_model(state), tier=tier, temperature=0.1)
    plan = build_planner_plan_v2(state, payload)
    initial_usage = model_usage_snapshot(model_client)
    nested_calls: list[dict[str, Any]] = []
    violations = plan.get("tool_policy_violations", [])
    if violations:
        repair_call: dict[str, Any] = {
            "name": "planner_brain_repair",
            "input": {"tier": tier, "violations": violations},
        }
        try:
            repaired_payload = await model_client.chat_json(
                planner_v2_repair_messages_for_model(
                    state,
                    original_plan=plan,
                    violations=violations,
                ),
                tier=tier,
                temperature=0.0,
            )
            repaired_plan = build_planner_plan_v2(state, repaired_payload)
            plan = repaired_plan
            repair_call["output"] = {
                "primary_task": plan.get("primary_task", {}).get("type", ""),
                "required_tools": len(plan.get("required_tools", [])),
                "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
            }
            repair_call["usage"] = model_usage_snapshot(model_client)
        except Exception as exc:
            repair_call["error"] = f"{type(exc).__name__}: {exc}"
            repair_call["usage"] = model_usage_snapshot(model_client)
        nested_calls.append(repair_call)
    model_call = {
        "name": "planner_brain_v2",
        "input": {"tier": tier},
        "output": {
            "primary_task": plan.get("primary_task", {}).get("type", ""),
            "secondary_tasks": len(plan.get("secondary_tasks", [])),
            "required_tools": len(plan.get("required_tools", [])),
            "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
        },
        "usage": initial_usage,
    }
    if nested_calls:
        model_call["nested_calls"] = nested_calls
    return plan, model_call


def _message_type(state: AgentState) -> str:
    image_info = state.get("image_info") or {}
    if image_info.get("has_image"):
        return str(image_info.get("image_type") or "image")
    return "text"


def _compact_customer_context(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keys = (
        "city",
        "confirmed_store_id",
        "confirmed_store_name",
        "detected_city",
        "appointment_info",
        "has_upcoming_appointment",
        "latest_store_candidates",
    )
    return {key: raw.get(key) for key in keys if raw.get(key) not in (None, "", [], {})}


def _compact_request_context(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keys = (
        "category_id",
        "customer_stage",
        "scene_type",
        "business_logic",
        "expected_policy_family_id",
        "confirmed_store_id",
        "confirmed_store_name",
        "store_id",
        "store_name",
        "appointment_id",
        "appointment_time",
    )
    return {key: raw.get(key) for key in keys if raw.get(key) not in (None, "", [], {})}
