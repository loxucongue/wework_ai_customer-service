from __future__ import annotations

import re
from typing import Any

from app.graph.state import AgentState


PLACEHOLDER_TERMS = ("XX号", "xx号", "某路", "某街", "某大厦", "某商场")


def sanitize_unsupported_placeholder_text(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any] | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    changed = False
    for message in messages:
        if not isinstance(message, dict) or str(message.get("type") or "") != "text":
            output.append(message)
            continue
        text = _text_content(message.get("content"))
        if not _has_placeholder(text):
            output.append(message)
            continue
        replacement = _fact_store_address_text(state) or _generic_store_card_text(messages)
        output.append({**message, "content": {"text": replacement}})
        changed = True
    if changed and warnings is not None:
        warnings.append(
            {
                "node": "message_sanitizer",
                "message": "unsupported_placeholder_text_replaced",
                "detail": {"terms": [term for term in PLACEHOLDER_TERMS if any(term in _text_content(item.get("content")) for item in messages if isinstance(item, dict))]},
            }
        )
    return _renumber(output)


def normalize_store_address_card_ids(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any] | None = None,
) -> list[dict[str, Any]]:
    desired_store_id = _desired_store_id_for_card(messages, state)
    if not desired_store_id:
        if not any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages):
            return messages
        if warnings is not None:
            warnings.append(
                {
                    "node": "message_sanitizer",
                    "message": "unsupported_store_address_card_removed",
                    "detail": {"reason": "no_current_history_or_fact_store_anchor"},
                }
            )
        return _renumber(_unanchored_store_address_fallback_messages(messages, state))
    changed = False
    output: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or str(message.get("type") or "") != "store_address":
            output.append(message)
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        current_id = str(content.get("store_id") or content.get("id") or "").strip()
        if current_id == desired_store_id:
            output.append(message)
            continue
        output.append({**message, "content": {"store_id": desired_store_id}})
        changed = True
    if changed and warnings is not None:
        warnings.append(
            {
                "node": "message_sanitizer",
                "message": "store_address_card_id_normalized",
                "detail": {"store_id": desired_store_id},
            }
        )
    return _renumber(output)


def _has_placeholder(text: str) -> bool:
    return any(term in text for term in PLACEHOLDER_TERMS)


def _text_content(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return str(content or "")


def _fact_store_address_text(state: AgentState) -> str:
    structured = _structured_facts(state)
    for store in _fact_stores(structured):
        name = str(store.get("name") or "").strip()
        address = str(store.get("address") or "").strip()
        if not address or _has_placeholder(address):
            continue
        parts = []
        parts.append(f"{name}地址：{address}" if name else f"门店地址：{address}")
        hours = str(store.get("business_hours") or "").strip()
        if hours and not _has_placeholder(hours):
            parts.append(f"营业时间{hours}")
        return "，".join(parts) + "。门店卡片我也发您，点开可以导航。"
    recommended = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    name = str(recommended.get("name") or "").strip()
    address = str(recommended.get("address") or "").strip()
    if address and not _has_placeholder(address):
        return (f"{name}地址：{address}" if name else f"门店地址：{address}") + "。门店卡片我也发您，点开可以导航。"
    return ""


def _fact_stores(structured: dict[str, Any]) -> list[dict[str, Any]]:
    stores = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _structured_facts(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    return structured


def _generic_store_card_text(messages: list[dict[str, Any]]) -> str:
    has_store_card = any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages)
    if has_store_card:
        return "门店位置卡片我发您，点开可以查看地址和导航。"
    return "这家门店的详细地址我还需要核对一下，您发下具体区域我帮您确认。"


def _desired_store_id_for_card(messages: list[dict[str, Any]], state: AgentState) -> str:
    current_store_id = _current_message_store_id(state)
    history_store_id = _history_store_id_for_explicit_request(state)
    current_region_store_id = _current_region_store_id(state)
    fact_store_id = _selected_fact_store_id(state)
    if _explicit_store_address_request(str(state.get("normalized_content") or state.get("content") or "")):
        return current_store_id or current_region_store_id or history_store_id or fact_store_id
    reply_text_store_id = _reply_text_store_id(messages, state)
    if reply_text_store_id and reply_text_store_id in {current_store_id, current_region_store_id, fact_store_id, history_store_id}:
        return reply_text_store_id
    return current_store_id or current_region_store_id or fact_store_id or history_store_id


def _current_message_store_id(state: AgentState) -> str:
    text = str(state.get("normalized_content") or state.get("content") or "")
    for store in _customer_scope_stores(state):
        name = str(store.get("store_name") or "").strip()
        store_id = str(store.get("store_id") or "").strip()
        if name and store_id and name in text:
            return store_id
    return ""


def _reply_text_store_id(messages: list[dict[str, Any]], state: AgentState) -> str:
    text = "\n".join(_text_content(item.get("content")) for item in messages if isinstance(item, dict))
    for store in _customer_scope_stores(state):
        name = str(store.get("store_name") or "").strip()
        store_id = str(store.get("store_id") or "").strip()
        if name and store_id and name in text:
            return store_id
    return ""


def _current_region_store_id(state: AgentState) -> str:
    text = str(state.get("normalized_content") or state.get("content") or "")
    if not text:
        return ""
    matches: list[str] = []
    for store in _customer_scope_stores(state):
        store_id = str(store.get("store_id") or "").strip()
        if not store_id:
            continue
        if any(token and token in text for token in _store_region_tokens(store)):
            matches.append(store_id)
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _store_region_tokens(store: dict[str, Any]) -> list[str]:
    tokens: set[str] = set()
    for key in ("district", "store_name"):
        value = str(store.get(key) or "").strip()
        if not value:
            continue
        tokens.add(value)
        for suffix in ("区", "县", "店"):
            if value.endswith(suffix) and len(value) > len(suffix):
                tokens.add(value[: -len(suffix)])
    return sorted({token for token in tokens if len(token) >= 2}, key=len, reverse=True)


def _selected_fact_store_id(state: AgentState) -> str:
    structured = _structured_facts(state)
    recommended = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    if str(recommended.get("reason") or "").strip() == "distance_calculate_rank_1":
        for key in ("id", "store_id"):
            value = str(recommended.get(key) or "").strip()
            if value:
                return value
    status = structured.get("store_lookup_status") if isinstance(structured.get("store_lookup_status"), dict) else {}
    store_facts = _fact_stores(structured)
    if int(status.get("candidate_count") or 0) == 1 and len(store_facts) == 1:
        value = str(store_facts[0].get("id") or store_facts[0].get("store_id") or "").strip()
        if value:
            return value
    for store in _fact_stores(structured):
        value = str(store.get("id") or store.get("store_id") or "").strip()
        name = str(store.get("name") or "").strip()
        if value and name and name in str(state.get("normalized_content") or state.get("content") or ""):
            return value
    for key in ("confirmed_store_id", "store_id"):
        value = str(state.get(key) or "").strip()
        if value:
            return value
    return ""


def _unanchored_store_address_fallback_messages(messages: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    text = str(state.get("normalized_content") or state.get("content") or "")
    output = [item for item in messages if isinstance(item, dict) and str(item.get("type") or "") != "store_address"]
    if _explicit_store_address_request(text):
        return [{"type": "text", "order": 1, "content": {"text": "您想发哪家门店地址？我先帮您确认具体门店后再发位置。"}}]
    return output


def _history_store_id_for_explicit_request(state: AgentState) -> str:
    text = str(state.get("normalized_content") or state.get("content") or "")
    if not _explicit_store_address_request(text):
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
        raw = str(item or "")
        parsed = _store_id_from_text(raw)
        if parsed:
            return parsed
        for store in _customer_scope_stores(state):
            name = str(store.get("store_name") or "").strip()
            store_id = str(store.get("store_id") or "").strip()
            if name and store_id and name in raw:
                return store_id
    return ""


def _explicit_store_address_request(text: str) -> bool:
    compact = "".join(str(text or "").split())
    return any(term in compact for term in ("发地址", "发一下地址", "地址发", "门店地址", "发位置", "发定位", "发导航", "发路线", "地址给我", "给我地址"))


def _store_id_from_text(text: str) -> str:
    match = re.search(r'"store_id"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1).strip()
    match = re.search(r"store_address[:：]\s*(\d+)", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"门店卡片[^0-9]*(\d{2,})", text)
    return match.group(1).strip() if match else ""


def _customer_scope_stores(state: AgentState) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    return [store for store in stores if isinstance(store, dict)]


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "order": index} for index, item in enumerate(messages, start=1)]
