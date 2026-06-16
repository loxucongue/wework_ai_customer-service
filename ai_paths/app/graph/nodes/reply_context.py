from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.common import clean_model_value, recent_assistant_replies
from app.graph.nodes.memory_usage_policy import (
    memory_usage_policy_for_reply,
    order_session_state,
    should_suppress_profile_memory_for_reply,
)
from app.graph.planner.runtime_plan import (
    planner_handoff,
    planner_primary_task,
    planner_reply_strategy,
    planner_required_tools,
    planner_secondary_tasks,
    planner_sop_stage,
    planner_sop_stage_rules,
    planner_sop_step,
    planner_task_views,
)
from app.graph.state import AgentState
from app.graph.runtime_turn_policy import should_suspend_appointment_context_for_current_turn
from app.policies.compliance_terms import (
    QUALIFICATION_CONTEXT_SAFE_NOTE,
    SERVICE_COMMITMENT_CONTEXT_SAFE_NOTE,
    UNSUPPORTED_QUALIFICATION_CONTEXT_TERMS,
    UNSUPPORTED_SERVICE_COMMITMENT_CONTEXT_TERMS,
)
from app.policies.s10_offer import attach_s10_offer_facts, s10_offer_context


def reply_user_payload_for_model(state: AgentState) -> dict[str, Any]:
    planner_views = planner_task_views(state)
    should_show_appointment_context = not should_suspend_appointment_context_for_current_turn(state, planner_views)
    suppress_profile_memory = should_suppress_profile_memory_for_reply(state)
    fact_envelope = _compact_fact_envelope_for_reply(
        attach_s10_offer_facts(state.get("fact_envelope") or {})
    )
    primary_task = _sanitize_planner_context_for_reply(planner_primary_task(state))
    secondary_tasks = _sanitize_planner_context_for_reply(planner_secondary_tasks(state))
    required_tools = planner_required_tools(state)
    reply_strategy = _sanitize_planner_context_for_reply(planner_reply_strategy(state))
    handoff = planner_handoff(state)
    sop_stage = planner_sop_stage(state)
    sop_step = planner_sop_step(state)
    sop_stage_rules = _sanitize_planner_context_for_reply(planner_sop_stage_rules(state))
    appointment_context = _appointment_context_for_model(state) if should_show_appointment_context else {}
    order_session = order_session_state(state)
    payload = {
        "content": state.get("normalized_content"),
        "conversation_history": [] if suppress_profile_memory else state.get("conversation_history", [])[-6:],
        "image_info": state.get("image_info", {}),
        "customer_profile": {} if suppress_profile_memory else state.get("customer_profile", {}),
        "customer_basic_info": {} if suppress_profile_memory else state.get("customer_basic_info", {}),
        "history_events": [] if suppress_profile_memory else state.get("history_events", [])[-8:],
        "memory_usage_policy": memory_usage_policy_for_reply(state),
        "order_session": order_session,
        "active_offer_context": _compact_active_offer_context(),
        "sop_stage": sop_stage,
        "sop_step": sop_step,
        "sop_stage_rules": sop_stage_rules,
        "recent_assistant_replies": recent_assistant_replies(state, 4),
        "recent_image_urls": _recent_image_urls(state),
        "guardrail_result": state.get("guardrail_result", {}),
        "primary_task": primary_task,
        "secondary_tasks": secondary_tasks,
        "required_tools": required_tools,
        "reply_strategy": reply_strategy,
        "scene_guidance_context": _sanitize_planner_context_for_reply(state.get("scene_guidance_context", [])),
        "handoff": handoff,
        "appointment_context": appointment_context,
        "fact_envelope": fact_envelope,
        "fact_notes": _fact_notes_for_model(fact_envelope),
    }
    return clean_model_value(payload, max_string_chars=1800)


def _compact_active_offer_context() -> dict[str, Any]:
    offer = s10_offer_context()
    return {
        "marketing_activity_name": offer.get("marketing_activity_name"),
        "customer_visible_project_name": offer.get("customer_visible_project_name"),
        "new_customer_price": offer.get("new_customer_price"),
        "reservation_deposit": offer.get("reservation_deposit"),
        "tail_payment": offer.get("tail_payment"),
        "original_price": offer.get("original_price"),
        "quota": offer.get("quota"),
        "package_items": offer.get("package_items"),
        "signup_rule": offer.get("signup_rule"),
        "hard_close_benefit": offer.get("hard_close_benefit"),
        "customer_visible_constraints": [
            "只说周年庆活动，不说内部活动名或项目编码",
            "不向客户解释老客报价阈值规则",
            "客户类型和老客价格必须以系统事实为准",
        ],
    }


def _compact_fact_envelope_for_reply(fact_envelope: dict[str, Any]) -> dict[str, Any]:
    envelope = dict(fact_envelope or {})
    structured = envelope.get("structured_facts") if isinstance(envelope.get("structured_facts"), dict) else {}
    compact_structured: dict[str, Any] = {}

    for key, limit in (
        ("store_facts", 5),
        ("distance_facts", 5),
        ("case_facts", 3),
        ("knowledge_facts", 3),
        ("sales_talk_scripts", 3),
        ("appointment_facts", 4),
        ("customer_profile_facts", 1),
    ):
        value = structured.get(key)
        if isinstance(value, list):
            compact_structured[key] = [_compact_fact_item(item, key) for item in value[:limit] if isinstance(item, dict)]

    for key in ("recommended_store", "store_lookup_status", "professional_assist"):
        value = structured.get(key)
        if isinstance(value, dict):
            compact_structured[key] = _compact_fact_item(value, key)

    compact_structured["price_facts"] = _compact_price_facts(structured.get("price_facts"))
    compact_structured["active_offer_context"] = _compact_active_offer_context()

    order_facts = structured.get("customer_order_facts")
    if isinstance(order_facts, list) and order_facts:
        latest = next((item for item in order_facts if isinstance(item, dict)), {})
        if latest:
            compact_structured["latest_customer_order_fact"] = {
                "status": latest.get("status"),
                "store_name": latest.get("store_name"),
                "appointment_time": latest.get("appointment_time"),
                "amount_for_quote": latest.get("amount_for_quote"),
            }

    return {
        "usable_facts": list(envelope.get("usable_facts") or [])[:6],
        "missing_facts": list(envelope.get("missing_facts") or [])[:4],
        "risky_facts": list(envelope.get("risky_facts") or [])[:4],
        "unsupported_claims": list(envelope.get("unsupported_claims") or [])[:4],
        "structured_facts": compact_structured,
    }


def _compact_price_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compact: list[dict[str, Any]] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "rule_id": item.get("rule_id"),
                "quote_type": item.get("quote_type"),
                "total_price": item.get("total_price"),
                "prepay_amount": item.get("prepay_amount"),
                "tail_amount": item.get("tail_amount"),
                "display_price": item.get("display_price"),
                "conditions": item.get("conditions"),
                "rule_note": item.get("rule_note"),
            }
        )
    return compact


def _compact_fact_item(item: dict[str, Any], source_key: str) -> dict[str, Any]:
    keep_by_source: dict[str, tuple[str, ...]] = {
        "store_facts": (
            "name",
            "address",
            "business_hours",
            "distance",
            "distance_text",
            "route_hint",
            "city",
            "district",
            "map_url",
            "parking_name",
            "parking_address",
            "parking_link",
            "detail_source",
            "has_detail",
        ),
        "recommended_store": (
            "name",
            "address",
            "business_hours",
            "distance",
            "distance_text",
            "route_hint",
            "city",
            "district",
            "map_url",
            "parking_name",
            "parking_address",
            "parking_link",
            "detail_source",
            "has_detail",
        ),
        "store_lookup_status": (
            "status",
            "query",
            "city",
            "area_or_landmark",
            "location_granularity",
            "location_preference",
            "city_store_count",
            "has_city_store_candidates",
            "needs_area_or_landmark",
            "no_store_match_confirmed",
            "count",
            "error",
        ),
        "distance_facts": ("name", "address", "distance_text"),
        "case_facts": ("source", "title", "content", "image_url"),
        "knowledge_facts": ("source", "title", "content"),
        "sales_talk_scripts": ("matched_question", "business_logic", "sales_script"),
        "appointment_facts": ("type", "store_name", "date", "time", "slots", "status", "summary"),
        "customer_profile_facts": ("kind", "kind_text", "is_old_customer", "source", "fallback_reason", "pricing_note"),
        "professional_assist": ("status", "task_type", "reason", "policy_hint"),
    }
    keys = keep_by_source.get(source_key, tuple(item.keys()))
    compact: dict[str, Any] = {}
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    return compact


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
        return value
    return value


def _fact_notes_for_model(
    fact_envelope: dict[str, Any],
) -> list[str]:
    notes: list[str] = []
    structured_facts = fact_envelope.get("structured_facts") or {}
    if not isinstance(structured_facts, dict):
        structured_facts = {}

    recommended_store = structured_facts.get("recommended_store") or {}
    if isinstance(recommended_store, dict) and recommended_store.get("name"):
        notes.append("已有推荐门店事实，可优先按推荐门店回答。")
    store_status = structured_facts.get("store_lookup_status") or {}
    if isinstance(store_status, dict) and store_status.get("needs_area_or_landmark"):
        city = str(store_status.get("city") or "").strip()
        if city:
            notes.append(f"客户只提供了城市{city}，不要输出具体门店地址；先追问区、地标、机场或商圈。")
        else:
            notes.append("客户位置不完整，不要输出具体门店地址；先追问城市/区/地标。")
    if isinstance(store_status, dict) and store_status.get("no_store_match_confirmed"):
        city = str(store_status.get("city") or "").strip()
        if city:
            notes.append(f"门店工具未匹配到{city}本地门店；不要编门店，询问客户是否接受附近城市或常去城市。")
        else:
            notes.append("门店工具未匹配到本地门店；不要编门店，询问客户是否接受附近城市或常去城市。")

    sales_talk_scripts = structured_facts.get("sales_talk_scripts") or []
    if isinstance(sales_talk_scripts, list) and sales_talk_scripts:
        notes.append("已有销冠话术骨架；最终回复应优先贴近 sales_talk_scripts.sales_script 的短句节奏。")

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
                notes.append("已有档期事实，可直接回答可约时间。")
                break

    professional_assist = structured_facts.get("professional_assist") or {}
    if isinstance(professional_assist, dict) and professional_assist.get("status") == "requested":
        notes.append("本轮已有专业同事协助事实；客户可见回复应先承接当前诉求，再说明会协助核对。")

    return notes[:6]


def _appointment_context_for_model(state: AgentState) -> dict[str, Any]:
    session = order_session_state(state)
    context: dict[str, Any] = {}
    for session_key, target_key in (
        ("confirmed_store_id", "store_id"),
        ("confirmed_store_name", "store_name"),
        ("visit_date", "date"),
        ("visit_time", "time"),
    ):
        value = session.get(session_key) if isinstance(session, dict) else ""
        text = str(value or "").strip()
        if text and target_key not in context:
            context[target_key] = text
    return context


def _recent_image_urls(state: AgentState, *, limit: int = 6) -> list[str]:
    urls: list[str] = []

    def append_urls(text: str) -> None:
        for match in re.finditer(r"https?://[^\s<>'\")]+", text or ""):
            url = match.group(0).strip()
            if url and url not in urls:
                urls.append(url)

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if str(value.get("type") or "").strip() == "image":
                content = value.get("content")
                if isinstance(content, str):
                    append_urls(content)
                elif isinstance(content, dict):
                    append_urls(str(content.get("url") or content.get("image_url") or ""))
            for item in value.values():
                collect(item)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if isinstance(value, str):
            append_urls(value)

    collect(state.get("conversation_history") or [])
    collect(state.get("reply_messages") or [])
    return urls[-limit:]
