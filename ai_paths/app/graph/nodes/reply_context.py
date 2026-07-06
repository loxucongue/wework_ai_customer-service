from __future__ import annotations

from typing import Any

from app.graph.nodes.common import recent_assistant_replies
from app.graph.nodes.appointment_time_utils import available_time_values, filter_times_by_preference, target_time_status
from app.graph.nodes.contextual_short_message import short_message_context_for_model
from app.graph.nodes.current_turn_context import build_current_turn_context
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.nodes.memory_usage_policy import (
    memory_usage_policy_for_reply,
)
from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_required_tools,
    planner_task_views,
)
from app.graph.state import AgentState
from app.graph.runtime_turn_policy import should_suspend_appointment_context_for_current_turn
from app.policies.business_rules import reply_business_rules_for_model
from app.policies.compliance_terms import (
    QUALIFICATION_CONTEXT_SAFE_NOTE,
    SERVICE_COMMITMENT_CONTEXT_SAFE_NOTE,
    UNSUPPORTED_QUALIFICATION_CONTEXT_TERMS,
    UNSUPPORTED_SERVICE_COMMITMENT_CONTEXT_TERMS,
)
from app.services.risk_hold import health_risk_hold


def reply_user_payload_for_model(state: AgentState) -> dict[str, Any]:
    planner_views = planner_task_views(state)
    should_show_appointment_context = not should_suspend_appointment_context_for_current_turn(state, planner_views)
    suppress_profile_memory = False
    fact_envelope = {} if suppress_profile_memory else (state.get("fact_envelope") or {})
    required_tools = planner_required_tools(state)
    handoff = planner_handoff(state)
    appointment_context = _appointment_context_for_model(state) if should_show_appointment_context else {}
    sent_message_summary = {} if suppress_profile_memory else sent_message_summary_for_model(state)
    sop_progress = _sop_progress_for_reply(
        state,
        sent_message_summary=sent_message_summary,
        fact_envelope=state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else fact_envelope,
    )
    current_turn_context = {} if suppress_profile_memory else build_current_turn_context(
        state,
        sent_message_summary=sent_message_summary,
    )
    risk_hold = {} if suppress_profile_memory else health_risk_hold(state)
    reply_mode = str(sop_progress.get("recommended_reply_mode") or "normal_answer").strip() or "normal_answer"
    return {
        "content": state.get("normalized_content"),
        "conversation_history": [] if suppress_profile_memory else state.get("conversation_history", [])[-20:],
        "short_message_context": {} if suppress_profile_memory else short_message_context_for_model(
            content=str(state.get("normalized_content") or state.get("content") or ""),
            conversation_history=state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else [],
            sent_message_summary=sent_message_summary,
        ),
        "image_info": state.get("image_info", {}),
        "customer_profile": {} if suppress_profile_memory else state.get("customer_profile", {}),
        "customer_basic_info": {} if suppress_profile_memory else state.get("customer_basic_info", {}),
        "history_events": [] if suppress_profile_memory else state.get("history_events", [])[-8:],
        "memory_usage_policy": memory_usage_policy_for_reply(state),
        "recent_assistant_replies": [] if suppress_profile_memory else recent_assistant_replies(state, 4),
        "guardrail_result": state.get("guardrail_result", {}),
        "required_tools": [] if suppress_profile_memory else required_tools,
        "planner_decision": state.get("planner_decision", ""),
        "planner_stage": state.get("planner_stage", ""),
        "planner_sub_rule_id": state.get("planner_sub_rule_id", ""),
        "conversion_stage": state.get("conversion_stage", ""),
        "customer_type": state.get("customer_type", ""),
        "main_blocker": state.get("main_blocker", ""),
        "next_step": state.get("next_step", ""),
        "reply_constraints": state.get("reply_constraints", []),
        "planner_tool_policy_violations": _compact_planner_violations(state.get("tool_policy_violations", [])),
        "handoff": {} if suppress_profile_memory else handoff,
        "appointment_context": {} if suppress_profile_memory else appointment_context,
        "current_turn_context": current_turn_context,
        "risk_hold": risk_hold,
        "store_scope_summary": _sanitize_planner_context_for_reply(_compact_store_knowledge(state.get("customer_store_knowledge") or {})),
        "sent_message_summary": sent_message_summary,
        "reply_mode": reply_mode,
        "sop_progress": sop_progress,
        "business_rules": reply_business_rules_for_model(
            stage=str(state.get("planner_stage") or ""),
            sub_rule_id=str(state.get("planner_sub_rule_id") or ""),
        ),
        "fact_envelope": fact_envelope,
        "fact_notes": _fact_notes_for_model(
            fact_envelope,
            content=str(state.get("normalized_content") or state.get("content") or ""),
            sent_message_summary=sent_message_summary,
        ),
    }


def _sop_progress_for_reply(
    state: AgentState,
    *,
    sent_message_summary: dict[str, Any],
    fact_envelope: dict[str, Any],
) -> dict[str, Any]:
    sent_categories = _sent_sop_like_categories(state, sent_message_summary=sent_message_summary)
    candidates = _sop_next_candidates(
        state,
        sent_categories=sent_categories,
        fact_envelope=fact_envelope,
    )
    if not sent_categories and not candidates:
        return {}
    reply_mode = "sop_sequence" if _should_use_sop_sequence(state, candidates) else "normal_answer"
    return {
        "recommended_reply_mode": reply_mode,
        "sent_categories": sent_categories,
        "next_candidates": candidates[:3],
        "usage": "normal_answer 最多短答并轻推一步；sop_sequence 允许 4-8 条短消息组成成交流程包。回答当前问题后，只能从 next_candidates 里选择一个主目标推进；不要照抄 SOP 模板，不要一次推进多个动作。",
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


def _sop_next_candidates(
    state: AgentState,
    *,
    sent_categories: list[str],
    fact_envelope: dict[str, Any],
) -> list[dict[str, str]]:
    sent = set(sent_categories)
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    store_facts = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    case_facts = structured.get("case_facts") if isinstance(structured.get("case_facts"), list) else []
    candidates: list[dict[str, str]] = []

    if store_facts and "store_address" not in sent:
        candidates.append(
            {
                "category": "store_address",
                "purpose": "把客户的位置兴趣落到具体可到店门店。",
                "how_to_push": "发真实门店位置后，问客户哪家或哪个区域方便。",
            }
        )
    if store_facts and "effect_case" not in sent:
        candidates.append(
            {
                "category": "effect_case",
                "purpose": "门店已承接后，铺垫斑点检测、操作时间和效果信心。",
                "how_to_push": "用一句话说明到店老师一对一看斑点，顺手问斑点多久或是否要看同类效果。",
            }
        )
    if case_facts and "effect_case" not in sent:
        candidates.append(
            {
                "category": "effect_case",
                "purpose": "客户在意效果时，用案例事实建立信心。",
                "how_to_push": "先回答效果顾虑，再带客户看门店或活动价。",
            }
        )
    if "activity_intro" not in sent and _should_offer_activity_candidate(state, sent):
        candidates.append(
            {
                "category": "activity_intro",
                "purpose": "客户已进入咨询后，说明周年庆活动价和权益。",
                "how_to_push": "用短句带出268、10元预约金、到店抵扣，不要写说明书。",
            }
        )
    if "deposit_push" not in sent and _should_offer_deposit_candidate(state):
        candidates.append(
            {
                "category": "deposit_push",
                "purpose": "客户已有到店或报名意向时，推进10元预约金锁名额。",
                "how_to_push": "说明10元用于锁活动名额，到店抵扣，不做退10元。",
            }
        )
    return _dedupe_candidate_categories(candidates)


def _should_offer_activity_candidate(state: AgentState, sent: set[str]) -> bool:
    if "price_quote" in sent or "activity_intro" in sent:
        return False
    values = " ".join(
        str(state.get(key) or "")
        for key in ("planner_stage", "planner_sub_rule_id", "conversion_stage", "customer_type", "main_blocker", "next_step")
    ).lower()
    return any(marker in values for marker in ("s3", "price", "activity", "objection", "store_match", "effect"))


def _should_offer_deposit_candidate(state: AgentState) -> bool:
    values = " ".join(
        str(state.get(key) or "")
        for key in ("conversion_stage", "customer_type", "main_blocker", "next_step", "planner_sub_rule_id")
    ).lower()
    return any(marker in values for marker in ("deposit", "send_deposit", "time_confirm", "appointment", "price"))


def _dedupe_candidate_categories(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        category = str(item.get("category") or "").strip()
        if not category or category in seen:
            continue
        seen.add(category)
        output.append(item)
    return output


def _should_use_sop_sequence(state: AgentState, candidates: list[dict[str, str]]) -> bool:
    if not candidates:
        return False
    stage = str(state.get("planner_stage") or "").upper()
    sub_rule = str(state.get("planner_sub_rule_id") or "").lower()
    conversion_stage = str(state.get("conversion_stage") or "").lower()
    next_step = str(state.get("next_step") or "").lower()
    if stage == "S4":
        return False
    if any(marker in sub_rule for marker in ("parking", "business_hours", "appointment_change", "appointment_cancel", "after_sales")):
        return False
    if any(marker in next_step for marker in ("handoff", "no_action")):
        return False
    if conversion_stage in {"interest_capture", "objection_resolution", "store_match", "deposit_push"}:
        return True
    candidate_categories = {str(item.get("category") or "") for item in candidates}
    return bool(candidate_categories & {"store_address", "effect_case", "activity_intro", "deposit_push"})


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
        if recommended_store.get("reason") == "distance_calculate_rank_1":
            notes.append("客户问附近或最近门店时，必须优先回答 distance_calculate 排序第一的推荐门店；距离只用于排序，客户可见回复不要输出几公里、几分钟、车程或步行时长。")

    store_lookup_status = structured_facts.get("store_lookup_status") or {}
    if isinstance(store_lookup_status, dict) and store_lookup_status.get("distance_lookup_required"):
        notes.append("客户在问距离或附近门店，但本轮没有真实距离排序结果；不要说最近、更近、几公里或几分钟，只能基于候选门店说明还需要按地图距离核对。")

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


def _appointment_context_for_model(state: AgentState) -> dict[str, Any]:
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    context: dict[str, Any] = {}
    for source_key, target_key in (
        ("store_id", "store_id"),
        ("store_name", "store_name"),
        ("date", "date"),
        ("appointment_date", "date"),
        ("time", "time"),
        ("appointment_time", "time"),
        ("people_count", "people_count"),
    ):
        value = appointment_cache.get(source_key)
        text = str(value or "").strip()
        if text and target_key not in context:
            context[target_key] = text
    return context


def _compact_store_knowledge(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    stores = raw.get("stores") if isinstance(raw.get("stores"), list) else []
    return {
        "source": raw.get("source"),
        "store_count": raw.get("store_count", len(stores)),
        "snapshot_generated_at": raw.get("snapshot_generated_at"),
        "store_scope_error": raw.get("store_scope_error") or raw.get("error") or "",
        "cache": raw.get("cache") if isinstance(raw.get("cache"), dict) else {},
        "missing_snapshot_store_ids": raw.get("missing_snapshot_store_ids", []),
        "province_counts": _province_counts(stores),
    }


def _province_counts(stores: list[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for store in stores:
        if not isinstance(store, dict):
            continue
        province = str(store.get("province") or "").strip() or "未识别省份"
        counts[province] = counts.get(province, 0) + 1
    return [
        {"province": province, "store_count": count}
        for province, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
