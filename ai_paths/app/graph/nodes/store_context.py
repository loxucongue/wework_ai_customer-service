from __future__ import annotations

import json

from app.graph.planner_store_followup import store_location_preference_from_context
from app.graph.state import AgentState
from app.graph.task_state import appointment_slot_value
from app.policies.constants import CITY_NAMES


def extract_city(content: str) -> str:
    for city in CITY_NAMES:
        if city in content:
            return city
    return ""


def store_query_from_state(content: str, state: AgentState) -> str:
    content = (content or "").strip()
    use_context_store = should_use_known_store_context(content) or should_use_recent_store_fact_context(content, state)
    city = extract_city(content) or (known_city_from_state(state) if use_context_store else "")
    area = extract_store_area(content)
    location_preference = store_location_preference_from_context(state)
    explicit_store = ""
    if use_context_store:
        explicit_store = (
            str(state.get("confirmed_store_name") or state.get("store_name") or "").strip()
            or appointment_slot_value(state, "store_name")
            or known_store_name_from_history(state)
        )
    parts: list[str] = []
    if city and city not in content:
        parts.append(city)
    if area and area not in content:
        parts.append(area)
    if location_preference and location_preference not in content:
        parts.append(location_preference)
    if explicit_store and explicit_store not in content:
        parts.append(explicit_store)
    parts.append(content)
    return " ".join(part for part in parts if part).strip()


def should_use_known_store_context(content: str) -> bool:
    content = (content or "").strip()
    if not content:
        return False
    reference_terms = [
        "刚刚那家",
        "刚才那家",
        "刚说的",
        "你刚说",
        "你发的",
        "那家",
        "这家",
        "这个店",
        "那边",
        "我约的",
        "预约的",
        "之前那个",
        "上面那个",
    ]
    return any(term in content for term in reference_terms)


def should_use_recent_store_fact_context(content: str, state: AgentState) -> bool:
    content = (content or "").strip()
    if not content or extract_city(content):
        return False
    fact_terms = [
        "停车",
        "停车场",
        "车位",
        "导航",
        "地址",
        "怎么过去",
        "营业时间",
        "几点开",
        "几点关",
        "还营业",
        "还开",
        "发给我",
        "发我",
        "发一个",
    ]
    if not any(term in content for term in fact_terms):
        return False
    recent = "\n".join(str(item) for item in (state.get("conversation_history") or [])[-8:])
    return bool(known_store_name_from_text(recent) or any(term in recent for term in ["门店", "地址", "推荐", "优先"]))


def known_store_name_from_history(state: AgentState) -> str:
    fallback = ""
    for item in reversed(state.get("conversation_history", [])[-10:]):
        text = str(item)
        preferred = preferred_store_name_from_text(text)
        if preferred:
            return preferred
        if not fallback:
            fallback = known_store_name_from_text(text)
    return fallback


def known_store_name_from_text(text: str) -> str:
    matches = known_store_name_matches(text)
    return matches[-1][0] if matches else ""


def preferred_store_name_from_text(text: str) -> str:
    matches = known_store_name_matches(text)
    for name, index in matches:
        window = (text or "")[max(0, index - 80) : index + len(name) + 80]
        if any(term in window for term in ["优先推荐", "推荐门店", "推荐的门店", "推荐这家", "这家推荐", "优先看"]):
            return name
    return ""


def known_store_name_matches(text: str) -> list[tuple[str, int]]:
    known_names = [
        "厦门百星",
        "厦门二店",
        "厦门思明店",
        "上海徐汇店",
        "上海静安店",
        "上海浦东店",
        "西安中贸店",
        "西安安康店",
        "北京朝阳店",
        "天津河西店",
        "重庆渝北店",
        "重庆南岸店",
        "重庆渝中店",
    ]
    matches: list[tuple[str, int]] = []
    for name in known_names:
        index = (text or "").find(name)
        if index >= 0:
            matches.append((name, index))
    matches.sort(key=lambda item: item[1])
    return matches


def known_city_from_state(state: AgentState) -> str:
    basic = state.get("customer_basic_info") or {}
    if isinstance(basic, dict):
        city = str(basic.get("city") or "").strip()
        if city:
            return city
    for event in reversed(state.get("history_events", [])[-10:]):
        if isinstance(event, dict):
            facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
            city = str(facts.get("city") or "").strip()
            if city:
                return city
            text = _json_dumps(event)
        else:
            text = str(event)
        city = extract_city(text)
        if city:
            return city
    for message in reversed(state.get("conversation_history", [])[-10:]):
        city = extract_city(str(message))
        if city:
            return city
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        city = extract_city(_json_dumps(profile))
        if city:
            return city
    return ""


def extract_store_area(content: str) -> str:
    for area in ["徐汇", "静安", "浦东", "思明", "湖里", "百星", "渝北", "南岸", "渝中", "中贸"]:
        if area in content:
            return area
    return ""


def extract_time_text(content: str) -> str:
    for word in ["今天", "明天", "后天", "周六", "周日", "周末", "上午", "下午", "晚上"]:
        if word in content:
            return word
    return ""


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
