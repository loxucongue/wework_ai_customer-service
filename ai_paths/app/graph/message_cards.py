from __future__ import annotations

import re
from typing import Any

from app.graph.message_send_policy import can_send_store_address
from app.graph.state import AgentState


def append_store_address_card(messages: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    signal_text = _store_card_signal_text(state, messages)
    if not _should_send_store_address_card(state, signal_text):
        return messages
    if any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages):
        return messages
    store_id = _store_address_card_store_id(state, signal_text)
    if not store_id:
        return messages
    if not can_send_store_address(state, store_id):
        return messages
    output = list(messages)
    output.append({"type": "store_address", "order": len(output) + 1, "content": {"store_id": store_id}})
    return _renumber(output)


def _should_send_store_address_card(state: AgentState, signal_text: str) -> bool:
    stage = str(state.get("planner_stage") or "").strip()
    sub_rule_id = str(state.get("planner_sub_rule_id") or "").strip().lower()
    if stage != "S2" and "store" not in sub_rule_id and "address" not in sub_rule_id and "parking" not in sub_rule_id:
        return False
    if not signal_text:
        return False
    if "city_only" in sub_rule_id and not _explicit_store_address_resend_request(signal_text):
        return bool(_matched_exact_customer_store_id(state, signal_text))
    if any(
        term in signal_text
        for term in (
            "地址",
            "位置",
            "导航",
            "路线",
            "停车",
            "附近",
            "哪家",
            "哪里",
            "这边",
            "那边",
            "这里",
            "那里",
            "发我",
            "发一下",
        )
    ):
        return True
    return bool(_matched_exact_customer_store_id(state, signal_text))


def _store_address_card_store_id(state: AgentState, signal_text: str) -> str:
    current_text = str(state.get("normalized_content") or state.get("content") or "")
    for candidate in (
        _request_store_id(state),
        _matched_exact_customer_store_id(state, current_text),
        _matched_unique_region_store_id(state, current_text),
        _history_store_id_for_detail_turn(state),
        _selected_fact_store_id(state),
    ):
        if candidate:
            return candidate
    return ""


def _selected_fact_store_id(state: AgentState) -> str:
    structured = _structured_facts(state)
    recommended = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    if str(recommended.get("reason") or "").strip() == "distance_calculate_rank_1":
        value = str(recommended.get("id") or recommended.get("store_id") or "").strip()
        if value:
            return value
    status = structured.get("store_lookup_status") if isinstance(structured.get("store_lookup_status"), dict) else {}
    store_facts = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    if int(status.get("candidate_count") or 0) == 1 and len(store_facts) == 1:
        first = store_facts[0] if isinstance(store_facts[0], dict) else {}
        return str(first.get("id") or first.get("store_id") or "").strip()
    return ""


def _request_store_id(state: AgentState) -> str:
    for key in ("confirmed_store_id", "store_id"):
        value = state.get(key)
        if value not in (None, ""):
            return str(value).strip()
    appointment = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    value = appointment.get("store_id")
    return str(value).strip() if value not in (None, "") else ""


def _history_store_id_for_detail_turn(state: AgentState) -> str:
    current_text = str(state.get("normalized_content") or state.get("content") or "")
    if not (_explicit_store_address_resend_request(current_text) or _store_detail_request(current_text)):
        return ""
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in reversed(events[-20:]):
        if not isinstance(event, dict) or str(event.get("event_type") or "") != "store_address_sent":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        value = str(facts.get("store_id") or facts.get("id") or "").strip()
        if value:
            return value
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in reversed(history[-10:]):
        value = _store_id_from_text(str(item or ""))
        if value:
            return value
    return ""


def _store_detail_request(text: str) -> bool:
    return any(term in str(text or "") for term in ("地址", "位置", "定位", "导航", "路线", "停车", "营业时间", "几点开", "几点关"))


def _matched_exact_customer_store_id(state: AgentState, signal_text: str) -> str:
    stores = _customer_scope_stores(state)
    if not stores:
        return ""
    exact_matches = [
        store
        for store in stores
        if str(store.get("store_name") or "").strip() and str(store.get("store_name") or "").strip() in signal_text
    ]
    if exact_matches:
        return str(exact_matches[-1].get("store_id") or "").strip()
    return ""


def _matched_unique_region_store_id(state: AgentState, signal_text: str) -> str:
    stores = _customer_scope_stores(state)
    if not stores:
        return ""
    region_matches = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        if any(token and token in signal_text for token in _store_match_tokens(store)):
            region_matches.append(store)
    if len(region_matches) == 1:
        return str(region_matches[0].get("store_id") or "").strip()
    return ""


def _store_match_tokens(store: dict[str, Any]) -> list[str]:
    tokens: set[str] = set()
    for key in ("province", "city", "district", "store_name"):
        value = str(store.get(key) or "").strip()
        if not value:
            continue
        tokens.add(value)
        for suffix in ("省", "市", "区", "县", "店"):
            if value.endswith(suffix) and len(value) > len(suffix):
                tokens.add(value[: -len(suffix)])
    address = str(store.get("store_address") or "").strip()
    for value in tokens.copy():
        if len(value) >= 2 and value in address:
            tokens.add(value)
    return sorted({token for token in tokens if len(token) >= 2}, key=len, reverse=True)


def _store_card_signal_text(state: AgentState, messages: list[dict[str, Any]]) -> str:
    chunks = [str(state.get("normalized_content") or state.get("content") or "")]
    chunks.extend(_message_text(item) for item in messages if isinstance(item, dict))
    return "\n".join(chunks)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or content.get("store_id") or "")
    return str(content or "")


def _store_id_from_text(text: str) -> str:
    match = re.search(r'"store_id"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1).strip()
    match = re.search(r"store_address[:：]\s*(\d+)", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"门店卡片[^0-9]*(\d{2,})", text)
    return match.group(1).strip() if match else ""


def _explicit_store_address_resend_request(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if any(term in compact for term in ("再发", "重新发", "没收到")) and any(term in compact for term in ("地址", "位置", "定位", "导航", "路线", "门店")):
        return True
    return any(
        term in compact
        for term in (
            "发地址",
            "地址发",
            "地址给我",
            "给我地址",
            "门店地址",
            "发位置",
            "位置发",
            "位置给我",
            "给我位置",
            "发定位",
            "定位发",
            "定位给我",
            "给我定位",
            "发导航",
            "导航给我",
            "发路线",
            "路线给我",
            "门店卡片",
        )
    )


def _structured_facts(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    return structured


def _customer_scope_stores(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "order": index} for index, item in enumerate(messages, start=1)]
