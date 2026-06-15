from __future__ import annotations

from typing import Callable

from app.graph.nodes.common import clean_model_value
from app.graph.nodes.case_context import case_request_lacks_specific_context
from app.graph.nodes.memory_usage_policy import should_suppress_profile_memory_for_reply
from app.graph.nodes.profile_event_text import event_impact, event_summary, event_type_for_intent
from app.graph.nodes.store_context import extract_city, extract_time_text
from app.graph.planner.runtime_plan import planner_scene, planner_task_views
from app.graph.state import AgentState


def extract_event_updates(
    state: AgentState,
    profile_update: dict[str, object],
    *,
    canonical_price_project: Callable[[str], str],
    contextual_price_project: Callable[[AgentState], str],
    extract_price_digits: Callable[[str], list[str]],
    extract_project: Callable[[str], str],
    known_visible_concerns: Callable[[AgentState], list[str]],
    project_direction_names: Callable[[AgentState], list[str]],
) -> list[dict[str, object]]:
    if should_suppress_profile_memory_for_reply(state):
        return []

    content = str(state.get("normalized_content") or "")
    task_views = planner_task_views(state)
    if not task_views and not profile_update:
        return []

    events: list[dict[str, object]] = []
    for index, item in enumerate(task_views[:3], start=1):
        if not isinstance(item, dict):
            continue
        event_type = event_type_for_intent(
            str(item.get("intent") or item.get("subtype") or item.get("type") or "")
        )
        if not event_type:
            continue
        facts = _event_facts(
            event_type,
            content,
            state,
            canonical_price_project=canonical_price_project,
            contextual_price_project=contextual_price_project,
            extract_price_digits=extract_price_digits,
            extract_project=extract_project,
            known_visible_concerns=known_visible_concerns,
            project_direction_names=project_direction_names,
        )
        events.append(_event_record(state, index, event_type, facts))

    if profile_update and not events:
        facts = clean_model_value(_event_common_facts(content, state, known_visible_concerns, project_direction_names))
        events.append(
            {
                "event_id": f"evt_{state.get('request_id', 'unknown')}_profile",
                "event_time": "",
                "event_type": "profile_update",
                "stage": planner_scene(state),
                "summary": event_summary("profile_update", facts),
                "facts": facts,
                "impact": "补充客户画像，后续回复应承接已知需求和顾虑。",
                "confidence": 0.72,
            }
        )
    return events


def _event_record(state: AgentState, index: int, event_type: str, facts: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": f"evt_{state.get('request_id', 'unknown')}_{index}",
        "event_time": "",
        "event_type": event_type,
        "stage": planner_scene(state),
        "summary": event_summary(event_type, facts),
        "facts": facts,
        "impact": event_impact(event_type),
        "confidence": 0.78,
    }


def _event_facts(
    event_type: str,
    content: str,
    state: AgentState,
    *,
    canonical_price_project: Callable[[str], str],
    contextual_price_project: Callable[[AgentState], str],
    extract_price_digits: Callable[[str], list[str]],
    extract_project: Callable[[str], str],
    known_visible_concerns: Callable[[AgentState], list[str]],
    project_direction_names: Callable[[AgentState], list[str]],
) -> dict[str, object]:
    image_info = state.get("image_info", {}) or {}
    project = extract_project(content)
    common = _event_common_facts(content, state, known_visible_concerns, project_direction_names)

    if event_type == "price_inquiry":
        return clean_model_value({
            **common,
            "project": canonical_price_project(contextual_price_project(state) or project),
            "price_focus": "价格咨询",
            "budget_sens": (
                "high"
                if any(term in content for term in ["贵", "预算", "便宜", "太高"])
                else "unknown"
            ),
            "seen_price": extract_price_digits(content)[:3],
        })
    if event_type == "project_inquiry":
        return clean_model_value({
            **common,
            "project": project,
            "question_focus": "项目方向",
            "visible_concerns": image_info.get("visible_concerns", []),
            "image_desc": image_info.get("image_desc", ""),
            "project_directions": [] if case_request_lacks_specific_context(state) else project_direction_names(state),
        })
    if event_type == "image_inquiry":
        return clean_model_value({
            **common,
            "image_type": image_info.get("image_type", ""),
            "image_intent": image_info.get("image_intent", ""),
            "body_part": image_info.get("body_part", ""),
            "visible_concerns": image_info.get("visible_concerns", []),
            "text_clues": image_info.get("text_clues", []),
        })
    if event_type == "trust_issue":
        return clean_model_value({**common, "concern": "正规性或服务保障", "trust_level": "low"})
    if event_type == "store_inquiry":
        return clean_model_value({
            **common,
            "city": extract_city(content),
            "location_focus": "门店/地址/路线",
            "matched_stores": _matched_store_names(state),
        })
    if event_type == "appoint_intent":
        return clean_model_value({
            **common,
            "intent_level": "medium",
            "preferred_time": extract_time_text(content) or _appointment_cache_slot(state, "time"),
            "preferred_store": _appointment_cache_slot(state, "store_name"),
            "preferred_date": _appointment_cache_slot(state, "date"),
            "people_count": _appointment_cache_slot(state, "people_count"),
        })
    if event_type == "after_sales":
        return clean_model_value({**common, "issue": "售后/恢复咨询", "severity": "unknown"})
    if event_type == "competitor_compare":
        return clean_model_value({**common, "compare_focus": "竞品/报价对比"})
    if event_type == "campaign_inquiry":
        return clean_model_value({**common, "campaign_focus": "活动/优惠咨询", "seen_price": extract_price_digits(content)[:3]})
    if event_type == "human_request":
        return clean_model_value({**common, "request": "需要专业同事协助"})
    return clean_model_value(common)


def _event_common_facts(
    content: str,
    state: AgentState,
    known_visible_concerns: Callable[[AgentState], list[str]],
    project_direction_names: Callable[[AgentState], list[str]],
) -> dict[str, object]:
    image_info = state.get("image_info", {}) or {}
    facts: dict[str, object] = {}
    city = extract_city(content)
    if city:
        facts["city"] = city
    visible = image_info.get("visible_concerns") or known_visible_concerns(state)
    if visible:
        facts["visible_concerns"] = list(visible[:6])
    directions = project_direction_names(state)
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


def _appointment_cache_slot(state: AgentState, key: str) -> str:
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    alias_map = {
        "date": ["date", "appointment_date"],
        "time": ["time", "appointment_time"],
        "store_name": ["store_name"],
        "people_count": ["people_count"],
    }
    for alias in alias_map.get(key, [key]):
        text = str(appointment_cache.get(alias) or "").strip()
        if text:
            return text
    return ""


def _matched_store_names(state: AgentState) -> list[str]:
    result: list[str] = []
    fact_envelope = state.get("fact_envelope") or {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope, dict) else {}
    stores = structured.get("store_facts") if isinstance(structured, dict) else []
    for store in stores if isinstance(stores, list) else []:
        if isinstance(store, dict):
            name = str(store.get("name") or "").strip()
            if name and name not in result:
                result.append(name)
    return result[:5]
