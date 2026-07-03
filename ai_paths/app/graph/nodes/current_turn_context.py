from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.contextual_short_message import is_contextual_short_message
from app.policies.constants import (
    KNOWN_STORE_NAMES,
    STORE_CONTEXT_FACT_TERMS,
    STORE_CONTEXT_REFERENCE_TERMS,
    TIME_REFERENCE_TERMS,
)


STORE_REFERENCE_HINTS = tuple(STORE_CONTEXT_REFERENCE_TERMS) + ("这家", "那家", "这个店", "刚才", "刚刚")
STORE_FACT_EXTRA_TERMS = ("位置", "定位", "发位置", "发个位置", "发一下位置", "发下位置")
APPOINTMENT_HINTS = ("预约", "约", "到店", "名额", "时间", "明天", "今天", "后天", "上午", "下午", "晚上")
PAYMENT_HINTS = ("预约金", "付款入口", "收款入口", "报名入口", "付款", "交10", "交 10", "10元", "10 元")


def build_current_turn_context(
    state: dict[str, Any],
    *,
    current_known_store: dict[str, Any] | None = None,
    sent_message_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = str(state.get("normalized_content") or state.get("content") or "").strip()
    sent_summary = sent_message_summary if isinstance(sent_message_summary, dict) else {}
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    is_short = is_contextual_short_message(content)
    is_reference = is_context_reference_message(content)
    last_assistant = _last_assistant_text(history)
    store_anchor = current_store_anchor_from_state(
        state,
        current_known_store=current_known_store,
        allow_profile=True,
        prefer_recent=is_short or is_reference or _has_store_fact_request(content),
    )
    appointment = confirmed_appointment_from_state(state)
    last_action = _last_assistant_action(last_assistant, sent_summary)
    deposit_state = _deposit_state(state, sent_summary=sent_summary, last_assistant=last_assistant)
    open_task = _open_task(
        content=content,
        is_short=is_short,
        is_reference=is_reference,
        store_anchor=store_anchor,
        appointment=appointment,
        last_assistant_action=last_action,
        deposit_state=deposit_state,
    )
    binding_source = _binding_source(
        is_short=is_short,
        is_reference=is_reference,
        open_task=open_task,
        last_assistant=last_assistant,
        store_anchor=store_anchor,
    )
    output: dict[str, Any] = {
        "is_contextual_short_message": is_short,
        "binding_source": binding_source,
        "open_task": open_task,
    }
    if is_reference:
        output["is_context_reference_message"] = True
    if last_action != "none":
        output["last_assistant_action"] = last_action
    if store_anchor:
        output["confirmed_store"] = _store_for_context(store_anchor)
        output["current_store_anchor"] = store_anchor
    if appointment:
        output["confirmed_appointment"] = appointment
    if deposit_state != "unknown":
        output["deposit_state"] = deposit_state
    reply_anchor = _reply_anchor(
        open_task=open_task,
        store_anchor=store_anchor,
        appointment=appointment,
        deposit_state=deposit_state,
        last_assistant=last_assistant,
        binding_source=binding_source,
    )
    if reply_anchor:
        output["reply_anchor"] = reply_anchor
    return _drop_empty(output)


def current_store_anchor_from_state(
    state: dict[str, Any],
    *,
    current_known_store: dict[str, Any] | None = None,
    allow_profile: bool,
    prefer_recent: bool = False,
) -> dict[str, Any]:
    request_store = _store_from_request(state)
    if request_store:
        return request_store

    if isinstance(current_known_store, dict):
        if current_known_store.get("ambiguous"):
            return {
                "ambiguous": True,
                "matched_store_names": current_known_store.get("matched_store_names") or [],
                "source": str(current_known_store.get("source") or "current_known_store"),
            }
        known_source = str(current_known_store.get("source") or "").strip()
        known_store = _compact_store(current_known_store, source=known_source or "current_known_store")
        if known_store and known_source in {"request", "current_message"}:
            return known_store

    current_message_store = _store_from_text(
        str(state.get("normalized_content") or state.get("content") or ""),
        state,
        source="current_message",
    )
    if current_message_store:
        return current_message_store

    if prefer_recent:
        for store in (
            _store_from_appointment_context(state),
            _store_from_history_events(state),
            _store_from_recent_conversation(state),
        ):
            if store:
                return store

    if isinstance(current_known_store, dict):
        known_source = str(current_known_store.get("source") or "").strip()
        known_store = _compact_store(current_known_store, source=known_source or "current_known_store")
        if known_store and known_source not in {"customer_profile"}:
            return known_store

    if allow_profile:
        profile_store = _store_from_profile(state)
        if profile_store:
            return profile_store
    return {}


def confirmed_appointment_from_state(state: dict[str, Any]) -> dict[str, Any]:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    for mapping, source in (
        (state, "request"),
        (request_context, "request_context"),
        (state.get("appointment_cache"), "appointment_cache"),
        (_customer_context_mapping(state, "appointment"), "appointment_context"),
        (_customer_context_mapping(state, "appointment_info"), "appointment_context"),
        (_customer_context_mapping(state, "appointment_info_v2"), "appointment_context"),
        (_latest_appointment_fact(state), "fact_envelope"),
    ):
        appointment = _appointment_from_mapping(mapping, source=source)
        if appointment:
            return appointment
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    appointment = _appointment_from_mapping(
        {"date": basic.get("intent_date"), "time": basic.get("intent_time")},
        source="customer_profile",
    )
    if appointment:
        return appointment
    return _appointment_from_history(state)


def is_context_reference_message(content: str) -> bool:
    text = "".join(str(content or "").split())
    if not text:
        return False
    if any(term in text for term in STORE_REFERENCE_HINTS):
        return True
    if len(text) <= 16 and _has_store_fact_request(text):
        return True
    if len(text) <= 18 and any(term in text for term in ("发地址", "发位置", "发定位", "发导航")):
        return True
    return False


def can_use_contextual_store_for_message(content: str, state: dict[str, Any]) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if is_contextual_short_message(text) and _recent_history_has_context_task(state):
        return True
    if is_context_reference_message(text):
        return True
    if any(term in text for term in ("预约", "改约", "取消", "档期", "已约", "约过", "预约记录")):
        return True
    return False


def _open_task(
    *,
    content: str,
    is_short: bool,
    is_reference: bool,
    store_anchor: dict[str, Any],
    appointment: dict[str, Any],
    last_assistant_action: str,
    deposit_state: str,
) -> str:
    if deposit_state == "payment_link_sent" or last_assistant_action in {"sent_payment_collection", "asked_for_payment"}:
        return "deposit_push"
    if appointment and (is_short or _has_appointment_hint(content) or last_assistant_action == "asked_for_time_or_store"):
        return "appointment_confirm"
    if store_anchor and (is_reference or _has_store_fact_request(content)):
        return "store_followup"
    return "none"


def _binding_source(
    *,
    is_short: bool,
    is_reference: bool,
    open_task: str,
    last_assistant: str,
    store_anchor: dict[str, Any],
) -> str:
    if (is_short or is_reference) and open_task != "none":
        return "open_task"
    if is_reference and store_anchor:
        return "recent_store"
    if is_short and last_assistant:
        return "last_assistant"
    return "none"


def _last_assistant_action(last_assistant: str, sent_summary: dict[str, Any]) -> str:
    text = str(last_assistant or "")
    if sent_summary.get("payment_collection_sent") or "payment_collection" in text or "预约金收款" in text:
        return "sent_payment_collection"
    if any(term in text for term in ("付款入口", "收款入口", "报名入口", "预约金入口", "交10", "交 10")):
        return "sent_payment_collection"
    if any(term in text for term in ("预约金", "到店抵扣", "不做退10", "锁名额")):
        return "asked_for_payment"
    if any(term in text for term in ("什么时候", "几点", "时间", "哪家门店", "哪个门店", "哪个区", "城市", "姓名", "电话")):
        return "asked_for_time_or_store"
    return "none"


def _deposit_state(state: dict[str, Any], *, sent_summary: dict[str, Any], last_assistant: str) -> str:
    recent_text = _recent_text(state, limit=8)
    if sent_summary.get("payment_collection_sent") or any(term in recent_text for term in ("payment_collection", "预约金收款", "付款入口", "收款入口")):
        return "payment_link_sent"
    if any(term in f"{last_assistant}\n{recent_text}" for term in ("预约金", "到店抵扣", "不做退10", "锁活动名额", "锁名额")):
        return "deposit_explained"
    return "unknown"


def _reply_anchor(
    *,
    open_task: str,
    store_anchor: dict[str, Any],
    appointment: dict[str, Any],
    deposit_state: str,
    last_assistant: str,
    binding_source: str,
) -> str:
    store_name = str(store_anchor.get("store_name") or store_anchor.get("name") or "").strip() if isinstance(store_anchor, dict) else ""
    appointment_bits = []
    if appointment.get("date"):
        appointment_bits.append(str(appointment.get("date")))
    if appointment.get("time"):
        appointment_bits.append(str(appointment.get("time")))
    appointment_text = " ".join(appointment_bits)
    facts = "，".join(part for part in (store_name, appointment_text) if part)
    if open_task == "deposit_push":
        suffix = f"已确认{facts}，" if facts else ""
        if deposit_state == "payment_link_sent":
            return f"客户在催刚才的预约金/预约安排；{suffix}应承接预约金入口或到店抵扣顾虑，不要重新问城市或项目。"
        return f"客户在承接预约金解释；{suffix}不要重新问城市或项目。"
    if open_task == "appointment_confirm":
        suffix = f"已确认{facts}，" if facts else ""
        return f"客户在承接刚才的预约安排；{suffix}应继续确认预约，不要冷启动。"
    if open_task == "store_followup":
        suffix = f"当前门店是{store_name}，" if store_name else ""
        return f"客户在追问刚才门店；{suffix}应围绕这家门店承接，不要重新问城市或项目。"
    if binding_source == "last_assistant" and last_assistant:
        return f"客户在回应上一轮助手问题：{last_assistant[:120]}"
    return ""


def _store_from_request(state: dict[str, Any]) -> dict[str, Any]:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    for source in (state, request_context):
        if not isinstance(source, dict):
            continue
        store = _compact_store(
            {
                "store_id": source.get("confirmed_store_id") or source.get("store_id"),
                "store_name": source.get("confirmed_store_name") or source.get("store_name"),
                "city": source.get("city") or source.get("current_city"),
            },
            source="request",
        )
        if store:
            return store
    return {}


def _store_from_appointment_context(state: dict[str, Any]) -> dict[str, Any]:
    for mapping, source in (
        (state.get("appointment_cache"), "appointment_cache"),
        (_customer_context_mapping(state, "appointment"), "appointment_context"),
        (_customer_context_mapping(state, "appointment_info"), "appointment_context"),
        (_customer_context_mapping(state, "appointment_info_v2"), "appointment_context"),
        (_latest_appointment_fact(state), "fact_envelope"),
    ):
        store = _compact_store(mapping if isinstance(mapping, dict) else {}, source=source)
        if store:
            return store
    return {}


def _store_from_history_events(state: dict[str, Any]) -> dict[str, Any]:
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in reversed(events[-20:]):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        if event_type in {"store_matched", "store_address_sent", "appointment_confirmed", "payment_collection_sent"}:
            store = _compact_store(facts, source="history_event")
            if store:
                return store
    return {}


def _store_from_recent_conversation(state: dict[str, Any]) -> dict[str, Any]:
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    stores = _store_candidates(state)
    store_by_id = {
        str(store.get("store_id") or store.get("id") or "").strip(): store
        for store in stores
        if str(store.get("store_id") or store.get("id") or "").strip()
    }
    chunks = [_conversation_item_text(item) for item in history[-8:]]
    chunks = [chunk for chunk in chunks if chunk]
    for chunk in reversed(chunks):
        match = re.search(r"(?:store_id|门店ID)\s*[=:：]\s*(\d+)", chunk, flags=re.IGNORECASE)
        if match and match.group(1) in store_by_id:
            return _compact_store(store_by_id[match.group(1)], source="recent_store_address_message")
    matched_overall: list[dict[str, Any]] = []
    for chunk in chunks:
        matched_overall.extend(_stores_matching_text(chunk, stores))
    matched_overall = _dedupe_stores(matched_overall)
    if len(matched_overall) > 1:
        return {
            "ambiguous": True,
            "matched_store_names": [_store_name(store) for store in matched_overall[:5]],
            "source": "recent_conversation",
        }
    if len(matched_overall) == 1:
        return _compact_store(matched_overall[0], source="recent_conversation")
    return {}


def _store_from_text(text: str, state: dict[str, Any], *, source: str) -> dict[str, Any]:
    matched = _stores_matching_text(text, _store_candidates(state))
    if len(matched) == 1:
        return _compact_store(matched[0], source=source)
    if len(matched) > 1:
        return {
            "ambiguous": True,
            "matched_store_names": [_store_name(store) for store in matched[:5]],
            "source": source,
        }
    return {}


def _store_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = [store for store in knowledge.get("stores", []) if isinstance(store, dict)] if isinstance(knowledge.get("stores"), list) else []
    seen = {_store_name(store) for store in stores if _store_name(store)}
    for name in KNOWN_STORE_NAMES:
        if name and name not in seen:
            stores.append({"store_name": name})
            seen.add(name)
    return stores


def _stores_matching_text(text: str, stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched = [store for store in stores if _store_name(store) and _store_name(store) in str(text or "")]
    return _without_subsumed_stores(_dedupe_stores(matched))


def _dedupe_stores(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for store in stores:
        key = (str(store.get("store_id") or store.get("id") or "").strip(), _store_name(store))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        output.append(store)
    return output


def _without_subsumed_stores(stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [_store_name(store) for store in stores]
    output: list[dict[str, Any]] = []
    for store, name in zip(stores, names):
        if any(name != other and name in other for other in names):
            continue
        output.append(store)
    return output


def _store_from_profile(state: dict[str, Any]) -> dict[str, Any]:
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    return _compact_store(
        {
            "store_id": basic.get("preferred_store_id") or basic.get("confirmed_store_id"),
            "store_name": basic.get("preferred_store_name") or basic.get("confirmed_store_name"),
            "city": basic.get("city") or basic.get("current_city"),
        },
        source="customer_profile",
    )


def _compact_store(value: dict[str, Any], *, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    store_id = str(value.get("store_id") or value.get("id") or "").strip()
    store_name = str(value.get("store_name") or value.get("name") or value.get("store") or value.get("preferred_store") or "").strip()
    if not (store_id or store_name):
        return {}
    return _drop_empty(
        {
            "store_id": store_id,
            "store_name": store_name,
            "city": str(value.get("city") or "").strip(),
            "district": str(value.get("district") or "").strip(),
            "source": source,
        }
    )


def _store_for_context(store: dict[str, Any]) -> dict[str, Any]:
    if store.get("ambiguous"):
        return {"ambiguous": True, "matched_store_names": store.get("matched_store_names") or [], "source": store.get("source")}
    return _drop_empty(
        {
            "store_id": store.get("store_id"),
            "store_name": store.get("store_name"),
            "source": store.get("source"),
        }
    )


def _appointment_from_mapping(value: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_time = " ".join(
        str(value.get(key) or "")
        for key in ("appointment_time", "appoint_time", "time", "intent_time", "slot", "summary")
        if value.get(key)
    )
    raw_date = " ".join(
        str(value.get(key) or "")
        for key in ("appointment_date", "appoint_date", "date", "intent_date", "day", "summary")
        if value.get(key)
    )
    appointment_date = _extract_date(raw_date or raw_time)
    appointment_time = _extract_time(raw_time or raw_date)
    store = _compact_store(value, source=source)
    if not (appointment_date or appointment_time or store):
        return {}
    return _drop_empty(
        {
            "date": appointment_date,
            "time": appointment_time,
            "store_id": store.get("store_id"),
            "store_name": store.get("store_name"),
            "source": source,
        }
    )


def _appointment_from_history(state: dict[str, Any]) -> dict[str, Any]:
    text = _recent_text(state, limit=10)
    date = _extract_date(text)
    time = _extract_time(text)
    if not (date or time):
        return {}
    return _drop_empty({"date": date, "time": time, "source": "recent_conversation"})


def _latest_appointment_fact(state: dict[str, Any]) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts") if isinstance(fact_envelope.get("structured_facts"), dict) else {}
    facts = structured.get("appointment_facts") if isinstance(structured.get("appointment_facts"), list) else []
    for item in reversed(facts):
        if isinstance(item, dict):
            return item
    return {}


def _customer_context_mapping(state: dict[str, Any], key: str) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    value = customer_context.get(key)
    return value if isinstance(value, dict) else {}


def _extract_date(text: str) -> str:
    raw = str(text or "")
    match = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", raw)
    if match:
        return match.group(0).replace("/", "-")
    match = re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]", raw)
    if match:
        return re.sub(r"\s+", "", match.group(0))
    for word in TIME_REFERENCE_TERMS:
        if word in raw and word in {"今天", "明天", "后天", "周六", "周日", "周末"}:
            return word
    return ""


def _extract_time(text: str) -> str:
    raw = str(text or "")
    match = re.search(r"(?P<hour>\d{1,2})\s*[:：]\s*(?P<minute>\d{2})", raw)
    if match:
        return f"{int(match.group('hour')):02d}:{int(match.group('minute')):02d}"
    match = re.search(r"(?P<hour>\d{1,2})\s*点\s*(?P<half>半)?", raw)
    if match:
        minute = "30" if match.group("half") else "00"
        return f"{int(match.group('hour')):02d}:{minute}"
    return ""


def _recent_history_has_context_task(state: dict[str, Any]) -> bool:
    text = _recent_text(state, limit=8)
    if any(term in text for term in PAYMENT_HINTS):
        return True
    if any(term in text for term in APPOINTMENT_HINTS):
        return True
    if any(term in text for term in STORE_CONTEXT_FACT_TERMS):
        return True
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    return any(str(event.get("event_type") or "") in {"payment_collection_sent", "store_address_sent", "store_matched"} for event in events if isinstance(event, dict))


def _has_appointment_hint(content: str) -> bool:
    return any(term in str(content or "") for term in APPOINTMENT_HINTS)


def _has_store_fact_request(content: str) -> bool:
    text = str(content or "")
    return any(term in text for term in tuple(STORE_CONTEXT_FACT_TERMS) + STORE_FACT_EXTRA_TERMS)


def _recent_text(state: dict[str, Any], *, limit: int) -> str:
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    return "\n".join(_conversation_item_text(item) for item in history[-limit:])


def _last_assistant_text(history: list[Any]) -> str:
    for item in reversed(history or []):
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or item.get("sender_type") or "").lower()
            if role and role not in {"assistant", "staff", "service", "bot", "outgoing", "out", "sent", "send", "cs"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if text:
                return text
            continue
        raw = str(item or "").strip()
        for prefix in ("小贝:", "小贝：", "客服:", "客服：", "助手:", "助手：", "AI回复:", "AI回复："):
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
    return ""


def _conversation_item_text(item: Any) -> str:
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or content.get("url") or content.get("store_id") or "").strip()
        return str(content or "").strip()
    return str(item or "").strip()


def _store_name(store: dict[str, Any]) -> str:
    return str(store.get("store_name") or store.get("name") or "").strip()


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
