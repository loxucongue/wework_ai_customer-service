from __future__ import annotations

import json

from app.graph import planner_helpers, task_state
from app.graph.store_anchor import current_store_anchor_from_state
from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES


def extract_city(content: str) -> str:
    for city in CITY_NAMES:
        if city in content:
            return city
    return ""


def store_query_from_state(content: str, state: AgentState) -> str:
    content = (content or "").strip()
    use_context_store = (
        should_use_known_store_context(content)
        or should_use_recent_store_fact_context(content, state)
        or should_use_store_context_for_appointment(content, state)
    )
    current_city = _current_city_from_text(content)
    explicit_location = _has_explicit_location_reference(content)
    city = current_city or (known_city_from_state(state) if use_context_store and not explicit_location else "")
    area = extract_store_area(content)
    location_preference = planner_helpers._store_location_preference_from_context(state)
    explicit_store = current_store_anchor_from_state(state) if use_context_store and not explicit_location else ""
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
        "推荐一家",
        "推荐一个",
        "帮我选",
        "近一点",
        "近点",
    ]
    return any(term in content for term in reference_terms)


def should_use_recent_store_fact_context(content: str, state: AgentState) -> bool:
    content = (content or "").strip()
    if not content or extract_city(content):
        return False
    fact_terms = ["停车", "停车场", "车位", "导航", "地址", "怎么过去", "营业时间", "几点开", "几点关", "还营业", "还开", "发给我", "发我", "发一个"]
    if not any(term in content for term in fact_terms):
        return False
    recent = "\n".join(str(item) for item in (state.get("conversation_history") or [])[-8:])
    return bool(current_store_anchor_from_state(state) or any(term in recent for term in ["门店", "地址", "推荐", "优先"]))


def should_use_store_context_for_appointment(content: str, state: AgentState) -> bool:
    if not content or extract_city(content):
        return False
    if not current_store_anchor_from_state(state):
        return False
    if bool(task_state.is_active_appointment_task(state)):
        return True
    return any(term in content for term in ["能约吗", "可以约", "可约", "周六", "周日", "今天", "明天", "下午", "上午", "几点"])


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
    for area in ["虹口", "浦东", "嘉定", "徐汇", "思明", "湖里", "百星", "渝北", "南岸", "渝中", "大坪", "中贸", "小寨", "未央", "碑林"]:
        if area in content:
            return area
    return ""


def _current_city_from_text(content: str) -> str:
    current = extract_city(content)
    if current:
        return current
    for hint, city in {
        "浦东机场": "上海",
        "虹桥机场": "上海",
        "高崎机场": "厦门",
        "厦门机场": "厦门",
        "中贸": "西安",
        "小寨": "西安",
        "未央": "西安",
        "碑林": "西安",
        "枋湖": "厦门",
        "湖里": "厦门",
        "浦东": "上海",
        "虹口": "上海",
        "嘉定": "上海",
    }.items():
        if hint in content:
            return city
    return ""


def _has_explicit_location_reference(content: str) -> bool:
    return any(
        term in content
        for term in [
            "机场",
            "火车站",
            "高铁站",
            "车站",
            "附近",
            "浦东",
            "虹桥",
            "高崎",
            "中贸",
            "小寨",
            "未央",
            "碑林",
            "枋湖",
            "湖里",
        ]
    )


def extract_time_text(content: str) -> str:
    for word in ["今天", "明天", "后天", "周六", "周日", "周末", "上午", "下午", "晚上"]:
        if word in content:
            return word
    return ""


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
