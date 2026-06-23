from __future__ import annotations

import re
from typing import Any

from app.graph.state import AgentState


def extract_system_action_events(state: AgentState) -> list[dict[str, object]]:
    """Record customer-visible actions already sent by the system.

    These events are not customer portrait data. They are operational memory for
    the next turn, so the final reply model can avoid repeating a store card,
    case image, offer explanation, or deposit explanation.
    """
    reply_messages = state.get("reply_messages") or []
    if not isinstance(reply_messages, list):
        return []

    events: list[dict[str, object]] = []
    for index, message in enumerate(reply_messages, start=1):
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip()
        content = message.get("content")
        if message_type == "store_address":
            event = _store_address_event(state, content, index)
            if event:
                events.append(event)
            continue
        if message_type == "image":
            event = _image_event(state, content, index)
            if event:
                events.append(event)
            continue
        if message_type == "payment_collection":
            event = _payment_collection_event(content, index, state)
            if event:
                events.append(event)
            continue
        if message_type == "human_handoff":
            events.append(_handoff_event(content, index, state))
            continue
        if message_type == "text":
            events.extend(_text_action_events(str(_message_text(content)), index, state))

    return events


def _store_address_event(state: AgentState, content: Any, index: int) -> dict[str, object] | None:
    store_id = _message_value(content, "store_id")
    if not store_id:
        return None
    store = _store_by_id(state, store_id)
    store_name = str(store.get("name") or "").strip()
    facts = {
        "store_id": store_id,
        "store_name": store_name,
        "address": str(store.get("address") or "").strip(),
        "distance": str(store.get("distance") or store.get("distance_text") or "").strip(),
    }
    return _event(
        state,
        index,
        "store_address_sent",
        f"已发送门店卡片：{store_name or store_id}。",
        _clean_facts(facts),
        "后续不要重复发送同一门店卡片；客户再次索要地址、导航或更换位置时再发送。",
    )


def _image_event(state: AgentState, content: Any, index: int) -> dict[str, object] | None:
    url = _message_value(content, "url") or _message_value(content, "image_url") or _message_value(content, "imageUrl")
    if not url:
        text = str(content or "")
        match = re.search(r"https?://[^\s<>'\")]+", text)
        url = match.group(0) if match else ""
    if not url:
        return None
    return _event(
        state,
        index,
        "case_image_sent",
        "已发送效果案例图片。",
        {"image_url": url},
        "后续优先解释案例参考和推进到店，不要连续重复发送同一张效果图。",
    )


def _payment_collection_event(content: Any, index: int, state: AgentState) -> dict[str, object] | None:
    amount = _message_value(content, "amount")
    return _event(
        state,
        index,
        "payment_collection_sent",
        "已发送10元预约金入口。",
        {"amount": amount or 10},
        "后续应承接客户支付、姓名电话和到店时间，不要重复解释整套报名规则。",
    )


def _handoff_event(content: Any, index: int, state: AgentState) -> dict[str, object]:
    reason = _message_value(content, "handoff_reason")
    return _event(
        state,
        index,
        "handoff_requested",
        "已请求专业同事协助。",
        {"handoff_reason": reason},
        "后续不要继续自动承诺处理结果，等待专业同事核对。",
    )


def _text_action_events(text: str, index: int, state: AgentState) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return events

    if any(term in compact for term in ("268", "周年庆", "活动价", "做付258", "报名")):
        events.append(
            _event(
                state,
                index,
                "offer_explained",
                "已向客户解释周年庆活动价或报名规则。",
                {"text": text[:180]},
                "后续只需轻量承接活动规则，不要反复完整复述价格套餐。",
            )
        )
    if "10元" in compact and any(term in compact for term in ("预约金", "抵扣", "退", "微信转", "登记")):
        events.append(
            _event(
                state,
                index,
                "deposit_explained",
                "已向客户解释10元预约金。",
                {"text": text[:180]},
                "后续应推进姓名电话、到店时间或支付动作，不要重复讲10元规则。",
            )
        )
    return events


def _event(
    state: AgentState,
    index: int,
    event_type: str,
    summary: str,
    facts: dict[str, object],
    impact: str,
) -> dict[str, object]:
    return {
        "event_id": f"evt_{state.get('request_id', 'unknown')}_system_{index}_{event_type}",
        "event_time": "",
        "event_type": event_type,
        "stage": str(state.get("sop_stage") or ""),
        "summary": summary,
        "facts": _clean_facts(facts),
        "impact": impact,
        "confidence": 0.9,
    }


def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return str(content or "")


def _message_value(content: Any, key: str) -> str:
    if isinstance(content, dict):
        return str(content.get(key) or "").strip()
    return ""


def _store_by_id(state: AgentState, store_id: str) -> dict[str, Any]:
    structured = _structured_facts(state)
    stores: list[Any] = []
    for key in ("store_facts",):
        value = structured.get(key)
        if isinstance(value, list):
            stores.extend(value)
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        stores.append(recommended)
    for item in stores:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or item.get("store_id") or "").strip() == store_id:
            return item
    return {}


def _structured_facts(state: AgentState) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts")
    return structured if isinstance(structured, dict) else {}


def _clean_facts(facts: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in facts.items() if value not in ("", None, [], {})}
