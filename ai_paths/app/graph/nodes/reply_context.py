from __future__ import annotations

from typing import Any

from app.graph.nodes.common import recent_assistant_replies
from app.graph.nodes.appointment_time_utils import available_time_values, filter_times_by_preference
from app.graph.nodes.memory_usage_policy import (
    memory_usage_policy_for_reply,
    should_suppress_profile_memory_for_reply,
)
from app.graph.message_send_policy import action_message_policy_for_model
from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_primary_task,
    planner_reply_strategy,
    planner_required_tools,
    planner_secondary_tasks,
    planner_task_views,
)
from app.graph.state import AgentState
from app.graph.runtime_turn_policy import should_suspend_appointment_context_for_current_turn
from app.policies.business_rules import load_business_rules
from app.policies.compliance_terms import (
    QUALIFICATION_CONTEXT_SAFE_NOTE,
    SERVICE_COMMITMENT_CONTEXT_SAFE_NOTE,
    UNSUPPORTED_QUALIFICATION_CONTEXT_TERMS,
    UNSUPPORTED_SERVICE_COMMITMENT_CONTEXT_TERMS,
)


def reply_user_payload_for_model(state: AgentState) -> dict[str, Any]:
    planner_views = planner_task_views(state)
    should_show_appointment_context = not should_suspend_appointment_context_for_current_turn(state, planner_views)
    suppress_profile_memory = should_suppress_profile_memory_for_reply(state)
    fact_envelope = {} if suppress_profile_memory else (state.get("fact_envelope") or {})
    primary_task = _sanitize_planner_context_for_reply(planner_primary_task(state))
    secondary_tasks = _sanitize_planner_context_for_reply(planner_secondary_tasks(state))
    required_tools = planner_required_tools(state)
    reply_strategy = _sanitize_planner_context_for_reply(planner_reply_strategy(state))
    handoff = planner_handoff(state)
    appointment_context = _appointment_context_for_model(state) if should_show_appointment_context else {}
    return {
        "content": state.get("normalized_content"),
        "conversation_history": [] if suppress_profile_memory else state.get("conversation_history", [])[-6:],
        "image_info": state.get("image_info", {}),
        "customer_profile": {} if suppress_profile_memory else state.get("customer_profile", {}),
        "customer_basic_info": {} if suppress_profile_memory else state.get("customer_basic_info", {}),
        "history_events": [] if suppress_profile_memory else state.get("history_events", [])[-8:],
        "memory_usage_policy": memory_usage_policy_for_reply(state),
        "recent_assistant_replies": [] if suppress_profile_memory else recent_assistant_replies(state, 4),
        "guardrail_result": state.get("guardrail_result", {}),
        "primary_task": {} if suppress_profile_memory else primary_task,
        "secondary_tasks": [] if suppress_profile_memory else secondary_tasks,
        "required_tools": [] if suppress_profile_memory else required_tools,
        "reply_strategy": {} if suppress_profile_memory else reply_strategy,
        "planner_decision": state.get("planner_decision", ""),
        "planner_stage": state.get("planner_stage", ""),
        "planner_sub_rule_id": state.get("planner_sub_rule_id", ""),
        "reply_constraints": state.get("reply_constraints", []),
        "handoff": {} if suppress_profile_memory else handoff,
        "appointment_context": {} if suppress_profile_memory else appointment_context,
        "customer_store_knowledge": _sanitize_planner_context_for_reply(_compact_store_knowledge(state.get("customer_store_knowledge") or {})),
        "sales_talk_reference": _sanitize_planner_context_for_reply(_compact_sales_talk_reference(state.get("sales_talk_reference") or {})),
        "action_message_policy": action_message_policy_for_model(state),
        "business_rules": load_business_rules(),
        "fact_envelope": fact_envelope,
        "fact_notes": _fact_notes_for_model(fact_envelope, content=str(state.get("normalized_content") or state.get("content") or "")),
    }


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
) -> list[str]:
    notes: list[str] = []
    structured_facts = fact_envelope.get("structured_facts") or {}
    if not isinstance(structured_facts, dict):
        structured_facts = {}

    recommended_store = structured_facts.get("recommended_store") or {}
    if isinstance(recommended_store, dict) and recommended_store.get("name"):
        notes.append("已有推荐门店事实，可优先按推荐门店回答。")
        if recommended_store.get("reason") == "distance_calculate_rank_1":
            notes.append("客户问附近或最近门店时，必须优先回答 distance_calculate 排序第一的推荐门店，不要泛泛列多家门店或反问客户选哪家。")

    store_lookup_status = structured_facts.get("store_lookup_status") or {}
    if isinstance(store_lookup_status, dict) and store_lookup_status.get("distance_lookup_required"):
        notes.append("客户在问距离或附近门店，但本轮没有真实距离结果；不要说最近、更近、几公里或几分钟，只能基于候选门店说明还需要按地图距离核对。")

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
            if item.get("type") == "available_time" and item.get("slots"):
                summary = _available_time_fact_note(item, content)
                notes.append(summary or "已有档期事实，可直接回答可约时间。")
                break

    professional_assist = structured_facts.get("professional_assist") or {}
    if isinstance(professional_assist, dict) and professional_assist.get("status") == "requested":
        notes.append("本轮已有专业同事协助事实；客户可见回复应先承接当前诉求，再说明会协助核对。")

    return notes[:6]


def _available_time_fact_note(item: dict[str, Any], content: str) -> str:
    slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
    if not slots:
        return ""
    preferred_times = available_time_values({"new": slots.get("new")})
    if not preferred_times:
        preferred_times = available_time_values(slots)
    preferred_times = filter_times_by_preference(preferred_times, content) or preferred_times
    times = preferred_times[:6]
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
    parts.append(f"可约时间包括{'、'.join(times)}")
    return prefix + "，".join(parts) + "。客户问有没有时间时，第一句必须先回答这些可约时间；可以结合上下文顺带推进10元预约金，但不能只发收款入口或只说继续查询。"


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
        "usage": "style_reference_only_not_business_fact",
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
