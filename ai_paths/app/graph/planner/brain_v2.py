from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.graph.nodes.common import model_usage_snapshot
from app.graph.nodes.current_turn_context import (
    build_current_turn_context,
    can_use_contextual_store_for_message,
    current_store_anchor_from_state,
)
from app.graph.nodes.location_card import location_card_from_state
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model, store_anchor_fact_for_model
from app.graph.nodes.store_scope_summary import build_store_scope_summary
from app.graph.nodes.turn_evidence_view import turn_evidence_for_model
from app.graph.planner.planner_contract import ALLOWED_TOOLS
from app.graph.planner.brain_v2_prompts import (
    PLANNER_REPAIR_PROMPT,
    PLANNER_PRECISION_QA_CONTRACT,
    PLANNER_RISK_PATCH_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    PLANNER_TRANSACTION_PATCH_PROMPT,
    PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT,
)
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2, planner_unavailable_fallback_plan, safety_fallback_plan
from app.graph.state import AgentState
from app.policies.business_rules import (
    planner_business_rules_prompt_section,
    planner_recovery_business_rules_prompt_section,
)
from app.policies.sales_flow import precision_qa_context_for_planner
from app.policies.constants import KNOWN_STORE_NAMES
from app.services.customer_payment_state import normalize_prepay_facts
from app.services.model_client import ModelClient
from app.services.risk_hold import HEALTH_RISK_TERMS, current_health_risk_hold_for_model
from app.services.runtime_budget import can_start_model_retry, model_deadline_monotonic, runtime_budget_snapshot
from app.services.store_snapshot_service import store_snapshot_rows

PLANNER_TIMEOUT_RECOVERY_PROMPT = """# Planner Timeout Recovery
你是企业微信线上活动接待的应急 planner。上一轮完整 planner 超时，本轮只用精简事实输出合法 JSON。

# Principles
- 当前消息优先；近聊、turn_evidence、customer_context 只作证据，不替你决定业务动作。
- 不把技术超时理解成客户高风险；除非客户当前消息本身明确健康高风险、投诉退款、付款异常、严重不适或强人工诉求，否则不要输出 human_handoff_notice。
- 不编造门店、地址、停车、营业时间、距离、档期、案例图、价格、支付状态、订单状态或医疗结论。
- 需要具体门店/地址/停车/营业时间/导航/附近候选时，用 customer_store_lookup。
- 需要最近/哪家更近/地标附近排序时，先 customer_store_lookup，再 distance_calculate；客户可见不要输出公里、分钟、车程。
- 当前普通流程只登记到店日期和时间意向，不调用 available_time/create_order_plan；没有既有正式预约事实不能承诺已安排或已留位。
- 效果、怕没效果、怕反黑、要效果图时，用 kb_search(case_studies)，不要让客户先发照片做线上诊断。
- 预约金由 payment_decision 决定；客户口头声称已付不能确认到账，只有当前订单 `prepay_paid>0` 或清晰支付成功截图才能推进付款后信息确认。
- `stage/sub_rule_id` 必须从 Current Recovery Business Rules 的 `scene_index` 选择，不能自造英文场景名。
- `need_tools` 必须提供可执行的扁平 `tool_calls`，工具名字段只能是 `name`；禁止 `tool_name/arguments/tool/args` 包装。门店查询示例：`{"name":"customer_store_lookup","query":"双流区","purpose":"existence"}`。
- `direct_reply` 必须有对象数组 reply_messages 且 tool_calls=[]；`need_tools` 必须 reply_messages=[] 且 tool_calls 非空。

# Output JSON Schema
只输出 JSON：
{
  "decision": "direct_reply | need_tools | no_reply",
  "stage": "S1 | S2 | S3 | S4",
  "sub_rule_id": "",
  "conversion_stage": "interest_capture | objection_resolution | store_match | time_confirm | deposit_push",
  "customer_type": "price | effect | distance | time | risk | accompany | unknown",
  "main_blocker": "price | effect | distance | time | risk | trust | logistics | none",
  "next_step": "ask_intent | solve_blocker | lookup_store | confirm_time | send_deposit | no_action",
  "payment_state": "unknown | link_sent | customer_claimed_paid | resend_requested | payment_failed | needs_payment",
  "payment_action": "unknown | none | send_now | manual_transfer | offer_resend | explain_existing | confirm_next_step",
  "payment_decision": {"action":"none | explain | send_now | resend | manual_transfer | after_paid_next_step | ask_party_size","party_size":1,"amount":10,"source":"","confidence":"high | medium | low","basis":[]},
  "store_binding_decision": {"status":"none | accepted_explicit | accepted_implicit | exploring | rejected | ambiguous","store_id":"","confidence":"high | medium | low","source":"","basis":[]},
  "order_decision": {"action":"none | create_work | use_existing","order_id":"","store_id":"","amount":10,"source":"","basis":[]},
  "appointment_decision": {"action":"none | ask_store | ask_time | lookup_store | check_availability | confirm_existing | tentative_arrange | create_plan","commitment_level":"none | tentative | confirmed","basis":[]},
  "sales_progression": {"status":"continue | pause | terminal","target_stage":"need_and_case | trust | store | activity | deposit | registration | appointment | service | close | risk","action":"ask_need_context | deliver_value | confirm_store | explain_deposit | send_payment_card | manual_transfer | collect_registration | confirm_visit_time | confirm_appointment | close | risk_pause","goal":"","basis":[]},
  "closing_move": {"action":"none | ask_city | ask_spot_history | send_case | introduce_offer | ask_store_choice | send_payment | manual_transfer | ask_party_size | ask_registration | ask_visit_intent | resolve_risk | close","mainline_stage":"need_and_case | trust | store | activity | deposit | registration | appointment | service | close | risk","reason":"","required_slot":"","must_not_repeat":[]},
  "reply_messages": [],
  "tool_calls": [],
  "handoff": {"needed": false, "reason": ""}
}
"""


def planner_v2_model_tier(state: AgentState) -> str:
    return "planner"


def planner_v2_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    payload = _planner_payload_for_model(state)
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "system", "content": PLANNER_PRECISION_QA_CONTRACT},
        {"role": "system", "content": "# Current Business Facts\n" + planner_business_rules_prompt_section()},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": PLANNER_TRANSACTION_PATCH_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        {"role": "system", "content": PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT},
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
        {"role": "system", "content": PLANNER_PRECISION_QA_CONTRACT},
        {"role": "system", "content": "# Current Business Facts\n" + planner_business_rules_prompt_section()},
        {"role": "system", "content": PLANNER_RISK_PATCH_PROMPT},
        {"role": "system", "content": PLANNER_TRANSACTION_PATCH_PROMPT},
        {"role": "system", "content": PLANNER_REPAIR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        {"role": "system", "content": PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT},
    ]


def planner_v2_timeout_retry_messages_for_model(
    state: AgentState,
    *,
    previous_error: str,
) -> list[dict[str, Any]]:
    payload = _compact_timeout_retry_payload_for_model(state, previous_error=previous_error)
    return [
        {"role": "system", "content": PLANNER_TIMEOUT_RECOVERY_PROMPT},
        {"role": "system", "content": "# Current Recovery Business Rules\n" + planner_recovery_business_rules_prompt_section()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


async def run_planner_brain_v2(
    state: AgentState,
    model_client: ModelClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    planner_state = _planner_state_with_derived_facts(state)
    tier = planner_v2_model_tier(planner_state)
    initial_messages = planner_v2_messages_for_model(planner_state)
    nested_calls: list[dict[str, Any]] = []
    initial_error = ""
    node_started_at = time.monotonic()
    primary_budget = _model_budget_seconds(model_client, "model_planner_primary_budget_seconds", 25.0)
    recovery_budget = _model_budget_seconds(model_client, "model_planner_recovery_budget_seconds", 10.0)
    primary_deadline = _capped_deadline(
        node_started_at + primary_budget,
        model_deadline_monotonic(planner_state, tier="planner", reserve_reply=True),
    )
    try:
        payload = await _chat_json_with_deadline(
            model_client,
            initial_messages,
            tier=tier,
            deadline_monotonic=primary_deadline,
        )
        plan = build_planner_plan_v2(planner_state, payload)
        initial_usage = model_usage_snapshot(model_client)
    except Exception as exc:
        initial_error = f"{type(exc).__name__}: {exc}"
        initial_usage = model_usage_snapshot(model_client)
        retry_call: dict[str, Any] = {
            "name": "planner_brain_timeout_retry",
            "input": {
                "tier": "fast",
                "previous_error": initial_error,
                "runtime_budget": runtime_budget_snapshot(planner_state, tier="planner", reserve_reply=True),
            },
        }
        if not can_start_model_retry(planner_state, tier="planner", reserve_reply=True):
            retry_call["skipped"] = "insufficient_round_budget"
            nested_calls.append(retry_call)
            plan = planner_unavailable_fallback_plan(
                planner_state,
                reason=f"{initial_error}; timeout_retry_skipped=insufficient_round_budget",
            )
            _attach_derived_planner_facts(plan, planner_state)
            return plan, {
                "name": "planner_brain_v2",
                "input": {"tier": tier, "messages": initial_messages},
                "error": f"{initial_error}; timeout_retry_skipped=insufficient_round_budget",
                "raw_json_output": {},
                "output": _planner_call_output(plan),
                "usage": initial_usage,
                "deadline": {
                    "primary_budget_seconds": primary_budget,
                    "recovery_budget_seconds": recovery_budget,
                    "runtime_budget": runtime_budget_snapshot(planner_state, tier="planner", reserve_reply=True),
                    "elapsed_ms": int((time.monotonic() - node_started_at) * 1000),
                },
                "nested_calls": nested_calls,
            }
        retry_messages = planner_v2_timeout_retry_messages_for_model(planner_state, previous_error=initial_error)
        retry_call["input"]["messages"] = retry_messages
        recovery_deadline = _capped_deadline(
            time.monotonic() + recovery_budget,
            model_deadline_monotonic(planner_state, tier="planner", reserve_reply=True),
        )
        try:
            payload = await _chat_json_with_deadline(
                model_client,
                retry_messages,
                tier="fast",
                deadline_monotonic=recovery_deadline,
            )
            plan = build_planner_plan_v2(planner_state, payload)
            retry_call["raw_json_output"] = payload
            retry_call["output"] = _planner_call_output(plan)
            retry_call["usage"] = model_usage_snapshot(model_client)
        except Exception as retry_exc:
            retry_error = f"{type(retry_exc).__name__}: {retry_exc}"
            retry_call["error"] = retry_error
            retry_call["usage"] = model_usage_snapshot(model_client)
            nested_calls.append(retry_call)
            plan = planner_unavailable_fallback_plan(
                planner_state,
                reason=f"{initial_error}; timeout_retry_failed={retry_error}",
            )
            _attach_derived_planner_facts(plan, planner_state)
            model_call = {
                "name": "planner_brain_v2",
                "input": {"tier": tier, "messages": initial_messages},
                "error": f"{initial_error}; timeout_retry_failed={retry_error}",
                "raw_json_output": {},
                "output": _planner_call_output(plan),
                "usage": initial_usage,
                "deadline": {
                    "primary_budget_seconds": primary_budget,
                    "recovery_budget_seconds": recovery_budget,
                    "elapsed_ms": int((time.monotonic() - node_started_at) * 1000),
                },
                "nested_calls": nested_calls,
            }
            return plan, model_call
        nested_calls.append(retry_call)
    for repair_attempt in range(1, 2):
        violations = list(plan.get("tool_policy_violations", []))
        if not violations:
            break
        if not can_start_model_retry(planner_state, tier="planner", reserve_reply=True):
            nested_calls.append(
                {
                    "name": "planner_brain_repair",
                    "input": {
                        "tier": tier,
                        "attempt": repair_attempt,
                        "violations": violations,
                        "runtime_budget": runtime_budget_snapshot(planner_state, tier="planner", reserve_reply=True),
                    },
                    "skipped": "insufficient_round_budget",
                }
            )
            break
        repair_call: dict[str, Any] = {
            "name": "planner_brain_repair",
            "input": {
                "tier": tier,
                "attempt": repair_attempt,
                "violations": violations,
                "runtime_budget": runtime_budget_snapshot(planner_state, tier="planner", reserve_reply=True),
            },
        }
        try:
            repair_messages = planner_v2_repair_messages_for_model(
                planner_state,
                original_plan=plan,
                violations=violations,
            )
            repair_call["input"]["messages"] = repair_messages
            repair_tier = "fast" if initial_error else tier
            repair_call["input"]["tier"] = repair_tier
            repair_deadline = _capped_deadline(
                time.monotonic() + recovery_budget,
                model_deadline_monotonic(planner_state, tier="planner", reserve_reply=True),
            )
            repaired_payload = await _chat_json_with_deadline(
                model_client,
                repair_messages,
                tier=repair_tier,
                deadline_monotonic=repair_deadline,
            )
            repaired_plan = build_planner_plan_v2(planner_state, repaired_payload)
            plan = repaired_plan
            repair_call["raw_json_output"] = repaired_payload
            repair_call["output"] = _planner_call_output(plan)
            repair_call["usage"] = model_usage_snapshot(model_client)
        except Exception as exc:
            repair_call["error"] = f"{type(exc).__name__}: {exc}"
            repair_call["usage"] = model_usage_snapshot(model_client)
            nested_calls.append(repair_call)
            break
        nested_calls.append(repair_call)
    model_call = {
        "name": "planner_brain_v2",
        "input": {"tier": tier, "messages": initial_messages},
        "raw_json_output": payload,
        "output": _planner_call_output(plan),
        "usage": initial_usage,
        "deadline": {
            "primary_budget_seconds": primary_budget,
            "recovery_budget_seconds": recovery_budget,
            "remaining_primary_budget_ms": max(0, int((primary_deadline - time.monotonic()) * 1000)),
            "runtime_budget": runtime_budget_snapshot(planner_state, tier="planner", reserve_reply=True),
            "elapsed_ms": int((time.monotonic() - node_started_at) * 1000),
        },
    }
    if initial_error:
        model_call["initial_error"] = initial_error
    if nested_calls:
        model_call["nested_calls"] = nested_calls
    _attach_derived_planner_facts(plan, planner_state)
    return plan, model_call


async def _chat_json_with_deadline(
    model_client: ModelClient,
    messages: list[dict[str, Any]],
    *,
    tier: str,
    deadline_monotonic: float,
) -> dict[str, Any]:
    """Pass node deadlines to the real client while keeping lightweight test doubles compatible."""
    try:
        return await model_client.chat_json(
            messages,
            tier=tier,
            temperature=0.0,
            deadline_monotonic=deadline_monotonic,
        )
    except TypeError as exc:
        if "deadline_monotonic" not in str(exc):
            raise
        return await model_client.chat_json(messages, tier=tier, temperature=0.0)


def _model_budget_seconds(model_client: ModelClient, name: str, default: float) -> float:
    settings = getattr(model_client, "settings", None)
    value = getattr(settings, name, default) if settings is not None else default
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return default


def _capped_deadline(node_deadline: float, round_deadline: float | None) -> float:
    return min(node_deadline, round_deadline) if round_deadline is not None else node_deadline


def _planner_state_with_derived_facts(state: AgentState) -> AgentState:
    """Share the same authoritative store evidence with the model and normalizer."""
    output: AgentState = dict(state)
    current_known_store = _current_known_store_for_planner(state)
    if current_known_store:
        output["current_known_store"] = current_known_store
    store_candidate = _store_candidate_for_planner(state)
    if store_candidate:
        output["store_candidate"] = store_candidate
    return output


def _attach_derived_planner_facts(plan: dict[str, Any], planner_state: AgentState) -> None:
    for key in ("current_known_store", "store_candidate"):
        value = planner_state.get(key)
        if isinstance(value, dict) and value:
            plan[key] = dict(value)


def _planner_call_output(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": plan.get("planner_decision", ""),
        "stage": plan.get("planner_stage", ""),
        "sub_rule_id": plan.get("planner_sub_rule_id", ""),
        "conversion_stage": plan.get("conversion_stage", ""),
        "customer_type": plan.get("customer_type", ""),
        "main_blocker": plan.get("main_blocker", ""),
        "next_step": plan.get("next_step", ""),
        "payment_action": plan.get("payment_action", ""),
        "payment_decision": plan.get("payment_decision", {}),
        "store_binding_decision": plan.get("store_binding_decision", {}),
        "order_decision": plan.get("order_decision", {}),
        "appointment_decision": plan.get("appointment_decision", {}),
        "sales_progression": plan.get("sales_progression", {}),
        "closing_move": plan.get("closing_move", {}),
        "reply_messages": len(plan.get("planner_reply_messages", [])),
        "tool_calls": len(plan.get("planner_tool_calls", [])),
        "tool_policy_violations": len(plan.get("tool_policy_violations", [])),
    }


def _planner_payload_for_model(state: AgentState) -> dict[str, Any]:
    suppress_memory = False
    sent_message_summary = {} if suppress_memory else sent_message_summary_for_model(state)
    current_known_store = _current_known_store_for_planner(state)
    store_candidate = _store_candidate_for_planner(state)
    current_turn_context = {} if suppress_memory else build_current_turn_context(
        state,
        current_known_store=current_known_store,
        sent_message_summary=sent_message_summary,
    )
    risk_hold = {} if suppress_memory else current_health_risk_hold_for_model(state)
    turn_evidence = _turn_evidence_for_planner(current_turn_context)
    payload = {
        "current_date": _current_date_iso(),
        "timezone": "Asia/Shanghai",
        "current_message": state.get("normalized_content") or "",
        "location_card": location_card_from_state(state),
        "conversation_history": [] if suppress_memory else (state.get("conversation_history") or [])[-20:],
        "image_info": _compact_image_info(state.get("image_info") or {}),
        "category_id": str(((state.get("request_context") or {}).get("category_id") or "")).strip(),
        "customer_profile": {} if suppress_memory else _compact_customer_profile_for_planner(state.get("customer_profile") or {}),
        "transaction_facts": {} if suppress_memory else _transaction_facts_for_planner(state),
        "current_known_store": current_known_store,
        "store_candidate": store_candidate,
        "turn_evidence": turn_evidence,
        "risk_hold": risk_hold,
        "store_scope_summary": build_store_scope_summary(
            state.get("customer_store_knowledge") or {},
            location_hints=_store_scope_location_hints(state, current_known_store, store_candidate),
        ),
        "sent_message_summary": sent_message_summary,
        "sop_progress_evidence": _sop_progress_evidence_for_planner(state),
        "sop_gate_decision": _sop_gate_decision_for_planner(state),
        "precision_qa_playbook": precision_qa_context_for_planner(
            include_answer_details_in_index=True
        ),
        "available_tools": [tool for tool in ALLOWED_TOOLS if tool != "no_tool"],
    }
    return _drop_empty(payload)


def _compact_timeout_retry_payload_for_model(state: AgentState, *, previous_error: str) -> dict[str, Any]:
    base = _planner_payload_for_model(state)
    payload = {
        "current_date": base.get("current_date"),
        "timezone": base.get("timezone"),
        "current_message": base.get("current_message"),
        "conversation_history": (base.get("conversation_history") or [])[-8:],
        "image_info": base.get("image_info"),
        "category_id": base.get("category_id"),
        "transaction_facts": base.get("transaction_facts"),
        "current_known_store": base.get("current_known_store"),
        "store_candidate": base.get("store_candidate"),
        "turn_evidence": base.get("turn_evidence"),
        "risk_hold": base.get("risk_hold"),
        "store_scope_summary": base.get("store_scope_summary"),
        "sent_message_summary": base.get("sent_message_summary"),
        "sop_progress_evidence": base.get("sop_progress_evidence"),
        "sop_gate_decision": base.get("sop_gate_decision"),
        "precision_qa_playbook": _compact_precision_qa_for_timeout(
            base.get("precision_qa_playbook"),
            base.get("sop_gate_decision"),
        ),
        "available_tools": base.get("available_tools"),
        "timeout_recovery": {
            "previous_error": str(previous_error or "")[:240],
            "goal": "use_compact_context_to_return_valid_planner_json",
        },
    }
    return _drop_empty(payload)


def _compact_precision_qa_for_timeout(value: Any, gate_decision: Any) -> dict[str, Any]:
    playbook = value if isinstance(value, dict) else {}
    gate = gate_decision if isinstance(gate_decision, dict) else {}
    priority_question_id = str(gate.get("priority_question_id") or "").strip()
    question_index: list[dict[str, str]] = []
    selected_question = (
        precision_qa_context_for_planner(priority_question_id).get("selected_question") or {}
        if priority_question_id
        else {}
    )
    for item in playbook.get("question_index") or []:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("id") or "").strip()
        intent_definition = str(item.get("intent_definition") or "").strip()
        if question_id:
            question_index.append(_drop_empty({"id": question_id, "intent_definition": intent_definition}))
    return _drop_empty(
        {
            "global_answer_policy": playbook.get("global_answer_policy") or {},
            "question_index": question_index,
            "selected_question": selected_question,
        }
    )


def _turn_evidence_for_planner(value: Any) -> dict[str, Any]:
    return turn_evidence_for_model(value)


def _current_date_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _sop_progress_evidence_for_planner(state: AgentState) -> dict[str, Any]:
    raw = state.get("sop_progress_evidence")
    if not isinstance(raw, dict):
        gate = state.get("sop_gate") if isinstance(state.get("sop_gate"), dict) else {}
        raw = gate.get("sop_progress_evidence") if isinstance(gate.get("sop_progress_evidence"), dict) else {}
    completed_ids = [str(item) for item in raw.get("completed_pack_ids") or [] if str(item or "").strip()]
    completed_categories = [
        str(item) for item in raw.get("completed_categories") or [] if str(item or "").strip()
    ]
    unfinished: list[dict[str, Any]] = []
    for item in raw.get("unfinished_sops") or []:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        unfinished.append(
            {
                "id": str(item.get("id") or ""),
                "sop_category": str(item.get("sop_category") or ""),
                "name": str(item.get("name") or ""),
                "purpose": str(item.get("purpose") or "")[:240],
                "order": int(item.get("order") or 0),
                "triggers": [str(value) for value in item.get("triggers") or [] if str(value or "").strip()],
            }
        )
    if not completed_ids and not completed_categories and not unfinished:
        return {}
    return {
        "completed_pack_ids": completed_ids,
        "completed_categories": completed_categories,
        "unfinished_sops": sorted(unfinished, key=lambda item: (int(item.get("order") or 0), item["id"])),
        "usage": (
            "只作为真实流程进度证据。你根据当前问题和历史决定本轮如何推进；"
            "不能因为某个包未完成就机械触发，也不能为已完成包重复铺垫。"
        ),
    }


def _compact_customer_profile_for_planner(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    allowed_keys = (
        "decision_stage",
        "conversion_stage",
        "customer_stage",
        "deposit_state",
        "deposit_status",
        "payment_status",
        "intent_level",
        "trust_level",
        "main_objection",
        "main_concern",
        "risk_tags",
        "customer_type_tags",
        "tags",
        "preferred_project",
        "preferred_store",
        "preferred_store_name",
        "intent_date",
        "intent_time",
    )
    compact: dict[str, Any] = {}
    for key in allowed_keys:
        value = profile.get(key)
        if value not in (None, "", [], {}):
            if key in {"main_objection", "main_concern"} and _mentions_health_risk(value):
                continue
            if key in {"risk_tags", "customer_type_tags", "tags"} and isinstance(value, list):
                filtered = [item for item in value if not _mentions_health_risk(item)]
                if filtered:
                    compact[key] = filtered
                continue
            compact[key] = value
    return compact


def _mentions_health_risk(value: Any) -> bool:
    text = str(value or "")
    return any(term in text for term in HEALTH_RISK_TERMS) or "健康风险" in text


def _current_known_store_for_planner(state: AgentState) -> dict[str, Any]:
    store_id = str(state.get("confirmed_store_id") or state.get("store_id") or "").strip()
    store_name = str(state.get("confirmed_store_name") or state.get("store_name") or "").strip()
    if store_id or store_name:
        return _drop_empty({"store_id": store_id, "store_name": store_name, "source": "request"})

    current_message_store = _store_from_current_message(state)
    if current_message_store:
        return current_message_store

    content = str(state.get("normalized_content") or state.get("content") or "").strip()
    if can_use_contextual_store_for_message(content, state):
        explicit_store = _recent_explicit_store_for_planner(state)
        if explicit_store:
            return explicit_store
        contextual_store = current_store_anchor_from_state(
            state,
            current_known_store=None,
            allow_profile=False,
            prefer_recent=True,
        )
        if contextual_store:
            return contextual_store

    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    appointment = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    if _current_turn_can_use_appointment_store(state):
        store_id = str(appointment.get("store_id") or "").strip()
        store_name = str(appointment.get("store_name") or "").strip()
        return _drop_empty({"store_id": store_id, "store_name": store_name, "source": "appointment_context"})

    return {}


def _store_candidate_for_planner(state: AgentState) -> dict[str, Any]:
    basic_info = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    preferred = _store_from_basic_info(basic_info)
    if preferred:
        preferred["candidate_type"] = "preferred_store"
        preferred["confidence"] = "low"
        preferred["usage"] = "candidate_only_lookup_or_confirm_before_customer_visible_fact"
        return preferred
    return {}


def _recent_explicit_store_for_planner(state: AgentState) -> dict[str, Any]:
    event_store = _store_from_recent_events(state)
    if event_store:
        return event_store

    return _store_from_recent_conversation(state)


def _store_from_current_message(state: AgentState) -> dict[str, Any]:
    text = str(state.get("normalized_content") or state.get("content") or "").strip()
    matched = _stores_matching_text_for_planner(state, text)
    if len(matched) == 1:
        return {**_compact_store_for_planner(matched[0]), "source": "current_message"}
    if len(matched) > 1:
        return {
            "ambiguous": True,
            "matched_store_names": [_store_name_for_planner(store) for store in matched[:5]],
            "source": "current_message",
        }
    return {}


def _stores_matching_text_for_planner(state: AgentState, text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    candidates = _known_store_candidates_for_planner(state)
    exact = [
        store
        for store in candidates
        if (name := _store_name_for_planner(store)) and name in text
    ]
    if exact:
        return _without_subsumed_store_matches(_dedupe_store_matches(exact))
    matched: list[dict[str, Any]] = []
    for store in candidates:
        name = _store_name_for_planner(store)
        if _store_name_matches_text_for_planner(name, text):
            matched.append(store)
    return _without_subsumed_store_matches(_dedupe_store_matches(matched))


def _known_store_candidates_for_planner(state: AgentState) -> list[dict[str, Any]]:
    candidates = list(_customer_scope_stores_for_planner(state))
    seen_names = {_store_name_for_planner(store) for store in candidates if _store_name_for_planner(store)}
    for store in store_snapshot_rows():
        name = _store_name_for_planner(store)
        if name and name not in seen_names:
            candidates.append(store)
            seen_names.add(name)
    for name in KNOWN_STORE_NAMES:
        if name and name not in seen_names:
            candidates.append({"store_name": name})
            seen_names.add(name)
    return candidates


def _dedupe_store_matches(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for store in stores:
        store_id = str(store.get("store_id") or store.get("id") or "").strip()
        name = _store_name_for_planner(store)
        key = (store_id, name)
        if not name or key in seen:
            continue
        seen.add(key)
        output.append(store)
    return output


def _without_subsumed_store_matches(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [_store_name_for_planner(store) for store in stores]
    output: list[dict[str, Any]] = []
    for store, name in zip(stores, names):
        if any(name != other and name in other for other in names):
            continue
        output.append(store)
    return output


def _store_from_basic_info(raw: dict[str, Any]) -> dict[str, Any]:
    store_id = str(raw.get("preferred_store_id") or "").strip()
    store_name = str(raw.get("preferred_store_name") or "").strip()
    city = str(raw.get("city") or "").strip()
    if not (store_id or store_name):
        return {}
    return _drop_empty({"store_id": store_id, "store_name": store_name, "city": city, "source": "customer_profile"})


def _store_from_recent_events(state: AgentState) -> dict[str, Any]:
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in reversed(events[-20:]):
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") not in {"store_matched", "store_address_sent"}:
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        store_id = str(facts.get("store_id") or facts.get("id") or "").strip()
        store_name = str(facts.get("store_name") or facts.get("name") or "").strip()
        if store_id or store_name:
            return _drop_empty(
                {
                    "store_id": store_id,
                    "store_name": store_name,
                    "city": str(facts.get("city") or "").strip(),
                    "district": str(facts.get("district") or "").strip(),
                    "source": "history_event",
                }
            )
    return {}


def _store_from_recent_conversation(state: AgentState) -> dict[str, Any]:
    stores = _customer_scope_stores_for_planner(state)
    if not stores:
        return {}
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    chunks: list[str] = []
    for item in history[-6:]:
        text = _conversation_item_text(item)
        if text:
            chunks.append(text)
    store_by_id = {
        str(store.get("store_id") or store.get("id") or "").strip(): store
        for store in stores
        if str(store.get("store_id") or store.get("id") or "").strip()
    }
    for chunk in reversed(chunks):
        match = re.search(r"(?:store_id|门店ID)\s*[=:：]\s*(\d+)", chunk, flags=re.IGNORECASE)
        if match and match.group(1) in store_by_id:
            return {**_compact_store_for_planner(store_by_id[match.group(1)]), "source": "recent_store_address_message"}
    matched_overall: list[dict[str, Any]] = []
    for chunk in chunks:
        matched_overall.extend(store for store in stores if _store_name_matches_text_for_planner(_store_name_for_planner(store), chunk))
    matched_overall = _without_subsumed_store_matches(_dedupe_store_matches(matched_overall))
    if len(matched_overall) == 1:
        return {**_compact_store_for_planner(matched_overall[0]), "source": "recent_conversation"}
    if len(matched_overall) > 1:
        return {
            "ambiguous": True,
            "matched_store_names": [_store_name_for_planner(store) for store in matched_overall[:5]],
            "source": "recent_conversation",
        }
    return {}


def _customer_scope_stores_for_planner(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _store_name_for_planner(store: dict[str, Any]) -> str:
    return str(store.get("store_name") or store.get("name") or "").strip()


def _store_name_matches_text_for_planner(name: str, text: str) -> bool:
    raw_name = str(name or "").strip()
    raw_text = str(text or "").strip()
    if raw_name and raw_text and (raw_name in raw_text or (len(raw_text) >= 4 and raw_text in raw_name)):
        return True
    normalized_name = _normalize_store_name_for_planner_match(raw_name)
    normalized_text = _normalize_store_name_for_planner_match(raw_text)
    return bool(
        normalized_name
        and normalized_text
        and (normalized_name in normalized_text or (len(normalized_text) >= 4 and normalized_text in normalized_name))
    )


def _normalize_store_name_for_planner_match(value: str) -> str:
    return re.sub(r"[，,。？?！!\s]", "", str(value or "")).replace("市", "").replace("百星", "")


def _compact_store_for_planner(store: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "store_id": str(store.get("store_id") or store.get("id") or "").strip(),
            "store_name": _store_name_for_planner(store),
            "city": str(store.get("city") or "").strip(),
            "district": str(store.get("district") or "").strip(),
        }
    )


def _sop_gate_decision_for_planner(state: AgentState) -> dict[str, Any]:
    raw = state.get("sop_gate_decision")
    if not isinstance(raw, dict):
        gate = state.get("sop_gate") if isinstance(state.get("sop_gate"), dict) else {}
        raw = {
            "route": gate.get("route") or gate.get("mode"),
            "coverage": gate.get("coverage"),
            "priority_question_id": gate.get("priority_question_id"),
            "resume_stage": gate.get("resume_stage"),
            "sop_pack_id": gate.get("sop_pack_id"),
        }
    return _drop_empty(
        {
            "route": raw.get("route"),
            "coverage": raw.get("coverage"),
            "priority_question_id": raw.get("priority_question_id"),
            "resume_stage": raw.get("resume_stage"),
            "sop_pack_id": raw.get("sop_pack_id"),
            "source": raw.get("source") or "chat_sop_gate_model",
            "instruction": (
                "这是前置模型的语义路由证据，不是代码决定。请结合当前消息复核；"
                "若语义一致，优先使用其精准问题和主线恢复方向。"
            ),
        }
    )


def _conversation_item_text(item: Any) -> str:
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or content.get("url") or "").strip()
        return str(content or "").strip()
    return str(item or "").strip()


def _current_turn_can_use_appointment_store(state: AgentState) -> bool:
    content = "".join(str(state.get("normalized_content") or state.get("content") or "").split())
    if not content:
        return False
    if _recent_explicit_store_for_planner(state):
        return False
    return any(
        term in content
        for term in (
            "预约",
            "改约",
            "取消",
            "档期",
            "已约",
            "约过",
            "预约记录",
        )
    )


def _should_suppress_planner_memory(state: AgentState) -> bool:
    return False


def _compact_plan_for_repair(plan: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "decision": plan.get("planner_decision", ""),
            "stage": plan.get("planner_stage", ""),
            "sub_rule_id": plan.get("planner_sub_rule_id", ""),
            "conversion_stage": plan.get("conversion_stage", ""),
            "customer_type": plan.get("customer_type", ""),
            "main_blocker": plan.get("main_blocker", ""),
            "next_step": plan.get("next_step", ""),
            "payment_state": plan.get("payment_state", ""),
            "payment_action": plan.get("payment_action", ""),
            "payment_decision": plan.get("payment_decision", {}),
            "store_binding_decision": plan.get("store_binding_decision", {}),
            "order_decision": plan.get("order_decision", {}),
            "appointment_decision": plan.get("appointment_decision", {}),
            "sales_progression": plan.get("sales_progression", {}),
            "closing_move": plan.get("closing_move", {}),
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
    output = {key: raw.get(key) for key in keys if raw.get(key) not in (None, "", [], {})}
    appointment = raw.get("appointment") if isinstance(raw.get("appointment"), dict) else {}
    if appointment:
        output["appointment"] = appointment
    orders = raw.get("orders") if isinstance(raw.get("orders"), list) else []
    compact_orders = []
    for order in orders[:5]:
        if not isinstance(order, dict):
            continue
        compact_orders.append(
            {
                key: order.get(key)
                for key in (
                    "id",
                    "order_no",
                    "status",
                    "category_id",
                    "store_id",
                    "store_name",
                    "prepay_required",
                    "prepay_paid",
                    "deposit_state",
                    "paid_protection_status",
                    "paid_time_source",
                    "paid_time_value",
                    "appointment_time",
                )
                if order.get(key) not in (None, "", [], {})
            }
        )
    if compact_orders:
        output["orders"] = compact_orders
    return output


def _transaction_facts_for_planner(state: AgentState) -> dict[str, Any]:
    """Expose normalized order facts without deciding the customer's sales action."""

    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = context.get("orders") if isinstance(context.get("orders"), list) else []
    unpaid_orders: list[dict[str, Any]] = []
    paid_orders: list[dict[str, Any]] = []
    for raw in orders[:8]:
        if not isinstance(raw, dict):
            continue
        order_id = str(raw.get("id") or raw.get("order_id") or raw.get("order_no") or "").strip()
        if not order_id:
            continue
        required = raw.get("prepay_required")
        if required in (None, ""):
            required = raw.get("fee_required")
        paid = raw.get("prepay_paid")
        if paid in (None, ""):
            paid = raw.get("fee_paid")
        normalized_payment = normalize_prepay_facts(raw)
        deposit_state = str(raw.get("deposit_state") or normalized_payment.get("deposit_state") or "").strip()
        if not deposit_state:
            deposit_state = "paid_by_order" if _numeric_order_amount(paid) > 0 else (
                "required_unpaid" if _numeric_order_amount(required) > 0 else "unknown"
            )
        paid_protection_status = str(
            raw.get("paid_protection_status") or normalized_payment.get("paid_protection_status") or ""
        ).strip()
        item = {
            "order_id": order_id,
            "store_id": str(raw.get("store_id") or "").strip(),
            "prepay_required": required,
            "prepay_paid": paid,
            "deposit_state": deposit_state,
            "paid_protection_status": paid_protection_status,
            "paid_time_source": raw.get("paid_time_source") or normalized_payment.get("paid_time_source"),
            "paid_time_value": raw.get("paid_time_value") or normalized_payment.get("paid_time_value"),
        }
        item = {key: value for key, value in item.items() if value not in (None, "", [], {})}
        if deposit_state == "paid_by_order" and paid_protection_status != "expired":
            paid_orders.append(item)
        elif deposit_state == "required_unpaid" and _numeric_order_amount(required) > 0:
            unpaid_orders.append(item)
    return {
        "has_unpaid_order": bool(unpaid_orders),
        "unpaid_orders": unpaid_orders,
        "paid_orders": paid_orders,
        "store_anchor_fact": store_anchor_fact_for_model(state),
    }


def _numeric_order_amount(value: Any) -> int:
    try:
        return int(float(str(value or "0").strip()))
    except (TypeError, ValueError):
        return 0


def _compact_image_info(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    has_image = bool(raw.get("has_image"))
    if not has_image:
        return {}
    keys = (
        "has_image",
        "image_type",
        "image_intent",
        "body_part",
        "visible_concerns",
        "risk_signals",
        "extracted_text",
        "text_clues",
        "image_desc",
        "payment_result",
        "payment_amount",
        "payment_order_no",
        "confidence",
    )
    return {key: raw.get(key) for key in keys if raw.get(key) not in (None, "", [], {})}


def _store_scope_location_hints(
    state: AgentState,
    current_known_store: dict[str, Any],
    store_candidate: dict[str, Any],
) -> list[str]:
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    values = [
        state.get("normalized_content") or state.get("content"),
        *[_conversation_item_text(item) for item in (state.get("conversation_history") or [])[-6:]],
        basic.get("province"),
        basic.get("city") or basic.get("current_city"),
        basic.get("district") or basic.get("area_or_landmark") or basic.get("region"),
        request_context.get("province"),
        request_context.get("city"),
        request_context.get("district") or request_context.get("area_or_landmark"),
        current_known_store.get("province"),
        current_known_store.get("city"),
        current_known_store.get("district"),
        store_candidate.get("province"),
        store_candidate.get("city"),
        store_candidate.get("district"),
    ]
    return [str(value).strip() for value in values if str(value or "").strip()]


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
