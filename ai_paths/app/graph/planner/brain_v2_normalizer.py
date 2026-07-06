from __future__ import annotations

import json
from datetime import date
import re
from pathlib import Path
from typing import Any

from app.graph.nodes.contextual_short_message import is_contextual_short_message
from app.graph.nodes.current_turn_context import build_current_turn_context
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.planner.planner_contract import (
    ALLOWED_CONVERSION_STAGES,
    ALLOWED_CUSTOMER_TYPES,
    ALLOWED_KBS,
    ALLOWED_MAIN_BLOCKERS,
    ALLOWED_NEXT_STEPS,
    ALLOWED_TOOLS,
)
from app.graph.state import AgentState
from app.policies.constants import KNOWN_STORE_NAMES
from app.services.payment_collection import (
    normalize_deposit_refund_policy_text,
    payment_collection_content,
    payment_collection_context,
)
from app.services.risk_hold import HEALTH_RISK_TERMS, explicit_professional_assist_reason, health_risk_hold, is_hard_health_risk_hold


_STORE_SNAPSHOT_NAME_CACHE: list[str] | None = None


def build_planner_plan_v2(state: AgentState, model_payload: dict[str, Any]) -> dict[str, Any]:
    explicit_risk_reason = explicit_professional_assist_reason(state)
    risk_hold = health_risk_hold(state)
    decision = _normalize_decision(model_payload.get("decision") if isinstance(model_payload, dict) else "")
    stage = str(model_payload.get("stage") or "").strip() if isinstance(model_payload, dict) else ""
    sub_rule_id = str(model_payload.get("sub_rule_id") or "").strip() if isinstance(model_payload, dict) else ""
    conversion_stage = _normalize_enum(
        model_payload.get("conversion_stage") if isinstance(model_payload, dict) else "",
        ALLOWED_CONVERSION_STAGES,
        "",
    )
    customer_type = _normalize_enum(
        model_payload.get("customer_type") if isinstance(model_payload, dict) else "",
        ALLOWED_CUSTOMER_TYPES,
        "unknown",
    )
    main_blocker = _normalize_enum(
        model_payload.get("main_blocker") if isinstance(model_payload, dict) else "",
        ALLOWED_MAIN_BLOCKERS,
        "none",
    )
    next_step = _normalize_enum(
        model_payload.get("next_step") if isinstance(model_payload, dict) else "",
        ALLOWED_NEXT_STEPS,
        "no_action",
    )
    planner_reply_messages = _normalize_reply_messages(
        model_payload.get("reply_messages") if isinstance(model_payload, dict) else [],
        state=state,
    )
    planner_tool_calls = _normalize_tools(model_payload.get("tool_calls") if isinstance(model_payload, dict) else [])
    reply_constraints = _clean_str_list(model_payload.get("reply_constraints") if isinstance(model_payload, dict) else [])
    handoff_raw = model_payload.get("handoff") if isinstance(model_payload, dict) else {}
    memory_update_raw = model_payload.get("memory_update_hint") if isinstance(model_payload, dict) else {}

    primary_task: dict[str, Any] = {}
    secondary_tasks: list[dict[str, Any]] = []

    reply_strategy: dict[str, Any] = {}
    if risk_hold:
        reply_strategy["risk_hold"] = risk_hold
    required_tools = _dedupe_tools(planner_tool_calls)
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    required_tools = _rewrite_reference_store_lookup_queries(required_tools, state)
    executable_tools = [tool for tool in required_tools if tool.get("name") != "no_tool"]
    generic_store_guard = _generic_store_lookup_guard(required_tools, state)
    if generic_store_guard:
        decision = generic_store_guard["decision"]
        stage = generic_store_guard["stage"]
        sub_rule_id = generic_store_guard["sub_rule_id"]
        conversion_stage = generic_store_guard["conversion_stage"]
        customer_type = generic_store_guard["customer_type"]
        main_blocker = generic_store_guard["main_blocker"]
        next_step = generic_store_guard["next_step"]
        planner_reply_messages = generic_store_guard["reply_messages"]
        required_tools = generic_store_guard["required_tools"]
        executable_tools = []
        reply_strategy["current_turn_context_guard"] = generic_store_guard["guard_reason"]
    if (
        explicit_risk_reason
        and decision == "direct_reply"
        and planner_reply_messages
        and not executable_tools
    ):
        conversion_stage = "objection_resolution"
        customer_type = "risk"
        main_blocker = "risk"
        next_step = "solve_blocker"
        planner_reply_messages = _remove_payment_collection_messages(planner_reply_messages)
        if not _has_handoff_notice(planner_reply_messages):
            planner_reply_messages.append(
                {
                    "type": "human_handoff_notice",
                    "order": len(planner_reply_messages) + 1,
                    "content": {"handoff_reason": explicit_risk_reason},
                }
            )
        handoff_raw = {"needed": True, "reason": explicit_risk_reason}
    elif explicit_risk_reason:
        decision = "need_tools"
        stage = stage or "S4"
        sub_rule_id = sub_rule_id or "S4_PROFESSIONAL_ASSIST"
        conversion_stage = "objection_resolution"
        customer_type = "risk"
        main_blocker = "risk"
        next_step = "solve_blocker"
        planner_reply_messages = [_standard_transition_message()]
        required_tools = [{"name": "professional_assist", "reason": explicit_risk_reason}]
        executable_tools = required_tools
        handoff_raw = {"needed": True, "reason": explicit_risk_reason}
    elif _has_store_address_message(planner_reply_messages) and not executable_tools:
        lookup_query = _store_lookup_query_from_state(state)
        planner_reply_messages = [_standard_transition_message()]
        required_tools = [
            {
                "name": "customer_store_lookup",
                "purpose": "detail",
                "query": lookup_query,
            }
        ]
        executable_tools = required_tools
        decision = "need_tools"
    elif (
        decision == "direct_reply"
        and not executable_tools
        and _current_message_requests_store_detail(str(state.get("normalized_content") or state.get("content") or ""))
    ):
        lookup_query = _store_lookup_query_from_state(state)
        planner_reply_messages = [_standard_transition_message()]
        required_tools = [
            {
                "name": "customer_store_lookup",
                "purpose": "detail",
                "query": lookup_query,
            }
        ]
        executable_tools = required_tools
        decision = "need_tools"
    if executable_tools and decision == "direct_reply":
        decision = "need_tools"
    if decision == "need_tools":
        planner_reply_messages = [_standard_transition_message()]
    if _should_force_store_detail_stage(state, required_tools):
        stage = "S2"
        sub_rule_id = "S2_STORE_ADDRESS"
        conversion_stage = "store_match"
        customer_type = "distance"
        main_blocker = "logistics"
        next_step = "lookup_store"
        if handoff_raw and isinstance(handoff_raw, dict) and _mentions_health_risk_text(str(handoff_raw.get("reason") or "")):
            handoff_raw = {"needed": False, "reason": ""}
        reply_constraints.append("当前消息是门店地址/导航/停车/位置详情查询，本轮只查门店事实；不要保留 deposit_push/send_deposit。")
    if is_hard_health_risk_hold(risk_hold) and not explicit_risk_reason:
        planner_reply_messages = _remove_payment_collection_messages(planner_reply_messages)
        if conversion_stage == "deposit_push":
            conversion_stage = "time_confirm"
        if next_step == "send_deposit":
            next_step = "confirm_time"
        reply_constraints.append("健康/过敏高风险未完成到店检测前，先确认检测和到店安排，不发送 payment_collection。")
    turn_guard = _current_turn_context_guard(state, risk_hold=risk_hold)
    if turn_guard:
        decision = turn_guard["decision"]
        stage = turn_guard["stage"]
        sub_rule_id = turn_guard["sub_rule_id"]
        conversion_stage = turn_guard["conversion_stage"]
        customer_type = turn_guard["customer_type"]
        main_blocker = turn_guard["main_blocker"]
        next_step = turn_guard["next_step"]
        planner_reply_messages = turn_guard["reply_messages"]
        required_tools = turn_guard["required_tools"]
        executable_tools = [tool for tool in required_tools if tool.get("name") != "no_tool"]
        if turn_guard.get("handoff"):
            handoff_raw = turn_guard["handoff"]
        reply_constraints.extend(turn_guard.get("reply_constraints") or [])
        reply_strategy["current_turn_context_guard"] = turn_guard.get("guard_reason", "")
    advisory_health_guard = _advisory_health_professional_assist_guard(
        state=state,
        risk_hold=risk_hold,
        explicit_risk_reason=explicit_risk_reason,
        required_tools=required_tools,
        handoff_raw=handoff_raw,
    )
    if advisory_health_guard:
        decision = advisory_health_guard["decision"]
        stage = advisory_health_guard["stage"]
        sub_rule_id = advisory_health_guard["sub_rule_id"]
        conversion_stage = advisory_health_guard["conversion_stage"]
        customer_type = advisory_health_guard["customer_type"]
        main_blocker = advisory_health_guard["main_blocker"]
        next_step = advisory_health_guard["next_step"]
        planner_reply_messages = advisory_health_guard["reply_messages"]
        required_tools = advisory_health_guard["required_tools"]
        executable_tools = []
        handoff_raw = advisory_health_guard["handoff"]
        reply_constraints.extend(advisory_health_guard.get("reply_constraints") or [])
        reply_strategy["current_turn_context_guard"] = advisory_health_guard.get("guard_reason", "")
    elif not explicit_risk_reason and not is_hard_health_risk_hold(risk_hold):
        cleaned_messages, removed_advisory_handoff = _remove_advisory_health_handoff_notices(planner_reply_messages)
        if removed_advisory_handoff:
            planner_reply_messages = cleaned_messages
            handoff_raw = {"needed": False, "reason": ""}
            reply_constraints.append("历史健康风险只作为到店检测提醒；当前消息没有再次提病史/过敏/严重不适时，不输出 human_handoff_notice。")
            reply_strategy["current_turn_context_guard"] = "advisory_health_history_removed_handoff_notice"
    planner_reply_messages = _append_required_payment_collection(
        state=state,
        decision=decision,
        conversion_stage=conversion_stage,
        next_step=next_step,
        messages=planner_reply_messages,
    )
    handoff = _normalize_handoff(handoff_raw)
    tool_policy_violations = [
        *_rejected_tool_violations(model_payload.get("tool_calls") if isinstance(model_payload, dict) else []),
        *_tool_policy_violations(required_tools, state),
        *_store_detail_tool_violations(
            decision=decision,
            messages=planner_reply_messages,
            required_tools=required_tools,
            state=state,
        ),
        *_distance_tool_violations(required_tools),
        *_direct_reply_message_violations(
            decision=decision,
            messages=planner_reply_messages,
        ),
        *_direct_reply_store_consistency_violations(
            state=state,
            decision=decision,
            messages=planner_reply_messages,
        ),
        *_payment_consistency_violations(
            state=state,
            decision=decision,
            conversion_stage=conversion_stage,
            next_step=next_step,
            messages=planner_reply_messages,
        ),
        *_two_text_rhythm_violations(
            state=state,
            decision=decision,
            conversion_stage=conversion_stage,
            next_step=next_step,
            messages=planner_reply_messages,
        ),
        *_pending_lookup_reply_violations(
            decision=decision,
            messages=planner_reply_messages,
        ),
        *_appointment_availability_reply_violations(
            decision=decision,
            messages=planner_reply_messages,
        ),
    ]
    memory_update_hint = _normalize_memory_hint(memory_update_raw)

    return {
        "planner_decision": decision,
        "planner_stage": stage,
        "planner_sub_rule_id": sub_rule_id,
        "conversion_stage": conversion_stage,
        "customer_type": customer_type,
        "main_blocker": main_blocker,
        "next_step": next_step,
        "planner_reply_messages": planner_reply_messages,
        "planner_tool_calls": executable_tools,
        "reply_constraints": reply_constraints,
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "required_tools": required_tools,
        "tool_policy_violations": tool_policy_violations,
        "reply_strategy": reply_strategy,
        "handoff": handoff,
        "memory_update_hint": memory_update_hint,
    }


def safety_fallback_plan(state: AgentState, *, reason: str = "Planner unavailable") -> dict[str, Any]:
    handoff_reason = _fallback_handoff_reason(reason)
    return build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "HUMAN_HANDOFF_SYSTEM_UNAVAILABLE",
            "conversion_stage": "objection_resolution",
            "customer_type": "unknown",
            "main_blocker": "trust",
            "next_step": "solve_blocker",
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "这边先帮您把情况记录清楚，按实际情况核对后再处理。"},
                },
                {
                    "type": "human_handoff_notice",
                    "order": 2,
                    "content": {"handoff_reason": handoff_reason},
                }
            ],
            "tool_calls": [],
            "handoff": {"needed": True, "reason": reason or "Planner unavailable"},
        },
    )


def _fallback_handoff_reason(reason: str) -> str:
    text = " ".join(str(reason or "Planner unavailable").split())
    if text in {"Planner unavailable", ""}:
        return "模型调用失败，需要专业同事协助核对。"
    if len(text) > 180:
        text = text[:177] + "..."
    return f"模型调用失败：{text}"


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip()
    return decision if decision in {"direct_reply", "need_tools", "no_reply"} else "need_tools"


def _standard_transition_message() -> dict[str, Any]:
    return {"type": "text", "order": 1, "content": {"text": "稍等一下哈"}}


def _should_force_store_detail_stage(state: AgentState, required_tools: list[dict[str, Any]]) -> bool:
    content = str(state.get("normalized_content") or state.get("content") or "")
    if not _current_message_requests_store_detail(content):
        return False
    return any(
        isinstance(tool, dict) and str(tool.get("name") or "") == "customer_store_lookup"
        for tool in required_tools
    )


def _current_turn_context_guard(state: AgentState, *, risk_hold: dict[str, Any]) -> dict[str, Any]:
    turn_context = _turn_context_for_guard(state)
    open_task = str(turn_context.get("open_task") or "").strip()
    if open_task == "post_deposit_store_assignment":
        return _post_deposit_store_assignment_guard(turn_context, risk_hold=risk_hold)
    if open_task == "post_deposit_next_step_clarification":
        return _post_deposit_next_step_guard(turn_context, risk_hold=risk_hold)
    if open_task == "health_risk_followup":
        return _health_risk_followup_guard(turn_context, risk_hold=risk_hold)
    return {}


def _advisory_health_professional_assist_guard(
    *,
    state: AgentState,
    risk_hold: dict[str, Any],
    explicit_risk_reason: str,
    required_tools: list[dict[str, Any]],
    handoff_raw: Any,
) -> dict[str, Any]:
    if explicit_risk_reason or is_hard_health_risk_hold(risk_hold):
        return {}
    if not any(isinstance(tool, dict) and str(tool.get("name") or "") == "professional_assist" for tool in required_tools):
        return {}
    reason_text = _handoff_and_tool_reason_text(handoff_raw, required_tools)
    if not _mentions_health_risk_text(reason_text) and str(risk_hold.get("risk_hold") or "") != "health_check_context":
        return {}
    turn_context = _turn_context_for_guard(state)
    open_task = str(turn_context.get("open_task") or "").strip()
    if open_task == "health_risk_followup":
        return {}
    message = _advisory_health_context_message(turn_context, state)
    return _guard_plan(
        stage="S3",
        sub_rule_id="S3_APPOINTMENT_TIME",
        conversion_stage="time_confirm",
        customer_type="time",
        main_blocker="time",
        next_step="confirm_time",
        messages=[_text_message(message)],
        handoff=False,
        handoff_reason="",
        guard_reason="advisory_health_history_demoted_from_professional_assist",
        constraints=["历史健康风险只作为到店检测提醒；当前消息没有再次提病史/过敏/严重不适时，不调用 professional_assist。"],
    )


def _handoff_and_tool_reason_text(handoff_raw: Any, required_tools: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    if isinstance(handoff_raw, dict):
        chunks.append(str(handoff_raw.get("reason") or ""))
    for tool in required_tools:
        if isinstance(tool, dict):
            chunks.append(str(tool.get("reason") or tool.get("purpose") or ""))
    return "\n".join(chunk for chunk in chunks if chunk)


def _mentions_health_risk_text(text: str) -> bool:
    raw = str(text or "")
    return "健康风险" in raw or any(term in raw for term in HEALTH_RISK_TERMS)


def _advisory_health_context_message(turn_context: dict[str, Any], state: AgentState) -> str:
    appointment = turn_context.get("confirmed_appointment") if isinstance(turn_context.get("confirmed_appointment"), dict) else {}
    store = turn_context.get("confirmed_store") if isinstance(turn_context.get("confirmed_store"), dict) else {}
    store_name = str(store.get("store_name") or "").strip()
    appointment_text = _appointment_text(appointment)
    content = str(state.get("normalized_content") or state.get("content") or "")
    if "下午" in content and "下午" not in appointment_text:
        appointment_text = "明天下午" if "明天" in content or "明天" in appointment_text else "下午"
    if appointment_text and store_name:
        prefix = f"可以，那就按{appointment_text}继续给您确认{store_name}。"
    elif appointment_text:
        prefix = f"可以，那就按{appointment_text}继续给您确认到店安排。"
    elif store_name:
        prefix = f"可以，这边继续给您确认{store_name}的到店安排。"
    else:
        prefix = "可以，这边继续帮您确认到店安排。"
    return f"{prefix}到店会先做检测评估，确认适合再安排操作。"


def _turn_context_for_guard(state: AgentState) -> dict[str, Any]:
    existing = state.get("current_turn_context")
    if isinstance(existing, dict) and existing.get("open_task"):
        return existing
    try:
        return build_current_turn_context(state, sent_message_summary=sent_message_summary_for_model(state))
    except Exception:
        return {}


def _post_deposit_store_assignment_guard(turn_context: dict[str, Any], *, risk_hold: dict[str, Any]) -> dict[str, Any]:
    appointment = turn_context.get("confirmed_appointment") if isinstance(turn_context.get("confirmed_appointment"), dict) else {}
    time_text = _appointment_text(appointment)
    missing_slots = turn_context.get("missing_slots") if isinstance(turn_context.get("missing_slots"), list) else []
    ask_text = "您现在在哪个城市或区域？我按您这边就近匹配门店和地址。"
    if "city_or_region" not in missing_slots:
        ask_text = "您想去哪个门店或哪个区域？我按这个给您核对到店安排。"
    first = f"可以，{time_text}这边先按到店检测给您接上。" if time_text else "可以，这边先按到店检测给您接上。"
    messages = [_text_message(first), _text_message(ask_text)]
    if risk_hold:
        messages.append(_text_message("有健康或过敏情况的话，到店会先做检测评估，确认适合再安排操作。"))
    if is_hard_health_risk_hold(risk_hold):
        messages.append(_handoff_notice_message(risk_hold))
    return _guard_plan(
        stage="S3",
        sub_rule_id="S3_APPOINTMENT_TIME",
        conversion_stage="time_confirm",
        customer_type="time",
        main_blocker="logistics",
        next_step="lookup_store",
        messages=messages,
        handoff=is_hard_health_risk_hold(risk_hold),
        handoff_reason=_risk_hold_reason(risk_hold),
        guard_reason="post_deposit_store_assignment_missing_location",
        constraints=[
            "客户已付预约金并确认到店时间但缺门店/区域；先补城市/区域或门店，不调用 available_time，不重复发送 payment_collection。"
        ],
    )


def _post_deposit_next_step_guard(turn_context: dict[str, Any], *, risk_hold: dict[str, Any]) -> dict[str, Any]:
    missing_slots = turn_context.get("missing_slots") if isinstance(turn_context.get("missing_slots"), list) else []
    if "city_or_region" in missing_slots:
        text = "付完后这边先帮您匹配就近门店，到店先做检测评估，确认适合再安排操作。您现在在哪个城市或区域？"
    else:
        text = "付完后这边继续帮您核对门店和到店安排，到店先做检测评估，确认适合再安排操作。"
    messages = [_text_message(text)]
    if is_hard_health_risk_hold(risk_hold):
        messages.append(_handoff_notice_message(risk_hold))
    return _guard_plan(
        stage="S3",
        sub_rule_id="S3_PAYMENT_COLLECTION",
        conversion_stage="time_confirm",
        customer_type="time",
        main_blocker="logistics",
        next_step="lookup_store" if missing_slots else "confirm_time",
        messages=messages,
        handoff=is_hard_health_risk_hold(risk_hold),
        handoff_reason=_risk_hold_reason(risk_hold),
        guard_reason="post_deposit_next_step_clarification",
        constraints=["客户已付预约金后询问下一步；解释门店/检测/适配流程，不重复发送 payment_collection。"],
    )


def _health_risk_followup_guard(turn_context: dict[str, Any], *, risk_hold: dict[str, Any]) -> dict[str, Any]:
    appointment = turn_context.get("confirmed_appointment") if isinstance(turn_context.get("confirmed_appointment"), dict) else {}
    time_text = _appointment_text(appointment)
    prefix = f"{time_text}可以先按到店检测评估来接，" if time_text else "在的，这个先按到店检测评估来处理，"
    text = prefix + "确认适合再安排操作。您把想去的城市或门店发我，我先帮您接上检测安排。"
    return _guard_plan(
        stage="S4",
        sub_rule_id="S4_PROFESSIONAL_ASSIST",
        conversion_stage="time_confirm",
        customer_type="risk",
        main_blocker="risk",
        next_step="confirm_time",
        messages=[_text_message(text), _handoff_notice_message(risk_hold)],
        handoff=True,
        handoff_reason=_risk_hold_reason(risk_hold),
        guard_reason="health_risk_followup",
        constraints=["健康/过敏风险后续轮次先承接检测和到店安排，不发送 payment_collection。"],
    )


def _guard_plan(
    *,
    stage: str,
    sub_rule_id: str,
    conversion_stage: str,
    customer_type: str,
    main_blocker: str,
    next_step: str,
    messages: list[dict[str, Any]],
    handoff: bool,
    handoff_reason: str,
    guard_reason: str,
    constraints: list[str],
) -> dict[str, Any]:
    return {
        "decision": "direct_reply",
        "stage": stage,
        "sub_rule_id": sub_rule_id,
        "conversion_stage": conversion_stage,
        "customer_type": customer_type,
        "main_blocker": main_blocker,
        "next_step": next_step,
        "reply_messages": _renumber_messages(messages),
        "required_tools": [{"name": "no_tool", "purpose": guard_reason}],
        "handoff": {"needed": handoff, "reason": handoff_reason} if handoff else {"needed": False, "reason": ""},
        "reply_constraints": constraints,
        "guard_reason": guard_reason,
    }


def _text_message(text: str) -> dict[str, Any]:
    return {"type": "text", "order": 1, "content": {"text": normalize_deposit_refund_policy_text(text)}}


def _handoff_notice_message(risk_hold: dict[str, Any]) -> dict[str, Any]:
    return {"type": "human_handoff_notice", "order": 1, "content": {"handoff_reason": _risk_hold_reason(risk_hold)}}


def _risk_hold_reason(risk_hold: dict[str, Any]) -> str:
    return str(risk_hold.get("reason") or "健康/过敏高风险，需先到店检测确认适配性").strip()


def _appointment_text(appointment: dict[str, Any]) -> str:
    bits = [str(appointment.get(key) or "").strip() for key in ("date", "time")]
    return " ".join(bit for bit in bits if bit)


def _renumber_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        updated = dict(item)
        updated["order"] = len(output) + 1
        output.append(updated)
    return output


def _normalize_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def _normalize_reply_messages(value: Any, *, state: AgentState | None = None) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:4]:
        if isinstance(item, str):
            text = normalize_deposit_refund_policy_text(item.strip())
            if text:
                output.append({"type": "text", "order": len(output) + 1, "content": {"text": text}})
            continue
        if not isinstance(item, dict):
            continue
        msg_type = str(item.get("type") or "text").strip()
        if msg_type == "human_handoff":
            msg_type = "human_handoff_notice"
        if msg_type not in {"text", "image", "payment_collection", "human_handoff_notice", "store_address"}:
            msg_type = "text"
        content = item.get("content")
        if content in (None, "", [], {}) and any(item.get(key) for key in ("text", "url", "handoff_reason", "store_id")):
            content = {
                "text": item.get("text"),
                "url": item.get("url"),
                "handoff_reason": item.get("handoff_reason"),
                "store_id": item.get("store_id"),
            }
        if msg_type == "payment_collection":
            output.append(
                {
                    "type": "payment_collection",
                    "order": len(output) + 1,
                    "content": payment_collection_content(content, state=state, messages=output),
                }
            )
            continue
        if msg_type == "store_address":
            store_id = _store_address_id(content)
            if store_id:
                output.append({"type": "store_address", "order": len(output) + 1, "content": {"store_id": store_id}})
            continue
        text = normalize_deposit_refund_policy_text(_message_text(content))
        if text:
            key = "handoff_reason" if msg_type == "human_handoff_notice" else ("url" if msg_type == "image" else "text")
            output.append({"type": msg_type, "order": len(output) + 1, "content": {key: text}})
    return output


def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("text", "url", "handoff_reason"):
            if content.get(key):
                return str(content.get(key) or "").strip()
        return ""
    return str(content or "").strip()


def _store_address_id(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("store_id") or content.get("id") or "").strip()
    return str(content or "").strip()


def _has_store_address_message(messages: list[dict[str, Any]]) -> bool:
    return any(str(item.get("type") or "") == "store_address" for item in messages if isinstance(item, dict))


def _store_lookup_query_from_state(state: AgentState) -> str:
    recent_store_name = _recent_store_name_from_context(state)
    if recent_store_name:
        return recent_store_name
    return str(state.get("normalized_content") or state.get("content") or "").strip()


def _rewrite_reference_store_lookup_queries(required_tools: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or state.get("content") or "")
    if not _current_message_requests_store_detail(content):
        return required_tools
    if not any(term in content for term in ("这家", "那家", "刚刚", "地址", "位置", "定位", "导航")):
        return required_tools
    anchor = _recent_store_name_from_context(state)
    if not anchor:
        return required_tools
    rewritten: list[dict[str, Any]] = []
    for tool in required_tools:
        if isinstance(tool, dict) and str(tool.get("name") or "") == "customer_store_lookup":
            updated = dict(tool)
            updated["query"] = anchor
            rewritten.append(updated)
        else:
            rewritten.append(tool)
    return rewritten


def _generic_store_lookup_guard(required_tools: list[dict[str, Any]], state: AgentState) -> dict[str, Any]:
    content = str(state.get("normalized_content") or state.get("content") or "")
    if not _is_generic_store_location_question_without_current_scope(content, state):
        return {}
    if _generic_store_question_has_allowed_tool_query(required_tools, state):
        return {}
    if not any(isinstance(tool, dict) and str(tool.get("name") or "") == "customer_store_lookup" for tool in required_tools):
        return {}
    return {
        "decision": "direct_reply",
        "stage": "S2",
        "sub_rule_id": "S2_STORE_LOCATION_NEEDS_SCOPE",
        "conversion_stage": "store_match",
        "customer_type": "distance",
        "main_blocker": "logistics",
        "next_step": "lookup_store",
        "reply_messages": [_text_message("您想看哪个城市或区域的门店？发我城市或区名，我给您匹配附近门店。")],
        "required_tools": [{"name": "no_tool", "purpose": "generic_store_location_needs_city_or_region"}],
        "guard_reason": "generic_store_question_needs_current_scope",
    }


def _recent_store_name_from_context(state: AgentState) -> str:
    turn_context = state.get("current_turn_context") if isinstance(state.get("current_turn_context"), dict) else {}
    if not turn_context:
        turn_context = _turn_context_for_guard(state)
    for key in ("current_store_anchor", "confirmed_store"):
        value = turn_context.get(key)
        if isinstance(value, dict):
            source = str(value.get("source") or "").strip()
            if source in {"customer_profile", "profile", "preferred_store"}:
                continue
            name = str(value.get("store_name") or value.get("name") or "").strip()
            if name:
                return name
    text = _state_text_context(state)
    if not text:
        return ""
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    best_name = ""
    best_pos = -1
    for store in stores:
        if not isinstance(store, dict):
            continue
        name = str(store.get("store_name") or store.get("name") or "").strip()
        if not name:
            continue
        pos = text.rfind(name)
        if pos > best_pos:
            best_name = name
            best_pos = pos
    for name in _snapshot_store_names():
        compact_name = str(name or "").strip()
        if not compact_name:
            continue
        pos = text.rfind(compact_name)
        if pos > best_pos:
            best_name = compact_name
            best_pos = pos
    return best_name


def _state_text_context(state: AgentState) -> str:
    chunks: list[str] = [str(state.get("normalized_content") or state.get("content") or "")]
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in history[-20:]:
        if isinstance(item, dict):
            content = item.get("content")
            chunks.append(str(content.get("text") if isinstance(content, dict) else content or ""))
        else:
            chunks.append(str(item or ""))
    return "\n".join(chunks)


def _normalize_tools(raw_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if not isinstance(raw_tools, list):
        return tools
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in ALLOWED_TOOLS:
            continue
        tool = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        kb_name = str(item.get("kb_name") or "").strip()
        if kb_name:
            if name != "kb_search" or kb_name not in ALLOWED_KBS:
                continue
            tool["kb_name"] = kb_name
        query = str(item.get("query") or "").strip()
        if query:
            tool["query"] = query
        for key in (
            "origin",
            "candidate_store_ids",
            "candidate_source",
            "store_id",
            "date",
            "scope",
            "need_fields",
            "for_distance",
        ):
            if key in item:
                tool[key] = item[key]
        tools.append(tool)
    return tools


def _tool_policy_violations(required_tools: list[dict[str, Any]], state: AgentState) -> list[dict[str, str]]:
    concrete_tools = [tool for tool in required_tools if str(tool.get("name") or "").strip() != "no_tool"]
    violations: list[dict[str, str]] = []

    for tool in concrete_tools:
        name = str(tool.get("name") or "").strip()
        query = str(tool.get("query") or "").strip()
        if name == "kb_search":
            kb_name = str(tool.get("kb_name") or "").strip()
            missing_args: list[str] = []
            if not kb_name:
                missing_args.append("kb_name")
            if not query:
                missing_args.append("query")
            if missing_args:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "kb_search",
                        "missing": "kb_search_missing_query" if "query" in missing_args else "kb_search_missing_kb_name",
                        "note": "Every kb_search must include both kb_name and a concrete query; code will not invent missing search terms.",
                    }
                )
            continue
        if name == "customer_store_lookup":
            if _generic_store_question_uses_history_query(query, state):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "customer_store_lookup",
                        "missing": "store_lookup_query_over_anchors_history",
                        "note": (
                            "The current message is a generic store-location question without a current city, region, "
                            "landmark, or explicit store reference. Do not fill the query from historical store context; "
                            "ask the customer for their city or district instead. Contextual references like 'this store' "
                            "or 'the one just mentioned' may still use recent store context."
                        ),
                    }
                )
            elif not _location_query_has_scope_region(query, state) and not _query_matches_scope_store_name(query, state):
                violations.append(_ambiguous_location_tool_violation("customer_store_lookup"))
            continue
        if name == "distance_calculate":
            origin = str(tool.get("origin") or tool.get("address") or tool.get("query") or "").strip()
            if not _location_query_has_scope_region(origin, state):
                violations.append(_ambiguous_location_tool_violation("distance_calculate"))
            continue
        if name == "available_time":
            missing_args: list[str] = []
            store_id = str(tool.get("store_id") or "").strip()
            if not store_id:
                missing_args.append("store_id")
            if not str(tool.get("date") or "").strip():
                missing_args.append("date")
            if missing_args:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "available_time",
                        "missing": "available_time_missing_" + "_".join(missing_args),
                        "note": (
                            "available_time requires a concrete store_id and date. If the customer only provided a city, "
                            "district, landmark, or store name, call customer_store_lookup first or ask one missing field; "
                            "do not call available_time with an empty store_id/date."
                        ),
                    }
                )
            elif not _is_known_numeric_store_id(store_id, state):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "available_time",
                        "missing": "available_time_invalid_store_id",
                        "note": (
                            "available_time.store_id must be a real numeric store id from request_context, appointment_cache, "
                            "customer_context, or customer_store_knowledge. Do not invent symbolic ids such as store_xxx."
                        ),
                    }
                )
            elif _is_past_iso_date(str(tool.get("date") or "")):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "available_time",
                        "missing": "available_time_past_date",
                        "note": "available_time.date must be today or a future date based on current_date. Do not use old example dates.",
                    }
                )

    return violations


def _direct_reply_message_violations(*, decision: str, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    if decision == "direct_reply" and not messages:
        return [
            {
                "missing": "direct_reply_messages",
                "note": "decision=direct_reply must include at least one customer-visible reply_messages item. Rewrite with a short direct answer, or switch to need_tools/no_reply when appropriate.",
            }
        ]
    return []


def _direct_reply_store_consistency_violations(
    *,
    state: AgentState,
    decision: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "direct_reply" or _request_store_from_state(state):
        return []
    current_store = _store_from_current_message(state)
    if not current_store or current_store.get("ambiguous"):
        return []
    current_name = str(current_store.get("store_name") or "").strip()
    if not current_name:
        return []
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    compact_text = _compact_text(text)
    if not compact_text or _compact_text(current_name) in compact_text:
        return []
    other_names = [
        name
        for name in _known_store_names_for_state(state)
        if name != current_name and _compact_text(name) in compact_text
    ]
    other_names = _without_subsumed_store_names(_dedupe_names(other_names))
    if not other_names:
        return []
    return [
        {
            "task_type": "reply_fact_consistency",
            "subtype": "current_message_store",
            "missing": "direct_reply_store_mismatch",
            "note": (
                "The current customer message explicitly names store "
                f"{current_name}, but the direct reply mentions another known store "
                f"{'、'.join(other_names[:3])}. Rewrite the reply to use the current-message store, "
                "or ask the customer to clarify if multiple stores are intended."
            ),
        }
    ]


def _request_store_from_state(state: AgentState) -> dict[str, str]:
    store_id = str(state.get("confirmed_store_id") or state.get("store_id") or "").strip()
    store_name = str(state.get("confirmed_store_name") or state.get("store_name") or "").strip()
    if not (store_id or store_name):
        return {}
    return {"store_id": store_id, "store_name": store_name}


def _store_from_current_message(state: AgentState) -> dict[str, Any]:
    text = str(state.get("normalized_content") or state.get("content") or "").strip()
    matched_names = _store_names_matching_text(state, text)
    if len(matched_names) == 1:
        return {"store_name": matched_names[0], "source": "current_message"}
    if len(matched_names) > 1:
        return {"ambiguous": True, "matched_store_names": matched_names[:5], "source": "current_message"}
    return {}


def _store_names_matching_text(state: AgentState, text: str) -> list[str]:
    if not text:
        return []
    matched = [name for name in _known_store_names_for_state(state) if name and name in text]
    return _without_subsumed_store_names(_dedupe_names(matched))


def _known_store_names_for_state(state: AgentState) -> list[str]:
    names: list[str] = []
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    for store in stores:
        if not isinstance(store, dict):
            continue
        name = str(store.get("store_name") or store.get("name") or "").strip()
        if name:
            names.append(name)
    names.extend(name for name in KNOWN_STORE_NAMES if name)
    return _dedupe_names(names)


def _dedupe_names(names: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        output.append(name)
    return output


def _without_subsumed_store_names(names: list[str]) -> list[str]:
    return [name for name in names if not any(name != other and name in other for other in names)]


def _store_detail_tool_violations(
    *,
    decision: str,
    messages: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
    state: AgentState,
) -> list[dict[str, str]]:
    if decision != "direct_reply" or _has_tool(required_tools, "customer_store_lookup"):
        return []
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    current_text = str(state.get("normalized_content") or state.get("content") or "")
    if not (_direct_text_requires_store_detail_tool(text) or _current_message_requests_store_detail(current_text)):
        return []
    return [
        {
            "task_type": "tool_required",
            "subtype": "customer_store_lookup",
            "missing": "store_detail_tool_required",
            "note": (
                "Customer-visible store address, location card, navigation, route, or concrete store detail must come from "
                "customer_store_lookup facts. Switch to need_tools and call customer_store_lookup. If current_known_store "
                "has a single store, use its store_name as query; if it is ambiguous, ask which store instead of asserting details."
            ),
        }
    ]


def _distance_tool_violations(required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    if _has_tool(required_tools, "distance_calculate"):
        return []
    for tool in required_tools:
        if not isinstance(tool, dict) or str(tool.get("name") or "") != "customer_store_lookup":
            continue
        if str(tool.get("purpose") or "").strip() == "nearby_candidates":
            return [
                {
                    "task_type": "tool_required",
                    "subtype": "distance_calculate",
                    "missing": "distance_calculate_required",
                    "note": (
                        "Nearby, closest, airport-nearby, or distance-ranking store questions require distance_calculate after "
                        "customer_store_lookup. Add distance_calculate with candidate_source=customer_store_lookup, or change "
                        "the lookup purpose to existence/detail if no distance ranking is needed."
                    ),
                }
            ]
    return []


def _has_tool(required_tools: list[dict[str, Any]], name: str) -> bool:
    return any(isinstance(tool, dict) and str(tool.get("name") or "") == name for tool in required_tools)


def _direct_text_requires_store_detail_tool(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    if _asserts_store_address_detail(compact):
        return True
    return any(
        term in compact
        for term in (
            "地址:",
            "地址：",
            "营业时间",
            "停车",
            "地下停车",
            "可停",
            "定位卡",
            "门店卡",
            "位置卡",
            "路线卡",
            "导航过去",
            "直接导航",
            "发您地址",
            "发你地址",
            "已发地址",
            "发过地址",
            "地址发您",
            "地址发你",
        )
    )


def _current_message_requests_store_detail(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    if any(term in compact for term in ("发个位置", "发位置", "位置发我", "发个地址", "发地址", "地址发我", "发导航", "发定位", "定位发我")):
        return True
    return bool(re.search(r"发.{0,8}(地址|位置|定位|导航)", compact)) or bool(
        re.search(r"(地址|位置|定位|导航).{0,8}(发|给)", compact)
    )


def _asserts_store_address_detail(text: str) -> bool:
    if any(term in text for term in ("地址是", "地址在", "地址:", "地址：", "门店地址", "详细地址")):
        return True
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]{1,30}(?:路|街|大道|巷)\s*\d+\s*号", text))


def _is_known_numeric_store_id(store_id: str, state: AgentState) -> bool:
    text = str(store_id or "").strip()
    if not text.isdigit():
        return False
    known_ids = _known_store_ids(state)
    return bool(known_ids) and text in known_ids


def _is_past_iso_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(str(value or "").strip())
    except ValueError:
        return False
    return parsed < date.today()


def _known_store_ids(state: AgentState) -> set[str]:
    ids: set[str] = set()
    for source in (
        state,
        state.get("request_context") if isinstance(state.get("request_context"), dict) else {},
        state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {},
    ):
        if not isinstance(source, dict):
            continue
        for key in ("store_id", "confirmed_store_id"):
            value = str(source.get(key) or "").strip()
            if value and value != "0":
                ids.add(value)
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    appointment = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    value = str(appointment.get("store_id") or "").strip()
    if value and value != "0":
        ids.add(value)
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    recommended = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    for key in ("store_id", "id"):
        value = str(recommended.get(key) or "").strip()
        if value and value != "0":
            ids.add(value)
    store_facts = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    for store in store_facts:
        if not isinstance(store, dict):
            continue
        for key in ("store_id", "id"):
            value = str(store.get(key) or "").strip()
            if value and value != "0":
                ids.add(value)
    ids.update(_recent_explicit_store_ids(state))
    return ids


def _recent_explicit_store_ids(state: AgentState) -> set[str]:
    ids: set[str] = set()
    basic_info = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    preferred_id = str(basic_info.get("preferred_store_id") or "").strip()
    if preferred_id and preferred_id != "0":
        ids.add(preferred_id)
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in events[-20:]:
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") not in {"store_matched", "store_address_sent"}:
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        store_id = str(facts.get("store_id") or facts.get("id") or "").strip()
        if store_id and store_id != "0":
            ids.add(store_id)
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in history[-10:]:
        text = _history_item_text(item)
        for match in re.finditer(r"(?:store_id|门店ID)\s*[=:：]\s*(\d+)", text, flags=re.IGNORECASE):
            ids.add(match.group(1))
    return ids


def _ambiguous_location_tool_violation(tool_name: str) -> dict[str, str]:
    return {
        "task_type": "tool_argument",
        "subtype": tool_name,
        "missing": "location_query_missing_city_or_region",
        "note": (
            "Nearby/distance store tools require a query/origin that includes a concrete city or region from the current "
            "message, recent conversation, or customer profile. If only a nationwide ambiguous landmark is known, do not "
            "call store/distance tools; ask the customer which city or district first."
        ),
    }


def _generic_store_question_uses_history_query(query: str, state: AgentState) -> bool:
    current_text = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not _is_generic_store_location_question_without_current_scope(current_text, state):
        return False
    if _generic_store_question_can_use_contextual_anchor(state, query=query):
        return False
    query_compact = _compact_text(query)
    current_compact = _compact_text(current_text)
    return bool(query_compact and query_compact != current_compact)


def _generic_store_question_has_allowed_tool_query(required_tools: list[dict[str, Any]], state: AgentState) -> bool:
    for tool in required_tools:
        if not isinstance(tool, dict) or str(tool.get("name") or "") != "customer_store_lookup":
            continue
        if _generic_store_question_can_use_contextual_anchor(state, query=str(tool.get("query") or "")):
            return True
    return False


def _generic_store_question_can_use_contextual_anchor(state: AgentState, *, query: str = "") -> bool:
    turn_context = state.get("current_turn_context") if isinstance(state.get("current_turn_context"), dict) else {}
    if not turn_context:
        turn_context = _turn_context_for_guard(state)
    open_task = str(turn_context.get("open_task") or "").strip()
    if open_task in {"deposit_push", "appointment_confirm"} and _query_matches_scope_store_name(query, state):
        return True
    anchor = turn_context.get("current_store_anchor") if isinstance(turn_context.get("current_store_anchor"), dict) else {}
    if not anchor:
        anchor = turn_context.get("confirmed_store") if isinstance(turn_context.get("confirmed_store"), dict) else {}
    if not anchor or anchor.get("ambiguous"):
        return False
    source = str(anchor.get("source") or "").strip()
    if source in {"customer_profile", "profile", "preferred_store"}:
        return False
    if open_task in {
        "deposit_push",
        "appointment_confirm",
        "post_deposit_store_assignment",
        "post_deposit_next_step_clarification",
    }:
        return True
    return False


def _is_generic_store_location_question_without_current_scope(text: str, state: AgentState) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    if any(term in compact for term in ("这家", "那家", "这个店", "刚刚", "刚才", "上面那家", "前面那家")):
        return False
    generic_terms = (
        "门店在哪里",
        "门店在哪",
        "哪里有门店",
        "有哪些门店",
        "有门店吗",
        "门店地址",
        "门店位置",
        "你们店在哪里",
        "你们店在哪",
        "店在哪里",
        "店在哪",
    )
    if not any(term in compact for term in generic_terms):
        return False
    if _store_names_matching_text(state, text):
        return False
    return not _location_query_has_current_message_scope(text)


def _location_query_has_current_message_scope(value: str) -> bool:
    text = _compact_text(value)
    if not text:
        return False
    return _looks_like_specific_region(text)


def _location_query_has_scope_region(value: str, state: AgentState) -> bool:
    text = _compact_text(value)
    if not text:
        return False
    for token in _scope_region_tokens(state):
        compact = _compact_text(token)
        if compact and compact in text:
            return True
    if _looks_like_specific_region(text):
        return True
    return False


def _query_matches_scope_store_name(value: str, state: AgentState) -> bool:
    text = _compact_text(value)
    if not text:
        return False
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    for store in stores:
        if not isinstance(store, dict):
            continue
        for key in ("store_name", "name"):
            name = _compact_text(store.get(key))
            if _store_name_query_matches(name, text):
                return True
    for name in _snapshot_store_names():
        compact_name = _compact_text(name)
        if _store_name_query_matches(compact_name, text):
            return True
    return False


def _store_name_query_matches(name: str, text: str) -> bool:
    if name and text and (name in text or (len(text) >= 4 and text in name)):
        return True
    normalized_name = _normalize_store_name_for_match(name)
    normalized_text = _normalize_store_name_for_match(text)
    return bool(
        normalized_name
        and normalized_text
        and (normalized_name in normalized_text or (len(normalized_text) >= 4 and normalized_text in normalized_name))
    )


def _normalize_store_name_for_match(value: str) -> str:
    return _compact_text(value).replace("市", "")


def _snapshot_store_names() -> list[str]:
    global _STORE_SNAPSHOT_NAME_CACHE
    if _STORE_SNAPSHOT_NAME_CACHE is not None:
        return _STORE_SNAPSHOT_NAME_CACHE
    path = Path("data/store_snapshot.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _STORE_SNAPSHOT_NAME_CACHE = []
        return _STORE_SNAPSHOT_NAME_CACHE
    stores_by_id = data.get("stores_by_id") if isinstance(data, dict) else {}
    names = []
    if isinstance(stores_by_id, dict):
        names = [
            str(store.get("store_name") or store.get("name") or "").strip()
            for store in stores_by_id.values()
            if isinstance(store, dict) and str(store.get("store_name") or store.get("name") or "").strip()
        ]
    _STORE_SNAPSHOT_NAME_CACHE = list(dict.fromkeys(names))
    return _STORE_SNAPSHOT_NAME_CACHE


def _looks_like_specific_region(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]{2,}(省|市|区|县|镇|乡|旗|州|盟|新区|机场|高铁站|火车站)", text))


def _scope_region_tokens(state: AgentState) -> set[str]:
    tokens: set[str] = set()
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    for store in stores:
        if not isinstance(store, dict):
            continue
        for key in ("province", "city", "district"):
            raw = str(store.get(key) or "").strip()
            if not raw:
                continue
            tokens.add(raw)
            for suffix in ("省", "市", "区", "县", "旗", "自治州", "自治县", "新区"):
                if raw.endswith(suffix) and len(raw) > len(suffix):
                    tokens.add(raw[: -len(suffix)])
    return tokens


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _history_item_text(item: Any) -> str:
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or content.get("url") or "").strip()
        return str(content or "").strip()
    return str(item or "").strip()


def _rejected_tool_violations(raw_tools: Any) -> list[dict[str, str]]:
    if not isinstance(raw_tools, list):
        return []
    violations: list[dict[str, str]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        if name == "kb_search" and kb_name and kb_name not in ALLOWED_KBS:
            violations.append(
                {
                    "task_type": "planner_tool_rejected",
                    "subtype": "kb_search",
                    "missing": f"unsupported_kb:{kb_name}",
                    "note": "Planner may only call kb_search(case_studies). sales_talk_qa is currently disabled.",
                }
            )
    return violations


def _payment_consistency_violations(
    *,
    state: AgentState,
    decision: str,
    conversion_stage: str,
    next_step: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision == "no_reply":
        return []
    if _text_explains_previous_payment_entry(messages):
        return []
    if decision != "direct_reply" and not _text_mentions_payment_entry(messages):
        return []
    needs_payment = conversion_stage == "deposit_push" or next_step == "send_deposit" or _text_mentions_payment_entry(messages)
    payment_context = payment_collection_context(state=state, messages=messages)
    if needs_payment and payment_context["over_limit"]:
        return [
            {
                "task_type": "reply_schema_consistency",
                "subtype": "payment_collection",
                "missing": "payment_participant_count_confirm_required",
                "note": (
                    "The current message implies more than 4 participants. Do not auto-send payment_collection. "
                    "Reply with text to confirm the number of participants or ask store staff to handle group booking."
                ),
            }
        ]
    if not needs_payment or _has_payment_collection(messages):
        return []
    return [
        {
            "task_type": "reply_schema_consistency",
            "subtype": "payment_collection",
            "missing": "payment_collection_required",
            "note": (
                "When conversion_stage=deposit_push, next_step=send_deposit, or customer-facing text says an entrance/link "
                "will be sent, reply_messages must include payment_collection. If payment_collection is not appropriate, "
                "change conversion_stage/next_step/text instead of promising an entrance."
            ),
        }
    ]


def _has_payment_collection(messages: list[dict[str, Any]]) -> bool:
    return any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict))


def _has_handoff_notice(messages: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}
        for item in messages
        if isinstance(item, dict)
    )


def _remove_payment_collection_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _renumber_reply_messages(
        [item for item in messages if isinstance(item, dict) and str(item.get("type") or "") != "payment_collection"]
    )


def _remove_advisory_health_handoff_notices(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    output: list[dict[str, Any]] = []
    removed = False
    for item in messages:
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "")
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        reason = str(content.get("handoff_reason") or content.get("reason") or "")
        if message_type in {"human_handoff", "human_handoff_notice"} and _mentions_health_risk_text(reason):
            removed = True
            continue
        output.append(item)
    return _renumber_reply_messages(output), removed


def _append_required_payment_collection(
    *,
    state: AgentState,
    decision: str,
    conversion_stage: str,
    next_step: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if decision != "direct_reply":
        return messages
    if is_hard_health_risk_hold(health_risk_hold(state)):
        return _remove_payment_collection_messages(messages)
    if not messages or _has_payment_collection(messages) or _text_explains_previous_payment_entry(messages):
        return messages
    needs_payment = conversion_stage == "deposit_push" or next_step == "send_deposit" or _text_mentions_payment_entry(messages)
    if not needs_payment:
        return messages
    payment_context = payment_collection_context(state=state, messages=messages)
    if payment_context["over_limit"]:
        return messages
    return [
        *_renumber_reply_messages(messages),
        {
            "type": "payment_collection",
            "order": len(messages) + 1,
            "content": {"amount": payment_context["amount"], "remark": ""},
        },
    ]


def _renumber_reply_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["order"] = len(output) + 1
        output.append(normalized)
    return output


def _text_mentions_payment_entry(messages: list[dict[str, Any]]) -> bool:
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    compact = "".join(str(text or "").split())
    return any(
        term in compact
        for term in (
            "发入口",
            "发送入口",
            "重新发",
            "付款入口",
            "收款入口",
            "支付入口",
            "预约金入口",
            "报名入口",
            "发报名入口",
            "发送报名入口",
            "现在为您发",
            "马上发您",
        )
    )


def _text_explains_previous_payment_entry(messages: list[dict[str, Any]]) -> bool:
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    return any(term in text for term in ("刚刚发的是", "刚才发的是", "前面发的是", "之前发的是"))


def _two_text_rhythm_violations(
    *,
    state: AgentState,
    decision: str,
    conversion_stage: str,
    next_step: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "direct_reply" or conversion_stage == "deposit_push" or next_step == "send_deposit":
        return []
    if is_contextual_short_message(str(state.get("normalized_content") or state.get("content") or "")):
        return []
    if any(str(item.get("type") or "") != "text" for item in messages if isinstance(item, dict)):
        return []
    text_messages = [item for item in messages if isinstance(item, dict) and str(item.get("type") or "") == "text"]
    if len(text_messages) != 1:
        return []
    text = _message_text(text_messages[0].get("content"))
    if not _looks_like_answer_with_next_step(text):
        return []
    return [
        {
            "task_type": "reply_format",
            "subtype": "two_text_rhythm",
            "missing": "two_text_required",
            "note": "This direct text reply contains both an answer and a next-step prompt. Rewrite reply_messages as two short text messages: answer first, then one light next-step action.",
        }
    ]


def _pending_lookup_reply_violations(
    *,
    decision: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "direct_reply":
        return []
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    compact = _compact_text(text)
    if not compact:
        return []
    if re.search(r"(查|核对|看).{0,12}(档期|案例|参考)", compact):
        return [
            {
                "task_type": "reply_fact_consistency",
                "subtype": "pending_lookup_promise",
                "missing": "direct_reply_promises_unfinished_lookup",
                "note": (
                    "direct_reply must not promise pending lookup work such as checking schedule/cases. "
                    "If the answer needs cases, use kb_search(case_studies). If it needs real schedule, use available_time with store_id/date or ask one missing field."
                ),
            }
        ]
    return []


def _appointment_availability_reply_violations(
    *,
    decision: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "direct_reply":
        return []
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    compact = _compact_text(text)
    if not compact:
        return []
    if any(term in compact for term in ("可以约", "能约", "可以预约", "能预约", "有空档", "有档期")):
        return [
            {
                "task_type": "reply_fact_consistency",
                "subtype": "appointment_availability",
                "missing": "available_time_required_for_availability_claim",
                "note": (
                    "Direct replies must not claim a time can be booked without available_time facts. "
                    "Ask for the missing store/time field, or call available_time when store_id/date are known."
                ),
            }
        ]
    return []


def _looks_like_answer_with_next_step(text: str) -> bool:
    value = str(text or "").strip()
    if len(value) < 18:
        return False
    return any(
        term in value
        for term in (
            "您方便",
            "哪个区",
            "哪天",
            "今天还是明天",
            "上午还是下午",
            "周六还是周日",
            "到店看看",
            "到店看",
            "帮您看名额",
            "帮您查",
            "帮您看看",
            "我帮您看",
        )
    )


def _normalize_handoff(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    needed = bool(raw.get("needed"))
    return {
        "needed": needed,
        "reason": str(raw.get("reason") or "").strip()[:180],
    }


def _normalize_memory_hint(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "summary": str(raw.get("summary") or "").strip()[:180],
        "needs": _clean_str_list(raw.get("needs") or [])[:6],
        "concerns": _clean_str_list(raw.get("concerns") or [])[:6],
        "store_preference": str(raw.get("store_preference") or "").strip()[:80],
        "appointment_signals": _clean_str_list(raw.get("appointment_signals") or [])[:6],
    }


def _dedupe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        query = str(item.get("query") or "").strip()
        key = (name, kb_name, query)
        if name not in ALLOWED_TOOLS or key in seen:
            continue
        seen.add(key)
        normalized = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        if kb_name:
            normalized["kb_name"] = kb_name
        if query:
            normalized["query"] = query
        for extra_key in (
            "origin",
            "destination",
            "candidate_store_ids",
            "candidate_source",
            "store_id",
            "store_name",
            "date",
            "time",
            "address",
            "reason",
            "scope",
            "need_fields",
            "for_distance",
        ):
            if extra_key in item:
                normalized[extra_key] = item.get(extra_key)
        unique.append(normalized)
    return unique


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text[:180])
    return output
