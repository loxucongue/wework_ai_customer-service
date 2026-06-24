from __future__ import annotations

import json
from typing import Any

from app.graph.nodes.common import model_usage_snapshot
from app.graph.signals.general import is_low_information_content
from app.graph.planner.planner_contract import ALLOWED_TOOLS
from app.graph.planner.brain_v2_prompts import PLANNER_REPAIR_PROMPT, PLANNER_RISK_PATCH_PROMPT, PLANNER_SYSTEM_PROMPT
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2, safety_fallback_plan
from app.graph.state import AgentState
from app.policies.business_rules import business_rules_prompt_section
from app.services.model_client import ModelClient

def planner_v2_model_tier(state: AgentState) -> str:
    return "planner"


def planner_v2_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    payload = _planner_payload_for_model(state)
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": "# Four Stage Business Rules JSON\n" + business_rules_prompt_section()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def planner_v2_repair_messages_for_model(
    state: AgentState,
    *,
    original_plan: dict[str, Any],
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payload = {
        **_planner_payload_for_model(state),
        "original_plan": _compact_plan_for_repair(original_plan),
        "tool_policy_violations": _compact_violations_for_repair(violations),
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": "# Four Stage Business Rules JSON\n" + business_rules_prompt_section()},
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
    violations = list(plan.get("tool_policy_violations", []))
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
                "decision": plan.get("planner_decision", ""),
                "stage": plan.get("planner_stage", ""),
                "sub_rule_id": plan.get("planner_sub_rule_id", ""),
                "tool_calls": len(plan.get("planner_tool_calls", [])),
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
            "decision": plan.get("planner_decision", ""),
            "stage": plan.get("planner_stage", ""),
            "sub_rule_id": plan.get("planner_sub_rule_id", ""),
            "reply_messages": len(plan.get("planner_reply_messages", [])),
            "tool_calls": len(plan.get("planner_tool_calls", [])),
            "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
        },
        "usage": initial_usage,
    }
    if nested_calls:
        model_call["nested_calls"] = nested_calls
    return plan, model_call


def _planner_payload_for_model(state: AgentState) -> dict[str, Any]:
    suppress_memory = _should_suppress_planner_memory(state)
    payload = {
        "current_message": state.get("normalized_content") or "",
        "conversation_history": [] if suppress_memory else (state.get("conversation_history") or [])[-10:],
        "image_info": state.get("image_info") or {},
        "category_id": str(((state.get("request_context") or {}).get("category_id") or "")).strip(),
        "customer_profile": {} if suppress_memory else state.get("customer_profile") or {},
        "history_events": [] if suppress_memory else (state.get("history_events") or [])[-8:],
        "customer_context": {} if suppress_memory else _compact_customer_context(state.get("customer_context") or {}),
        "customer_store_knowledge": _compact_store_knowledge(state.get("customer_store_knowledge") or {}),
        "sales_talk_reference": _compact_sales_talk_reference(state.get("sales_talk_reference") or {}),
        "available_tools": [tool for tool in ALLOWED_TOOLS if tool != "no_tool"],
    }
    return _drop_empty(payload)


def _should_suppress_planner_memory(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or "").strip()
    if not is_low_information_content(content):
        return False
    return not any(term in content for term in ("刚刚", "刚才", "之前", "上次", "那个", "这家", "继续"))


def _compact_plan_for_repair(plan: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "decision": plan.get("planner_decision", ""),
            "stage": plan.get("planner_stage", ""),
            "sub_rule_id": plan.get("planner_sub_rule_id", ""),
            "reply_messages": plan.get("planner_reply_messages", []),
            "tool_calls": plan.get("planner_tool_calls", []),
            "handoff": plan.get("handoff", {}),
        }
    )


def _compact_violations_for_repair(violations: list[dict[str, Any]]) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for item in violations[:8]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "missing": str(item.get("missing") or ""),
                "note": str(item.get("note") or "")[:240],
            }
        )
    return [item for item in compact if item.get("missing") or item.get("note")]


def _customer_scope_stores(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


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


def _compact_store_knowledge(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    stores = raw.get("stores") if isinstance(raw.get("stores"), list) else []
    extras = raw.get("appointment_extra_stores") if isinstance(raw.get("appointment_extra_stores"), list) else []
    compact_stores = [_compact_store_brief_for_model(store) for store in stores[:260] if isinstance(store, dict)]
    compact_extras = [_compact_store_brief_for_model(store) for store in extras[:12] if isinstance(store, dict)]
    return {
        "source": raw.get("source"),
        "store_count": raw.get("store_count", len(stores)),
        "snapshot_generated_at": raw.get("snapshot_generated_at"),
        "missing_snapshot_store_ids": raw.get("missing_snapshot_store_ids", []),
        "regions": _group_store_briefs_by_region(compact_stores),
        "appointment_extra_stores": compact_extras,
    }


def _compact_store_brief_for_model(store: dict[str, Any]) -> dict[str, Any]:
    brief = {
        "id": str(store.get("store_id") or "").strip(),
        "name": str(store.get("store_name") or "").strip(),
        "province": str(store.get("province") or "").strip(),
        "city": str(store.get("city") or "").strip(),
        "district": str(store.get("district") or "").strip(),
    }
    return {key: value for key, value in brief.items() if value}


def _group_store_briefs_by_region(stores: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for store in stores:
        city = str(store.get("city") or "未识别城市").strip()
        district = str(store.get("district") or "未识别区域").strip()
        grouped.setdefault(city, {}).setdefault(district, []).append(
            {
                "id": store.get("id"),
                "name": store.get("name"),
            }
        )
    return grouped


def _compact_sales_talk_reference(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    return {
        "source": raw.get("source", ""),
        "query": raw.get("query", ""),
        "items": [
            {
                "document_id": str(item.get("document_id") or item.get("documentId") or ""),
                "content": str(item.get("content") or "")[:360],
            }
            for item in items[:3]
            if isinstance(item, dict)
        ],
        "error": raw.get("error", ""),
    }


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            compact_item = _drop_empty(item)
            if compact_item in (None, "", [], {}):
                continue
            output[key] = compact_item
        return output
    if isinstance(value, list):
        output_list = [_drop_empty(item) for item in value]
        return [item for item in output_list if item not in (None, "", [], {})]
    return value
