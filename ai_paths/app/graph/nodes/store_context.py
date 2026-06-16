from __future__ import annotations

import json

from app.graph.signals.store_followup import store_location_preference_from_context
from app.graph.state import AgentState
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
from app.services.store_text import extract_area_or_landmark, extract_location_preference


def extract_city(content: str) -> str:
    for city in CITY_NAMES:
        if city in content:
            return city
    return ""


def store_query_from_state(content: str, state: AgentState) -> str:
    content = (content or "").strip()
    inherit_location_context = should_inherit_store_location_context(content, state)
    use_explicit_store_context = should_use_known_store_context(content) or should_use_recent_store_fact_context(content, state)
    city = extract_city(content) or (known_city_from_state(state) if inherit_location_context else "")
    current_area = extract_area_or_landmark(content) or extract_store_area(content)
    area = current_area or (known_store_area_from_history(state) if inherit_location_context else "")
    location_preference = store_location_preference_from_context(state)
    explicit_store = ""
    if use_explicit_store_context:
        explicit_store = _current_store_name_from_state(state) or known_store_name_from_history(state)
    parts: list[str] = []
    if city and city not in content:
        parts.append(city)
    if area and area not in content:
        parts.append(area)
    if location_preference and location_preference not in content:
        parts.append(location_preference)
    if explicit_store and explicit_store not in content and not (area or location_preference):
        parts.append(explicit_store)
    parts.append(content)
    return " ".join(part for part in parts if part).strip()


def should_inherit_store_location_context(content: str, state: AgentState) -> bool:
    content = (content or "").strip()
    if not content:
        return False
    if should_use_known_store_context(content) or should_use_recent_store_fact_context(content, state):
        return True
    if extract_city(content):
        return True
    if extract_area_or_landmark(content) or extract_location_preference(content) or extract_store_area(content):
        return True
    return any(term in content for term in STORE_CONTEXT_FACT_TERMS)


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


def known_store_area_from_history(state: AgentState) -> str:
    for item in reversed(state.get("conversation_history", [])[-10:]):
        if not _is_customer_history_item(item):
            continue
        text = str(item)
        landmark = extract_area_or_landmark(text)
        if landmark:
            return landmark
        area = extract_store_area(text)
        if area:
            return area
    return ""


def _is_customer_history_item(item: object) -> bool:
    if isinstance(item, dict):
        role = str(item.get("role") or item.get("direction") or "").lower()
        if role:
            return role in {"user", "customer"}
        sender = str(item.get("sender") or item.get("sender_type") or "").lower()
        if sender:
            return sender in {"user", "customer"}
        return True
    text = str(item or "").strip()
    if text.startswith(("小贝：", "小贝:", "客服：", "客服:", "AI回复：", "AI回复:", "助手：", "助手:")):
        return False
    if text.startswith(("客户：", "客户:", "用户：", "用户:")):
        return True
    return True


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
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    for key in ("city", "current_city"):
        city = str(request_context.get(key) or "").strip()
        if city:
            return city
    current_store_city = _current_store_city_from_state(state)
    if current_store_city:
        return current_store_city
    for message in reversed(state.get("conversation_history", [])[-10:]):
        city = extract_city(str(message))
        if city:
            return city
    return ""


def _current_store_name_from_state(state: AgentState) -> str:
    structured = state.get("structured_facts") if isinstance(state.get("structured_facts"), dict) else {}
    if not structured:
        fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
        structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    recommended = structured.get("recommended_store") if isinstance(structured, dict) else {}
    if isinstance(recommended, dict):
        name = str(recommended.get("name") or "").strip()
        if name:
            return name
    stores = structured.get("store_facts") if isinstance(structured, dict) else None
    if isinstance(stores, list):
        for item in stores:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                return name
    return ""


def _current_store_city_from_state(state: AgentState) -> str:
    structured = state.get("structured_facts") if isinstance(state.get("structured_facts"), dict) else {}
    if not structured:
        fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
        structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    status = structured.get("store_lookup_status") if isinstance(structured, dict) else {}
    if isinstance(status, dict):
        city = str(status.get("city") or "").strip()
        if city:
            return city
    recommended = structured.get("recommended_store") if isinstance(structured, dict) else {}
    if isinstance(recommended, dict):
        city = str(recommended.get("city") or "").strip()
        if city:
            return city
    stores = structured.get("store_facts") if isinstance(structured, dict) else None
    if isinstance(stores, list):
        for item in stores:
            if not isinstance(item, dict):
                continue
            city = str(item.get("city") or "").strip()
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
