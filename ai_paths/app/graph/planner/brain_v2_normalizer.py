from __future__ import annotations

import json
from datetime import date, timedelta
import re
from pathlib import Path
from typing import Any

from app.graph.nodes.appointment_time_utils import normalize_time_text
from app.graph.nodes.current_turn_context import (
    build_current_turn_context,
    current_store_anchor_from_state,
    is_context_reference_message,
)
from app.graph.nodes.location_card import location_card_from_state
from app.graph.nodes.sent_message_summary import (
    latest_single_store_card_anchor_id,
    sent_message_summary_for_model,
    store_anchor_fact_for_model,
)
from app.graph.nodes.store_scope_summary import store_scope_ids
from app.graph.planner.planner_contract import (
    ALLOWED_CONVERSION_STAGES,
    ALLOWED_CUSTOMER_TYPES,
    ALLOWED_MAIN_BLOCKERS,
    ALLOWED_NEXT_STEPS,
)
from app.graph.planner.planner_reply_structure_guards import (
    has_handoff_notice as _has_handoff_notice,
    has_payment_collection as _has_payment_collection,
    remove_payment_collection_messages as _remove_payment_collection_messages,
    renumber_reply_messages as _renumber_reply_messages,
)
from app.graph.planner.planner_schema_normalizer import (
    clean_str_list as _clean_str_list,
    dedupe_tools as _dedupe_tools,
    normalize_enum as _normalize_enum,
    normalize_tools as _normalize_tools,
)
from app.graph.planner.planner_tool_fact_guards import rejected_tool_violations as _rejected_tool_violations
from app.graph.planner.planner_transaction_guards import (
    authoritative_paid_context as _authoritative_paid_context,
    current_unpaid_order as _current_unpaid_order,
    postpaid_scheduling_tool_violations as _structured_postpaid_scheduling_tool_violations,
)
from app.graph.state import AgentState
from app.policies.constants import KNOWN_STORE_FACTS, KNOWN_STORE_NAMES
from app.policies.sales_flow import precision_qa_for_id
from app.services.payment_collection import (
    activity_intro_completed_for_payment,
    payment_amount_for_party_size,
    payment_collection_content,
    payment_collection_context,
    payment_party_size_for_amount,
)
from app.services.customer_payment_state import normalize_prepay_facts
from app.services.risk_hold import HEALTH_RISK_TERMS, explicit_professional_assist_reason, health_risk_hold, is_hard_health_risk_hold


_STORE_SNAPSHOT_NAME_CACHE: list[str] | None = None
_STORE_SNAPSHOT_REGION_TOKEN_CACHE: set[str] | None = None
_STORE_SNAPSHOT_BROAD_REGION_TOKEN_CACHE: set[str] | None = None
ALLOWED_PAYMENT_STATES = (
    "unknown",
    "link_sent",
    "customer_claimed_paid",
    "resend_requested",
    "payment_failed",
    "needs_payment",
)
ALLOWED_PAYMENT_ACTIONS = (
    "unknown",
    "none",
    "send_now",
    "manual_transfer",
    "offer_resend",
    "explain_existing",
    "confirm_next_step",
)
ALLOWED_PAYMENT_DECISION_ACTIONS = (
    "none",
    "explain",
    "send_now",
    "resend",
    "manual_transfer",
    "after_paid_next_step",
    "ask_party_size",
)
ALLOWED_PAYMENT_METHODS = ("none", "mini_program", "transfer")
ALLOWED_PAYMENT_DECISION_CONFIDENCE = ("high", "medium", "low")
ALLOWED_APPOINTMENT_DECISION_ACTIONS = (
    "none",
    "ask_store",
    "ask_time",
    "lookup_store",
    "check_availability",
    "confirm_existing",
    "tentative_arrange",
    "create_plan",
)
ALLOWED_APPOINTMENT_COMMITMENT_LEVELS = ("none", "tentative", "confirmed")
ALLOWED_ORDER_DECISION_ACTIONS = ("none", "create_work", "use_existing")
ALLOWED_STORE_BINDING_LEVELS = (
    "none",
    "explicit_confirmed",
    "single_store_card_anchor",
    "request_confirmed",
    "appointment_context",
    "existing_order",
    "ambiguous",
)
ALLOWED_STORE_BINDING_STATUSES = (
    "none",
    "accepted_explicit",
    "accepted_implicit",
    "exploring",
    "rejected",
    "ambiguous",
)
ALLOWED_STORE_BINDING_CONFIDENCE = ("high", "medium", "low")
ALLOWED_SALES_PROGRESSION_STATUSES = ("unknown", "continue", "pause", "terminal")
ALLOWED_SALES_PROGRESSION_ACTIONS = (
    "none",
    "ask_need_context",
    "deliver_value",
    "confirm_store",
    "explain_deposit",
    "send_payment_card",
    "manual_transfer",
    "collect_registration",
    "confirm_visit_time",
    "confirm_appointment",
    "close",
    "risk_pause",
)
ALLOWED_SALES_PROGRESSION_TARGETS = (
    "none",
    "need_and_case",
    "trust",
    "store",
    "activity",
    "deposit",
    "registration",
    "appointment",
    "service",
    "close",
    "risk",
)
ALLOWED_CLOSING_MOVE_ACTIONS = (
    "none",
    "ask_city",
    "ask_spot_history",
    "send_case",
    "introduce_offer",
    "ask_store_choice",
    "send_payment",
    "manual_transfer",
    "ask_party_size",
    "ask_registration",
    "ask_visit_intent",
    "resolve_risk",
    "close",
)
ALLOWED_PRECISION_QA_CONFIDENCE = ("high", "medium", "low")
ALLOWED_PRECISION_QA_DEPTH = ("brief", "standard", "deep")


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
    payment_state = _normalize_enum(
        model_payload.get("payment_state") if isinstance(model_payload, dict) else "",
        ALLOWED_PAYMENT_STATES,
        "unknown",
    )
    payment_action = _normalize_enum(
        model_payload.get("payment_action") if isinstance(model_payload, dict) else "",
        ALLOWED_PAYMENT_ACTIONS,
        "unknown",
    )
    planner_reply_messages = _normalize_reply_messages(
        model_payload.get("reply_messages") if isinstance(model_payload, dict) else [],
        state=state,
    )
    planner_tool_calls = _normalize_tools(model_payload.get("tool_calls") if isinstance(model_payload, dict) else [])
    reply_constraints = _clean_str_list(model_payload.get("reply_constraints") if isinstance(model_payload, dict) else [])
    handoff_raw = model_payload.get("handoff") if isinstance(model_payload, dict) else {}
    memory_update_raw = model_payload.get("memory_update_hint") if isinstance(model_payload, dict) else {}
    payment_decision = _normalize_payment_decision(
        model_payload.get("payment_decision") if isinstance(model_payload, dict) else {},
        state=state,
        payment_state=payment_state,
        payment_action=payment_action,
        messages=planner_reply_messages,
    )
    appointment_decision = _normalize_appointment_decision(
        model_payload.get("appointment_decision") if isinstance(model_payload, dict) else {},
    )
    order_decision = _normalize_order_decision(
        model_payload.get("order_decision") if isinstance(model_payload, dict) else {},
    )
    store_binding_decision = _normalize_store_binding_decision(
        model_payload.get("store_binding_decision") if isinstance(model_payload, dict) else {},
        order_decision=order_decision,
    )
    sales_progression = _normalize_sales_progression(
        model_payload.get("sales_progression") if isinstance(model_payload, dict) else {},
    )
    closing_move = _normalize_closing_move(
        model_payload.get("closing_move") if isinstance(model_payload, dict) else {},
    )
    precision_qa_decision = _normalize_precision_qa_decision(
        model_payload.get("precision_qa_decision") if isinstance(model_payload, dict) else {},
    )
    unverified_paid_claim = (
        str(payment_decision.get("action") or "") == "after_paid_next_step"
        or payment_state == "customer_claimed_paid"
    ) and not _has_authoritative_paid_context(state)
    if unverified_paid_claim:
        payment_decision = _with_payment_decision_action(
            payment_decision,
            "after_paid_next_step",
            source="unverified_customer_claim",
            confidence="high",
            basis="客户已声明付款；先继续登记姓名电话并等待平台付款事实核对",
        )
        payment_state = "customer_claimed_paid"
        payment_action = "confirm_next_step"
        appointment_decision = {
            "action": "none",
            "commitment_level": "none",
            "basis": ["客户声明已付款但尚无权威到账事实；先登记信息，不确认正式预约"],
        }
        reply_constraints.append(
            "客户口头表示已付不能单独作为支付成功事实；可先收姓名电话并说明会结合平台付款记录核对，"
            "不得宣称已核款、不得重复发送 payment_collection、不得确认正式预约。"
        )
    payment_decision = _reconcile_paid_payment_decision(
        payment_decision=payment_decision,
        order_decision=order_decision,
        state=state,
    )
    payment_state, payment_action = _payment_fields_from_decision(
        payment_decision=payment_decision,
        payment_state=payment_state,
        payment_action=payment_action,
    )
    state_for_payment = {**state, "payment_decision": payment_decision}
    planner_reply_messages = _normalize_payment_collection_messages_for_decision(
        planner_reply_messages,
        state=state_for_payment,
        payment_decision=payment_decision,
    )

    primary_task: dict[str, Any] = {}
    secondary_tasks: list[dict[str, Any]] = []
    normalizer_policy_violations: list[dict[str, str]] = []
    if decision == "no_reply":
        # Platform auto-message suppression is resolved before Planner runs.
        # Any turn reaching Planner therefore requires a customer-visible reply.
        decision = "direct_reply"
        normalizer_policy_violations.append(
            {
                "task_type": "reply_liveness",
                "subtype": "customer_turn",
                "missing": "no_reply_not_allowed_for_customer_turn",
                "note": (
                    "This turn reached Planner and therefore requires a customer-visible answer. "
                    "Return a direct_reply, or use need_tools with an executable tool call. "
                    "Do not use no_reply for a real customer turn."
                ),
            }
        )

    reply_strategy: dict[str, Any] = {}
    if is_hard_health_risk_hold(risk_hold):
        reply_strategy["risk_hold"] = risk_hold
    required_tools = _dedupe_tools(planner_tool_calls)
    required_tools = _complete_explicit_kb_search_arguments(required_tools, state)
    required_tools = required_tools or [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    required_tools = _rewrite_reference_store_lookup_queries(required_tools, state)
    required_tools = _normalize_current_message_store_lookup_queries(required_tools, state)
    required_tools = _normalize_available_time_dates_from_context(required_tools, state)
    decision, planner_reply_messages, required_tools = _enforce_location_card_store_lookup(
        decision=decision,
        messages=planner_reply_messages,
        required_tools=required_tools,
        state=state,
    )
    decision, planner_reply_messages, required_tools = _enforce_sop_gate_active_task(
        decision=decision,
        messages=planner_reply_messages,
        required_tools=required_tools,
        state=state,
    )
    decision, planner_reply_messages, required_tools = _enforce_declared_store_detail_lookup(
        decision=decision,
        sub_rule_id=sub_rule_id,
        messages=planner_reply_messages,
        required_tools=required_tools,
        state=state,
    )
    decision, planner_reply_messages, required_tools = _enforce_explicit_location_store_lookup(
        decision=decision,
        messages=planner_reply_messages,
        required_tools=required_tools,
        state=state,
    )
    order_decision, required_tools = _reconcile_existing_order_for_payment(
        state=state,
        payment_decision=payment_decision,
        order_decision=order_decision,
        required_tools=required_tools,
    )
    store_binding_decision = _complete_store_binding_from_order_decision(
        store_binding_decision,
        order_decision=order_decision,
    )
    executable_tools = [tool for tool in required_tools if tool.get("name") != "no_tool"]
    if (
        decision == "need_tools"
        and not executable_tools
        and _has_complete_customer_visible_reply(planner_reply_messages)
    ):
        # The model already produced a complete customer-facing answer but mislabeled
        # the schema as need_tools. Preserve its semantics and normalize the envelope.
        decision = "direct_reply"
        required_tools = [{"name": "no_tool", "purpose": "Complete direct reply requires no external tool"}]
    if decision == "need_tools" and executable_tools:
        planner_reply_messages = []
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
        planner_reply_messages = []
        required_tools = [{"name": "professional_assist", "reason": explicit_risk_reason}]
        executable_tools = required_tools
        handoff_raw = {"needed": True, "reason": explicit_risk_reason}
    required_tools, removed_advisory_health_tool = _remove_advisory_health_professional_assist_tools(
        required_tools=required_tools,
        risk_hold=risk_hold,
        explicit_risk_reason=explicit_risk_reason,
    )
    if removed_advisory_health_tool:
        executable_tools = [tool for tool in required_tools if tool.get("name") != "no_tool"]
        planner_reply_messages, _ = _remove_advisory_health_handoff_notices(planner_reply_messages)
        if handoff_raw and isinstance(handoff_raw, dict) and _mentions_health_risk_text(str(handoff_raw.get("reason") or "")):
            handoff_raw = {"needed": False, "reason": ""}
        normalizer_policy_violations.append(
            {
                "task_type": "tool_argument",
                "subtype": "professional_assist",
                "missing": "professional_assist_from_advisory_health_context",
                "note": (
                    "Recent health risk is advisory evidence only. The current message does not explicitly raise a "
                    "health, dispute, severe discomfort, or human-assist request, so do not call professional_assist "
                    "or output human_handoff_notice solely from stale history. Repair by answering the current customer "
                    "question with available facts/tools."
                ),
            }
        )
        reply_constraints.append("历史健康风险只作为背景证据；当前消息没有再次提出健康/投诉/人工诉求时，不调用 professional_assist。")
        reply_strategy["current_turn_context_guard"] = "advisory_health_history_removed_professional_assist_tool"
    if executable_tools and decision == "direct_reply":
        decision = "need_tools"
    if decision == "need_tools":
        planner_reply_messages = []
    if is_hard_health_risk_hold(risk_hold) and not explicit_risk_reason:
        planner_reply_messages = _remove_payment_collection_messages(planner_reply_messages)
        payment_decision = _with_payment_decision_action(
            payment_decision,
            "none",
            source="health_risk_hold",
            confidence="high",
            basis="健康/过敏高风险未检测前不发送预约金卡",
        )
        state_for_payment = {**state, "payment_decision": payment_decision}
        if conversion_stage == "deposit_push":
            conversion_stage = "time_confirm"
        if next_step == "send_deposit":
            next_step = "confirm_time"
        reply_constraints.append("健康/过敏高风险未完成到店检测前，先确认检测和到店安排，不发送 payment_collection。")
    if not explicit_risk_reason and not is_hard_health_risk_hold(risk_hold):
        cleaned_messages, removed_advisory_handoff = _remove_advisory_health_handoff_notices(planner_reply_messages)
        if removed_advisory_handoff:
            planner_reply_messages = cleaned_messages
            handoff_raw = {"needed": False, "reason": ""}
            reply_constraints.append("历史健康风险只作为到店检测提醒；当前消息没有再次提病史/过敏/严重不适时，不输出 human_handoff_notice。")
            reply_strategy["current_turn_context_guard"] = "advisory_health_history_removed_handoff_notice"
    has_paid_deposit_context = _has_paid_deposit_context(state, payment_state=payment_state)
    if has_paid_deposit_context and payment_state == "unknown":
        payment_state = "customer_claimed_paid"
        payment_decision = _with_payment_decision_action(
            payment_decision,
            "after_paid_next_step",
            source="structured_paid_context",
            confidence="high",
            basis="结构化证据或 planner payment_state 表示客户已付",
        )
        state_for_payment = {**state, "payment_decision": payment_decision}
    precision_question_id = str(precision_qa_decision.get("question_id") or "").strip()
    if precision_question_id == "unsupported_online_projects":
        removed_payment = _has_payment_collection(planner_reply_messages)
        planner_reply_messages = _remove_payment_collection_messages(planner_reply_messages)
        payment_decision = _with_payment_decision_action(
            payment_decision,
            "none",
            source="precision_qa_boundary",
            confidence="high",
            basis="线上不支持项目只回答预约边界，不发送预约金卡",
        )
        payment_action = "none"
        if conversion_stage == "deposit_push":
            conversion_stage = "objection_resolution"
            removed_payment = True
        if next_step == "send_deposit":
            next_step = "answer_question"
            removed_payment = True
        state_for_payment = {**state, "payment_decision": payment_decision}
        if removed_payment:
            normalizer_policy_violations.append(
                {
                    "task_type": "reply_schema_consistency",
                    "subtype": "payment_collection",
                    "missing": "payment_collection_blocked_by_precision_qa_boundary",
                    "note": "The selected online project is unsupported, so no payment_collection may be sent.",
                }
            )
        reply_constraints.append("当前是不支持项目精准问答边界，不得发送 payment_collection。")
    if payment_action in {"none", "manual_transfer", "offer_resend", "explain_existing", "confirm_next_step"}:
        removed_payment = _has_payment_collection(planner_reply_messages)
        planner_reply_messages = _remove_payment_collection_messages(planner_reply_messages)
        if conversion_stage == "deposit_push":
            conversion_stage = "time_confirm"
            removed_payment = True
        if next_step == "send_deposit":
            next_step = "confirm_time"
            removed_payment = True
        if removed_payment:
            reply_constraints.append("payment_action 表示本轮不直接发送预约金入口；不要输出 payment_collection。")
            reply_strategy.setdefault("payment_action_guard", "payment_card_removed_by_payment_action")
    if has_paid_deposit_context:
        removed_payment = _has_payment_collection(planner_reply_messages)
        planner_reply_messages = _remove_payment_collection_messages(planner_reply_messages)
        if conversion_stage == "deposit_push":
            conversion_stage = "time_confirm"
            removed_payment = True
        if next_step == "send_deposit":
            next_step = "confirm_time"
            removed_payment = True
        if removed_payment:
            reply_constraints.append("payment_state 表示客户已付或结构化支付状态已付；本轮不要重复发送 payment_collection。")
            reply_strategy.setdefault("current_turn_context_guard", "payment_card_removed_after_model_paid_state")
    state_for_payment = {
        **state,
        "payment_decision": payment_decision,
        "order_decision": order_decision,
        "planner_tool_calls": required_tools,
    }
    if _payment_send_requires_activity_intro(
        conversion_stage=conversion_stage,
        next_step=next_step,
        payment_action=payment_action,
        payment_decision=payment_decision,
        messages=planner_reply_messages,
    ) and not activity_intro_completed_for_payment(state_for_payment):
        planner_reply_messages = _remove_payment_collection_messages(planner_reply_messages)
        payment_decision = _with_payment_decision_action(
            payment_decision,
            "explain",
            source="activity_intro_required",
            confidence="high",
            basis="payment_collection requires completed activity intro evidence",
        )
        payment_action = "explain_existing"
        if conversion_stage == "deposit_push":
            conversion_stage = "interest_capture"
        if next_step == "send_deposit":
            next_step = "introduce_activity"
        state_for_payment = {
            **state,
            "payment_decision": payment_decision,
            "order_decision": order_decision,
            "planner_tool_calls": required_tools,
        }
        normalizer_policy_violations.append(
            {
                "task_type": "reply_schema_consistency",
                "subtype": "payment_collection",
                "missing": "payment_collection_requires_activity_intro",
                "note": (
                    "The customer has not yet seen a completed activity price intro. "
                    "Do not send payment_collection or promise the payment card yet; first explain the 268 activity, "
                    "10 yuan deposit, offset, and refundable boundary, then continue the sales rhythm."
                ),
            }
        )
        reply_constraints.append(
            "活动报价/预约金规则尚无已完成证据时，不发送 payment_collection，也不要承诺本轮已发入口；先补活动价和预约金口径。"
        )
    planner_reply_messages = _append_required_payment_collection(
        state=state_for_payment,
        decision=decision,
        conversion_stage=conversion_stage,
        next_step=next_step,
        payment_state=payment_state,
        payment_action=payment_action,
        payment_decision=payment_decision,
        messages=planner_reply_messages,
    )
    handoff = _normalize_handoff(handoff_raw)
    tool_policy_violations = [
        *normalizer_policy_violations,
        *_store_binding_order_consistency_violations(
            state=state,
            store_binding_decision=store_binding_decision,
            order_decision=order_decision,
            required_tools=required_tools,
        ),
        *_rejected_tool_violations(model_payload.get("tool_calls") if isinstance(model_payload, dict) else []),
        *_tool_policy_violations(
            required_tools,
            {**state, "store_binding_decision": store_binding_decision},
        ),
        *_store_detail_tool_violations(
            decision=decision,
            messages=planner_reply_messages,
            required_tools=required_tools,
            state=state,
        ),
        *_explicit_location_store_lookup_violations(
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
        *_need_tools_without_tool_violations(
            decision=decision,
            required_tools=required_tools,
        ),
        *_direct_reply_store_consistency_violations(
            state=state,
            decision=decision,
            messages=planner_reply_messages,
        ),
        *_payment_consistency_violations(
            state=state_for_payment,
            decision=decision,
            conversion_stage=conversion_stage,
            next_step=next_step,
            payment_state=payment_state,
            payment_action=payment_action,
            payment_decision=payment_decision,
            sales_progression=sales_progression,
            messages=planner_reply_messages,
        ),
        *_postpaid_scheduling_tool_violations(
            state=state,
            payment_decision=payment_decision,
            required_tools=required_tools,
        ),
        *_pending_lookup_reply_violations(
            decision=decision,
            next_step=next_step,
            appointment_decision=appointment_decision,
            messages=planner_reply_messages,
        ),
        *_appointment_availability_reply_violations(
            state=state,
            decision=decision,
            appointment_decision=appointment_decision,
            messages=planner_reply_messages,
        ),
        *_appointment_create_tool_violations(
            appointment_decision=appointment_decision,
            required_tools=required_tools,
        ),
        *_registration_before_appointment_violations(
            state=state,
            payment_decision=payment_decision,
            appointment_decision=appointment_decision,
        ),
        *_appointment_change_fact_violations(
            state=state,
            appointment_decision=appointment_decision,
            required_tools=required_tools,
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
        "payment_state": payment_state,
        "payment_action": payment_action,
        "payment_decision": payment_decision,
        "store_binding_decision": store_binding_decision,
        "order_decision": order_decision,
        "appointment_decision": appointment_decision,
        "sales_progression": sales_progression,
        "closing_move": closing_move,
        "precision_qa_decision": precision_qa_decision,
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
            "stage": "S1",
            "sub_rule_id": "PLANNER_SYSTEM_UNAVAILABLE",
            "conversion_stage": "interest_capture",
            "customer_type": "unknown",
            "main_blocker": "none",
            "next_step": "no_action",
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，刚才这条我没接完整，麻烦您再发一下。"},
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


def planner_unavailable_fallback_plan(state: AgentState, *, reason: str = "Planner unavailable") -> dict[str, Any]:
    return build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S1",
            "sub_rule_id": "PLANNER_SYSTEM_UNAVAILABLE",
            "conversion_stage": "interest_capture",
            "customer_type": "unknown",
            "main_blocker": "none",
            "next_step": "no_action",
            "payment_state": "unknown",
            "payment_action": "none",
            "payment_decision": {"action": "none", "source": "planner_unavailable", "confidence": "low"},
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "亲，刚才这条我没接完整，麻烦您再发一下。"},
                }
            ],
            "tool_calls": [],
            "handoff": {"needed": False, "reason": reason or "Planner unavailable"},
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


def _mentions_health_risk_text(text: str) -> bool:
    raw = str(text or "")
    return "健康风险" in raw or any(term in raw for term in HEALTH_RISK_TERMS)


def _turn_context_for_guard(state: AgentState) -> dict[str, Any]:
    existing = state.get("current_turn_context")
    if isinstance(existing, dict) and existing:
        return existing
    try:
        return build_current_turn_context(state, sent_message_summary=sent_message_summary_for_model(state))
    except Exception:
        return {}


def _renumber_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        updated = dict(item)
        updated["order"] = len(output) + 1
        output.append(updated)
    return output


def _normalize_reply_messages(value: Any, *, state: AgentState | None = None) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:_reply_message_limit(value, state)]:
        if isinstance(item, str):
            text = item.strip()
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
        text = _message_text(content)
        if text:
            key = "handoff_reason" if msg_type == "human_handoff_notice" else ("url" if msg_type == "image" else "text")
            output.append({"type": msg_type, "order": len(output) + 1, "content": {key: text}})
    return output


def _reply_message_limit(value: list[Any], state: AgentState | None) -> int:
    """Only lift the normal cap for a fact-backed same-district card sequence."""
    if not isinstance(state, dict) or not value:
        return 4
    visible: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return 4
        message_type = str(item.get("type") or "text").strip()
        if message_type not in {"text", "store_address"}:
            return 4
        visible.append(item)
    text_count = sum(1 for item in visible if str(item.get("type") or "text").strip() == "text")
    card_ids = {
        _store_address_id(item.get("content"))
        for item in visible
        if str(item.get("type") or "").strip() == "store_address" and _store_address_id(item.get("content"))
    }
    if text_count > 2 or len(card_ids) < 2:
        return 4
    summary = state.get("store_scope_summary") if isinstance(state.get("store_scope_summary"), dict) else {}
    regions = summary.get("relevant_regions") if isinstance(summary.get("relevant_regions"), list) else []
    for region in regions:
        if not isinstance(region, dict):
            continue
        expected = {
            str(store.get("store_id") or store.get("id") or "").strip()
            for store in region.get("requested_district_stores") or []
            if isinstance(store, dict) and str(store.get("store_id") or store.get("id") or "").strip()
        }
        if card_ids and card_ids.issubset(expected):
            return len(value)
    return 4


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


def _rewrite_reference_store_lookup_queries(required_tools: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or state.get("content") or "")
    if not _should_rewrite_store_lookup_with_context_anchor(content, state):
        return required_tools
    anchor = _recent_store_name_from_context(state)
    if not anchor:
        return required_tools
    rewritten: list[dict[str, Any]] = []
    for tool in required_tools:
        if isinstance(tool, dict) and str(tool.get("name") or "") == "customer_store_lookup":
            query = str(tool.get("query") or "").strip()
            if _store_lookup_query_has_explicit_scope(query, state):
                rewritten.append(tool)
                continue
            updated = dict(tool)
            updated["query"] = anchor
            rewritten.append(updated)
        else:
            rewritten.append(tool)
    return rewritten


def _normalize_current_message_store_lookup_queries(
    required_tools: list[dict[str, Any]], state: AgentState
) -> list[dict[str, Any]]:
    current_text = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not current_text or not _raw_text_mentions_store_location_request(current_text):
        return required_tools
    normalized: list[dict[str, Any]] = []
    for tool in required_tools:
        if not isinstance(tool, dict) or str(tool.get("name") or "") != "customer_store_lookup":
            normalized.append(tool)
            continue
        query = str(tool.get("query") or "").strip()
        if not _store_lookup_query_comes_from_current_message(query, state):
            normalized.append(tool)
            continue
        cleaned = _strip_location_answer_prefixes(_clean_scoped_location_query(query))
        if cleaned and (_looks_like_bare_location_token(cleaned) or _looks_like_specific_region(_compact_text(cleaned))):
            updated = dict(tool)
            updated["query"] = cleaned
            normalized.append(updated)
        else:
            normalized.append(tool)
    return normalized


def _should_rewrite_store_lookup_with_context_anchor(content: str, state: AgentState) -> bool:
    if not _current_message_requests_store_detail(content):
        return False
    if not is_context_reference_message(content):
        return False
    if _current_message_has_explicit_store_or_location_scope(content, state):
        return False
    return True


def _current_message_has_explicit_store_or_location_scope(content: str, state: AgentState) -> bool:
    if _store_names_matching_text(state, content):
        return True
    return _location_query_has_current_message_scope(content, state)


def _store_lookup_query_has_explicit_scope(query: str, state: AgentState) -> bool:
    if not str(query or "").strip():
        return False
    if _store_lookup_query_comes_from_current_message(query, state):
        return True
    if _store_lookup_query_comes_from_recent_customer_location(query, state):
        return True
    if _query_matches_current_message_store_name(query, state):
        return True
    if _query_is_unique_real_store_name(query, state):
        return True
    if _query_matches_scope_store_name(query, state):
        return True
    return False


def _store_lookup_query_comes_from_current_message(query: str, state: AgentState) -> bool:
    query_text = str(query or "").strip()
    query_compact = _compact_text(query_text)
    if not query_compact:
        return False
    current_text = str(state.get("normalized_content") or state.get("content") or "").strip()
    current_compact = _compact_text(current_text)
    if not current_compact:
        return False
    if query_compact == current_compact:
        return True
    stripped_current = _strip_location_answer_prefixes(_strip_store_question_words(current_text))
    stripped_compact = _compact_text(stripped_current)
    if stripped_compact and query_compact == stripped_compact and _looks_like_bare_location_token(stripped_current):
        return True
    if (
        query_compact in current_compact
        and _looks_like_short_place_query(query_text)
        and _raw_text_mentions_store_location_request(current_text)
    ):
        return True
    return False


def _looks_like_short_place_query(value: str) -> bool:
    text = re.sub(r"[\s,，。？！?：:;；、\"'()（）\[\]【】<>《》]+", "", str(value or "").strip())
    return bool(2 <= len(text) <= 16 and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]+", text))


def _raw_text_mentions_store_location_request(value: str) -> bool:
    text = str(value or "")
    return any(
        term in text
        for term in (
            "门店",
            "店在哪里",
            "店在哪",
            "有店",
            "附近",
            "最近",
            "地址",
            "位置",
            "定位",
            "导航",
        )
    )


def _clean_scoped_location_query(value: str) -> str:
    text = re.sub(r"[，,。？?！!\s]", "", str(value or "").strip())
    if not text:
        return ""
    text = re.sub(r"^(请问|麻烦|帮我|帮忙|你们|咱们|我们|我想|想看|想查|查一下|看一下|给我|发我|发个|发一下)", "", text)
    text = re.sub(r"^(哪家|哪个|哪一个)?离", "", text)
    text = re.sub(r"^(有没有|有无|哪里有|哪家|哪个|哪一个|在)", "", text)
    suffixes = (
        "附近有门店吗",
        "附近有店吗",
        "有门店吗",
        "有店吗",
        "有没有门店",
        "有没有店",
        "门店在哪里",
        "门店在哪",
        "店在哪里",
        "店在哪",
        "附近门店",
        "附近店",
        "门店地址",
        "门店位置",
        "附近",
        "周边",
        "最近",
        "更近",
        "比较近",
        "近一点",
        "近点",
        "地址",
        "位置",
        "导航",
        "路线",
        "停车",
        "营业时间",
        "几点下班",
        "几点关门",
        "发我一下",
        "给我一下",
        "发我",
        "给我",
        "一下",
        "有吗",
        "吗",
        "呢",
        "呀",
    )
    changed = True
    while changed and text:
        changed = False
        for suffix in suffixes:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text.strip()


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


def _normalize_available_time_dates_from_context(
    tools: list[dict[str, Any]],
    state: AgentState,
) -> list[dict[str, Any]]:
    inferred_date = _contextual_appointment_date(state)
    if not inferred_date:
        return tools
    normalized: list[dict[str, Any]] = []
    for item in tools:
        tool = dict(item)
        if str(tool.get("name") or "") == "available_time":
            tool["date"] = inferred_date
        normalized.append(tool)
    return normalized


def _contextual_appointment_date(state: AgentState) -> str:
    base_date = _state_current_date(state)
    current = str(state.get("normalized_content") or state.get("content") or "")
    resolved = _date_reference_from_text(current, base_date=base_date)
    if resolved:
        return resolved
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in reversed(history[-8:]):
        if isinstance(item, dict):
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "")
        else:
            text = str(item or "")
        resolved = _date_reference_from_text(text, base_date=base_date)
        if resolved:
            return resolved
    return ""


def _state_current_date(state: AgentState) -> date:
    raw = str(state.get("current_date") or "").strip()
    try:
        return date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        return date.today()


def _date_reference_from_text(text: str, *, base_date: date) -> str:
    value = str(text or "")
    iso_match = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", value)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:
            pass
    month_day = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", value)
    if month_day:
        try:
            candidate = date(base_date.year, int(month_day.group(1)), int(month_day.group(2)))
            if candidate < base_date:
                candidate = date(base_date.year + 1, candidate.month, candidate.day)
            return candidate.isoformat()
        except ValueError:
            pass
    if "后天" in value:
        return (base_date + timedelta(days=2)).isoformat()
    if "明天" in value:
        return (base_date + timedelta(days=1)).isoformat()
    if "今天" in value or "今日" in value:
        return base_date.isoformat()
    return ""


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
            if not query:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "customer_store_lookup",
                        "missing": "store_lookup_missing_query",
                        "note": "customer_store_lookup requires the customer's non-empty location or store query.",
                    }
                )
            elif (
                str(tool.get("location_specificity") or "").strip() == "typo_or_alias"
                and not list(tool.get("location_candidates") or [])
            ):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "customer_store_lookup",
                        "missing": "store_lookup_typo_candidate_required",
                        "note": (
                            "The Planner classified the customer's location as typo_or_alias, so it must preserve the raw "
                            "query and provide 1-3 location_candidates for geocode validation. Candidates are hypotheses, "
                            "not confirmed facts, and must require customer confirmation before any store card is sent."
                        ),
                    }
                )
            elif _store_lookup_reopens_stale_location_context(query, state):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "customer_store_lookup",
                        "missing": "store_lookup_not_relevant_to_current_turn",
                        "note": (
                            "The lookup query only comes from older location/store context, while the current customer "
                            "message neither asks about a store nor contributes a location fragment. Do not reopen a "
                            "completed store lookup. Continue the current conversation/mainline without this tool call."
                        ),
                    }
                )
            elif _ambiguous_reference_store_lookup_query(query, state):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "customer_store_lookup",
                        "missing": "store_lookup_query_over_ambiguous_reference",
                        "note": (
                            "The current message refers to a store by context, but recent evidence contains multiple "
                            "possible stores. Do not choose one store for customer_store_lookup. Repair to direct_reply "
                            "and ask the customer which store they mean, or ask for city/district."
                        ),
                    }
                )
            elif _generic_store_question_uses_history_query(query, state):
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
            elif _store_lookup_conflicts_with_accepted_binding(query, state):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "customer_store_lookup",
                        "missing": "store_lookup_conflicts_with_accepted_binding",
                        "note": (
                            "store_binding_decision accepts a real store, but customer_store_lookup.query does not identify "
                            "that store. Keep the accepted store without a new lookup, or change store_binding_decision to "
                            "exploring/ambiguous and use location evidence from the current customer message. Do not treat an "
                            "unrelated short reply as a new location."
                        ),
                    }
                )
            continue
        if name == "distance_calculate":
            origin = str(tool.get("origin") or tool.get("address") or tool.get("query") or "").strip()
            if not _location_query_has_scope_region(origin, state):
                violations.append(_ambiguous_location_tool_violation("distance_calculate"))
            elif _distance_origin_is_broad_region_only(origin, state):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "distance_calculate",
                        "missing": "distance_origin_too_broad_for_ranking",
                        "note": (
                            "distance_calculate needs a district, landmark, address, or customer location. A bare city/province "
                            "cannot support a nearest-store ranking. Use customer_store_lookup for the city list or ask for one "
                            "more precise location; do not claim a store is nearer from a city-only origin."
                        ),
                    }
                )
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
            continue
        if name == "create_work_order":
            store_id = str(tool.get("store_id") or "").strip()
            amount = str(tool.get("prepay") or tool.get("amount") or "").strip()
            confirmation_source = str(tool.get("store_confirmation_source") or "").strip()
            binding = state.get("store_binding_decision") if isinstance(state.get("store_binding_decision"), dict) else {}
            binding_status = str(binding.get("status") or "none").strip()
            binding_store_id = str(binding.get("store_id") or "").strip()
            missing_args = [
                key
                for key, value in (("store_id", store_id), ("prepay", amount), ("store_confirmation_source", confirmation_source))
                if not value
            ]
            if missing_args:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "create_work_order",
                        "missing": "create_work_order_missing_" + "_".join(missing_args),
                        "note": "create_work_order requires a confirmed real store_id and a 10/20/30/40 prepay amount.",
                    }
                )
            elif binding_status in {"exploring", "rejected", "ambiguous"}:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "create_work_order",
                        "missing": "create_work_order_store_binding_not_accepted",
                        "note": (
                            "The Planner says the customer is still comparing, rejected the store, or has an ambiguous "
                            "store choice. Resolve that semantic decision before creating an order."
                        ),
                    }
                )
            elif binding_status in {"accepted_explicit", "accepted_implicit"} and binding_store_id != store_id:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "create_work_order",
                        "missing": "create_work_order_store_binding_mismatch",
                        "note": "create_work_order.store_id must match store_binding_decision.store_id.",
                    }
                )
            elif confirmation_source not in {
                "request_confirmed",
                "current_message",
                "recent_explicit_choice",
                "single_store_card_anchor",
                "appointment_context",
            }:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "create_work_order",
                        "missing": "create_work_order_store_confirmation_invalid",
                        "note": "A profile preference or unconfirmed lookup candidate cannot authorize order creation.",
                    }
                )
            elif confirmation_source == "single_store_card_anchor" and latest_single_store_card_anchor_id(state) != store_id:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "create_work_order",
                        "missing": "create_work_order_single_store_card_anchor_mismatch",
                        "note": (
                            "single_store_card_anchor requires the exact store id from the latest authoritative delivery batch, "
                            "and that batch must contain exactly one store card. Multi-store deliveries and profile preferences "
                            "cannot authorize order creation."
                        ),
                    }
                )
            elif not _is_known_numeric_store_id(store_id, state):
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "create_work_order",
                        "missing": "create_work_order_store_id_not_confirmed",
                        "note": "Confirm a real current store before creating the work order; profile preferred_store is not confirmation.",
                    }
                )
            continue
        if name == "add_customer_mobile":
            mobile = re.sub(r"\D", "", str(tool.get("mobile") or ""))
            if len(mobile) != 11:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "add_customer_mobile",
                        "missing": "add_customer_mobile_invalid_mobile",
                        "note": "add_customer_mobile requires an 11-digit phone number from current customer facts; ask the customer to provide the complete number instead of calling the tool.",
                    }
                )
            else:
                tool["mobile"] = mobile
            continue
        if name == "create_order_plan":
            missing_args = [
                key
                for key, value in (("store_id", tool.get("store_id")), ("date", tool.get("date") or tool.get("appointment_time")))
                if value in (None, "")
            ]
            if not tool.get("order_id") and not _has_paid_order_id(state):
                missing_args.append("order_id")
            if not tool.get("customer_name") and not _has_registration_field(state, "customer_name"):
                missing_args.append("customer_name")
            if not tool.get("mobile") and not _has_registration_field(state, "phone"):
                missing_args.append("mobile")
            if not _has_paid_deposit_context(state, payment_state=str(state.get("payment_state") or "unknown")):
                missing_args.append("paid_deposit")
            if missing_args:
                violations.append(
                    {
                        "task_type": "tool_argument",
                        "subtype": "create_order_plan",
                        "missing": "create_order_plan_missing_" + "_".join(missing_args),
                        "note": "create_order_plan requires a paid order_id, real store_id, and the exact customer-confirmed available datetime.",
                    }
                )
            continue

    return violations


def _store_lookup_conflicts_with_accepted_binding(query: str, state: AgentState) -> bool:
    binding = state.get("store_binding_decision") if isinstance(state.get("store_binding_decision"), dict) else {}
    if str(binding.get("status") or "").strip() not in {"accepted_explicit", "accepted_implicit"}:
        return False
    bound_store_id = str(binding.get("store_id") or "").strip()
    bound_store_name = str(binding.get("store_name") or "").strip()
    if not bound_store_name and bound_store_id:
        bound_store_name = _store_name_for_id(bound_store_id, state)
    compact_query = _compact_text(query)
    compact_name = _compact_text(bound_store_name)
    return not bool(compact_query and compact_name and _store_name_query_matches(compact_name, compact_query))


def _store_name_for_id(store_id: str, state: AgentState) -> str:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    for store in stores:
        if not isinstance(store, dict):
            continue
        candidate_id = str(store.get("store_id") or store.get("id") or "").strip()
        if candidate_id == store_id:
            return str(store.get("store_name") or store.get("name") or "").strip()
    request_store_id = str(state.get("confirmed_store_id") or state.get("store_id") or "").strip()
    if request_store_id == store_id:
        return str(state.get("confirmed_store_name") or state.get("store_name") or "").strip()
    return ""


def _complete_explicit_kb_search_arguments(
    required_tools: list[dict[str, Any]],
    state: AgentState,
) -> list[dict[str, Any]]:
    """Complete schema fields after Planner has explicitly selected the case-study KB."""
    current_query = str(state.get("normalized_content") or state.get("content") or "").strip()
    completed: list[dict[str, Any]] = []
    for item in required_tools:
        tool = dict(item)
        if (
            str(tool.get("name") or "").strip() == "kb_search"
            and str(tool.get("purpose") or "").strip() == "case_studies"
        ):
            if not str(tool.get("kb_name") or "").strip():
                tool["kb_name"] = "case_studies"
            if current_query and not str(tool.get("query") or "").strip():
                tool["query"] = current_query[:600]
        completed.append(tool)
    return completed


def _ambiguous_reference_store_lookup_query(query: str, state: AgentState) -> bool:
    if not query:
        return False
    content = str(state.get("normalized_content") or state.get("content") or "")
    if _looks_like_payment_entry_request(content):
        return False
    if _query_matches_current_message_store_name(query, state):
        return False
    if _query_is_unique_real_store_name(query, state):
        return False
    if not is_context_reference_message(content):
        return False
    names = _ambiguous_store_names_from_context(state)
    if not names:
        return False
    compact_query = _compact_text(query)
    return any(_store_name_query_matches(_compact_text(name), compact_query) for name in names)


def _query_is_unique_real_store_name(query: str, state: AgentState) -> bool:
    compact_query = _compact_text(query)
    if not compact_query:
        return False
    all_names = [*_known_store_names_for_state(state), *_snapshot_store_names()]
    exact_names = _without_subsumed_store_names(
        _dedupe_names([name for name in all_names if name and _compact_text(name) == compact_query])
    )
    if len(exact_names) == 1:
        return True
    names = _without_subsumed_store_names(
        _dedupe_names(
            [
                name
                for name in all_names
                if name and _store_name_query_matches(_compact_text(name), compact_query)
            ]
        )
    )
    return len(names) == 1


def _looks_like_payment_entry_request(content: str) -> bool:
    text = _compact_text(content)
    if not text:
        return False
    payment_markers = ("预约金", "付款入口", "收款入口", "报名入口", "支付入口", "入口发", "发入口")
    return any(marker in text for marker in payment_markers)


def _query_matches_current_message_store_name(query: str, state: AgentState) -> bool:
    content = _compact_text(state.get("normalized_content") or state.get("content") or "")
    compact_query = _compact_text(query)
    if not content or not compact_query:
        return False

    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    names: list[str] = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        names.extend(str(store.get(key) or "").strip() for key in ("store_name", "name"))
    names.extend(_snapshot_store_names())

    for name in names:
        compact_name = _compact_text(name)
        if not compact_name:
            continue
        if _store_name_query_matches(compact_name, content) and _store_name_query_matches(compact_name, compact_query):
            return True
    return False


def _ambiguous_store_names_from_context(state: AgentState) -> list[str]:
    turn_context = state.get("current_turn_context") if isinstance(state.get("current_turn_context"), dict) else {}
    if not turn_context:
        turn_context = _turn_context_for_guard(state)
    for key in ("current_store_anchor", "confirmed_store"):
        value = turn_context.get(key)
        if isinstance(value, dict) and value.get("ambiguous"):
            return [str(name or "").strip() for name in value.get("matched_store_names") or [] if str(name or "").strip()]
    store_anchor = current_store_anchor_from_state(
        state,
        current_known_store=None,
        allow_profile=False,
        prefer_recent=True,
    )
    if isinstance(store_anchor, dict) and store_anchor.get("ambiguous"):
        return [str(name or "").strip() for name in store_anchor.get("matched_store_names") or [] if str(name or "").strip()]
    return []


def _normalize_payment_decision(
    value: Any,
    *,
    state: AgentState,
    payment_state: str,
    payment_action: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    action = _normalize_enum(str(raw.get("action") or ""), ALLOWED_PAYMENT_DECISION_ACTIONS, "")
    method = _normalize_enum(str(raw.get("method") or ""), ALLOWED_PAYMENT_METHODS, "none")
    legacy_action = _payment_decision_action_from_legacy(payment_state=payment_state, payment_action=payment_action)
    if not action:
        action = legacy_action
    if method == "transfer":
        action = "manual_transfer"
    elif action == "manual_transfer":
        method = "transfer"
    elif action in {"send_now", "resend"} and method == "none":
        method = "mini_program"
    if payment_state == "customer_claimed_paid" or _has_paid_deposit_context(state, payment_state=payment_state):
        action = "after_paid_next_step"
    elif (
        action == "none"
        and _has_payment_collection(messages)
        and payment_action not in {"none", "offer_resend", "explain_existing", "confirm_next_step"}
    ):
        action = "resend" if payment_state in {"resend_requested", "payment_failed"} else "send_now"

    party_size = _coerce_party_size(raw.get("party_size"))
    raw_amount = _coerce_payment_amount(raw.get("amount"))
    if party_size is None and raw_amount:
        party_size = payment_party_size_for_amount(raw_amount)

    context = payment_collection_context(state=state, messages=messages)
    if action in {"send_now", "resend"}:
        if party_size is None:
            party_size = int(context.get("participants") or 1)
        if party_size > 4:
            action = "ask_party_size"
        amount = payment_amount_for_party_size(party_size) if action in {"send_now", "resend"} else None
    else:
        amount = raw_amount
    if action == "manual_transfer":
        method = "transfer"
    elif action in {"send_now", "resend"}:
        method = "mini_program"
    else:
        method = "none"

    source = str(raw.get("source") or "").strip()
    if not source:
        if isinstance(value, dict):
            source = "planner"
        elif action in {"send_now", "resend"}:
            source = "legacy_payment_action"
        else:
            source = "legacy_fields"
    confidence = _normalize_enum(
        str(raw.get("confidence") or ""),
        ALLOWED_PAYMENT_DECISION_CONFIDENCE,
        "medium" if isinstance(value, dict) else "low",
    )
    basis = _clean_str_list(raw.get("basis") if isinstance(raw.get("basis"), list) else [])
    if not basis and legacy_action != "none":
        basis = [f"legacy payment_state={payment_state or 'unknown'} payment_action={payment_action or 'unknown'}"]

    output: dict[str, Any] = {
        "action": action,
        "method": method,
        "source": source,
        "confidence": confidence,
    }
    if party_size is not None:
        output["party_size"] = party_size
    if amount:
        output["amount"] = amount
    if basis:
        output["basis"] = basis[:5]
    return output


def _normalize_appointment_decision(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    action = _normalize_enum(
        str(raw.get("action") or ""),
        ALLOWED_APPOINTMENT_DECISION_ACTIONS,
        "none",
    )
    commitment_level = _normalize_enum(
        str(raw.get("commitment_level") or ""),
        ALLOWED_APPOINTMENT_COMMITMENT_LEVELS,
        "none",
    )
    output: dict[str, Any] = {
        "action": action,
        "commitment_level": commitment_level,
    }
    source = str(raw.get("source") or "").strip()
    if source:
        output["source"] = source[:80]
    basis = _clean_str_list(raw.get("basis") if isinstance(raw.get("basis"), list) else [])
    if basis:
        output["basis"] = basis[:6]
    return output


def _normalize_sales_progression(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "status": _normalize_enum(
            str(raw.get("status") or ""),
            ALLOWED_SALES_PROGRESSION_STATUSES,
            "unknown",
        ),
        "target_stage": _normalize_enum(
            str(raw.get("target_stage") or ""),
            ALLOWED_SALES_PROGRESSION_TARGETS,
            "none",
        ),
        "action": _normalize_enum(
            str(raw.get("action") or ""),
            ALLOWED_SALES_PROGRESSION_ACTIONS,
            "none",
        ),
        "goal": str(raw.get("goal") or "").strip()[:240],
        "basis": _clean_str_list(raw.get("basis") if isinstance(raw.get("basis"), list) else [])[:6],
    }


def _normalize_closing_move(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "action": _normalize_enum(
            str(raw.get("action") or ""),
            ALLOWED_CLOSING_MOVE_ACTIONS,
            "none",
        ),
        "mainline_stage": _normalize_enum(
            str(raw.get("mainline_stage") or ""),
            ALLOWED_SALES_PROGRESSION_TARGETS,
            "none",
        ),
        "reason": str(raw.get("reason") or "").strip()[:240],
        "required_slot": str(raw.get("required_slot") or "").strip()[:80],
        "must_not_repeat": _clean_str_list(
            raw.get("must_not_repeat") if isinstance(raw.get("must_not_repeat"), list) else []
        )[:6],
    }


def _normalize_order_decision(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    action = _normalize_enum(str(raw.get("action") or ""), ALLOWED_ORDER_DECISION_ACTIONS, "none")
    output: dict[str, Any] = {"action": action}
    for key in ("order_id", "store_id", "category_id", "source"):
        text = str(raw.get(key) or "").strip()
        if text:
            output[key] = text[:120]
    binding_level = _normalize_enum(
        str(raw.get("store_binding_level") or ""),
        ALLOWED_STORE_BINDING_LEVELS,
        "none",
    )
    if binding_level != "none":
        output["store_binding_level"] = binding_level
    try:
        amount = int(float(raw.get("amount") or raw.get("prepay") or 0))
    except (TypeError, ValueError):
        amount = 0
    if amount in {10, 20, 30, 40}:
        output["amount"] = amount
    basis = _clean_str_list(raw.get("basis") if isinstance(raw.get("basis"), list) else [])
    if basis:
        output["basis"] = basis[:6]
    return output


def _normalize_precision_qa_decision(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    question_id = str(raw.get("question_id") or raw.get("id") or "").strip()
    if question_id and not precision_qa_for_id(question_id):
        question_id = ""
    confidence = _normalize_enum(
        raw.get("confidence"),
        ALLOWED_PRECISION_QA_CONFIDENCE,
        "low",
    )
    answer_depth = _normalize_enum(
        raw.get("answer_depth"),
        ALLOWED_PRECISION_QA_DEPTH,
        "standard",
    )
    return {
        "question_id": question_id,
        "confidence": confidence if question_id else "low",
        "answer_depth": answer_depth,
        "basis": _clean_str_list(raw.get("basis"))[:5],
    }


def _normalize_store_binding_decision(
    value: Any,
    *,
    order_decision: dict[str, Any],
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    status = _normalize_enum(
        str(raw.get("status") or ""),
        ALLOWED_STORE_BINDING_STATUSES,
        "none",
    )
    if status == "none":
        status = _store_binding_status_from_order_decision(order_decision)
    store_id = str(raw.get("store_id") or order_decision.get("store_id") or "").strip()
    confidence = _normalize_enum(
        str(raw.get("confidence") or ""),
        ALLOWED_STORE_BINDING_CONFIDENCE,
        "",
    )
    output: dict[str, Any] = {
        "status": status,
        "store_id": store_id,
        "confidence": confidence,
        "source": str(raw.get("source") or order_decision.get("source") or "").strip()[:120],
        "basis": _clean_str_list(raw.get("basis") if isinstance(raw.get("basis"), list) else [])[:6],
    }
    return {key: item for key, item in output.items() if item not in (None, "", [], {})}


def _complete_store_binding_from_order_decision(
    value: dict[str, Any],
    *,
    order_decision: dict[str, Any],
) -> dict[str, Any]:
    output = dict(value or {})
    if str(output.get("status") or "none") == "none":
        output["status"] = _store_binding_status_from_order_decision(order_decision)
    if not output.get("store_id") and order_decision.get("store_id"):
        output["store_id"] = str(order_decision.get("store_id") or "").strip()
    if not output.get("source") and order_decision.get("source"):
        output["source"] = str(order_decision.get("source") or "").strip()[:120]
    return {key: item for key, item in output.items() if item not in (None, "", [], {})}


def _store_binding_status_from_order_decision(order_decision: dict[str, Any]) -> str:
    binding_level = str(order_decision.get("store_binding_level") or "").strip()
    if binding_level == "single_store_card_anchor":
        return "accepted_implicit"
    if binding_level == "ambiguous":
        return "ambiguous"
    if binding_level in {
        "explicit_confirmed",
        "request_confirmed",
        "appointment_context",
        "existing_order",
    }:
        return "accepted_explicit"
    return "none"


def _store_binding_order_consistency_violations(
    *,
    state: AgentState,
    store_binding_decision: dict[str, Any],
    order_decision: dict[str, Any],
    required_tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Validate the model's own store-acceptance decision against order structure."""

    status = str(store_binding_decision.get("status") or "none").strip()
    store_id = str(store_binding_decision.get("store_id") or "").strip()
    order_action = str(order_decision.get("action") or "none").strip()
    order_store_id = str(order_decision.get("store_id") or "").strip()
    create_tool = next(
        (
            item
            for item in required_tools
            if isinstance(item, dict) and str(item.get("name") or "").strip() == "create_work_order"
        ),
        {},
    )
    if status == "accepted_implicit":
        anchor = store_anchor_fact_for_model(state)
        if str(anchor.get("status") or "") != "eligible" or str(anchor.get("store_id") or "") != store_id:
            return [
                {
                    "task_type": "transaction_consistency",
                    "subtype": "store_binding",
                    "missing": "accepted_implicit_requires_eligible_store_anchor_fact",
                    "note": (
                        "accepted_implicit requires the latest authoritative store-card batch to contain exactly one "
                        "matching store. Unverified legacy events and multi-store batches must be exploring/ambiguous."
                    ),
                }
            ]
    if status in {"exploring", "rejected", "ambiguous"} and order_action in {"create_work", "use_existing"}:
        return [
            {
                "task_type": "transaction_consistency",
                "subtype": "store_binding",
                "missing": "unaccepted_store_binding_cannot_resolve_order",
                "note": "Do not create or bind an order while the customer is still comparing, rejected the store, or has an ambiguous choice.",
            }
        ]
    if status not in {"accepted_explicit", "accepted_implicit"}:
        return []
    if store_id and order_store_id and store_id != order_store_id:
        return [
            {
                "task_type": "transaction_consistency",
                "subtype": "store_binding",
                "missing": "accepted_store_binding_order_store_mismatch",
                "note": "The order decision must use the same real store selected by store_binding_decision.",
            }
        ]
    if explicit_professional_assist_reason(state) or is_hard_health_risk_hold(health_risk_hold(state)):
        return []
    if _has_authoritative_paid_context(state):
        return []
    return []


def _reconcile_paid_payment_decision(
    *,
    payment_decision: dict[str, Any],
    order_decision: dict[str, Any],
    state: AgentState,
) -> dict[str, Any]:
    if str(payment_decision.get("action") or "") != "after_paid_next_step":
        return payment_decision
    amount = _numeric_payment_amount(order_decision.get("amount"))
    if not amount:
        context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
        for order in reversed(context.get("orders") or []):
            if not isinstance(order, dict):
                continue
            normalized_payment = normalize_prepay_facts(order)
            paid = _numeric_payment_amount(order.get("prepay_paid") or order.get("fee_paid"))
            required = _numeric_payment_amount(order.get("prepay_required") or order.get("fee_required"))
            deposit_state = str(order.get("deposit_state") or normalized_payment.get("deposit_state") or "")
            if deposit_state in {
                "paid_by_order",
                "paid_by_screenshot",
                "paid_by_platform_transfer_event",
                "paid",
            }:
                amount = paid or required
                if amount:
                    break
    if not amount:
        return payment_decision
    return {
        **payment_decision,
        "party_size": payment_party_size_for_amount(amount),
        "amount": amount,
    }


def _appointment_create_tool_violations(
    *,
    appointment_decision: dict[str, Any],
    required_tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if str(appointment_decision.get("action") or "") != "create_plan":
        return []
    if any(str(item.get("name") or "") == "create_order_plan" for item in required_tools if isinstance(item, dict)):
        return []
    return [
        {
            "task_type": "tool_argument",
            "subtype": "create_order_plan",
            "missing": "create_order_plan_tool_required",
            "note": (
                "appointment_decision.action=create_plan must use decision=need_tools and call create_order_plan. "
                "Do not directly tell the customer the appointment is arranged before that tool succeeds."
            ),
        }
    ]


def _registration_before_appointment_violations(
    *,
    state: AgentState,
    payment_decision: dict[str, Any],
    appointment_decision: dict[str, Any],
) -> list[dict[str, str]]:
    if str(payment_decision.get("action") or "") != "after_paid_next_step":
        return []
    action = str(appointment_decision.get("action") or "")
    if action == "none":
        return []
    current = state.get("current_turn_context") if isinstance(state.get("current_turn_context"), dict) else {}
    registration = current.get("registration_evidence") if isinstance(current.get("registration_evidence"), dict) else {}
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    name_collected = bool(registration.get("customer_name_collected")) or bool(str(basic.get("customer_name") or "").strip())
    phone = re.sub(r"\D", "", str(basic.get("phone") or ""))
    phone_collected = bool(registration.get("phone_collected")) or len(phone) == 11
    if name_collected and phone_collected:
        return []
    missing = [name for name, present in (("customer_name", name_collected), ("phone", phone_collected)) if not present]
    return [
        {
            "task_type": "transaction_precondition",
            "subtype": "registration",
            "missing": "registration_required_before_appointment_decision",
            "note": (
                "The paid-order flow must finish customer name and phone registration before appointment decisions. "
                f"Missing registration facts: {missing}. Set appointment_decision.action=none and collect only the missing registration fields."
            ),
        }
    ]


def _appointment_change_fact_violations(
    *,
    state: AgentState,
    appointment_decision: dict[str, Any],
    required_tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if str(appointment_decision.get("action") or "") != "confirm_existing":
        return []
    requested_time = _requested_appointment_time_for_validation(state)
    existing_time = _existing_appointment_time_for_validation(state)
    if not requested_time or not existing_time or requested_time == existing_time:
        return []
    tools = {str(item.get("name") or "") for item in required_tools if isinstance(item, dict)}
    target_supported = requested_time in _available_appointment_times_for_validation(state)
    repair_direction = (
        "The target time is already supported by available_time facts, so use create_plan and call create_order_plan now."
        if target_supported
        else "Use check_availability with appointment_record_query/available_time before asking the customer to confirm."
    )
    return [
        {
            "task_type": "reply_fact_consistency",
            "subtype": "appointment_change",
            "missing": "appointment_change_requires_verification",
            "note": (
                f"The current target time {requested_time} differs from the existing appointment time {existing_time}. "
                "Do not use confirm_existing or treat the old appointment as the changed result. "
                f"{repair_direction} "
                f"Current executable tools: {sorted(tool for tool in tools if tool and tool != 'no_tool')}."
            ),
        }
    ]


def _existing_appointment_time_for_validation(state: AgentState) -> str:
    candidates: list[Any] = []
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    for key in ("appointment", "appointment_info", "appointment_info_v2"):
        if isinstance(context.get(key), dict):
            candidates.append(context[key].get("appointment_time") or context[key].get("time"))
    cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    candidates.append(cache.get("appointment_time") or cache.get("time"))
    envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = envelope.get("structured_facts") if isinstance(envelope.get("structured_facts"), dict) else {}
    for fact in structured.get("appointment_facts") or []:
        if not isinstance(fact, dict):
            continue
        if str(fact.get("type") or "") in {"appointment_created", "appointment_confirmed", "appointment_record"}:
            candidates.append(fact.get("appointment_time") or fact.get("time"))
    for value in candidates:
        normalized = normalize_time_text(str(value or ""))
        if normalized:
            return normalized
    return ""


def _requested_appointment_time_for_validation(state: AgentState) -> str:
    requested = normalize_time_text(str(state.get("normalized_content") or state.get("content") or ""))
    if not requested:
        return ""
    available = _available_appointment_times_for_validation(state)
    if requested in available:
        return requested
    try:
        hour, minute = (int(part) for part in requested.split(":", 1))
    except (TypeError, ValueError):
        return requested
    if hour < 12:
        afternoon = f"{hour + 12:02d}:{minute:02d}"
        if afternoon in available:
            return afternoon
        if afternoon == _existing_appointment_time_for_validation(state):
            return afternoon
    return requested


def _available_appointment_times_for_validation(state: AgentState) -> set[str]:
    values: set[str] = set()
    envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = envelope.get("structured_facts") if isinstance(envelope.get("structured_facts"), dict) else {}
    for fact in structured.get("appointment_facts") or []:
        if not isinstance(fact, dict) or str(fact.get("type") or "") != "available_time":
            continue
        candidates: list[Any] = [fact.get("recommended_slot")]
        candidates.extend(fact.get("backup_slots") or [])
        if fact.get("target_time_available") is True:
            candidates.append(fact.get("target_time"))
        slots = fact.get("slots")
        if isinstance(slots, dict):
            for group in slots.values():
                candidates.extend(group if isinstance(group, list) else [group])
        elif isinstance(slots, list):
            candidates.extend(slots)
        for candidate in candidates:
            normalized = normalize_time_text(str(candidate or ""))
            if normalized:
                values.add(normalized)
    turn_evidence = state.get("turn_evidence") if isinstance(state.get("turn_evidence"), dict) else {}
    appointment = turn_evidence.get("appointment_evidence") if isinstance(turn_evidence.get("appointment_evidence"), dict) else {}
    for candidate in appointment.get("available_slots") or []:
        normalized = normalize_time_text(str(candidate or ""))
        if normalized:
            values.add(normalized)
    return values


def _has_active_order_id(state: AgentState) -> bool:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    for order in context.get("orders") or []:
        if not isinstance(order, dict):
            continue
        if str(order.get("status") or "") in {"pending", "waiting_schedule", "scheduled"} and str(order.get("id") or order.get("order_id") or "").strip():
            return True
    return False


def _reconcile_existing_order_for_payment(
    *,
    state: AgentState,
    payment_decision: dict[str, Any],
    order_decision: dict[str, Any],
    required_tools: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if str(payment_decision.get("action") or "") not in {"send_now", "resend"}:
        return order_decision, required_tools
    create_tool = next(
        (item for item in required_tools if isinstance(item, dict) and str(item.get("name") or "") == "create_work_order"),
        {},
    )
    store_id = str(
        order_decision.get("store_id")
        or create_tool.get("store_id")
        or state.get("confirmed_store_id")
        or state.get("store_id")
        or ""
    ).strip()
    order = _matching_active_order_for_payment(
        state,
        store_id=store_id,
        amount=_payment_decision_amount(payment_decision),
    )
    if not order:
        return order_decision, required_tools
    normalized = {
        "action": "use_existing",
        "order_id": str(order.get("id") or order.get("order_id") or ""),
        "store_id": str(order.get("store_id") or store_id),
        "source": "structured_order",
        "basis": ["已有匹配的进行中预约金订单，无需重复开单"],
    }
    amount = _payment_decision_amount(payment_decision)
    if amount:
        normalized["amount"] = amount
    tools = [item for item in required_tools if str(item.get("name") or "") != "create_work_order"]
    return normalized, tools or [{"name": "no_tool", "purpose": "Reused matching active work order"}]


def _matching_active_order_for_payment(
    state: AgentState,
    *,
    store_id: str,
    amount: int,
) -> dict[str, Any]:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = [order for order in context.get("orders") or [] if isinstance(order, dict)]
    current_orders = [order for order in orders if order.get("is_current_order")]
    source_orders = current_orders or orders
    candidates: list[dict[str, Any]] = []
    for order in source_orders:
        status = str(order.get("status") or "").strip().lower()
        deposit_state = str(order.get("deposit_state") or normalize_prepay_facts(order).get("deposit_state") or "")
        # Some platform order payloads omit a lifecycle status but still expose the
        # authoritative required_unpaid payment state. Explicit terminal statuses
        # remain ineligible; absence of status must not force a duplicate work order.
        if status and status not in {"1", "pending", "waiting_schedule", "scheduled"}:
            continue
        order_id = str(order.get("id") or order.get("order_id") or "").strip()
        if not order_id:
            continue
        order_store_id = str(order.get("store_id") or "").strip()
        if store_id and order_store_id != store_id:
            continue
        required_amount = _numeric_payment_amount(order.get("prepay_required") or order.get("fee_required"))
        if amount and required_amount and required_amount != amount:
            continue
        if deposit_state != "required_unpaid":
            continue
        candidates.append(order)
    if store_id:
        return candidates[0] if candidates else {}
    return candidates[0] if len(candidates) == 1 else {}


def _payment_decision_amount(payment_decision: dict[str, Any]) -> int:
    return _numeric_payment_amount(payment_decision.get("amount"))


def _numeric_payment_amount(value: Any) -> int:
    try:
        amount = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return amount if amount in {10, 20, 30, 40} else 0


def _has_paid_order_id(state: AgentState) -> bool:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    for order in context.get("orders") or []:
        if not isinstance(order, dict):
            continue
        if str(order.get("deposit_state") or "") == "paid_by_order" and str(order.get("id") or order.get("order_id") or "").strip():
            return True
    return False


def _has_registration_field(state: AgentState, field: str) -> bool:
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    value = str(basic.get(field) or "").strip()
    if field == "phone":
        return len(re.sub(r"\D", "", value)) == 11
    return bool(value)


def _payment_decision_action_from_legacy(*, payment_state: str, payment_action: str) -> str:
    if payment_state == "customer_claimed_paid":
        return "after_paid_next_step"
    if payment_action == "send_now":
        return "resend" if payment_state in {"resend_requested", "payment_failed"} else "send_now"
    if payment_action == "manual_transfer":
        return "manual_transfer"
    if payment_action == "explain_existing":
        return "explain"
    if payment_action == "confirm_next_step":
        return "after_paid_next_step" if payment_state == "customer_claimed_paid" else "none"
    if payment_action in {"none", "offer_resend"}:
        return "none"
    return "none"


def _payment_fields_from_decision(
    *,
    payment_decision: dict[str, Any],
    payment_state: str,
    payment_action: str,
) -> tuple[str, str]:
    action = str(payment_decision.get("action") or "")
    if action == "send_now":
        return ("needs_payment" if payment_state == "unknown" else payment_state, "send_now")
    if action == "resend":
        return ("resend_requested" if payment_state == "unknown" else payment_state, "send_now")
    if action == "after_paid_next_step":
        return ("customer_claimed_paid", "confirm_next_step")
    if action == "explain":
        return (payment_state if payment_state != "unknown" else "link_sent", "explain_existing")
    if action == "manual_transfer":
        return (payment_state if payment_state != "unknown" else "needs_payment", "manual_transfer")
    if action == "ask_party_size":
        return (payment_state, "none")
    if action == "none":
        return (payment_state, payment_action)
    return payment_state, payment_action


def _coerce_party_size(value: Any) -> int | None:
    try:
        participants = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return None
    if participants < 1:
        return None
    return participants


def _coerce_payment_amount(value: Any) -> int | None:
    try:
        amount = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return None
    if amount not in {10, 20, 30, 40}:
        return None
    return amount


def _normalize_payment_collection_messages_for_decision(
    messages: list[dict[str, Any]],
    *,
    state: AgentState,
    payment_decision: dict[str, Any],
) -> list[dict[str, Any]]:
    action = str(payment_decision.get("action") or "")
    if action not in {"send_now", "resend"}:
        return messages
    amount = _coerce_payment_amount(payment_decision.get("amount"))
    if not amount:
        return messages
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") == "payment_collection":
            output.append(
                {
                    **item,
                    "content": payment_collection_content({"amount": amount}, state=state, messages=messages),
                }
            )
            continue
        output.append(item)
    return _renumber_reply_messages(output)


def _with_payment_decision_action(
    payment_decision: dict[str, Any],
    action: str,
    *,
    source: str,
    confidence: str,
    basis: str,
) -> dict[str, Any]:
    output = dict(payment_decision) if isinstance(payment_decision, dict) else {}
    output["action"] = _normalize_enum(action, ALLOWED_PAYMENT_DECISION_ACTIONS, "none")
    if output["action"] == "manual_transfer":
        output["method"] = "transfer"
    elif output["action"] in {"send_now", "resend"}:
        output["method"] = "mini_program"
    else:
        output["method"] = "none"
    output["source"] = source
    output["confidence"] = _normalize_enum(confidence, ALLOWED_PAYMENT_DECISION_CONFIDENCE, "high")
    basis_list = _clean_str_list(output.get("basis") if isinstance(output.get("basis"), list) else [])
    if basis:
        basis_list.append(basis)
    if basis_list:
        output["basis"] = basis_list[:5]
    if output["action"] not in {"send_now", "resend"}:
        output.pop("amount", None)
        output.pop("party_size", None)
    return output


def _direct_reply_message_violations(*, decision: str, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    if decision == "direct_reply" and not messages:
        return [
            {
                "missing": "direct_reply_messages",
                "note": "decision=direct_reply must include at least one customer-visible reply_messages item. Rewrite with a short direct answer, or switch to need_tools/no_reply when appropriate.",
            }
        ]
    return []


def _has_complete_customer_visible_reply(messages: list[dict[str, Any]]) -> bool:
    visible = [
        item
        for item in messages
        if isinstance(item, dict)
        and str(item.get("type") or "text") in {"text", "image", "video", "store_address", "payment_collection"}
    ]
    if not visible:
        return False
    if any(str(item.get("type") or "text") != "text" for item in visible):
        return True
    generic_placeholders = {
        "稍等一下哈",
        "稍等哈",
        "稍等一下",
        "我在继续帮您处理",
        "我在，继续帮您处理",
        "继续帮您处理",
    }
    texts = {
        _compact_text(_message_text(item.get("content")))
        for item in visible
        if _compact_text(_message_text(item.get("content")))
    }
    return bool(texts and not texts.issubset({_compact_text(item) for item in generic_placeholders}))


def _need_tools_without_tool_violations(
    *,
    decision: str,
    required_tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "need_tools":
        return []
    if any(
        str(item.get("name") or "").strip() not in {"", "no_tool"}
        for item in required_tools
        if isinstance(item, dict)
    ):
        return []
    return [
        {
            "task_type": "tool_structure",
            "subtype": "need_tools",
            "missing": "need_tools_requires_executable_tool",
            "note": (
                "decision=need_tools must include at least one executable tool_call. "
                "Repair by adding the required tool, or switch to a complete direct_reply."
            ),
        }
    ]


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
    has_store_card = _has_store_address_message(messages)
    scope_backed_store_card = has_store_card and (
        _store_address_messages_are_requested_district_backed(messages, state)
        or _store_address_messages_are_scope_backed(messages, state)
    )
    if scope_backed_store_card and not _current_turn_supports_store_card(state, current_text):
        return [
            {
                "task_type": "reply_fact_consistency",
                "subtype": "stale_store_card",
                "missing": "store_card_requires_current_turn_support",
                "note": (
                    "The proposed store card is backed only by earlier conversation or store scope facts, while the current "
                    "customer message does not provide a location, ask for store details, or name a store. Remove the repeated "
                    "store_address and answer the current message. Do not start a new store lookup unless the current turn needs it."
                ),
            }
        ]
    if has_store_card and _store_address_messages_are_requested_district_backed(messages, state):
        return []
    if (
        has_store_card
        and _store_address_messages_are_scope_backed(messages, state)
        and not _asserts_store_address_detail(text)
    ):
        return []
    if not (
        _has_store_address_message(messages)
        or _direct_text_requires_store_detail_tool(text)
        or _current_message_requests_store_detail(current_text)
    ):
        return []
    if (
        _current_message_requests_store_detail(current_text)
        and _direct_text_is_store_scope_clarification(text)
        and not _direct_text_requires_store_detail_tool(text)
    ):
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


def _current_turn_supports_store_card(state: AgentState, current_text: str) -> bool:
    if location_card_from_state(state):
        return True
    if _current_message_has_explicit_location_for_lookup(current_text, state):
        return True
    if _current_message_requests_store_detail(current_text):
        return True
    if _current_message_mentions_basic_location(state, current_text):
        return True
    current_store = _store_from_current_message(state)
    return bool(current_store and not current_store.get("ambiguous"))


def _current_message_mentions_basic_location(state: AgentState, current_text: str) -> bool:
    compact = _compact_text(current_text)
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    for key in ("province", "city", "current_city", "district", "area_or_landmark", "region"):
        value = _compact_text(basic.get(key))
        if not value:
            continue
        variants = {value}
        for suffix in ("自治区", "特别行政区", "自治州", "自治县", "新区", "省", "市", "区", "县", "镇", "乡", "村"):
            if value.endswith(suffix) and len(value) > len(suffix):
                variants.add(value[: -len(suffix)])
        if any(len(item) >= 2 and item in compact for item in variants):
            return True
    return False


def _enforce_declared_store_detail_lookup(
    *,
    decision: str,
    sub_rule_id: str,
    messages: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
    state: AgentState,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Require factual detail lookup after Planner has declared a store-detail scene."""
    if sub_rule_id not in {"S2_LOCATION_DETAIL", "S2_ADDRESS_PARKING_HOURS"}:
        return decision, messages, required_tools
    if _has_tool(required_tools, "customer_store_lookup"):
        return decision, messages, required_tools
    if decision != "direct_reply":
        return decision, messages, required_tools

    reply_text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    query = (
        str(_store_from_current_message(state).get("store_name") or "").strip()
        or str(_request_store_from_state(state).get("store_name") or "").strip()
        or _generic_store_contextual_anchor_name(state)
        or _recent_store_name_from_context(state)
        or _store_name_from_reply_messages(messages, state)
    )
    if not query and _direct_text_requires_store_detail_tool(reply_text):
        query = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not query:
        return decision, messages, required_tools

    tools = [tool for tool in required_tools if str(tool.get("name") or "") != "no_tool"]
    tools.append(
        {
            "name": "customer_store_lookup",
            "purpose": "detail",
            "query": query,
        }
    )
    return "need_tools", [], _dedupe_tools(tools)


def _store_name_from_reply_messages(messages: list[dict[str, Any]], state: AgentState) -> str:
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    matched = [name for name in _known_store_names_for_state(state) if name and name in text]
    matched = _without_subsumed_store_names(_dedupe_names(matched))
    return matched[0] if len(matched) == 1 else ""


def _enforce_location_card_store_lookup(
    *,
    decision: str,
    messages: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
    state: AgentState,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """A structured location card is factual input, not an authoritative store match."""
    card = location_card_from_state(state)
    if not card or _has_tool(required_tools, "customer_store_lookup") or _fact_envelope_store_ids(state):
        return decision, messages, required_tools
    query = str(card.get("address") or card.get("title") or card.get("coordinates") or "").strip()
    if not query:
        return decision, messages, required_tools
    tools = [tool for tool in required_tools if str(tool.get("name") or "") != "no_tool"]
    tools.append(
        {
            "name": "customer_store_lookup",
            "purpose": "nearby_candidates",
            "query": query,
        }
    )
    return "need_tools", [], _dedupe_tools(tools)


def _enforce_sop_gate_active_task(
    *,
    decision: str,
    messages: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
    state: AgentState,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Honor the preceding model's structured task without re-deciding customer semantics in Python."""
    gate = state.get("sop_gate_decision") if isinstance(state.get("sop_gate_decision"), dict) else {}
    task = gate.get("active_task") if isinstance(gate.get("active_task"), dict) else {}
    if str(task.get("status") or "") != "pending":
        return decision, messages, required_tools
    required_tool = str(task.get("required_tool") or "").strip()
    query = str(task.get("query") or "").strip()
    if required_tool != "customer_store_lookup" or not query:
        return decision, messages, required_tools
    if _has_tool(required_tools, required_tool):
        return "need_tools", [], required_tools
    tools = [tool for tool in required_tools if str(tool.get("name") or "") != "no_tool"]
    tools.append(
        {
            "name": "customer_store_lookup",
            "purpose": "existence",
            "query": query,
            "location_specificity": "confirmed_region",
            "confirmed_by_customer": True,
        }
    )
    return "need_tools", [], _dedupe_tools(tools)


def _enforce_explicit_location_store_lookup(
    *,
    decision: str,
    messages: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
    state: AgentState,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the store-fact requirement whenever the model omits a required lookup."""
    violations = _explicit_location_store_lookup_violations(
        decision=decision,
        messages=messages,
        required_tools=required_tools,
        state=state,
    )
    if not violations:
        return decision, messages, required_tools
    raw_query = str(state.get("normalized_content") or state.get("content") or "").strip()
    query = _strip_location_answer_prefixes(_clean_scoped_location_query(raw_query)) or raw_query
    if not query:
        return decision, messages, required_tools
    tools = [tool for tool in required_tools if str(tool.get("name") or "") != "no_tool"]
    tools.append(
        {
            "name": "customer_store_lookup",
            "purpose": "existence",
            "query": query,
        }
    )
    return "need_tools", [], _dedupe_tools(tools)


def _explicit_location_store_lookup_violations(
    *,
    decision: str,
    messages: list[dict[str, Any]],
    required_tools: list[dict[str, Any]],
    state: AgentState,
) -> list[dict[str, str]]:
    if decision != "direct_reply" or _has_tool(required_tools, "customer_store_lookup"):
        return []
    current_text = str(state.get("normalized_content") or state.get("content") or "")
    if not _current_message_has_explicit_location_for_lookup(current_text, state):
        return []
    if _current_message_is_province_only_location(current_text):
        return []
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    if _direct_text_is_store_scope_clarification(text) and _location_question_can_clarify_without_lookup(current_text, state):
        return []
    if _has_store_address_message(messages) and _store_address_messages_are_requested_district_backed(messages, state):
        return []
    message_ids = {
        _store_address_id(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "") == "store_address"
    }
    message_ids.discard("")
    expected_ids = _snapshot_store_ids_for_current_location(current_text)
    if expected_ids and message_ids == expected_ids:
        return []
    if _fact_envelope_store_ids(state):
        return []
    return [
        {
            "task_type": "tool_required",
            "subtype": "customer_store_lookup",
            "missing": "store_location_lookup_required_before_direct_reply",
            "note": (
                "The customer provided a concrete city, district, county, town, village, or landmark. Do not direct_reply from "
                "profile or partial scope facts. Use need_tools + customer_store_lookup(query=current customer location). "
                "If the tool reports ambiguity or no exact local store, the reply model can ask for a higher-level city/location "
                "or recommend verified fallback candidates from tool facts."
            ),
        }
    ]


def _current_message_has_explicit_location_for_lookup(text: str, state: AgentState) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    if _looks_like_specific_region(compact):
        return True
    if _current_message_has_bare_location_for_store_lookup(compact, state):
        return True
    return bool(_matching_current_message_region_tokens(compact, state))


def _current_message_is_province_only_location(text: str) -> bool:
    compact = _strip_store_question_words(_compact_text(text))
    return bool(compact.endswith("省") and not re.search(r"(市|区|县|镇|乡|村|街道|自治州|地区|盟|旗)", compact[:-1]))


def _location_question_can_clarify_without_lookup(text: str, state: AgentState) -> bool:
    compact = _strip_store_question_words(_compact_text(text))
    if max(_snapshot_city_store_count_for_query(compact), _state_city_store_count_for_query(compact, state)) > 3:
        return True
    return False


def _strip_store_question_words(text: str) -> str:
    return re.sub(
        r"(你们|我们|门店|店|地址|位置|定位|导航|发我|发一下|给我|在哪|在哪里|哪家|哪个|"
        r"有吗|有没有|附近|最近|帮我|帮您|查一下|看一下|看下|这边|那里|哪里|吗|呢|呀|的|有)",
        "",
        text,
    )


def _current_message_has_bare_location_for_store_lookup(text: str, state: AgentState) -> bool:
    stripped = _strip_location_answer_prefixes(_strip_store_question_words(text))
    if not _looks_like_bare_location_token(stripped):
        return False
    return _current_message_asks_store_with_bare_location(text) or _recent_assistant_asked_for_store_location(state)


def _current_message_asks_store_with_bare_location(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    return any(
        term in compact
        for term in (
            "门店",
            "店在哪里",
            "店在哪",
            "有店",
            "有门店",
            "附近",
            "最近",
            "地址",
            "位置",
            "定位",
        )
    )


def _recent_assistant_asked_for_store_location(state: AgentState) -> bool:
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in reversed(history[-6:]):
        text = _history_item_text(item)
        if not text:
            continue
        compact = _compact_text(text)
        is_assistant = compact.startswith(("小贝:", "客服:", "员工:", "助手:", "ai:"))
        if isinstance(item, dict):
            role = _compact_text(item.get("role") or item.get("sender") or item.get("speaker") or "")
            if role in {"assistant", "ai", "staff", "employee", "agent"}:
                is_assistant = True
        if not is_assistant:
            continue
        asks_scope = any(term in compact for term in ("哪个城市", "哪个区", "城市哪个区", "城市或区域", "发个定位", "发定位", "附近方便", "最近门店"))
        mentions_store = any(term in compact for term in ("门店", "店", "地址", "位置", "定位", "附近"))
        if asks_scope and mentions_store:
            return True
    return False


def _strip_location_answer_prefixes(text: str) -> str:
    output = re.sub(r"[，,。.!！?？、：:；;（）()【】\[\]\"']", "", text)
    output = re.sub(r"^(我在|人在|就在|在|我是|这边是|这边|附近是|离|靠近|到)", "", output)
    output = re.sub(r"(发个|发一下|发我|给我|一下|一个|这边|附近)$", "", output)
    return output.strip()


def _looks_like_bare_location_token(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{2,16}", compact):
        return False
    if any(term in compact for term in ("我", "你", "您", "他", "她", "这家", "那家", "这个", "那个", "昨天", "今天", "明天", "没去", "把")):
        return False
    non_location_terms = {
        "你好",
        "您好",
        "好的",
        "可以",
        "行",
        "嗯",
        "哦",
        "谢谢",
        "多少钱",
        "价格",
        "贵不贵",
        "怎么付费",
        "怎么预约",
        "没时间",
        "不方便",
        "不用",
        "不要了",
        "考虑",
        "效果",
        "反弹",
        "痘印",
        "痘坑",
        "发个",
        "发一下",
        "发我",
        "给我",
        "一下",
        "一个",
        "这家",
        "那家",
    }
    return compact not in non_location_terms


def _snapshot_store_ids_for_current_location(text: str) -> set[str]:
    city = _snapshot_city_token_for_query(text)
    if city:
        return {
            str(store.get("store_id") or store.get("id") or "").strip()
            for store in _snapshot_store_values_for_guard()
            if _region_name_matches(store.get("city"), city)
            and str(store.get("store_id") or store.get("id") or "").strip()
        }
    return set()


def _snapshot_city_store_count_for_query(text: str) -> int:
    city = _snapshot_city_token_for_query(text)
    if not city:
        return 0
    return sum(1 for store in _snapshot_store_values_for_guard() if _region_name_matches(store.get("city"), city))


def _state_city_store_count_for_query(text: str, state: AgentState) -> int:
    city = _state_city_token_for_query(text, state)
    if not city:
        return 0
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return sum(1 for store in stores if isinstance(store, dict) and _region_name_matches(store.get("city"), city))


def _snapshot_city_token_for_query(text: str) -> str:
    compact = _compact_text(text)
    best = ""
    for store in _snapshot_store_values_for_guard():
        city = str(store.get("city") or "").strip()
        if not city:
            continue
        for token in _region_token_variants(city):
            token_compact = _compact_text(token)
            if len(token_compact) >= 2 and token_compact in compact and len(token_compact) > len(_compact_text(best)):
                best = city
    return best


def _state_city_token_for_query(text: str, state: AgentState) -> str:
    compact = _compact_text(text)
    best = ""
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    for store in stores:
        if not isinstance(store, dict):
            continue
        city = str(store.get("city") or "").strip()
        if not city:
            continue
        for token in _region_token_variants(city):
            token_compact = _compact_text(token)
            if len(token_compact) >= 2 and token_compact in compact and len(token_compact) > len(_compact_text(best)):
                best = city
    return best


def _region_name_matches(value: Any, expected: str) -> bool:
    left = {_compact_text(token) for token in _region_token_variants(value) if _compact_text(token)}
    right = {_compact_text(token) for token in _region_token_variants(expected) if _compact_text(token)}
    return bool(left & right)


def _store_address_messages_are_requested_district_backed(messages: list[dict[str, Any]], state: AgentState) -> bool:
    message_ids = {
        _store_address_id(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "") == "store_address"
    }
    message_ids.discard("")
    if not message_ids:
        return False
    summary = state.get("store_scope_summary") if isinstance(state.get("store_scope_summary"), dict) else {}
    regions = summary.get("relevant_regions") if isinstance(summary.get("relevant_regions"), list) else []
    for region in regions:
        if not isinstance(region, dict):
            continue
        expected = {
            str(store.get("store_id") or store.get("id") or "").strip()
            for store in region.get("requested_district_stores") or []
            if isinstance(store, dict) and str(store.get("store_id") or store.get("id") or "").strip()
        }
        if message_ids.issubset(expected):
            return True
    return False


def _store_address_messages_are_scope_backed(messages: list[dict[str, Any]], state: AgentState) -> bool:
    message_ids = {
        _store_address_id(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "") == "store_address"
    }
    message_ids.discard("")
    fact_store_ids = _fact_envelope_store_ids(state)
    if message_ids and message_ids.issubset(fact_store_ids):
        return True
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    if not message_ids or not message_ids.issubset(store_scope_ids(knowledge)):
        return False
    stores = [item for item in knowledge.get("stores") or [] if isinstance(item, dict)]
    records = {
        str(item.get("store_id") or item.get("id") or "").strip(): item
        for item in stores
        if str(item.get("store_id") or item.get("id") or "").strip()
    }
    evidence_text = " ".join(
        [
            str(state.get("normalized_content") or state.get("content") or ""),
            *[_history_item_text(item) for item in (state.get("conversation_history") or [])[-6:]],
        ]
    )
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    preferred_store_id = str(basic.get("preferred_store_id") or basic.get("store_id") or "").strip()
    location_evidence = " ".join(
        str(value or "").strip()
        for value in (
            evidence_text,
            basic.get("province"),
            basic.get("city") or basic.get("current_city"),
            basic.get("district") or basic.get("area_or_landmark") or basic.get("region"),
            basic.get("preferred_store_name") or basic.get("store_name"),
        )
        if str(value or "").strip()
    )
    for store_id in message_ids:
        record = records.get(store_id) or {}
        if store_id == preferred_store_id:
            continue
        if not any(
            value and _normalize_store_name_for_match(value) in _normalize_store_name_for_match(location_evidence)
            for value in (
                str(record.get("store_name") or record.get("name") or "").strip(),
                str(record.get("province") or "").strip(),
                str(record.get("city") or "").strip(),
                str(record.get("district") or "").strip(),
            )
        ):
            return False
    return True


def _fact_envelope_store_ids(state: AgentState) -> set[str]:
    """Return store IDs backed by authoritative facts from the current tool round."""
    envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = envelope.get("structured_facts") if isinstance(envelope.get("structured_facts"), dict) else {}
    sources: list[dict[str, Any]] = []
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        sources.append(recommended)
    sources.extend(item for item in structured.get("store_facts") or [] if isinstance(item, dict))
    return {
        str(item.get("store_id") or item.get("id") or "").strip()
        for item in sources
        if str(item.get("store_id") or item.get("id") or "").strip()
    }


def _distance_tool_violations(required_tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    if _has_tool(required_tools, "distance_calculate"):
        return []
    for tool in required_tools:
        if not isinstance(tool, dict) or str(tool.get("name") or "") != "customer_store_lookup":
            continue
        if str(tool.get("purpose") or "").strip() == "nearby_candidates":
            if str(tool.get("location_specificity") or "").strip() in {
                "generic_landmark_without_region",
                "ambiguous_place_without_region",
            }:
                return []
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
            "再发地址",
            "重发地址",
            "发您导航",
            "发你导航",
            "再发导航",
            "重发导航",
            "已发地址",
            "发过地址",
            "地址发您",
            "地址发你",
        )
    )


def _direct_text_is_store_scope_clarification(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    ask_terms = ("哪家", "哪个店", "哪边", "哪个门店", "哪一个门店", "哪座城市", "哪个城市", "哪个区域", "哪个区")
    return any(term in compact for term in ask_terms)


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
    current_known_store = state.get("current_known_store") if isinstance(state.get("current_known_store"), dict) else {}
    for key in ("store_id", "id"):
        value = str(current_known_store.get(key) or "").strip()
        if value and value != "0":
            ids.add(value)
    for order in customer_context.get("orders") or []:
        if not isinstance(order, dict):
            continue
        value = str(order.get("store_id") or "").strip()
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
            "message or traceable recent customer conversation. If only a nationwide ambiguous landmark is known, do not "
            "call store/distance tools; ask the customer which city or district first."
        ),
    }


def _generic_store_question_uses_history_query(query: str, state: AgentState) -> bool:
    current_text = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not _is_generic_store_location_question_without_current_scope(current_text, state):
        return False
    if _store_lookup_query_comes_from_current_message(query, state):
        return False
    if _store_lookup_query_comes_from_recent_customer_location(query, state):
        return False
    if _generic_store_question_can_use_contextual_anchor(state, query=query):
        return False
    query_compact = _compact_text(query)
    current_compact = _compact_text(current_text)
    return bool(query_compact and query_compact != current_compact)


def _store_lookup_query_comes_from_recent_customer_location(query: str, state: AgentState) -> bool:
    """Allow composed location facts from recent customer messages, not soft profile data."""

    query_compact = _compact_text(query)
    if not query_compact:
        return False
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    matched_fragments: list[str] = []
    for item in history[-20:]:
        role, text = _history_item_role_text(item)
        if role != "customer":
            continue
        compact = _compact_text(_strip_location_answer_prefixes(_strip_store_question_words(text)))
        if len(compact) < 2 or compact not in query_compact:
            continue
        matched_fragments.append(compact)
    current = _compact_text(
        _strip_location_answer_prefixes(
            _strip_store_question_words(str(state.get("normalized_content") or state.get("content") or ""))
        )
    )
    if len(current) >= 2 and current in query_compact:
        matched_fragments.append(current)
    return len(set(matched_fragments)) >= 2


def _store_lookup_reopens_stale_location_context(query: str, state: AgentState) -> bool:
    """Reject a historical location lookup that has no current-turn factual trigger."""

    current_text = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not current_text or _store_lookup_query_comes_from_current_message(query, state):
        return False
    if location_card_from_state(state):
        return False
    if _raw_text_mentions_store_location_request(current_text):
        return False
    if _current_message_has_explicit_location_for_lookup(current_text, state):
        return False

    query_compact = _compact_text(query)
    if not query_compact:
        return False
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in history[-20:]:
        role, text = _history_item_role_text(item)
        if role != "customer":
            continue
        fragment = _compact_text(_strip_location_answer_prefixes(_strip_store_question_words(text)))
        if len(fragment) >= 2 and fragment in query_compact:
            return True
    return False


def _generic_store_question_can_use_contextual_anchor(state: AgentState, *, query: str = "") -> bool:
    current_text = _compact_text(state.get("normalized_content") or state.get("content") or "")
    if not current_text:
        return False
    if not _is_generic_store_location_question_without_current_scope(current_text, state):
        return False
    anchor_name = _generic_store_contextual_anchor_name(state)
    if not anchor_name:
        return False
    if not _store_name_query_matches(_compact_text(anchor_name), _compact_text(query)):
        return False
    return _query_matches_scope_store_name(query, state)


def _generic_store_contextual_anchor_name(state: AgentState) -> str:
    turn_context = state.get("current_turn_context") if isinstance(state.get("current_turn_context"), dict) else {}
    if not turn_context:
        turn_context = _turn_context_for_guard(state)
    for key in ("current_store_anchor", "confirmed_store"):
        value = turn_context.get(key)
        if not isinstance(value, dict):
            continue
        if value.get("ambiguous"):
            return ""
        source = str(value.get("source") or "").strip()
        if source in {"customer_profile", "profile", "preferred_store"}:
            continue
        name = str(value.get("store_name") or value.get("name") or "").strip()
        if name and _query_matches_scope_store_name(name, state):
            return name
    store_anchor = current_store_anchor_from_state(
        state,
        current_known_store=None,
        allow_profile=False,
        prefer_recent=True,
    )
    if isinstance(store_anchor, dict):
        if store_anchor.get("ambiguous"):
            return ""
        name = str(store_anchor.get("store_name") or store_anchor.get("name") or "").strip()
        if name and _query_matches_scope_store_name(name, state):
            return name
    name = _unique_store_name_from_recent_text(state)
    if name and _query_matches_scope_store_name(name, state):
        return name
    return ""


def _unique_store_name_from_recent_text(state: AgentState) -> str:
    text = _state_text_context(state)
    if not text:
        return ""
    names = _without_subsumed_store_names(
        _dedupe_names([name for name in [*_known_store_names_for_state(state), *_snapshot_store_names()] if name and name in text])
    )
    if len(names) == 1:
        return names[0]
    return ""


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
    return not _location_query_has_current_message_scope(text, state)


def _location_query_has_current_message_scope(value: str, state: AgentState) -> bool:
    text = _compact_text(value)
    if not text:
        return False
    if _matching_current_message_region_tokens(text, state):
        return True
    return _looks_like_specific_region(text)


def _location_query_has_scope_region(value: str, state: AgentState) -> bool:
    text = _compact_text(value)
    if not text:
        return False
    for token in _scope_region_tokens(state):
        compact = _compact_text(token)
        if compact and compact in text:
            return True
    for token in _snapshot_region_tokens():
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
    return _compact_text(value).replace("市", "").replace("百星", "")


def _snapshot_store_names() -> list[str]:
    global _STORE_SNAPSHOT_NAME_CACHE
    if _STORE_SNAPSHOT_NAME_CACHE is not None:
        return _STORE_SNAPSHOT_NAME_CACHE
    names = [
        str(store.get("store_name") or store.get("name") or "").strip()
        for store in _snapshot_store_values_for_guard()
        if str(store.get("store_name") or store.get("name") or "").strip()
    ]
    _STORE_SNAPSHOT_NAME_CACHE = list(dict.fromkeys(names))
    return _STORE_SNAPSHOT_NAME_CACHE


def _snapshot_region_tokens() -> set[str]:
    global _STORE_SNAPSHOT_REGION_TOKEN_CACHE
    if _STORE_SNAPSHOT_REGION_TOKEN_CACHE is not None:
        return _STORE_SNAPSHOT_REGION_TOKEN_CACHE
    tokens: set[str] = set()
    for store in _snapshot_store_values_for_guard():
        for key in ("province", "city", "district"):
            raw = str(store.get(key) or "").strip()
            if not raw:
                continue
            tokens.add(raw)
            for suffix in ("省", "市", "区", "县", "旗", "自治州", "自治县", "新区"):
                if raw.endswith(suffix) and len(raw) > len(suffix):
                    tokens.add(raw[: -len(suffix)])
    _STORE_SNAPSHOT_REGION_TOKEN_CACHE = {token for token in tokens if len(_compact_text(token)) >= 2}
    return _STORE_SNAPSHOT_REGION_TOKEN_CACHE


def _distance_origin_is_broad_region_only(value: str, state: AgentState) -> bool:
    text = _compact_text(value)
    if not text or _query_matches_scope_store_name(value, state):
        return False
    broad_tokens = set(_snapshot_broad_region_tokens())
    for mapping in (
        state,
        state.get("request_context") if isinstance(state.get("request_context"), dict) else {},
        state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {},
        state.get("current_known_store") if isinstance(state.get("current_known_store"), dict) else {},
    ):
        if not isinstance(mapping, dict):
            continue
        for key in ("province", "city", "current_city"):
            broad_tokens.update(_region_token_variants(mapping.get(key)))
    return text in {_compact_text(token) for token in broad_tokens if token}


def _snapshot_broad_region_tokens() -> set[str]:
    global _STORE_SNAPSHOT_BROAD_REGION_TOKEN_CACHE
    if _STORE_SNAPSHOT_BROAD_REGION_TOKEN_CACHE is not None:
        return _STORE_SNAPSHOT_BROAD_REGION_TOKEN_CACHE
    tokens: set[str] = set()
    for store in _snapshot_store_values_for_guard():
        for key in ("province", "city"):
            tokens.update(_region_token_variants(store.get(key)))
    _STORE_SNAPSHOT_BROAD_REGION_TOKEN_CACHE = {token for token in tokens if len(_compact_text(token)) >= 2}
    return _STORE_SNAPSHOT_BROAD_REGION_TOKEN_CACHE


def _snapshot_store_values_for_guard() -> list[dict[str, Any]]:
    path = Path("data/store_snapshot.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [dict(item) for item in KNOWN_STORE_FACTS]
    stores_by_id = data.get("stores_by_id") if isinstance(data, dict) else {}
    if isinstance(stores_by_id, dict) and stores_by_id:
        return [dict(store) for store in stores_by_id.values() if isinstance(store, dict)]
    return [dict(item) for item in KNOWN_STORE_FACTS]


def _region_token_variants(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    output = {raw}
    for suffix in ("省", "市", "自治区", "特别行政区"):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            output.add(raw[: -len(suffix)])
    return output


def _matching_current_message_region_tokens(value: str, state: AgentState) -> list[str]:
    text = _compact_text(value)
    if not text:
        return []
    candidates = [token for token in (*_scope_region_tokens(state), *_snapshot_region_tokens()) if _compact_text(token) in text]
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for token in candidates:
        compact = _compact_text(token)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        ranked.append((text.find(compact), -len(compact), token))
    ranked.sort()
    output: list[str] = []
    for _, _, token in ranked:
        compact = _compact_text(token)
        if any(compact != _compact_text(other) and compact in _compact_text(other) for other in output):
            continue
        output.append(token)
    return output


def _looks_like_specific_region(text: str) -> bool:
    return bool(
        re.search(
            r"[\u4e00-\u9fff]{2,}"
            r"(省|市|区|县|镇|乡|旗|州|盟|新区|机场|高铁站|火车站|地铁站|车站|"
            r"大厦|广场|商场|中心|购物中心|商务中心|写字楼|医院|学校|公园|园区|"
            r"小区|社区|村|路|街|街道)",
            text,
        )
    )


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


def _history_item_role_text(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        text = _history_item_text(item)
        role = str(item.get("role") or item.get("sender_type") or item.get("direction") or "").lower()
        if role in {"user", "customer", "external", "inbound"}:
            return "customer", text
        if role in {"assistant", "staff", "employee", "ai", "outbound"}:
            return "assistant", text
        return "", text
    text = str(item or "").strip()
    lowered = text.lower()
    for prefix in ("用户:", "客户:", "user:", "customer:"):
        if lowered.startswith(prefix.lower()):
            return "customer", text[len(prefix) :].strip()
    for prefix in ("小贝:", "助手:", "员工:", "assistant:", "staff:"):
        if lowered.startswith(prefix.lower()):
            return "assistant", text[len(prefix) :].strip()
    return "", text


def _payment_consistency_violations(
    *,
    state: AgentState,
    decision: str,
    conversion_stage: str,
    next_step: str,
    payment_state: str,
    payment_action: str,
    payment_decision: dict[str, Any],
    sales_progression: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision == "no_reply":
        return []
    decision_action = str(payment_decision.get("action") or "")
    if _has_paid_deposit_context(state, payment_state=payment_state):
        if _has_payment_collection(messages):
            return [
                {
                    "task_type": "reply_schema_consistency",
                    "subtype": "payment_collection",
                    "missing": "payment_collection_blocked_by_paid_deposit_context",
                    "note": "Customer has stated the deposit is already paid. Do not send payment_collection again; continue with appointment/store/time follow-up.",
                }
            ]
        return []
    progression_action = str(sales_progression.get("action") or "")
    if progression_action == "send_payment_card" and decision_action not in {"send_now", "resend"}:
        return [
            {
                "task_type": "reply_schema_consistency",
                "subtype": "payment_collection",
                "missing": "payment_progression_decision_mismatch",
                "note": (
                    "sales_progression.action=send_payment_card requires payment_decision.action=send_now/resend. "
                    "Use payment_decision as the single payment authority: either send the card, or change the "
                    "sales progression to an explain-only action."
                ),
            }
        ]
    payment_context = payment_collection_context(state={**state, "payment_decision": payment_decision}, messages=messages)
    if _payment_send_requires_activity_intro(
        conversion_stage=conversion_stage,
        next_step=next_step,
        payment_action=payment_action,
        payment_decision=payment_decision,
        messages=messages,
    ) and not activity_intro_completed_for_payment(state):
        return [
            {
                "task_type": "reply_schema_consistency",
                "subtype": "payment_collection",
                "missing": "payment_collection_requires_activity_intro",
                "note": (
                    "Activity price intro is not completed yet. First explain the activity price and deposit policy; "
                    "do not send or promise payment_collection in this turn."
                ),
            }
        ]
    if (conversion_stage == "deposit_push" or next_step == "send_deposit" or _text_mentions_payment_entry(messages)) and payment_context["over_limit"]:
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
    if decision_action in {"none", ""} and (
        conversion_stage == "deposit_push" or next_step == "send_deposit"
    ):
        return [
            {
                "task_type": "reply_schema_consistency",
                "subtype": "payment_collection",
                "missing": "payment_decision_required",
                "note": (
                    "This turn is trying to send or promise a payment entry, but payment_decision.action is not send_now/resend. "
                    "If the customer should pay now, set payment_decision.action=send_now/resend with party_size and amount; "
                    "otherwise remove payment-entry wording and downgrade conversion_stage/next_step."
                ),
            }
        ]
    if decision_action in {"none", "explain", "manual_transfer", "after_paid_next_step", "ask_party_size"} or payment_action in {"none", "manual_transfer", "offer_resend", "explain_existing", "confirm_next_step"}:
        if _has_payment_collection(messages):
            return [
                {
                    "task_type": "reply_schema_consistency",
                    "subtype": "payment_collection",
                    "missing": "payment_collection_blocked_by_payment_action",
                    "note": (
                        "Planner payment_action says this turn should not send the payment entry. "
                        "Remove payment_collection or change payment_action to send_now if the customer clearly requested the entry."
                    ),
                }
            ]
        return []
    if _text_explains_previous_payment_entry(messages):
        return []
    if decision != "direct_reply":
        return []
    needs_payment = decision_action in {"send_now", "resend"} or payment_action == "send_now"
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
    if str(payment_decision.get("action") or "") not in {"send_now", "resend", "manual_transfer"} and (
        conversion_stage == "deposit_push" or next_step == "send_deposit"
    ):
        return [
            {
                "task_type": "reply_schema_consistency",
                "subtype": "payment_collection",
                "missing": "payment_decision_required",
                "note": (
                    "Deposit push/send_deposit must be backed by payment_decision.action=send_now or resend. "
                    "If the turn should not send a payment card, change conversion_stage/next_step and remove payment-entry wording."
                ),
            }
        ]
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


def _payment_send_requires_activity_intro(
    *,
    conversion_stage: str,
    next_step: str,
    payment_action: str,
    payment_decision: dict[str, Any],
    messages: list[dict[str, Any]],
) -> bool:
    decision_action = str(payment_decision.get("action") or "")
    return (
        decision_action in {"send_now", "resend"}
        or payment_action == "send_now"
        or conversion_stage == "deposit_push"
        or next_step == "send_deposit"
        or _has_payment_collection(messages)
        or _text_mentions_payment_entry(messages)
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


def _remove_advisory_health_professional_assist_tools(
    *,
    required_tools: list[dict[str, Any]],
    risk_hold: dict[str, Any] | None,
    explicit_risk_reason: str,
) -> tuple[list[dict[str, Any]], bool]:
    if explicit_risk_reason or is_hard_health_risk_hold(risk_hold) or not _is_advisory_health_risk_hold(risk_hold):
        return required_tools, False
    output: list[dict[str, Any]] = []
    removed = False
    for tool in required_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        reason = " ".join(
            str(value or "")
            for value in (
                tool.get("reason"),
                tool.get("purpose"),
                tool.get("query"),
            )
        )
        if name == "professional_assist" and _mentions_health_risk_text(reason):
            removed = True
            continue
        output.append(tool)
    if not removed:
        return required_tools, False
    if not any(isinstance(tool, dict) and str(tool.get("name") or "").strip() != "no_tool" for tool in output):
        output = [{"name": "no_tool", "purpose": "Advisory health history is not a current professional_assist request"}]
    return output, True


def _is_advisory_health_risk_hold(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    return str(value.get("risk_hold") or "") == "health_check_context" or str(value.get("severity") or "") == "advisory"


def _append_required_payment_collection(
    *,
    state: AgentState,
    decision: str,
    conversion_stage: str,
    next_step: str,
    payment_state: str,
    payment_action: str,
    payment_decision: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if decision != "direct_reply":
        return messages
    decision_action = str(payment_decision.get("action") or "")
    if is_hard_health_risk_hold(health_risk_hold(state)):
        return _remove_payment_collection_messages(messages)
    if _has_paid_deposit_context(state, payment_state=payment_state):
        return _remove_payment_collection_messages(messages)
    if decision_action in {"none", "explain", "manual_transfer", "after_paid_next_step", "ask_party_size"}:
        return _remove_payment_collection_messages(messages)
    if payment_action in {"none", "manual_transfer", "offer_resend", "explain_existing", "confirm_next_step"} and decision_action not in {"send_now", "resend"}:
        return _remove_payment_collection_messages(messages)
    if not activity_intro_completed_for_payment(state):
        return _remove_payment_collection_messages(messages)
    if not messages or _has_payment_collection(messages) or _text_explains_previous_payment_entry(messages):
        return messages
    if decision_action not in {"send_now", "resend"} and payment_action != "send_now":
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


def _has_paid_deposit_context(state: AgentState, *, payment_state: str = "unknown") -> bool:
    """Return only authoritative order or payment-screenshot success, never a text claim."""
    if _has_authoritative_paid_context(state):
        return True
    if payment_state in {"customer_claimed_paid", "resend_requested", "payment_failed", "needs_payment"}:
        return False
    return False


def _has_authoritative_paid_context(state: AgentState) -> bool:
    """Check structured current-order or successful screenshot evidence for payment."""
    return _authoritative_paid_context(state, _turn_context_for_guard(state))


def _has_current_unpaid_order(state: AgentState) -> bool:
    """Return whether the fresh current order explicitly remains unpaid."""
    return _current_unpaid_order(state)


def _postpaid_scheduling_tool_violations(
    *,
    state: AgentState,
    payment_decision: dict[str, Any],
    required_tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Block formal availability and scheduling tools in the paid information-confirmation flow."""
    return _structured_postpaid_scheduling_tool_violations(
        payment_decision=payment_decision,
        required_tools=required_tools,
        is_postpaid_registration_flow=(
            str(payment_decision.get("action") or "") == "after_paid_next_step"
        ),
    )


def _compact_text(text: str) -> str:
    return "".join(str(text or "").split())


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
            "入口还在",
            "入口还有效",
            "重发",
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


def _pending_lookup_reply_violations(
    *,
    decision: str,
    next_step: str,
    appointment_decision: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "direct_reply":
        return []
    if str(appointment_decision.get("action") or "") in {"ask_store", "ask_time"}:
        return []
    store_lookup_pending = (
        str(appointment_decision.get("action") or "") == "lookup_store" or next_step == "lookup_store"
    )
    if store_lookup_pending and not any(
        isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages
    ):
        return [
            {
                "task_type": "reply_fact_consistency",
                "subtype": "store_lookup_action",
                "missing": "store_lookup_action_requires_tool_or_store_card",
                "note": (
                    "A planner result that still requires store lookup cannot end as a direct reply without a verified store card. "
                    "Use need_tools + customer_store_lookup when store facts are missing, or include store_address from the "
                    "authoritative store facts and change the appointment action to the actual next step."
                ),
            }
        ]
    texts = [
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    ]
    pending_lookup = any(
        re.search(
            r"(?:帮|给|我先|这边)?(?:您|你)?(?:查一下|查询|核对一下|核对|找一下|看看|看一下|看下).{0,8}(?:档期|案例|参考)",
            _compact_text(text),
        )
        for text in texts
        if _compact_text(text)
    )
    if pending_lookup:
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
    state: AgentState,
    decision: str,
    appointment_decision: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if decision != "direct_reply":
        return []
    if (
        isinstance(appointment_decision, dict)
        and appointment_decision.get("commitment_level") == "confirmed"
        and not _has_confirmed_appointment_or_available_time_fact(state)
    ):
        return [
            {
                "task_type": "reply_fact_consistency",
                "subtype": "appointment_availability",
                "missing": "available_time_required_for_confirmed_appointment_decision",
                "note": (
                    "Planner appointment_decision.commitment_level=confirmed requires available_time, appointment_record, "
                    "or explicit request confirmed appointment facts. If store_id/date are known, switch to need_tools "
                    "and call available_time; if only a store candidate is known, lookup/confirm the store first; otherwise "
                    "use a tentative reply without promising the visit is confirmed."
                ),
            }
        ]
    text = " ".join(
        _message_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )
    compact = _compact_text(text)
    if not compact:
        return []
    if (
        _current_message_requests_appointment_availability(state)
        and not _direct_reply_asks_missing_appointment_scope(compact)
    ) or _direct_reply_claims_appointment_availability_or_hold(compact):
        return [
            {
                "task_type": "reply_fact_consistency",
                "subtype": "appointment_availability",
                "missing": "available_time_required_for_availability_claim",
                "note": (
                    "Direct replies must not claim a time can be booked, arranged, held, or reserved without available_time "
                    "or appointment facts. Ask for the missing store/time field, or call available_time when store_id/date are known."
                ),
            }
        ]
    return []


def _has_confirmed_appointment_or_available_time_fact(state: AgentState) -> bool:
    for source in (
        state,
        state.get("request_context") if isinstance(state.get("request_context"), dict) else {},
    ):
        if not isinstance(source, dict):
            continue
        if str(source.get("appointment_id") or "").strip():
            return True
        if str(source.get("confirmed_appointment_id") or "").strip():
            return True
        if str(source.get("appointment_time") or "").strip() and (
            str(source.get("confirmed_store_id") or source.get("store_id") or "").strip()
        ):
            return True
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    if str(appointment_cache.get("status") or "").strip() in {"confirmed", "booked", "scheduled", "已预约", "已确认"}:
        return True
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    appointment_facts = structured.get("appointment_facts") if isinstance(structured.get("appointment_facts"), list) else []
    for item in appointment_facts:
        if not isinstance(item, dict):
            continue
        fact_type = str(item.get("type") or "").strip()
        if fact_type in {"appointment_record", "appointment_confirmed", "appointment_created"}:
            return True
        if fact_type == "available_time" and (
            item.get("slots")
            or str(item.get("recommended_slot") or "").strip()
            or item.get("backup_slots")
            or item.get("target_time_available") is True
        ):
            return True
    return False


def _direct_reply_claims_appointment_availability_or_hold(compact: str) -> bool:
    # Customer-controlled timing is not a claim that a real store slot exists.
    if re.search(r"(?:到店)?(?:时间|日期).{0,8}(?:后面|之后).{0,8}按(?:您|你)方便", compact) or re.search(
        r"按(?:您|你)方便.{0,6}(?:定|安排|确认)",
        compact,
    ):
        concrete_slot = re.search(r"(?:今天|明天|后天|上午|下午|晚上|\d{1,2}点(?:半|左右)?)", compact)
        if not concrete_slot:
            return False
    if any(
        term in compact
        for term in (
            "可以约",
            "能约",
            "可以预约",
            "能预约",
            "可以先去",
            "可以过去",
            "可以到店",
            "能过去",
            "能到店",
            "有空档",
            "有档期",
            "有空位",
        )
    ):
        return True
    if re.search(r"锁.{0,8}(?:时段|时间|今天|明天|后天|上午|下午|晚上|\d{1,2}点)", compact):
        return True
    time_scope = r"(?:时段|时间|今天|明天|后天|上午|下午|晚上|\d{1,2}点(?:半|左右)?)"
    hold_action = r"(?:先留|留着|留好|预留|记上)"
    if re.search(rf"{time_scope}.{{0,8}}{hold_action}", compact) or re.search(
        rf"{hold_action}.{{0,8}}{time_scope}", compact
    ):
        return True
    if re.search(r"(?:今天|明天|后天|上午|下午|晚上|\d{1,2}点(?:半|左右)?).{0,8}(?:可以|有空|有时间|有名额|有位置|能约|可约|安排)", compact):
        return True
    if re.search(r"(?:有空|有时间|有名额|有位置|能约|可约|安排).{0,8}(?:今天|明天|后天|上午|下午|晚上|\d{1,2}点(?:半|左右)?)", compact):
        return True
    return False


def _current_message_requests_appointment_availability(state: AgentState) -> bool:
    compact = _compact_text(str(state.get("normalized_content") or state.get("content") or ""))
    if not compact:
        return False
    has_time = bool(re.search(r"(?:今天|明天|后天|上午|下午|晚上|\d{1,2}点(?:半|左右)?)", compact))
    has_availability_question = any(
        term in compact
        for term in (
            "有空",
            "有时间",
            "有档期",
            "有名额",
            "有位置",
            "能约",
            "可约",
            "可以吗",
            "可以吧",
            "行吗",
            "方便吗",
        )
    )
    return has_time and has_availability_question


def _direct_reply_asks_missing_appointment_scope(compact: str) -> bool:
    return any(term in compact for term in ("哪家门店", "哪个门店", "哪个区", "哪个城市", "城市", "区域", "门店"))


def _claims_arrangement_without_fit_context(compact: str) -> bool:
    if "安排" not in compact:
        return False
    if any(term in compact for term in ("适合再安排", "确认适合再安排", "检测评估", "皮肤状态")):
        return False
    return bool(re.search(r"按.{0,12}安排", compact) or re.search(r"帮[你您]按.{0,12}安排", compact))


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
