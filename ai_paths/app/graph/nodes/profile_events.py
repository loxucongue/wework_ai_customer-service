from __future__ import annotations

from typing import Any

from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.profile_event_text import event_impact, event_summary, event_type_for_intent
from app.graph.nodes.store_context import extract_city, extract_time_text
from app.graph.state import AgentState


def extract_event_updates(
    state: AgentState,
    profile_update: dict[str, Any],
    callbacks: Any,
) -> list[dict[str, Any]]:
    content = state.get("normalized_content") or ""
    intents = state.get("intents", [])
    if not intents and not profile_update:
        return []

    events: list[dict[str, Any]] = []
    for index, item in enumerate(intents[:3], start=1):
        if not isinstance(item, dict):
            continue
        event_type = event_type_for_intent(str(item.get("intent") or ""))
        if not event_type:
            continue
        facts = _event_facts(event_type, content, state, callbacks)
        events.append(_event_record(state, index, event_type, facts))

    if profile_update and not events:
        facts = _event_common_facts(content, state, callbacks)
        events.append(
            {
                "event_id": f"evt_{state.get('request_id', 'unknown')}_profile",
                "event_time": "",
                "event_type": "profile_update",
                "stage": state.get("route_result", {}).get("scene", "S3_deep_consult"),
                "summary": event_summary("profile_update", facts),
                "facts": facts,
                "impact": "补充客户画像，后续回复应承接已知需求和顾虑。",
                "confidence": 0.72,
            }
        )
    return events


def _event_record(state: AgentState, index: int, event_type: str, facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": f"evt_{state.get('request_id', 'unknown')}_{index}",
        "event_time": "",
        "event_type": event_type,
        "stage": state.get("route_result", {}).get("scene", "S3_deep_consult"),
        "summary": event_summary(event_type, facts),
        "facts": facts,
        "impact": event_impact(event_type),
        "confidence": 0.78,
    }


def _event_facts(event_type: str, content: str, state: AgentState, callbacks: Any) -> dict[str, Any]:
    image_info = state.get("image_info", {}) or {}
    project = callbacks.extract_project(content)
    common = _event_common_facts(content, state, callbacks)
    if event_type == "price_inquiry":
        return {
            **common,
            "project": callbacks.canonical_price_project(callbacks.contextual_price_project(state) or project),
            "price_focus": "价格咨询",
            "budget_sens": "high" if any(term in content for term in ["贵", "预算", "便宜", "太高"]) else "unknown",
            "seen_price": callbacks.extract_price_digits(content)[:3],
        }
    if event_type == "project_inquiry":
        return {
            **common,
            "project": project,
            "question_focus": "项目方向",
            "visible_concerns": image_info.get("visible_concerns", []),
            "image_desc": image_info.get("image_desc", ""),
            "project_directions": []
            if case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns)
            else callbacks.project_direction_names(state),
        }
    if event_type == "image_inquiry":
        return {
            **common,
            "image_type": image_info.get("image_type", ""),
            "image_intent": image_info.get("image_intent", ""),
            "body_part": image_info.get("body_part", ""),
            "visible_concerns": image_info.get("visible_concerns", []),
            "text_clues": image_info.get("text_clues", []),
        }
    if event_type == "trust_issue":
        return {**common, "concern": "正规性/服务保障", "trust_level": "low"}
    if event_type == "store_inquiry":
        return {**common, "city": extract_city(content), "location_focus": "门店/地址/路线", "matched_stores": _matched_store_names(state)}
    if event_type == "appoint_intent":
        return {
            **common,
            "intent_level": "medium",
            "preferred_time": extract_time_text(content) or _active_task_slot(state, "time"),
            "preferred_store": _active_task_slot(state, "store_name"),
            "preferred_date": _active_task_slot(state, "date"),
            "people_count": _active_task_slot(state, "people_count"),
        }
    if event_type == "after_sales":
        return {**common, "issue": "售后/恢复咨询", "severity": "unknown"}
    if event_type == "competitor_compare":
        return {**common, "compare_focus": "竞品/报价对比"}
    if event_type == "campaign_inquiry":
        return {**common, "campaign_focus": "活动/优惠咨询", "seen_price": callbacks.extract_price_digits(content)[:3]}
    if event_type == "human_request":
        return {**common, "request": "需要专业人士协助"}
    return common


def _event_common_facts(content: str, state: AgentState, callbacks: Any) -> dict[str, Any]:
    image_info = state.get("image_info", {}) or {}
    facts: dict[str, Any] = {}
    city = extract_city(content)
    if city:
        facts["city"] = city
    visible = image_info.get("visible_concerns") or callbacks.known_visible_concerns(state)
    if visible:
        facts["visible_concerns"] = list(visible[:6])
    directions = callbacks.project_direction_names(state)
    if directions:
        facts["project_directions"] = directions[:3]
    customer_goal = _customer_goal_from_content(content)
    if customer_goal:
        facts["customer_goal"] = customer_goal
    if any(term in content for term in ["预算", "贵", "便宜", "多少钱", "价格"]):
        facts["budget_or_price_signal"] = content[:80]
    return facts


def _customer_goal_from_content(content: str) -> str:
    if any(term in content for term in ["斑", "色沉", "肤色不均"]):
        return "改善斑点/色沉/肤色不均"
    if any(term in content for term in ["毛孔", "出油", "黑头"]):
        return "改善毛孔出油"
    if any(term in content for term in ["痘印", "痘坑", "闭口"]):
        return "改善痘印痘坑/闭口"
    if any(term in content for term in ["暗沉", "提亮", "美白", "变白"]):
        return "提亮肤色"
    if any(term in content for term in ["松弛", "法令纹", "抗衰"]):
        return "抗衰紧致"
    return ""


def _active_task_slot(state: AgentState, key: str) -> str:
    active_task = state.get("active_task") or {}
    if not isinstance(active_task, dict):
        return ""
    slots = active_task.get("known_slots")
    if not isinstance(slots, dict):
        return ""
    return str(slots.get(key) or "").strip()


def _matched_store_names(state: AgentState) -> list[str]:
    lookup = (state.get("tool_results") or {}).get("store_lookup") or {}
    stores = lookup.get("stores") if isinstance(lookup, dict) else []
    result: list[str] = []
    for store in stores if isinstance(stores, list) else []:
        if isinstance(store, dict):
            name = str(store.get("name") or "").strip()
            if name and name not in result:
                result.append(name)
    return result[:5]

