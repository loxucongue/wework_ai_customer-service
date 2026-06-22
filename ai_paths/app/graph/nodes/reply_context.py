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


SYSTEM_ACTION_EVENT_TYPES = {
    "store_address_sent",
    "case_image_sent",
    "book_order_sent",
    "handoff_requested",
    "offer_explained",
    "deposit_explained",
}


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
        "history_events": _history_events_for_reply(state, suppress_profile_memory),
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
        "fact_notes": _fact_notes_for_model(state, fact_envelope),
    }
    return clean_model_value(payload, max_string_chars=1800)


def _history_events_for_reply(state: AgentState, suppress_profile_memory: bool) -> list[dict[str, Any]]:
    events = [item for item in state.get("history_events", []) if isinstance(item, dict)]
    if not suppress_profile_memory:
        return events[-8:]

    # 低信息开场只隐藏客户软画像；系统已经执行过的动作仍要保留，
    # 否则模型会重复发门店卡片、案例图或完整复述报价。
    return [
        item
        for item in events
        if str(item.get("event_type") or "").strip() in SYSTEM_ACTION_EVENT_TYPES
    ][-8:]


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
    selected_store_policy = _selected_store_policy_for_reply(compact_structured)
    usable_facts = list(envelope.get("usable_facts") or [])[:6]
    if selected_store_policy:
        compact_structured = _restrict_store_candidates_for_reply(compact_structured, selected_store_policy)
        usable_facts = _restrict_store_usable_facts_for_reply(usable_facts, selected_store_policy)

    return {
        "usable_facts": usable_facts,
        "missing_facts": list(envelope.get("missing_facts") or [])[:4],
        "risky_facts": list(envelope.get("risky_facts") or [])[:4],
        "unsupported_claims": list(envelope.get("unsupported_claims") or [])[:4],
        "structured_facts": compact_structured,
    }


def _selected_store_policy_for_reply(structured: dict[str, Any]) -> dict[str, str]:
    recommended = structured.get("recommended_store")
    status = structured.get("store_lookup_status")
    if not isinstance(recommended, dict) or not isinstance(status, dict):
        return {}
    if str(status.get("data_authority") or "").strip().lower() != "platform":
        return {}
    if bool(status.get("needs_area_or_landmark")) or bool(status.get("no_store_match_confirmed")):
        return {}
    store_id = str(recommended.get("id") or recommended.get("store_id") or "").strip()
    store_name = str(recommended.get("name") or "").strip()
    if not store_id and not store_name:
        return {}
    granularity = str(status.get("location_granularity") or "").strip()
    distance_required = bool(status.get("distance_lookup_required"))
    distance_ok = str(status.get("distance_lookup_status") or "").strip().lower() == "ok"
    if distance_required or distance_ok or granularity in {"area_or_landmark", "store_name"}:
        return {"store_id": store_id, "store_name": store_name}
    return {}


def _restrict_store_candidates_for_reply(
    structured: dict[str, Any],
    selected: dict[str, str],
) -> dict[str, Any]:
    result = dict(structured)
    store_id = str(selected.get("store_id") or "").strip()
    store_name = str(selected.get("store_name") or "").strip()
    recommended = result.get("recommended_store") if isinstance(result.get("recommended_store"), dict) else {}
    if recommended:
        result["store_facts"] = [recommended]
    result["distance_facts"] = [
        item
        for item in (result.get("distance_facts") or [])
        if isinstance(item, dict)
        and (
            (store_id and str(item.get("store_id") or item.get("id") or "").strip() == store_id)
            or (store_name and str(item.get("name") or "").strip() == store_name)
        )
    ][:1]
    scripts = result.get("sales_talk_scripts")
    if isinstance(scripts, list):
        conflicting_names = {
            str(item.get("name") or "").strip()
            for item in (structured.get("store_facts") or [])
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
            and str(item.get("name") or "").strip() != store_name
        }
        result["sales_talk_scripts"] = [
            item
            for item in scripts
            if isinstance(item, dict)
            and not any(
                name in " ".join(str(item.get(key) or "") for key in ("matched_question", "business_logic", "sales_script"))
                for name in conflicting_names
            )
        ]
    result["store_candidate_policy"] = {
        "selected_store_only": True,
        "selected_store_id": store_id,
        "selected_store_name": store_name,
        "reason": "distance_or_specific_store_recommendation",
    }
    return result


def _restrict_store_usable_facts_for_reply(facts: list[Any], selected: dict[str, str]) -> list[Any]:
    store_name = str(selected.get("store_name") or "").strip()
    if not store_name:
        return facts
    filtered: list[Any] = []
    for item in facts:
        text = str(item or "")
        if text.startswith("store_lookup: matched_stores="):
            continue
        if text.startswith("distance_lookup:"):
            parts = [part.strip() for part in text.removeprefix("distance_lookup:").split(";")]
            selected_parts = [part for part in parts if store_name in part]
            if selected_parts:
                filtered.append("distance_lookup: " + "; ".join(selected_parts[:1]))
            continue
        filtered.append(item)
    return filtered[:6]


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
    if source_key == "sales_talk_scripts":
        return _compact_sales_talk_style_reference(item)

    keep_by_source: dict[str, tuple[str, ...]] = {
        "store_facts": (
            "id",
            "store_id",
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
            "status_code",
            "shore_show_code",
            "schedule_status",
            "plan_status",
            "is_pause",
            "pause_start",
            "pause_end",
            "is_public",
            "status_summary",
        ),
        "recommended_store": (
            "id",
            "store_id",
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
            "status_code",
            "shore_show_code",
            "schedule_status",
            "plan_status",
            "is_pause",
            "pause_start",
            "pause_end",
            "is_public",
            "status_summary",
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
            "data_authority",
            "distance_lookup_required",
            "distance_lookup_status",
            "distance_origin",
            "planned_distance_origin",
        ),
        "distance_facts": ("store_id", "id", "name", "address", "distance_text"),
        "case_facts": ("source", "title", "content", "image_url"),
        "knowledge_facts": ("source", "title", "content"),
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


def _compact_sales_talk_style_reference(item: dict[str, Any]) -> dict[str, Any]:
    script = str(item.get("sales_script") or "").strip()
    hints: list[str] = []
    if len(script) <= 36:
        hints.append("短句承接")
    if any(term in script for term in ("哈", "呀", "这边", "您")):
        hints.append("微信口语")
    if any(term in script for term in ("哪个", "哪里", "方便", "发您", "给您")):
        hints.append("只推进一个自然下一步")
    if not hints:
        hints.append("自然销售语气")
    return {
        "source": "sales_talk_qa",
        "style_only": True,
        "style_reference": "、".join(dict.fromkeys(hints)),
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
        return value
    return value


def _fact_notes_for_model(
    state: AgentState,
    fact_envelope: dict[str, Any],
) -> list[str]:
    notes: list[str] = []
    structured_facts = fact_envelope.get("structured_facts") or {}
    if not isinstance(structured_facts, dict):
        structured_facts = {}

    recommended_store = structured_facts.get("recommended_store") or {}
    if isinstance(recommended_store, dict) and recommended_store.get("name"):
        notes.append(
            f"已有按工具事实选定的推荐门店：{recommended_store.get('name')}；本轮只能推荐这家，不要从候选门店或话术样例里改选其他门店。"
        )
    store_status = structured_facts.get("store_lookup_status") or {}
    if (
        isinstance(recommended_store, dict)
        and recommended_store.get("name")
        and isinstance(store_status, dict)
        and str(store_status.get("location_granularity") or "") in {"area_or_landmark", "store_name"}
    ):
        notes.append("客户已给具体区/地标或明确门店，本轮可直接推荐这家真实门店，不要再追问方向。")
    store_policy = structured_facts.get("store_candidate_policy") or {}
    if isinstance(store_policy, dict) and store_policy.get("selected_store_only"):
        notes.append(
            f"本轮门店事实已收敛为唯一推荐门店：{store_policy.get('selected_store_name') or recommended_store.get('name') or ''}；不要提其他门店为更近或更方便。"
        )
    recent_store_note = _recent_store_address_note_for_model(state, structured_facts)
    if recent_store_note:
        notes.append(recent_store_note)
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
    if isinstance(store_status, dict) and store_status.get("area_or_landmark_direct_store_missing"):
        area = str(store_status.get("area_or_landmark") or "").strip()
        if area:
            notes.append(f"门店工具未查到{area}本区/该地标内的直营门店；可以基于真实候选门店或距离结果推荐附近门店，但不要说“{area}有门店”。")

    sales_talk_scripts = structured_facts.get("sales_talk_scripts") or []
    if isinstance(sales_talk_scripts, list) and sales_talk_scripts:
        notes.append("sales_talk_scripts 只提供语气和短句节奏，不提供事实；其中的门店、价格、档期、效果判断必须以工具事实为准，不能照搬。")

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
                notes.append("已有档期事实，可直接回答可约时间。")
                break
        for item in appointment_facts:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "appointment_opening" and item.get("status") in {"created", "dry_run_created", "reused_open_order"} and item.get("order_id"):
                notes.append("已有真实预约金订单，可直接解释10元预约金并输出 book_order。")
                break

    professional_assist = structured_facts.get("professional_assist") or {}
    if isinstance(professional_assist, dict) and professional_assist.get("status") == "requested":
        notes.append("本轮已有专业同事协助事实；客户可见回复应先承接当前诉求，再说明会协助核对。")

    return notes[:6]


def _recent_store_address_note_for_model(
    state: AgentState,
    structured_facts: dict[str, Any],
) -> str:
    recommended_store = structured_facts.get("recommended_store") or {}
    if not isinstance(recommended_store, dict):
        recommended_store = {}
    store_policy = structured_facts.get("store_candidate_policy") or {}
    if not isinstance(store_policy, dict):
        store_policy = {}

    target_id = str(
        recommended_store.get("id")
        or recommended_store.get("store_id")
        or store_policy.get("selected_store_id")
        or ""
    ).strip()
    target_name = str(
        recommended_store.get("name")
        or store_policy.get("selected_store_name")
        or ""
    ).strip()
    if not target_id and not target_name:
        return ""

    events = [item for item in state.get("history_events", []) if isinstance(item, dict)]
    for event in reversed(events[-12:]):
        if str(event.get("event_type") or "").strip() != "store_address_sent":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        sent_id = str(facts.get("store_id") or "").strip()
        sent_name = str(facts.get("store_name") or "").strip()
        same_store = bool(target_id and sent_id and target_id == sent_id) or bool(
            target_name and sent_name and target_name == sent_name
        )
        if not same_store:
            continue
        store_label = target_name or sent_name or target_id
        return (
            f"客户最近已收到{store_label}的门店卡片；如果本轮只是问哪家最近、哪家更方便或是不是这家，"
            "直接说明就是刚刚发的这家，不要重复发卡片；如果客户明确说再发地址、位置、导航或发我，则可以再次输出 store_address。"
        )
    return ""


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
