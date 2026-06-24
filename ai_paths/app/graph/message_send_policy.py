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
    payment_policy = payment_collection_policy_for_current_turn(state)
    return {
        "payment_collection_already_sent": payment_sent,
        "payment_collection_resend_allowed": payment_policy["can_send"],
        "payment_collection_turn_kind": payment_policy["turn_kind"],
        "payment_collection_block_reason": payment_policy["block_reason"],
        "sent_store_address_ids": sent_store_ids,
        "store_address_resend_allowed": (not sent_store_ids) or _explicit_store_address_resend_request(_current_text(state)),
        "rule": "payment_collection/store_address are customer-visible action cards. Do not repeat them unless the current customer message explicitly asks to resend or did not receive them. Do not send payment_collection on deposit explanation/refusal turns.",
    }


def can_send_payment_collection(state: AgentState) -> bool:
    return bool(payment_collection_policy_for_current_turn(state)["can_send"])


def should_auto_add_payment_collection(state: AgentState, marker: str) -> bool:
    policy = payment_collection_policy_for_current_turn(state)
    if not policy["can_send"]:
        return False
    compact_marker = _compact(marker)
    if _explicit_payment_send_request(compact_marker):
        return True
    if _payment_fee_refusal(compact_marker) or _payment_fee_explanation(compact_marker):
        return False
    return _high_intent_payment_request(compact_marker)


def payment_collection_policy_for_current_turn(state: AgentState) -> dict[str, Any]:
    text = _compact(_current_text(state))
    already_sent = _payment_collection_already_sent(state)
    explicit_resend = _explicit_payment_resend_request(text)
    explicit_send = _explicit_payment_send_request(text)
    refusal = _payment_fee_refusal(text)
    explanation = _payment_fee_explanation(text)
    turn_kind = "explicit_send" if explicit_send else "neutral"
    block_reason = ""
    can_send = True
    if refusal:
        turn_kind = "fee_refusal"
        block_reason = "payment_fee_refusal_or_direct_visit"
        can_send = False
    elif explanation and not explicit_send:
        turn_kind = "fee_explanation"
        block_reason = "payment_fee_explanation_only"
        can_send = False
    elif already_sent and not explicit_resend:
        turn_kind = "already_sent"
        block_reason = "already_sent_without_explicit_resend"
        can_send = False
    elif explicit_resend:
        turn_kind = "explicit_resend"
    return {
        "can_send": can_send,
        "turn_kind": turn_kind,
        "block_reason": block_reason,
        "already_sent": already_sent,
        "explicit_resend": explicit_resend,
    }


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
    return any(term in compact for term in ("再发", "重新发", "没收到", "发付款", "发收款", "发链接", "发预约金", "预约金发", "付款入口", "收款入口", "支付入口", "预约金入口"))


def _explicit_payment_send_request(text: str) -> bool:
    compact = _compact(text)
    if any(term in compact for term in ("发付款", "发收款", "发链接", "发预约金", "预约金发", "付款入口", "收款入口", "支付入口", "预约金入口")):
        return True
    return any(term in compact for term in ("怎么付", "哪里付", "现在付", "我付", "交预约金", "付预约金", "锁名额", "留名额"))


def _high_intent_payment_request(text: str) -> bool:
    compact = _compact(text)
    if any(term in compact for term in ("不报名", "不预约", "不去了", "算了")):
        return False
    return any(term in compact for term in ("报名", "我要约", "想预约", "可以预约", "帮我约", "我要去", "想去", "定下来", "就这个时间", "这个时间可以"))


def _payment_fee_refusal(text: str) -> bool:
    compact = _compact(text)
    if any(
        term in compact
        for term in (
            "不想付",
            "不想交",
            "不交预约金",
            "不付预约金",
            "先不付",
            "先不交",
            "不要发预约金",
            "别发预约金",
            "不用发预约金",
            "不需要发预约金",
            "到店再付",
            "去了再付",
            "不付可以",
            "不交可以",
        )
    ):
        return True
    payment_terms = ("预约金", "定金", "订金", "10元", "十元", "十块", "10块", "付款", "支付")
    direct_visit_terms = ("直接去", "直接过去", "到店付", "到店再说", "现场付")
    return any(term in compact for term in payment_terms) and any(term in compact for term in direct_visit_terms)


def _payment_fee_explanation(text: str) -> bool:
    compact = _compact(text)
    if _explicit_payment_send_request(compact):
        return False
    payment_terms = ("预约金", "定金", "订金", "10元", "十元", "十块", "10块", "尾款")
    explain_terms = ("为什么", "干嘛", "做什么", "能退", "可退", "退吗", "怎么退", "抵扣", "怎么抵", "额外收费", "另收费", "做完付", "做完付款", "到店付")
    return any(term in compact for term in payment_terms) and any(term in compact for term in explain_terms)


def _explicit_store_address_resend_request(text: str) -> bool:
    compact = _compact(text)
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
