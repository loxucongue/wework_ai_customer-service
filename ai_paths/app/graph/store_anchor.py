from __future__ import annotations

import re

from app.graph.state import AgentState


_GENERIC_STORE_PATTERN = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,16}(?:门店|店))")
_INVALID_STORE_TERMS = (
    "来店",
    "到店",
    "店吗",
    "哪家",
    "哪个",
    "不知道",
    "不确定",
    "地址",
    "路",
    "街",
    "号",
    "楼",
    "大厦",
    "园区",
    "技术园",
)


def current_store_anchor_from_state(state: AgentState) -> str:
    for key in ["confirmed_store_name", "store_name"]:
        value = str(state.get(key) or "").strip()
        if is_valid_store_anchor(value):
            return value

    active_task = state.get("active_task") or {}
    if isinstance(active_task, dict):
        known_slots = active_task.get("known_slots") if isinstance(active_task.get("known_slots"), dict) else {}
        value = str(known_slots.get("store_name") or "").strip()
        if is_valid_store_anchor(value):
            return value

    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    preference = basic.get("appointment_preference") if isinstance(basic.get("appointment_preference"), dict) else {}
    value = str(preference.get("store_name") or "").strip()
    if is_valid_store_anchor(value):
        return value

    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    if isinstance(lookup, dict):
        recommended = lookup.get("recommended_store") if isinstance(lookup.get("recommended_store"), dict) else {}
        value = str(recommended.get("name") or "").strip()
        if value:
            return value
        stores = lookup.get("stores") if isinstance(lookup.get("stores"), list) else []
        if len(stores) == 1 and isinstance(stores[0], dict):
            value = str(stores[0].get("name") or "").strip()
            if value:
                return value

    return (
        preferred_store_name_from_history(state)
        or explicit_store_alias_from_history(state)
        or known_store_name_from_history(state)
        or latest_store_name_from_events(state)
    )


def preferred_store_name_from_history(state: AgentState) -> str:
    for item in reversed(state.get("conversation_history", [])[-10:]):
        text = str(item)
        preferred = preferred_store_name_from_text(text)
        if preferred:
            return preferred
    return ""


def explicit_store_alias_from_history(state: AgentState) -> str:
    for item in reversed(state.get("conversation_history", [])[-10:]):
        text = str(item)
        if not text.startswith(("用户：", "客户：")):
            continue
        name = explicit_store_alias_from_text(text)
        if name:
            return name
    return ""


def explicit_store_alias_from_text(text: str) -> str:
    city = ""
    if "厦门" in text:
        city = "厦门"
    if "上海" in text:
        city = "上海"
    aliases = [
        ("百星", "厦门百星"),
        ("思明", "厦门思明店"),
        ("二店", "厦门二店"),
        ("集美", "厦门集美店"),
        ("徐汇", "上海徐汇店"),
        ("静安", "上海静安店"),
        ("浦东", "上海浦东二店"),
    ]
    for alias, name in aliases:
        if alias in text:
            if name.startswith("厦门") or name.startswith("上海") or not city:
                return name
            return f"{city}{alias}"
    return ""


def known_store_name_from_history(state: AgentState) -> str:
    fallback = ""
    for item in reversed(state.get("conversation_history", [])[-10:]):
        text = str(item)
        matches = known_store_name_matches(text)
        if matches and not fallback:
            fallback = matches[-1][0]
    return fallback


def latest_store_name_from_events(state: AgentState) -> str:
    for event in reversed(state.get("history_events", [])[-10:]):
        if not isinstance(event, dict):
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        for key in ["preferred_store", "store", "store_name"]:
            value = str(facts.get(key) or "").strip()
            if is_valid_store_anchor(value):
                return value
    return ""


def preferred_store_name_from_text(text: str) -> str:
    matches = known_store_name_matches(text)
    for name, index in matches:
        window = (text or "")[max(0, index - 80) : index + len(name) + 80]
        if any(term in window for term in ["优先推荐", "推荐门店", "推荐的门店", "推荐这家", "这家推荐", "优先看", "推荐您选择"]):
            return name
    return ""


def known_store_name_matches(text: str) -> list[tuple[str, int]]:
    seen: list[tuple[str, int]] = []
    for match in _GENERIC_STORE_PATTERN.finditer(text or ""):
        name = match.group(1).strip()
        if not is_valid_store_anchor(name):
            continue
        seen.append((name, match.start(1)))
    seen.sort(key=lambda item: item[1])
    return seen


def is_valid_store_anchor(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    if any(term in text for term in _INVALID_STORE_TERMS):
        return False
    if any(
        term in text
        for term in [
            "帮你",
            "帮您",
            "看看",
            "最近的门店",
            "附近门店",
            "这个时间",
            "可约",
            "预约金",
            "小程序",
            "姓名",
            "电话",
            "手机号",
        ]
    ):
        return False
    if explicit_store_alias_from_text(text):
        return True
    return bool(_GENERIC_STORE_PATTERN.search(text))
