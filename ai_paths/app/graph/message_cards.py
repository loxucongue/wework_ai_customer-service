from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def append_store_address_card(messages: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    if not _should_send_store_address_card(state):
        return messages
    if any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages):
        return messages
    store_id = _store_address_card_store_id(state)
    if not store_id:
        return messages
    output = list(messages)
    output.append({"type": "store_address", "order": len(output) + 1, "content": {"store_id": store_id}})
    return _renumber(output)


def _should_send_store_address_card(state: AgentState) -> bool:
    stage = str(state.get("planner_stage") or "").strip()
    sub_rule_id = str(state.get("planner_sub_rule_id") or "").strip().lower()
    if stage != "S2" and "store" not in sub_rule_id and "address" not in sub_rule_id and "parking" not in sub_rule_id:
        return False
    text = _store_card_signal_text(state)
    if not text:
        return False
    if any(
        term in text
        for term in (
            "门店",
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
    return bool(_matched_customer_store_id(state))


def _store_address_card_store_id(state: AgentState) -> str:
    for candidate in (
        _recommended_store_id(state),
        _first_store_fact_id(state),
        _request_store_id(state),
        _matched_customer_store_id(state),
    ):
        if candidate:
            return candidate
    return ""


def _recommended_store_id(state: AgentState) -> str:
    structured = _structured_facts(state)
    recommended = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    return str(recommended.get("id") or recommended.get("store_id") or "").strip()


def _first_store_fact_id(state: AgentState) -> str:
    structured = _structured_facts(state)
    store_facts = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    if not store_facts:
        return ""
    first = store_facts[0] if isinstance(store_facts[0], dict) else {}
    return str(first.get("id") or first.get("store_id") or "").strip()


def _request_store_id(state: AgentState) -> str:
    for key in ("confirmed_store_id", "store_id"):
        value = state.get(key)
        if value not in (None, ""):
            return str(value).strip()
    appointment = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    value = appointment.get("store_id")
    return str(value).strip() if value not in (None, "") else ""


def _matched_customer_store_id(state: AgentState) -> str:
    stores = _customer_scope_stores(state)
    if not stores:
        return ""
    text = _store_card_signal_text(state)
    exact_matches = [
        store
        for store in stores
        if str(store.get("store_name") or "").strip() and str(store.get("store_name") or "").strip() in text
    ]
    if exact_matches:
        return str(exact_matches[-1].get("store_id") or "").strip()

    region_matches = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        if any(token and token in text for token in _store_match_tokens(store)):
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


def _store_card_signal_text(state: AgentState) -> str:
    chunks = [str(state.get("normalized_content") or state.get("content") or "")]
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    chunks.extend(str(item or "") for item in history[-6:])
    return "\n".join(chunks)


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
