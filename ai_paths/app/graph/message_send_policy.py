from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def suppress_repeated_action_messages(messages: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip()
        if message_type == "payment_collection" and not can_send_payment_collection(state):
            continue
        if message_type == "store_address" and not can_send_store_address(state, _store_id_from_message(message)):
            continue
        output.append(message)
    return _renumber(output)


def action_message_policy_for_model(state: AgentState) -> dict[str, Any]:
    sent_store_ids = sorted(_sent_store_address_ids(state))
    payment_sent = _payment_collection_already_sent(state)
    return {
        "payment_collection_already_sent": payment_sent,
        "payment_collection_resend_allowed": (not payment_sent) or _explicit_payment_resend_request(_current_text(state)),
        "sent_store_address_ids": sent_store_ids,
        "store_address_resend_allowed": (not sent_store_ids) or _explicit_store_address_resend_request(_current_text(state)),
        "rule": "payment_collection/store_address are customer-visible action cards. Do not repeat them unless the current customer message explicitly asks to resend or did not receive them.",
    }


def can_send_payment_collection(state: AgentState) -> bool:
    if not _payment_collection_already_sent(state):
        return True
    return _explicit_payment_resend_request(_current_text(state))


def can_send_store_address(state: AgentState, store_id: str = "") -> bool:
    sent_store_ids = _sent_store_address_ids(state)
    if not sent_store_ids:
        return True
    if store_id and store_id not in sent_store_ids:
        return True
    return _explicit_store_address_resend_request(_current_text(state))


def _payment_collection_already_sent(state: AgentState) -> bool:
    for event in _history_events(state):
        if str(event.get("event_type") or "").strip() == "payment_collection_sent":
            return True
    text = _assistant_history_text(state)
    return any(term in text for term in ("payment_collection", "收款入口", "付款入口", "预约金入口", "对外收款", "付款给：", "付款给:"))


def _sent_store_address_ids(state: AgentState) -> set[str]:
    store_ids: set[str] = set()
    for event in _history_events(state):
        if str(event.get("event_type") or "").strip() != "store_address_sent":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        store_id = str(facts.get("store_id") or "").strip()
        if store_id:
            store_ids.add(store_id)
    text = _assistant_history_text(state)
    for marker in ("store_address:", "门店卡片:", "门店卡片："):
        if marker in text:
            tail = text.split(marker, 1)[-1].strip()
            digits = "".join(ch for ch in tail[:24] if ch.isdigit())
            if digits:
                store_ids.add(digits)
    return store_ids


def _explicit_payment_resend_request(text: str) -> bool:
    compact = _compact(text)
    return any(term in compact for term in ("再发", "重新发", "没收到", "发付款", "发收款", "发链接", "付款入口", "收款入口", "支付入口", "预约金入口"))


def _explicit_store_address_resend_request(text: str) -> bool:
    compact = _compact(text)
    if any(term in compact for term in ("再发", "重新发", "没收到")) and any(term in compact for term in ("地址", "位置", "定位", "导航", "路线", "门店")):
        return True
    return any(term in compact for term in ("发地址", "地址发", "发位置", "位置发", "发定位", "发导航", "发路线", "导航给我", "路线给我", "门店卡片"))


def _history_events(state: AgentState) -> list[dict[str, Any]]:
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    return [event for event in events if isinstance(event, dict)]


def _assistant_history_text(state: AgentState) -> str:
    chunks: list[str] = []
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in history[-12:]:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role and role not in {"assistant", "staff", "service", "bot"}:
                continue
            content = item.get("content")
            chunks.append(str(content.get("text") if isinstance(content, dict) else content or ""))
            continue
        raw = str(item or "")
        if raw.startswith(("小贝:", "小贝：", "客服:", "客服：", "AI回复:", "AI回复：")):
            chunks.append(raw)
    return "\n".join(chunks)


def _current_text(state: AgentState) -> str:
    return str(state.get("normalized_content") or state.get("content") or "")


def _compact(text: str) -> str:
    return "".join(str(text or "").split())


def _store_id_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("store_id") or content.get("id") or "").strip()
    return str(content or "").strip()


def _renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "order": index} for index, item in enumerate(messages, start=1)]
