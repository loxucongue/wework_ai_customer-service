from __future__ import annotations

from typing import Any

from app.graph.nodes.appointment_time_utils import available_time_values, filter_times_by_preference, target_time_status
from app.graph.nodes.current_turn_context import build_current_turn_context
from app.graph.nodes.location_card import location_card_from_state
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.nodes.store_scope_summary import build_store_scope_summary
from app.graph.nodes.turn_evidence_view import turn_evidence_for_model
from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_required_tools,
)
from app.graph.state import AgentState
from app.policies.business_rules import reply_business_rules_for_model
from app.policies.sales_flow import appointment_blocker_reference_for_reply, precision_qa_context_for_planner
from app.policies.compliance_terms import (
    QUALIFICATION_CONTEXT_SAFE_NOTE,
    SERVICE_COMMITMENT_CONTEXT_SAFE_NOTE,
    UNSUPPORTED_QUALIFICATION_CONTEXT_TERMS,
    UNSUPPORTED_SERVICE_COMMITMENT_CONTEXT_TERMS,
)
from app.services.risk_hold import current_health_risk_hold_for_model
from app.services.store_resolution import customer_location_hint_texts


def reply_user_payload_for_model(state: AgentState) -> dict[str, Any]:
    suppress_profile_memory = False
    fact_envelope = {} if suppress_profile_memory else (state.get("fact_envelope") or {})
    required_tools = planner_required_tools(state)
    handoff = planner_handoff(state)
    sent_message_summary = {} if suppress_profile_memory else sent_message_summary_for_model(state)
    sop_progress = _sop_progress_for_reply(
        state,
        sent_message_summary=sent_message_summary,
    )
    raw_current_turn_context = {} if suppress_profile_memory else build_current_turn_context(
        state,
        sent_message_summary=sent_message_summary,
    )
    current_turn_context = _current_turn_context_for_reply(raw_current_turn_context)
    risk_hold = {} if suppress_profile_memory else current_health_risk_hold_for_model(state)
    reply_mode = str(sop_progress.get("recommended_reply_mode") or "normal_answer").strip() or "normal_answer"
    store_scope_summary = _sanitize_planner_context_for_reply(
        build_store_scope_summary(
            state.get("customer_store_knowledge") or {},
            location_hints=_store_scope_location_hints_for_reply(state),
        )
    )
    selected_scene_id = _selected_appointment_scene_id(state)
    return _drop_empty({
        "current_message": state.get("normalized_content"),
        "location_card": location_card_from_state(state),
        "conversation_history": [] if suppress_profile_memory else state.get("conversation_history", [])[-50:],
        "image_info": state.get("image_info", {}),
        "customer_background_facts": {} if suppress_profile_memory else _compact_customer_background(state),
        "guardrail_result": state.get("guardrail_result", {}),
        "required_tools": [] if suppress_profile_memory else required_tools,
        "planner_decision": state.get("planner_decision", ""),
        "planner_stage": state.get("planner_stage", ""),
        "planner_sub_rule_id": state.get("planner_sub_rule_id", ""),
        "conversion_stage": state.get("conversion_stage", ""),
        "customer_type": state.get("customer_type", ""),
        "main_blocker": state.get("main_blocker", ""),
        "next_step": state.get("next_step", ""),
        "payment_state": state.get("payment_state", ""),
        "payment_action": state.get("payment_action", ""),
        "payment_decision": state.get("payment_decision", {}),
        "store_binding_decision": state.get("store_binding_decision", {}),
        "order_decision": state.get("order_decision", {}),
        "appointment_decision": state.get("appointment_decision", {}),
        "conversation_state": state.get("conversation_state", {}),
        "current_turn_resolution": state.get("current_turn_resolution", {}),
        "sales_progression": state.get("sales_progression", {}),
        "ai_sales_policy": _ai_sales_policy_for_reply(state),
        "primary_task": state.get("primary_task", {}),
        "secondary_tasks": state.get("secondary_tasks", []),
        "realtime_intent": state.get("realtime_intent", {}),
        "emotion_decision": state.get("emotion_decision", {}),
        "closing_decision": state.get("closing_decision", {}),
        "cardpoint_decision": state.get("cardpoint_decision", {}),
        "cardpoint_candidates": _sales_strategy_candidates_for_reply(state.get("cardpoint_candidates")),
        "reply_contract": state.get("reply_contract", {}),
        "sop_gate_decision": state.get("sop_gate_decision", {}),
        "sop_gate_candidate_messages": state.get("sop_gate_candidate_messages", []),
        "authorized_sop_delivery_manifest": state.get("authorized_sop_delivery_manifest", {}),
        "closing_move": state.get("closing_move", {}),
        "precision_qa_decision": state.get("precision_qa_decision", {}),
        "reply_constraints": state.get("reply_constraints", []),
        "planner_tool_policy_violations": _compact_planner_violations(state.get("tool_policy_violations", [])),
        "planner_direct_reply_draft": _planner_direct_reply_draft_for_reply(state),
        "handoff": {} if suppress_profile_memory else handoff,
        "turn_evidence": current_turn_context,
        "transaction_facts": _transaction_facts_for_reply(fact_envelope),
        "store_candidate": _store_candidate_for_reply(state),
        "risk_hold": risk_hold,
        "store_scope_summary": store_scope_summary,
        "planner_structured_actions": _planner_structured_actions_for_reply(
            state,
            store_scope_summary=store_scope_summary,
        ),
        "sent_message_summary": sent_message_summary,
        "reply_mode": reply_mode,
        "sop_progress": sop_progress,
        "business_rules": reply_business_rules_for_model(
            stage=str(state.get("planner_stage") or ""),
            sub_rule_id=str(state.get("planner_sub_rule_id") or ""),
        ),
        "precision_qa_playbook": precision_qa_context_for_planner(
            str((state.get("precision_qa_decision") or {}).get("question_id") or "")
        ),
        "appointment_blocker_reference": appointment_blocker_reference_for_reply(selected_scene_id),
        "tool_facts": _tool_facts_for_reply(fact_envelope),
        "fact_notes": _fact_notes_for_model(
            fact_envelope,
            content=str(state.get("normalized_content") or state.get("content") or ""),
            sent_message_summary=sent_message_summary,
        ),
    })


def _selected_appointment_scene_id(state: AgentState) -> str:
    for key in ("sop_gate_decision", "sop_gate"):
        gate = state.get(key)
        if isinstance(gate, dict):
            value = gate.get("selected_scene_id") or gate.get("priority_question_id")
            if str(value or "").strip().startswith("scene_"):
                return str(value).strip()
    return ""


def reply_recovery_payload_for_model(state: AgentState) -> dict[str, Any]:
    """Keep the semantic contract intact while dropping duplicated recovery input."""

    full = reply_user_payload_for_model(state)
    history = full.get("conversation_history") if isinstance(full.get("conversation_history"), list) else []
    keys = (
        "current_message",
        "location_card",
        "image_info",
        "planner_decision",
        "planner_stage",
        "planner_sub_rule_id",
        "conversion_stage",
        "main_blocker",
        "next_step",
        "payment_state",
        "payment_action",
        "payment_decision",
        "store_binding_decision",
        "order_decision",
        "appointment_decision",
        "sales_progression",
        "ai_sales_policy",
        "primary_task",
        "secondary_tasks",
        "realtime_intent",
        "emotion_decision",
        "closing_decision",
        "cardpoint_decision",
        "cardpoint_candidates",
        "closing_move",
        "precision_qa_decision",
        "planner_direct_reply_draft",
        "turn_evidence",
        "transaction_facts",
        "risk_hold",
        "store_scope_summary",
        "planner_structured_actions",
        "sent_message_summary",
        "sop_progress",
        "conversation_state",
        "current_turn_resolution",
        "reply_contract",
        "sop_gate_decision",
        "sop_gate_candidate_messages",
        "authorized_sop_delivery_manifest",
        "business_rules",
        "precision_qa_playbook",
        "tool_facts",
        "fact_notes",
        "reply_constraints",
    )
    payload = {
        key: full.get(key)
        for key in keys
        if full.get(key) not in (None, "", [], {})
    }
    payload["conversation_history"] = history[-12:]
    payload["recovery_contract"] = {
        "history_policy": "最近12条按原顺序保留；不可把发过卡当成已付，也不可让旧风险覆盖当前普通问题",
        "rule_policy": "business_rules 与结构事实仍然有效；精简只删除重复字段，不降低业务或事实边界",
    }
    return _drop_empty(payload)


def _compact_customer_background(state: AgentState) -> dict[str, Any]:
    """Expose only stable location facts; soft portrait semantics are recomputed from chat."""

    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    allowed_basic_keys = ("city", "province", "district")
    return _drop_empty(
        {
            "basic_location": {
                key: basic.get(key)
                for key in allowed_basic_keys
                if basic.get(key) not in (None, "", [], {})
            },
            "priority": "location_hint_only_current_message_recent_history_and_tool_facts_win",
        }
    )


def _tool_facts_for_reply(fact_envelope: dict[str, Any]) -> dict[str, Any]:
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope, dict) else {}
    if not isinstance(structured, dict):
        return {}
    allowed_keys = (
        "store_resolution",
        "store_facts",
        "recommended_store",
        "store_lookup_status",
        "store_resolution_fact",
        "distance_facts",
        "appointment_facts",
        "case_facts",
        "professional_assist",
        "order_facts",
        "registration_facts",
        "payment_facts",
        "tool_errors",
    )
    output: dict[str, Any] = {}
    for key in allowed_keys:
        value = structured.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            output[key] = [_sanitize_planner_context_for_reply(item) for item in value[-8:]]
        else:
            output[key] = _sanitize_planner_context_for_reply(value)
    if output:
        output["source_policy"] = "authoritative_current_turn_tool_facts"
    return output


def _planner_structured_actions_for_reply(
    state: AgentState,
    *,
    store_scope_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose only Planner-approved, scope-backed non-text actions to Reply."""
    known_store_ids = {
        str(store.get("store_id") or store.get("id") or "").strip()
        for region in (store_scope_summary.get("relevant_regions") or [])
        if isinstance(region, dict)
        for store in (region.get("stores") or [])
        if isinstance(store, dict)
    }
    actions: list[dict[str, Any]] = []
    for message in state.get("planner_reply_messages") or []:
        if not isinstance(message, dict) or str(message.get("type") or "") != "store_address":
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        store_id = str(content.get("store_id") or "").strip()
        if store_id and store_id in known_store_ids:
            actions.append(
                {
                    "type": "store_address",
                    "content": {"store_id": store_id},
                    "source": "planner_scope_verified",
                }
            )
    return actions


def _planner_direct_reply_draft_for_reply(state: AgentState) -> dict[str, Any]:
    """Expose Planner's customer-visible direct-reply draft as a styleable model input.

    The draft is produced by the Planner model, so it is an AI semantic decision rather than
    a Python business template. Reply may polish it, but should not drop the chosen action.
    """
    if str(state.get("planner_decision") or "") != "direct_reply":
        return {}
    if str(state.get("planner_sub_rule_id") or "") == "PLANNER_SYSTEM_UNAVAILABLE":
        return {}
    if state.get("tool_policy_violations"):
        return {}

    messages: list[dict[str, Any]] = []
    for message in state.get("planner_reply_messages") or []:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip()
        if message_type not in {"text", "payment_collection", "store_address", "image", "human_handoff_notice"}:
            continue
        content = message.get("content")
        if message_type == "text":
            text = ""
            if isinstance(content, dict):
                text = str(content.get("text") or content.get("content") or "").strip()
            else:
                text = str(content or "").strip()
            if not text:
                continue
            messages.append({"type": "text", "content": text})
            continue
        if isinstance(content, dict):
            safe_content = {
                key: content.get(key)
                for key in ("store_id", "amount", "url", "handoff_reason")
                if content.get(key) not in (None, "", [], {})
            }
        else:
            safe_content = {}
        messages.append(_drop_empty({"type": message_type, "content": safe_content}))

    if not messages:
        return {}
    return {
        "messages": messages[:5],
        "usage": (
            "Planner direct-reply draft. Reply may polish wording and split/merge text, "
            "but must preserve its concrete answer and sales/progression action unless hard facts forbid it."
        ),
    }


def _current_turn_context_for_reply(value: dict[str, Any]) -> dict[str, Any]:
    """Expose current-turn evidence to reply model without legacy task conclusions."""
    return turn_evidence_for_model(value)


def _transaction_facts_for_reply(fact_envelope: dict[str, Any]) -> dict[str, Any]:
    """Lift current-turn transaction results out of the large fact envelope."""
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope, dict) else {}
    if not isinstance(structured, dict):
        return {}

    registration = [
        _drop_empty(
            {
                "type": item.get("type"),
                "status": item.get("status"),
                "source": item.get("source"),
            }
        )
        for item in (structured.get("registration_facts") or [])
        if isinstance(item, dict)
    ]
    orders = [
        _drop_empty(
            {
                "type": item.get("type"),
                "status": item.get("status"),
                "order_id": item.get("order_id"),
                "store_id": item.get("store_id"),
                "deposit_state": item.get("deposit_state"),
                "creation_mode": item.get("creation_mode"),
                "missing": item.get("missing"),
                "missing_optional_fields": item.get("missing_optional_fields"),
                "error": item.get("error"),
            }
        )
        for item in (structured.get("order_facts") or [])
        if isinstance(item, dict)
    ]
    appointments = [
        _drop_empty(
            {
                "type": item.get("type"),
                "status": item.get("status"),
                "store_id": item.get("store_id") or item.get("store"),
                "store_name": item.get("store_name"),
                "date": item.get("date"),
                "appointment_time": item.get("appointment_time"),
                "recommended_slot": item.get("recommended_slot"),
                "backup_slots": item.get("backup_slots"),
                "target_time": item.get("target_time"),
                "target_time_available": item.get("target_time_available"),
            }
        )
        for item in (structured.get("appointment_facts") or [])
        if isinstance(item, dict)
    ]
    return _drop_empty(
        {
            "registration": registration[-2:],
            "orders": orders[-2:],
            "appointments": appointments[-3:],
            "source_policy": "current_turn_tool_facts_are_authoritative",
        }
    )


def _store_candidate_for_reply(state: AgentState) -> dict[str, Any]:
    return {}


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _ai_sales_policy_for_reply(state: AgentState) -> dict[str, Any]:
    raw = state.get("ai_sales_policy")
    if not isinstance(raw, dict):
        return {}
    routing = raw.get("routing") if isinstance(raw.get("routing"), dict) else {}
    intent = raw.get("intent") if isinstance(raw.get("intent"), dict) else {}
    emotion = raw.get("emotion") if isinstance(raw.get("emotion"), dict) else {}
    closing = raw.get("closing") if isinstance(raw.get("closing"), dict) else {}
    primary_key = str((state.get("primary_task") or {}).get("type") or "").strip()
    intent_key = str((state.get("realtime_intent") or {}).get("type") or "").strip()
    emotion_key = str((state.get("emotion_decision") or {}).get("label") or "").strip()
    closing_decision = state.get("closing_decision") if isinstance(state.get("closing_decision"), dict) else {}
    sequence_key = str(closing_decision.get("sequence_key") or "").strip()
    node_key = str(closing_decision.get("node_key") or "").strip()
    selected_task = next(
        (item for item in routing.get("business_tasks") or [] if isinstance(item, dict) and item.get("key") == primary_key),
        {},
    )
    selected_intent = next(
        (item for item in intent.get("realtime_intents") or [] if isinstance(item, dict) and item.get("key") == intent_key),
        {},
    )
    selected_emotion = next(
        (item for item in emotion.get("labels") or [] if isinstance(item, dict) and item.get("key") == emotion_key),
        {},
    )
    selected_sequence = next(
        (
            item
            for item in closing.get("sequences") or []
            if isinstance(item, dict) and item.get("sequence_key") == sequence_key
        ),
        {},
    )
    selected_node = next(
        (
            item
            for item in selected_sequence.get("nodes") or []
            if isinstance(item, dict) and item.get("node_key") == node_key
        ),
        {},
    )
    return _drop_empty(
        {
            "schema_version": raw.get("schema_version"),
            "policy_version": raw.get("policy_version"),
            "checksum": raw.get("checksum"),
            "runtime_mode": raw.get("runtime_mode"),
            "silent_tasks_mode": closing.get("silent_tasks_mode"),
            "selected_task": _drop_empty({"key": selected_task.get("key"), "goal": selected_task.get("goal")}),
            "selected_intent": _drop_empty(
                {
                    "key": selected_intent.get("key"),
                    "meaning": selected_intent.get("definition"),
                    "usage": selected_intent.get("usage"),
                }
            ),
            "selected_emotion": _drop_empty(
                {
                    "key": selected_emotion.get("key"),
                    "reply_effect": selected_emotion.get("reply_effect"),
                    "flow_action": selected_emotion.get("flow_action"),
                }
            ),
            "selected_closing_node": _drop_empty(
                {
                    "sequence_key": selected_sequence.get("sequence_key"),
                    "applies_when": selected_sequence.get("applies_when"),
                    "node_key": selected_node.get("node_key"),
                    "timing": selected_node.get("timing"),
                    "goal": selected_node.get("goal"),
                    "required_facts": selected_node.get("required_facts"),
                    "pressure": selected_node.get("pressure"),
                    "ai_guidance": selected_node.get("ai_guidance"),
                }
            ),
        }
    )


def _sales_strategy_candidates_for_reply(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        result.append(
            _drop_empty(
                {
                    "content_id": item.get("content_id"),
                    "scenario_name": item.get("scenario_name"),
                    "tactic_tag": item.get("tactic_tag"),
                    "solution_idea": item.get("solution_idea"),
                    "reference_text": item.get("reference_text"),
                    "image_url": item.get("image_url"),
                    "video_url": item.get("video_url"),
                    "image_urls": item.get("image_urls"),
                    "video_urls": item.get("video_urls"),
                    "content_types": item.get("content_types"),
                    "usage": "reference_only_rephrase_do_not_copy",
                }
            )
        )
    return result


def _sop_progress_for_reply(
    state: AgentState,
    *,
    sent_message_summary: dict[str, Any],
) -> dict[str, Any]:
    sent_categories = _sent_sop_like_categories(state, sent_message_summary=sent_message_summary)
    raw_evidence = state.get("sop_progress_evidence")
    evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
    progression = state.get("sales_progression")
    selected_progression = progression if isinstance(progression, dict) else {}
    if not sent_categories and not evidence and not selected_progression:
        return {}
    return {
        "recommended_reply_mode": "normal_answer",
        "sent_categories": sent_categories,
        "completed_pack_ids": [str(item) for item in evidence.get("completed_pack_ids") or [] if str(item or "").strip()],
        "completed_categories": [
            str(item) for item in evidence.get("completed_categories") or [] if str(item or "").strip()
        ],
        "unfinished_sops": [
            _sanitize_planner_context_for_reply(item)
            for item in evidence.get("unfinished_sops") or []
            if isinstance(item, dict)
        ][:8],
        "selected_progression": _sanitize_planner_context_for_reply(selected_progression),
        "usage": "这是事实进度，不是代码候选。先完整回答当前问题，再严格实现 Planner 选择的 sales_progression 和唯一 closing_move；不要照抄 SOP 静态话术，也不要一次推进多个动作。",
    }


def _sent_sop_like_categories(state: AgentState, *, sent_message_summary: dict[str, Any]) -> list[str]:
    categories: list[str] = []
    if sent_message_summary.get("store_address_sent_by_store_id"):
        categories.append("store_address")
    if sent_message_summary.get("activity_intro_image_sent"):
        categories.append("activity_intro")
    for event in state.get("history_events") or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type in {"store_matched", "store_address_sent"}:
            categories.append("store_address")
        elif event_type == "case_image_sent":
            categories.append("effect_case")
        elif event_type == "activity_intro_image_sent":
            categories.append("activity_intro")
        elif event_type == "offer_explained":
            categories.append("price_quote")
    return list(dict.fromkeys(item for item in categories if item))


def _sanitize_planner_context_for_reply(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_planner_context_for_reply(item) for key, item in value.items()}
    if isinstance(value, list):
        cleaned: list[Any] = []
        seen: set[str] = set()
        for item in value:
            sanitized = _sanitize_planner_context_for_reply(item)
            marker = repr(sanitized)
            if marker in seen:
                continue
            seen.add(marker)
            cleaned.append(sanitized)
        return cleaned
    if isinstance(value, str):
        if any(term in value for term in UNSUPPORTED_QUALIFICATION_CONTEXT_TERMS):
            return QUALIFICATION_CONTEXT_SAFE_NOTE
        if any(term in value for term in UNSUPPORTED_SERVICE_COMMITMENT_CONTEXT_TERMS):
            return SERVICE_COMMITMENT_CONTEXT_SAFE_NOTE
        return _sanitize_internal_project_context_text(value)
    return value


def _compact_planner_violations(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, str]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        compact = {
            "missing": str(item.get("missing") or "").strip(),
            "note": str(item.get("note") or "").strip()[:240],
        }
        if compact["missing"] or compact["note"]:
            output.append(compact)
    return output


def _sanitize_internal_project_context_text(value: str) -> str:
    text = str(value or "")
    replacements = (
        ("S10色素管理项目", "淡斑活动"),
        ("S10 色素管理项目", "淡斑活动"),
        ("S10色素管理", "淡斑活动"),
        ("S10 色素管理", "淡斑活动"),
        ("S10色素管理(色素体验)", "淡斑活动"),
        ("S10 色素管理(色素体验)", "淡斑活动"),
        ("色素管理项目", "淡斑活动"),
        ("色素管理", "淡斑"),
        ("S10项目", "淡斑活动"),
        ("S10 项目", "淡斑活动"),
        ("S10活动", "淡斑活动"),
        ("S10 活动", "淡斑活动"),
        ("S10N", "淡斑活动"),
        ("K10", "淡斑活动"),
        ("M10", "淡斑活动"),
        ("S10", "淡斑活动"),
        ("项目代号", "活动"),
        ("品项名称", "活动名称"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text.replace("淡斑活动淡斑活动", "淡斑活动").replace("淡斑活动淡斑", "淡斑活动")


def _fact_notes_for_model(
    fact_envelope: dict[str, Any],
    *,
    content: str = "",
    sent_message_summary: dict[str, Any] | None = None,
) -> list[str]:
    notes: list[str] = []
    structured_facts = fact_envelope.get("structured_facts") or {}
    if not isinstance(structured_facts, dict):
        structured_facts = {}

    recommended_store = structured_facts.get("recommended_store") or {}
    if isinstance(recommended_store, dict) and recommended_store.get("name"):
        notes.append("已有推荐门店事实，可优先按推荐门店回答。")
        if recommended_store.get("reason") in {"distance_calculate_rank_1", "haversine_rank_1"}:
            notes.append("客户问附近或最近门店时，只能按 Haversine 直线距离排序第一的推荐门店回答；客户可见不要输出几公里、几分钟、车程、路线或步行时长。")

    store_lookup_status = structured_facts.get("store_lookup_status") or {}
    if isinstance(store_lookup_status, dict) and store_lookup_status.get("distance_lookup_required"):
        notes.append("客户在问距离或附近门店，但本轮没有真实距离排序结果；不要说最近、更近、几公里或几分钟，只能基于候选门店说明还需要按地图距离核对。")
    if (
        isinstance(store_lookup_status, dict)
        and store_lookup_status.get("source") == "distance_calculate"
        and store_lookup_status.get("recommendation_status") == "insufficient_comparable_candidates"
    ):
        notes.append("本轮不足两家可比较门店，没有真实排序结论；可以说明查到的候选，但不能称某家优先、更方便、更顺路或最近。")

    sent_store_ids = {
        str(item).strip()
        for item in ((sent_message_summary or {}).get("store_address_sent_by_store_id") or [])
        if str(item).strip()
    }
    if sent_store_ids:
        store_facts = structured_facts.get("store_facts") or []
        current_store_ids = {
            str(item.get("store_id") or item.get("id") or "").strip()
            for item in store_facts
            if isinstance(item, dict) and str(item.get("store_id") or item.get("id") or "").strip()
        }
        recommended = structured_facts.get("recommended_store") or {}
        if isinstance(recommended, dict) and str(recommended.get("store_id") or recommended.get("id") or "").strip():
            current_store_ids.add(str(recommended.get("store_id") or recommended.get("id") or "").strip())
        repeated = sorted(sent_store_ids & current_store_ids)
        if repeated:
            notes.append(f"同一门店位置卡已发过：{', '.join(repeated[:4])}。本轮默认只用text回答，不要再次输出store_address；只有客户明确索要发地址、发导航、发路线、发位置、没收到或再发时才可以重发。")

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
            if item.get("type") == "available_time" and item.get("missing"):
                missing = [
                    str(value).strip()
                    for value in (item.get("missing") or [])
                    if str(value).strip()
                ]
                if missing:
                    notes.append(
                        "档期工具缺少必要信息："
                        + "、".join(missing[:3])
                        + "。本轮不能说已经查到档期，也不要承诺继续查；只能问客户补最关键的一个信息。"
                    )
                    break
            if item.get("type") == "available_time" and (
                item.get("slots")
                or str(item.get("recommended_slot") or "").strip()
                or item.get("backup_slots")
            ):
                summary = _available_time_fact_note(item, content)
                notes.append(summary or "已有档期事实，可直接回答可约时间。")
                break

    case_facts = structured_facts.get("case_facts") or []
    if isinstance(case_facts, list) and case_facts:
        has_case_image = any(isinstance(item, dict) and str(item.get("image_url") or "").strip() for item in case_facts)
        has_no_new_marker = any(
            isinstance(item, dict) and str(item.get("status") or "").strip() == "no_new_case_image"
            for item in case_facts
        )
        if not has_case_image and has_no_new_marker:
            notes.append("本轮没有可发送的案例图片事实；不要承诺稍后发案例图，也不要输出 image。")

    professional_assist = structured_facts.get("professional_assist") or {}
    if isinstance(professional_assist, dict) and professional_assist.get("status") == "requested":
        notes.append("本轮已有内部关注 notice 事实；客户可见回复应先正面承接诉求。健康类引导到店检测，纠纷类核对门店/付款/项目，并追加 human_handoff_notice。")

    return notes[:6]


def _available_time_fact_note(item: dict[str, Any], content: str) -> str:
    recommended_slot = str(item.get("recommended_slot") or "").strip()
    backup_slots = [
        str(slot).strip()
        for slot in (item.get("backup_slots") or [])
        if str(slot or "").strip()
    ]
    if recommended_slot:
        target_time = str(item.get("target_time") or "").strip()
        target_available = item.get("target_time_available")
        date = str(item.get("date") or "").strip()
        store = str(item.get("store") or item.get("store_id") or "").strip()
        parts = []
        if store:
            parts.append(f"门店ID {store}")
        if date:
            parts.append(date)
        parts.append(f"推荐时间 {recommended_slot}")
        if backup_slots:
            parts.append(f"备选时间 {backup_slots[0]}")
        slot_count = item.get("slot_count")
        if slot_count:
            parts.append(f"共有{slot_count}个可约时间")
        if target_time and target_available is False:
            return (
                "已有档期事实："
                + "，".join(parts)
                + f"。客户问的{target_time}暂未看到可约；第一句先说明该时间不可约，再推荐时间，最多1个备选。"
            )
        if target_time and target_available is True:
            return (
                "已有档期事实："
                + "，".join(parts)
                + "。第一句可以确认客户问的时间可约，再推进确认或预约金。"
            )
        return (
            "已有档期事实："
            + "，".join(parts)
            + "。客户问有没有时间时，只推荐 recommended_slot，最多补1个 backup_slot；不要列完整时间表。"
        )

    slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
    if not slots:
        return ""
    target_status = target_time_status(slots, str(item.get("target_time") or ""), content)
    target_time = str(item.get("target_time") or target_status.get("target_time") or "").strip()
    target_available = item.get("target_time_available")
    if target_available is None:
        target_available = target_status.get("target_time_available")
    preferred_times = available_time_values({"new": slots.get("new")})
    if not preferred_times:
        preferred_times = available_time_values(slots)
    preferred_times = filter_times_by_preference(preferred_times, content) or preferred_times
    times = preferred_times[:2]
    date = str(item.get("date") or "").strip()
    store = str(item.get("store") or item.get("store_id") or "").strip()
    if not times:
        return f"已有档期事实：{date or '该日期'}暂未看到可直接引用的可约时间，不能说已约成功。"
    prefix = "已有档期事实："
    parts = []
    if store:
        parts.append(f"门店ID {store}")
    if date:
        parts.append(date)
    parts.append(f"推荐时间{'、'.join(times[:1])}")
    if len(times) > 1:
        parts.append(f"备选时间{times[1]}")
    if target_time and target_available is False:
        parts.append(f"客户问的{target_time}不在可约时间内")
        nearby = item.get("nearby_times") if isinstance(item.get("nearby_times"), list) else target_status.get("nearby_times") or []
        if nearby:
            parts.append(f"临近可选时间为{'、'.join(str(time) for time in nearby[:2])}")
        return prefix + "，".join(parts) + "。第一句必须说明客户问的具体时间暂未看到可约，不能说该时间可以约；再推荐时间，最多1个备选。"
    if target_time and target_available is True:
        parts.append(f"客户问的{target_time}可约")
        return prefix + "，".join(parts) + "。第一句可以直接确认该时间可约，再推进预约金或确认信息。"
    return prefix + "，".join(parts) + "。客户问有没有时间时，只推荐第一个可约时间，最多补1个备选；不要列完整时间表，也不要同轮追加payment_collection，除非客户本轮已经明确选定时间或要付款入口。"


def _store_scope_location_hints_for_reply(state: AgentState) -> list[str]:
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    values = [
        *customer_location_hint_texts(state, limit=6),
        basic.get("province"),
        basic.get("city") or basic.get("current_city"),
        basic.get("district") or basic.get("area_or_landmark") or basic.get("region"),
        state.get("confirmed_store_name") or state.get("store_name"),
    ]
    return [str(value).strip() for value in values if str(value or "").strip()]
