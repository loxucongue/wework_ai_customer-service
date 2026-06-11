from __future__ import annotations

import json

from app.graph.signals.store_followup import store_location_preference_from_context
from app.graph.state import AgentState
from app.graph.task_state import appointment_slot_value
from app.policies.constants import (
    CITY_NAMES,
    KNOWN_STORE_NAMES,
    STORE_AREA_TERMS,
    STORE_CONTEXT_FACT_TERMS,
    STORE_CONTEXT_RECENT_FACT_HINT_TERMS,
    STORE_CONTEXT_REFERENCE_TERMS,
    STORE_PREFERRED_HINT_TERMS,
    TIME_REFERENCE_TERMS,
)


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
    return any(term in content for term in STORE_CONTEXT_REFERENCE_TERMS)


def should_use_recent_store_fact_context(content: str, state: AgentState) -> bool:
    content = (content or "").strip()
    if not content or extract_city(content):
        return False
    if not any(term in content for term in STORE_CONTEXT_FACT_TERMS):
        return False
    recent = "\n".join(str(item) for item in (state.get("conversation_history") or [])[-8:])
    return bool(known_store_name_from_text(recent) or any(term in recent for term in STORE_CONTEXT_RECENT_FACT_HINT_TERMS))


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
        if any(term in window for term in STORE_PREFERRED_HINT_TERMS):
            return name
    return ""


def known_store_name_matches(text: str) -> list[tuple[str, int]]:
    matches: list[tuple[str, int]] = []
    for name in KNOWN_STORE_NAMES:
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
    for area in STORE_AREA_TERMS:
        if area in content:
            return area
    return ""


def extract_time_text(content: str) -> str:
    for word in TIME_REFERENCE_TERMS:
        if word in content:
            return word
    return ""


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
