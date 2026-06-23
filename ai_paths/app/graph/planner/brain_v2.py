from __future__ import annotations

import json
import re
from typing import Any

from app.graph.nodes.common import model_usage_snapshot
from app.graph.signals.general import has_current_after_sales_signal, is_low_information_content
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
    violations = [*plan.get("tool_policy_violations", []), *_planner_message_contract_violations(state, plan)]
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
            post_repair_violations = _planner_message_contract_violations(state, plan)
            if _needs_transport_policy_safe_fallback(state, plan, post_repair_violations):
                plan = _transport_policy_safe_plan(state, plan)
            elif _needs_distance_tool_safe_fallback(state, plan, post_repair_violations):
                plan = _distance_tool_safe_plan(state, plan)
            elif _needs_after_sales_safe_fallback(state, post_repair_violations):
                plan = _after_sales_safe_plan(state, plan)
            elif _needs_appointment_anchor_safe_fallback(post_repair_violations):
                plan = _appointment_anchor_safe_plan(state, plan)
            elif _needs_store_detail_safe_fallback(state, post_repair_violations):
                plan = _store_detail_safe_plan(state, plan)
            elif _needs_location_context_safe_fallback(state, post_repair_violations):
                plan = _location_context_safe_plan(state, plan)
            elif _needs_case_effect_safe_fallback(state, post_repair_violations):
                plan = _case_effect_safe_plan(state, plan)
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
    from app.graph.message_send_policy import action_message_policy_for_model

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
        "action_message_policy": action_message_policy_for_model(state),
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


def _planner_message_contract_violations(state: AgentState, plan: dict[str, Any]) -> list[dict[str, str]]:
    decision = str(plan.get("planner_decision") or "").strip()
    messages = plan.get("planner_reply_messages") if isinstance(plan.get("planner_reply_messages"), list) else []
    tool_calls = plan.get("planner_tool_calls") if isinstance(plan.get("planner_tool_calls"), list) else []
    message_text = _planner_reply_text(messages)
    violations: list[dict[str, str]] = []
    if decision == "direct_reply" and not messages:
        violations.append(
            {
                "task_type": str((plan.get("primary_task") or {}).get("type") or "direct_reply"),
                "subtype": str((plan.get("primary_task") or {}).get("subtype") or ""),
                "missing": "direct_reply_reply_messages",
                "note": "decision=direct_reply must include at least one customer-visible reply_messages item.",
            }
        )
    if decision == "need_tools" and tool_calls and not messages:
        violations.append(
            {
                "task_type": str((plan.get("primary_task") or {}).get("type") or "need_tools"),
                "subtype": str((plan.get("primary_task") or {}).get("subtype") or ""),
                "missing": "need_tools_transition_reply",
                "note": "decision=need_tools with tool_calls must include one short transition reply_messages item for the synchronous platform response.",
            }
        )
    if _is_transport_policy_turn(state, plan) and any(term in message_text for term in ("车费报销", "包接送", "打车报销")):
        violations.append(
            {
                "task_type": "store_inquiry",
                "subtype": "pre_visit_transport_policy",
                "missing": "transport_policy_safe_wording",
                "note": "For S2_PRE_VISIT_TRANSPORT_POLICY, customer-visible text must say '交通费用需自理/没有接送服务' and must not repeat risky commitment terms such as 车费报销、包接送、打车报销.",
            }
        )
    has_distance_tool = any(str(item.get("name") or "") == "distance_calculate" for item in tool_calls if isinstance(item, dict))
    has_available_time_tool = any(str(item.get("name") or "") == "available_time" for item in tool_calls if isinstance(item, dict))
    if _is_distance_request_turn(state) and not has_distance_tool:
        violations.append(
            {
                "task_type": str((plan.get("primary_task") or {}).get("type") or ""),
                "subtype": str((plan.get("primary_task") or {}).get("subtype") or ""),
                "missing": "distance_calculate_required",
                "note": "Current turn asks for nearest/nearby store. Planner must call distance_calculate with customer-scope candidate store ids instead of answering directly.",
            }
        )
    if not has_distance_tool and any(term in message_text for term in ("最近", "更近", "距离", "几公里", "几分钟", "交通便利")):
        violations.append(
            {
                "task_type": str((plan.get("primary_task") or {}).get("type") or ""),
                "subtype": str((plan.get("primary_task") or {}).get("subtype") or ""),
                "missing": "distance_fact_required",
                "note": "Do not claim closest/nearer/distance/travel convenience without distance_calculate results. Ask whether to check routes or nearby stores instead.",
            }
        )
    if _is_after_sales_effect_turn(state) and str(plan.get("planner_stage") or "") != "S4":
        violations.append(
            {
                "task_type": str((plan.get("primary_task") or {}).get("type") or ""),
                "subtype": str((plan.get("primary_task") or {}).get("subtype") or ""),
                "missing": "after_sales_stage_required",
                "note": "Current turn describes after-sales/effect feedback after service. Planner must use S4 and professional_assist instead of treating it as new project consultation.",
            }
        )
    if _is_store_detail_turn(state) and _has_case_studies_tool(tool_calls):
        violations.append(
            {
                "task_type": "store_inquiry",
                "subtype": "store_detail",
                "missing": "store_detail_fact_tool_required",
                "note": "Parking/address/business-hours questions must use store facts, not kb_search(case_studies).",
            }
        )
    if _is_store_detail_turn(state) and (
        str(plan.get("planner_stage") or "") == "S4"
        or _has_professional_assist_tool(tool_calls)
        or bool((plan.get("handoff") or {}).get("needed") if isinstance(plan.get("handoff"), dict) else False)
    ):
        violations.append(
            {
                "task_type": "store_inquiry",
                "subtype": "store_detail",
                "missing": "store_detail_stage_required",
                "note": "Parking/address/business-hours questions are store detail questions. Do not route them to S4, handoff, or professional_assist unless the customer explicitly complains or asks for human handling.",
            }
        )
    if _is_store_detail_turn(state) and has_distance_tool and not _store_detail_has_anchor(state):
        violations.append(
            {
                "task_type": "store_inquiry",
                "subtype": "store_detail",
                "missing": "store_detail_anchor_required",
                "note": "Address/parking/business-hours questions need a selected store, exact store name, unique district/landmark match, or recent store card before calling distance_calculate. If the customer only asks to send an address without a selected store, ask which store or area first.",
            }
        )
    if has_available_time_tool and not _appointment_has_store_anchor(state):
        violations.append(
            {
                "task_type": "appointment",
                "subtype": "available_time",
                "missing": "appointment_store_anchor_required",
                "note": "Do not call available_time or send payment_collection when the customer has not selected a concrete store, unique district/landmark, or recent store card. Ask which store/area first.",
            }
        )
    if _is_case_effect_request_turn(state) and (
        str(plan.get("planner_stage") or "") == "S4"
        or _has_professional_assist_tool(tool_calls)
        or not _has_case_studies_tool(tool_calls)
    ):
        violations.append(
            {
                "task_type": "project_consult",
                "subtype": "case_effect_request",
                "missing": "case_studies_required",
                "note": "Customer asks to see effect/case reference. This is S1 case request and must call kb_search(case_studies), not S4 or professional_assist.",
            }
        )
    if _is_location_context_turn(state) and _has_case_studies_tool(tool_calls):
        violations.append(
            {
                "task_type": "store_inquiry",
                "subtype": "location_context",
                "missing": "location_context_not_case_request",
                "note": "Current turn only provides a city/area/landmark. Treat it as S2 store context, not a case/effect request.",
            }
        )
    if _is_location_context_turn(state) and has_distance_tool and not _is_distance_request_turn(state):
        violations.append(
            {
                "task_type": "store_inquiry",
                "subtype": "location_context",
                "missing": "location_context_direct_reply_required",
                "note": "Current turn only provides location context and does not ask nearest/distance. Do not call distance_calculate; answer from customer_store_knowledge region.",
            }
        )
    return violations


def _planner_reply_text(messages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, dict):
            chunks.extend(str(content.get(key) or "") for key in ("text", "handoff_reason", "url"))
        else:
            chunks.append(str(content or ""))
    return " ".join(chunk for chunk in chunks if chunk)


def _is_transport_policy_turn(state: AgentState, plan: dict[str, Any]) -> bool:
    primary = plan.get("primary_task") if isinstance(plan.get("primary_task"), dict) else {}
    marker = " ".join(
        str(value or "")
        for value in (
            state.get("normalized_content"),
            plan.get("planner_sub_rule_id"),
            primary.get("subtype"),
            primary.get("policy_hint"),
            primary.get("customer_need"),
            primary.get("answer_goal"),
        )
    )
    return any(term in marker for term in ("S2_PRE_VISIT_TRANSPORT_POLICY", "车费", "报销", "接送", "路费", "交通费"))


def _needs_transport_policy_safe_fallback(
    state: AgentState,
    plan: dict[str, Any],
    violations: list[dict[str, str]],
) -> bool:
    if not _is_transport_policy_turn(state, plan):
        return False
    return any(
        item.get("missing") in {"transport_policy_safe_wording", "distance_fact_required"}
        for item in violations
    )


def _needs_distance_tool_safe_fallback(
    state: AgentState,
    plan: dict[str, Any],
    violations: list[dict[str, str]],
) -> bool:
    if not _is_distance_request_turn(state):
        return False
    return any(item.get("missing") == "distance_calculate_required" for item in violations)


def _needs_after_sales_safe_fallback(state: AgentState, violations: list[dict[str, str]]) -> bool:
    return _is_after_sales_effect_turn(state) and any(item.get("missing") == "after_sales_stage_required" for item in violations)


def _needs_store_detail_safe_fallback(state: AgentState, violations: list[dict[str, str]]) -> bool:
    return _is_store_detail_turn(state) and any(
        item.get("missing") in {"store_detail_fact_tool_required", "store_detail_stage_required", "store_detail_anchor_required"}
        for item in violations
    )


def _needs_appointment_anchor_safe_fallback(violations: list[dict[str, str]]) -> bool:
    return any(item.get("missing") == "appointment_store_anchor_required" for item in violations)


def _needs_case_effect_safe_fallback(state: AgentState, violations: list[dict[str, str]]) -> bool:
    return _is_case_effect_request_turn(state) and any(item.get("missing") == "case_studies_required" for item in violations)


def _needs_location_context_safe_fallback(state: AgentState, violations: list[dict[str, str]]) -> bool:
    return _is_location_context_turn(state) and any(
        item.get("missing") in {"location_context_not_case_request", "location_context_direct_reply_required"}
        for item in violations
    )


def _transport_policy_safe_plan(state: AgentState, plan: dict[str, Any]) -> dict[str, Any]:
    safe_plan = dict(plan)
    safe_plan["planner_decision"] = "direct_reply"
    safe_plan["planner_stage"] = "S2"
    safe_plan["planner_sub_rule_id"] = "S2_PRE_VISIT_TRANSPORT_POLICY"
    safe_plan["planner_reply_messages"] = [
        {
            "type": "text",
            "order": 1,
            "content": {"text": "目前没有接送服务，交通费用需自理。我可以帮您看更方便的门店路线、停车或导航。"},
        }
    ]
    safe_plan["planner_tool_calls"] = []
    safe_plan["required_tools"] = [{"name": "no_tool", "purpose": "Transport policy can be answered directly"}]
    primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
    primary.update(
        {
            "type": "store_inquiry",
            "subtype": "pre_visit_transport_policy",
            "policy_hint": "S2_PRE_VISIT_TRANSPORT_POLICY",
            "customer_need": str(state.get("normalized_content") or "")[:120],
            "answer_goal": "Explain transport cost boundary and offer route/store support.",
            "must_answer": ["没有接送服务", "交通费用需自理", "可帮看门店路线、停车或导航"],
            "must_avoid": ["费用报销承诺", "接送承诺", "无距离事实的近距表达"],
            "tools": [{"name": "no_tool", "purpose": "Transport policy can be answered directly"}],
        }
    )
    safe_plan["primary_task"] = primary
    safe_plan["reply_strategy"] = {
        "tone": "自然、简短、像真人客服",
        "must_answer": ["没有接送服务", "交通费用需自理"],
        "can_push": "帮客户看门店路线、停车或导航",
        "must_avoid": ["费用报销承诺", "接送承诺", "无距离事实的近距表达"],
        "max_questions": 0,
    }
    return safe_plan


def _distance_tool_safe_plan(state: AgentState, plan: dict[str, Any]) -> dict[str, Any]:
    origin = str(state.get("normalized_content") or "").strip()
    city = _distance_request_city(state)
    candidate_ids = _store_ids_for_city(state, city)
    tool: dict[str, Any] = {
        "name": "distance_calculate",
        "purpose": "Need real distance before ranking customer-scope stores",
        "origin": origin,
        "candidate_store_ids": candidate_ids,
    }
    if city:
        tool["candidate_city"] = city
        tool["candidate_scope"] = "customer_store_knowledge.city"

    safe_plan = dict(plan)
    safe_plan["planner_decision"] = "need_tools"
    safe_plan["planner_stage"] = "S2"
    safe_plan["planner_sub_rule_id"] = "S2_LOCATION_DETAIL"
    safe_plan["planner_reply_messages"] = [
        {
            "type": "text",
            "order": 1,
            "content": {"text": "我帮您按这个位置核对一下更方便的门店。"},
        }
    ]
    safe_plan["planner_tool_calls"] = [tool]
    safe_plan["required_tools"] = [tool]
    safe_plan["tool_policy_violations"] = []
    primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
    primary.update(
        {
            "type": "store_inquiry",
            "subtype": "location_detail",
            "policy_hint": "S2_LOCATION_DETAIL",
            "customer_need": origin[:120],
            "answer_goal": "Use distance_calculate to rank customer-scope stores by real distance.",
            "must_answer": ["先核对真实距离，再推荐门店"],
            "must_avoid": ["无距离事实直接说最近", "编造距离或通勤时间"],
            "tools": [tool],
        }
    )
    safe_plan["primary_task"] = primary
    safe_plan["reply_strategy"] = {
        "tone": "自然、简短、像真人客服",
        "must_answer": ["先核对真实距离"],
        "can_push": "",
        "must_avoid": ["无距离事实直接说最近", "编造距离或通勤时间"],
        "max_questions": 0,
    }
    return safe_plan


def _after_sales_safe_plan(state: AgentState, plan: dict[str, Any]) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    tool = {"name": "professional_assist", "purpose": "After-sales effect feedback needs professional follow-up"}
    safe_plan = dict(plan)
    safe_plan["planner_decision"] = "need_tools"
    safe_plan["planner_stage"] = "S4"
    safe_plan["planner_sub_rule_id"] = "S4_AFTER_SALES_EFFECT_FEEDBACK"
    safe_plan["planner_reply_messages"] = [
        {"type": "text", "order": 1, "content": {"text": "这个我先帮您记录清楚，让专业同事接着核对。"}}
    ]
    safe_plan["planner_tool_calls"] = [tool]
    safe_plan["required_tools"] = [tool]
    safe_plan["tool_policy_violations"] = []
    safe_plan["handoff"] = {"needed": True, "reason": content[:180]}
    primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
    primary.update(
        {
            "type": "after_sales",
            "subtype": "effect_feedback",
            "policy_hint": "S4_AFTER_SALES_EFFECT_FEEDBACK",
            "customer_need": content[:120],
            "answer_goal": "Acknowledge after-sales effect feedback and involve professional colleague without promising result.",
            "must_answer": ["先记录售后效果反馈", "专业同事协助核对"],
            "must_avoid": ["当作新客咨询", "承诺退款或效果结果"],
            "tools": [tool],
        }
    )
    safe_plan["primary_task"] = primary
    safe_plan["reply_strategy"] = {
        "tone": "稳住情绪，短句承接",
        "must_answer": ["先记录售后效果反馈", "专业同事协助核对"],
        "can_push": "",
        "must_avoid": ["承诺退款或效果结果", "重新销售式介绍"],
        "max_questions": 1,
    }
    return safe_plan


def _store_detail_safe_plan(state: AgentState, plan: dict[str, Any]) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    if not _store_detail_has_anchor(state):
        safe_plan = dict(plan)
        safe_plan["planner_decision"] = "direct_reply"
        safe_plan["planner_stage"] = "S2"
        safe_plan["planner_sub_rule_id"] = "S2_STORE_DETAIL_NEEDS_ANCHOR"
        safe_plan["planner_reply_messages"] = [
            {
                "type": "text",
                "order": 1,
                "content": {"text": "您想查哪家门店？也可以告诉我所在区或附近地标，我确认具体门店后再发地址、停车和路线。"},
            }
        ]
        safe_plan["planner_tool_calls"] = []
        safe_plan["required_tools"] = [{"name": "no_tool", "purpose": "Store detail needs a selected store or area anchor first"}]
        safe_plan["tool_policy_violations"] = []
        primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
        primary.update(
            {
                "type": "store_inquiry",
                "subtype": "store_detail_needs_anchor",
                "policy_hint": "S2_STORE_DETAIL_NEEDS_ANCHOR",
                "customer_need": content[:120],
                "answer_goal": "Ask customer to confirm selected store, district, or landmark before sending store detail.",
                "must_answer": ["先确认具体门店、区或地标"],
                "must_avoid": ["默认推荐一家门店", "无锚点发送门店卡片", "无锚点调用距离工具"],
                "tools": [{"name": "no_tool", "purpose": "Missing store anchor"}],
            }
        )
        safe_plan["primary_task"] = primary
        return safe_plan
    city = _distance_request_city(state) or _city_from_known_store(state)
    candidate_ids = _store_ids_for_detail_turn(state, city)
    tool: dict[str, Any] = {
        "name": "distance_calculate",
        "purpose": "Need store detail facts before answering parking/address/business hours",
        "origin": _store_detail_origin(state) or content,
        "candidate_store_ids": candidate_ids,
    }
    if city:
        tool["candidate_city"] = city
        tool["candidate_scope"] = "customer_store_knowledge.city"
    safe_plan = dict(plan)
    safe_plan["planner_decision"] = "need_tools"
    safe_plan["planner_stage"] = "S2"
    safe_plan["planner_sub_rule_id"] = "S2_PARKING_OR_HOURS"
    safe_plan["planner_reply_messages"] = [
        {"type": "text", "order": 1, "content": {"text": "我帮您核对一下这家门店的具体信息。"}}
    ]
    safe_plan["planner_tool_calls"] = [tool]
    safe_plan["required_tools"] = [tool]
    safe_plan["tool_policy_violations"] = []
    primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
    primary.update(
        {
            "type": "store_inquiry",
            "subtype": "parking_or_hours",
            "policy_hint": "S2_PARKING_OR_HOURS",
            "customer_need": content[:120],
            "answer_goal": "Use store facts to answer parking/address/business-hours question.",
            "must_answer": ["基于门店事实回答停车、地址或营业时间"],
            "must_avoid": ["调用案例知识库", "编造停车或营业时间"],
            "tools": [tool],
        }
    )
    safe_plan["primary_task"] = primary
    return safe_plan


def _appointment_anchor_safe_plan(state: AgentState, plan: dict[str, Any]) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    city = _distance_request_city(state)
    if city:
        text = f"{city}有多家门店，您在哪个区或想约哪家店？我确认门店后帮您查可约时间。"
    else:
        text = "您想约哪个城市或哪家门店？我确认门店后帮您查可约时间。"
    safe_plan = dict(plan)
    safe_plan["planner_decision"] = "direct_reply"
    safe_plan["planner_stage"] = "S3"
    safe_plan["planner_sub_rule_id"] = "S3_APPOINTMENT_NEEDS_STORE"
    safe_plan["planner_reply_messages"] = [{"type": "text", "order": 1, "content": {"text": text}}]
    safe_plan["planner_tool_calls"] = []
    safe_plan["required_tools"] = [{"name": "no_tool", "purpose": "Appointment time lookup needs selected store first"}]
    safe_plan["tool_policy_violations"] = []
    primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
    primary.update(
        {
            "type": "appointment",
            "subtype": "appointment_needs_store",
            "policy_hint": "S3_APPOINTMENT_NEEDS_STORE",
            "customer_need": content[:120],
            "answer_goal": "Confirm selected store or area before checking available time.",
            "must_answer": ["先确认城市/区域/门店", "确认后再查档期"],
            "must_avoid": ["默认选择门店", "无门店锚点查档期", "无门店锚点发送预约金"],
            "tools": [{"name": "no_tool", "purpose": "Missing appointment store anchor"}],
        }
    )
    safe_plan["primary_task"] = primary
    return safe_plan


def _case_effect_safe_plan(state: AgentState, plan: dict[str, Any]) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    tool = {"name": "kb_search", "kb_name": "case_studies", "query": f"{content} 淡斑 效果 对比"}
    safe_plan = dict(plan)
    safe_plan["planner_decision"] = "need_tools"
    safe_plan["planner_stage"] = "S1"
    safe_plan["planner_sub_rule_id"] = "S1_CASE_REQUEST"
    safe_plan["planner_reply_messages"] = [
        {"type": "text", "order": 1, "content": {"text": "可以，我帮您找下同类型的改善参考。"}}
    ]
    safe_plan["planner_tool_calls"] = [tool]
    safe_plan["required_tools"] = [tool]
    safe_plan["tool_policy_violations"] = []
    safe_plan["handoff"] = {"needed": False, "reason": ""}
    primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
    primary.update(
        {
            "type": "project_consult",
            "subtype": "case_request",
            "policy_hint": "S1_CASE_REQUEST",
            "customer_need": content[:120],
            "answer_goal": "Fetch case image facts and reply with real case reference only.",
            "must_answer": ["查找真实案例参考"],
            "must_avoid": ["转人工", "编造图片链接", "承诺每个人效果一致"],
            "tools": [tool],
        }
    )
    safe_plan["primary_task"] = primary
    return safe_plan


def _location_context_safe_plan(state: AgentState, plan: dict[str, Any]) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    stores = _stores_matching_text_region(state, content)
    safe_plan = dict(plan)
    safe_plan["planner_decision"] = "direct_reply"
    safe_plan["planner_stage"] = "S2"
    safe_plan["planner_sub_rule_id"] = "S2_LOCATION_DETAIL" if stores else "S2_CITY_ONLY"
    if len(stores) == 1:
        store = stores[0]
        store_id = str(store.get("store_id") or "").strip()
        store_name = str(store.get("store_name") or "").strip()
        safe_plan["planner_reply_messages"] = [
            {
                "type": "text",
                "order": 1,
                "content": {"text": f"{store_name}可以安排，我把门店卡片发您。您看今天还是明天方便到店检测？"},
            },
            {"type": "store_address", "order": 2, "content": {"store_id": store_id}},
        ]
    else:
        city = _distance_request_city(state)
        if city:
            safe_plan["planner_reply_messages"] = [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": f"{city}有门店，您在哪个区或附近哪个地标？我帮您看更方便的门店。"},
                }
            ]
        else:
            safe_plan["planner_reply_messages"] = [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "您在哪个城市或哪个区？我帮您看更方便的门店。"},
                }
            ]
    safe_plan["planner_tool_calls"] = []
    safe_plan["required_tools"] = [{"name": "no_tool", "purpose": "Location context can be answered from customer_store_knowledge"}]
    safe_plan["tool_policy_violations"] = []
    safe_plan["handoff"] = {"needed": False, "reason": ""}
    primary = dict(safe_plan.get("primary_task") if isinstance(safe_plan.get("primary_task"), dict) else {})
    primary.update(
        {
            "type": "store_inquiry",
            "subtype": "location_context",
            "policy_hint": safe_plan["planner_sub_rule_id"],
            "customer_need": content[:120],
            "answer_goal": "Use customer-scope store regions to continue store/appointment cadence.",
            "must_answer": ["承接客户位置", "推进到门店或到店时间"],
            "must_avoid": ["误查案例库", "编造门店详情"],
            "tools": [{"name": "no_tool", "purpose": "Store region is already in context"}],
        }
    )
    safe_plan["primary_task"] = primary
    return safe_plan


def _is_distance_request_turn(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "").strip()
    if not text:
        return False
    has_store_target = any(term in text for term in ("门店", "店", "地址", "哪里", "哪家"))
    has_distance_need = any(term in text for term in ("最近", "附近", "更近", "离", "距离", "几公里", "几分钟", "机场", "地铁站", "商圈"))
    return has_store_target and has_distance_need


def _is_after_sales_effect_turn(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "").strip()
    if _is_case_effect_request_turn(state):
        return False
    if not has_current_after_sales_signal(text):
        return False
    hypothetical = any(term in text for term in ("会不会", "怕", "担心", "如果", "万一", "有没有风险", "怎么办"))
    actual_after_service = any(
        term in text
        for term in (
            "已经做",
            "做过",
            "做完了",
            "做了之后",
            "术后",
            "恢复期",
            "昨天做",
            "前天做",
            "刚做",
            "现在不舒服",
            "一直不舒服",
            "退款",
            "退钱",
            "投诉",
            "维权",
        )
    ) or "做完没效果" in text
    symptom_after_service = actual_after_service and any(
        term in text for term in ("没效果", "反黑", "红肿", "流脓", "出血", "疼", "痛", "不满意")
    )
    if hypothetical and not symptom_after_service:
        return False
    if symptom_after_service:
        return True
    return "没效果" in text and not any(term in text for term in ("怕没效果", "担心没效果", "会不会没效果", "如果没效果"))


def _is_store_detail_turn(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "").strip()
    return bool(text) and any(term in text for term in ("停车", "停车场", "营业时间", "几点开", "几点关", "地址", "位置", "定位", "导航", "路线"))


def _store_detail_has_anchor(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "").strip()
    if any(str(state.get(key) or "").strip() for key in ("confirmed_store_id", "store_id")):
        return True
    for store in _customer_scope_stores(state):
        name = str(store.get("store_name") or "").strip()
        if name and name in text:
            return True
    if len(_stores_matching_text_region(state, text)) == 1:
        return True
    return bool(_history_store_detail_anchor_id(state))


def _appointment_has_store_anchor(state: AgentState) -> bool:
    return _store_detail_has_anchor(state)


def _history_store_detail_anchor_id(state: AgentState) -> str:
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in reversed(events[-20:]):
        if not isinstance(event, dict) or str(event.get("event_type") or "") != "store_address_sent":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        value = str(facts.get("store_id") or facts.get("id") or "").strip()
        if value:
            return value
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in reversed(history[-8:]):
        raw = str(item or "")
        parsed = _store_id_from_text(raw)
        if parsed:
            return parsed
        if any(term in raw for term in ("地址", "门店卡片", "停车", "营业时间")):
            for store in _customer_scope_stores(state):
                name = str(store.get("store_name") or "").strip()
                store_id = str(store.get("store_id") or "").strip()
                if name and store_id and name in raw:
                    return store_id
    return ""


def _store_id_from_text(text: str) -> str:
    match = re.search(r'"store_id"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1).strip()
    match = re.search(r"store_address[:：]\s*(\d+)", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"门店卡片[^0-9]*(\d{2,})", text)
    return match.group(1).strip() if match else ""


def _is_case_effect_request_turn(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "").strip()
    if not text:
        return False
    if any(term in text for term in ("没效果", "不满意", "退款", "投诉", "维权")):
        return False
    return any(term in text for term in ("效果图", "案例", "对比", "参考", "做完效果", "做完后的效果", "看效果", "看看效果"))


def _is_location_context_turn(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "").strip()
    if not text:
        return False
    if any(term in text for term in ("效果", "案例", "对比", "参考", "多少钱", "价格", "预约金", "退款", "投诉")):
        return False
    if any(term in text for term in ("我在", "这边", "附近", "常去", "区", "市", "机场", "地铁", "商圈")):
        return bool(_distance_request_city(state) or _stores_matching_text_region(state, text))
    return False


def _has_case_studies_tool(tool_calls: list[dict[str, Any]]) -> bool:
    for tool in tool_calls:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("name") or "") == "kb_search" and str(tool.get("kb_name") or "") == "case_studies":
            return True
    return False


def _has_professional_assist_tool(tool_calls: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(tool, dict) and str(tool.get("name") or "") == "professional_assist"
        for tool in tool_calls
    )


def _distance_request_city(state: AgentState) -> str:
    text = str(state.get("normalized_content") or "").strip()
    stores = _customer_scope_stores(state)
    city_names = sorted({str(store.get("city") or "").strip() for store in stores if store.get("city")}, key=len, reverse=True)
    for city in city_names:
        short_city = city[:-1] if city.endswith("市") else city
        if city and (city in text or (short_city and short_city in text)):
            return city
    district_to_city = {
        str(store.get("district") or "").strip(): str(store.get("city") or "").strip()
        for store in stores
        if store.get("district") and store.get("city")
    }
    for district, city in sorted(district_to_city.items(), key=lambda item: len(item[0]), reverse=True):
        short_district = district[:-1] if district.endswith(("区", "县", "镇")) else district
        if district in text or (short_district and short_district in text):
            return city
    return ""


def _city_from_known_store(state: AgentState) -> str:
    text = str(state.get("normalized_content") or "").strip()
    for store in _customer_scope_stores(state):
        name = str(store.get("store_name") or "").strip()
        if name and name in text:
            return str(store.get("city") or "").strip()
    return ""


def _store_ids_for_detail_turn(state: AgentState, city: str) -> list[str]:
    text = str(state.get("normalized_content") or "").strip()
    exact_ids: list[str] = []
    for store in _customer_scope_stores(state):
        name = str(store.get("store_name") or "").strip()
        store_id = str(store.get("store_id") or "").strip()
        if name and store_id and name in text:
            exact_ids.append(store_id)
    if exact_ids:
        return exact_ids
    return _store_ids_for_city(state, city)


def _store_detail_origin(state: AgentState) -> str:
    text = str(state.get("normalized_content") or "").strip()
    for store in _customer_scope_stores(state):
        name = str(store.get("store_name") or "").strip()
        if name and name in text:
            return str(store.get("store_address") or name).strip()
    return ""


def _store_ids_for_city(state: AgentState, city: str) -> list[str]:
    if not city:
        return []
    ids: list[str] = []
    for store in _customer_scope_stores(state):
        if str(store.get("city") or "").strip() != city:
            continue
        store_id = str(store.get("store_id") or "").strip()
        if store_id:
            ids.append(store_id)
    return list(dict.fromkeys(ids))


def _customer_scope_stores(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _stores_matching_text_region(state: AgentState, text: str) -> list[dict[str, Any]]:
    stores = _customer_scope_stores(state)
    for key in ("store_name", "district", "city"):
        matches: list[dict[str, Any]] = []
        for store in stores:
            value = str(store.get(key) or "").strip()
            tokens = [value]
            if key in {"city", "district"} and value.endswith(("市", "区", "县")) and len(value) > 1:
                tokens.append(value[:-1])
            if any(token and token in text for token in tokens):
                matches.append(store)
        if matches:
            unique: dict[str, dict[str, Any]] = {}
            for store in matches:
                store_id = str(store.get("store_id") or "").strip()
                if store_id:
                    unique[store_id] = store
            return list(unique.values())
    return []


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
