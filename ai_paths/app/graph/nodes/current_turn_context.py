from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.contextual_short_message import is_contextual_short_message
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.nodes.turn_evidence_appointment import build_appointment_evidence
from app.graph.nodes.turn_evidence_history import build_history_evidence
from app.graph.nodes.turn_evidence_payment import build_payment_turn_evidence
from app.graph.nodes.turn_evidence_risk import build_risk_evidence
from app.graph.nodes.turn_evidence_store import build_store_evidence
from app.services.customer_payment_state import is_paid_deposit_state, resolved_payment_fact
from app.services.store_snapshot_service import store_snapshot_rows
from app.policies.constants import (
    KNOWN_STORE_NAMES,
    STORE_CONTEXT_FACT_TERMS,
    STORE_CONTEXT_REFERENCE_TERMS,
    TIME_REFERENCE_TERMS,
)
from app.services.risk_hold import health_risk_hold, is_hard_health_risk_hold


STORE_REFERENCE_HINTS = tuple(STORE_CONTEXT_REFERENCE_TERMS) + ("这家", "那家", "这个店", "刚才", "刚刚")
STORE_FACT_EXTRA_TERMS = ("位置", "定位", "发位置", "发个位置", "发一下位置", "发下位置")
APPOINTMENT_HINTS = ("预约", "约", "到店", "名额", "时间", "明天", "今天", "后天", "上午", "下午", "晚上")
PAYMENT_HINTS = ("预约金", "付款入口", "收款入口", "报名入口", "付款", "交10", "交 10", "10元", "10 元")
PAYMENT_EVIDENCE_TERMS = (
    "payment_collection",
    "预约金",
    "付款",
    "支付",
    "收款",
    "报名入口",
    "付款入口",
    "收款入口",
    "支付入口",
    "入口",
    "发吧",
    "现在付",
    "我付",
    "先付",
    "已付",
    "付了",
    "付完",
    "没收到",
    "打不开",
    "失败",
    "再发",
    "重发",
)
STRUCTURED_DEPOSIT_PAID_VALUES = {"1", "true", "yes", "y", "paid", "deposit_paid", "已支付", "已付款", "支付成功"}
STRUCTURED_DEPOSIT_FAILED_VALUES = {"failed", "failure", "payment_failed", "支付失败", "付款失败"}
NEXT_STEP_TERMS = ("付完然后呢", "付完了然后呢", "然后呢", "下一步", "接下来", "后面怎么", "之后怎么")
VISIT_CONFIRM_TERMS = ("可以", "就可以", "方便", "没问题", "行", "好", "到店", "过去", "来店", "去店")
LOCATION_KEYS = ("city", "current_city", "district", "area", "region", "address_region", "intent_city", "intent_area")


def build_current_turn_context(
    state: dict[str, Any],
    *,
    current_known_store: dict[str, Any] | None = None,
    sent_message_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = str(state.get("normalized_content") or state.get("content") or "").strip()
    sent_summary = (
        sent_message_summary
        if isinstance(sent_message_summary, dict)
        else sent_message_summary_for_model(state)
    )
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    is_short = is_contextual_short_message(content)
    is_reference = is_context_reference_message(content)
    last_assistant = _last_assistant_text(history)
    store_anchor = current_store_anchor_from_state(
        state,
        current_known_store=current_known_store,
        allow_profile=False,
        prefer_recent=is_short or is_reference or _has_store_fact_request(content),
    )
    appointment = _merge_appointments(_appointment_from_current_message(content), confirmed_appointment_from_state(state))
    last_action = _last_assistant_action(last_assistant)
    deposit_state = _structured_deposit_state(state, sent_summary=sent_summary)
    payment_evidence = _payment_evidence(state, sent_summary=sent_summary, last_assistant=last_assistant)
    risk_hold = health_risk_hold(state)
    location_missing = _missing_location_slots(state, store_anchor=store_anchor)
    resolved_slots = _resolved_slots(
        state,
        appointment=appointment,
        store_anchor=store_anchor,
        deposit_state=deposit_state,
        risk_hold=risk_hold,
    )
    current_time_confirmed = _is_current_turn_time_confirmation(content, appointment)
    next_step_clarification = _is_next_step_clarification(content)
    registration_evidence = _registration_evidence(state)
    context_hints = _context_hints(
        content=content,
        is_short=is_short,
        is_reference=is_reference,
        store_anchor=store_anchor,
        appointment=appointment,
        last_assistant_action=last_action,
        deposit_state=deposit_state,
        payment_evidence=payment_evidence,
        risk_hold=risk_hold,
        location_missing=location_missing,
        current_time_confirmed=current_time_confirmed,
        next_step_clarification=next_step_clarification,
    )
    blocked_actions = _blocked_actions(
        deposit_state=deposit_state,
        risk_hold=risk_hold,
        appointment=appointment,
        location_missing=location_missing,
    )
    binding_source = _binding_source(
        is_short=is_short,
        is_reference=is_reference,
        last_assistant=last_assistant,
        store_anchor=store_anchor,
    )
    output: dict[str, Any] = {
        "is_contextual_short_message": is_short,
        "binding_source": binding_source,
    }
    if context_hints:
        output["context_hints"] = context_hints
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
    if payment_evidence:
        output["payment_evidence"] = payment_evidence
    if registration_evidence:
        output["registration_evidence"] = registration_evidence
    if resolved_slots:
        output["resolved_slots"] = resolved_slots
    visible_missing_slots = _visible_missing_slots(
        is_reference=is_reference,
        content=content,
        appointment=appointment,
        location_missing=location_missing,
    )
    if visible_missing_slots:
        output["missing_slots"] = visible_missing_slots
    if blocked_actions:
        output["blocked_actions"] = blocked_actions
    turn_evidence = _turn_evidence(
        history=history,
        is_short=is_short,
        is_reference=is_reference,
        binding_source=binding_source,
        last_assistant_action=last_action,
        last_assistant=last_assistant,
        store_anchor=store_anchor,
        appointment=appointment,
        visible_missing_slots=visible_missing_slots,
        blocked_actions=blocked_actions,
        current_time_confirmed=current_time_confirmed,
        next_step_clarification=next_step_clarification,
        deposit_state=deposit_state,
        payment_evidence=payment_evidence,
        registration_evidence=registration_evidence,
        risk_hold=risk_hold,
        sent_summary=sent_summary,
    )
    if turn_evidence:
        output["turn_evidence"] = turn_evidence
    return _drop_empty(output)


def _turn_evidence(
    *,
    history: list[Any],
    is_short: bool,
    is_reference: bool,
    binding_source: str,
    last_assistant_action: str,
    last_assistant: str,
    store_anchor: dict[str, Any],
    appointment: dict[str, Any],
    visible_missing_slots: list[str],
    blocked_actions: list[str],
    current_time_confirmed: bool,
    next_step_clarification: bool,
    deposit_state: str,
    payment_evidence: dict[str, Any],
    registration_evidence: dict[str, Any],
    risk_hold: dict[str, Any],
    sent_summary: dict[str, Any],
) -> dict[str, Any]:
    return _drop_empty(
        {
            "history_evidence": build_history_evidence(
                is_short_message=is_short,
                is_reference_message=is_reference,
                binding_source=binding_source,
                last_assistant_action=last_assistant_action,
                last_assistant_text=last_assistant,
                history=history,
            ),
            "store_evidence": build_store_evidence(
                store_anchor,
                store_address_delivery=(sent_summary or {}).get("store_address_delivery")
                if isinstance(sent_summary, dict)
                else {},
                store_anchor_fact=(sent_summary or {}).get("store_anchor_fact")
                if isinstance(sent_summary, dict)
                else {},
            ),
            "appointment_evidence": build_appointment_evidence(
                appointment=appointment,
                missing_slots=visible_missing_slots,
                blocked_actions=blocked_actions,
                current_time_confirmed=current_time_confirmed,
                next_step_clarification=next_step_clarification,
            ),
            "payment_evidence": build_payment_turn_evidence(
                deposit_state=deposit_state,
                payment_evidence=payment_evidence,
                blocked_actions=blocked_actions,
            ),
            "registration_evidence": registration_evidence,
            "risk_evidence": build_risk_evidence(risk_hold),
            "evidence_conflicts": _evidence_conflicts(store_anchor=store_anchor),
            "source_policy": "evidence_only_planner_decides_business_action",
        }
    )


def _registration_evidence(state: dict[str, Any]) -> dict[str, Any]:
    return _registration_evidence_from_basic(state)


def _registration_evidence_from_basic(state: dict[str, Any]) -> dict[str, Any]:
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    registration = basic.get("registration_state") if isinstance(basic.get("registration_state"), dict) else {}
    name = str(basic.get("customer_name") or "").strip()
    phone = re.sub(r"\D", "", str(basic.get("phone") or ""))
    output = {
        "customer_name_collected": bool(name) or bool(registration.get("customer_name_collected")),
        "phone_collected": len(phone) == 11 or bool(registration.get("phone_collected")),
        "mobile_sync_status": str(registration.get("mobile_sync_status") or ""),
    }
    return _drop_empty(output)


def _evidence_conflicts(*, store_anchor: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(store_anchor, dict) and store_anchor.get("ambiguous"):
        return [
            _drop_empty(
                {
                    "type": "store_ambiguous",
                    "source": store_anchor.get("source"),
                    "matched_store_names": store_anchor.get("matched_store_names") or [],
                }
            )
        ]
    return []


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
        if source in {"appointment_cache", "appointment_context"} and _appointment_mapping_low_confidence(state, mapping):
            continue
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
    if _appointment_from_current_message(text) and _recent_history_has_context_task(state):
        return True
    if any(term in text for term in ("预约", "改约", "取消", "档期", "已约", "约过", "预约记录")):
        return True
    return False


def _context_hints(
    *,
    content: str,
    is_short: bool,
    is_reference: bool,
    store_anchor: dict[str, Any],
    appointment: dict[str, Any],
    last_assistant_action: str,
    deposit_state: str,
    payment_evidence: dict[str, Any],
    risk_hold: dict[str, Any],
    location_missing: list[str],
    current_time_confirmed: bool,
    next_step_clarification: bool,
) -> list[str]:
    hints: list[str] = []
    if is_short:
        hints.append("short_message")
    if is_reference:
        hints.append("reference_message")
    if store_anchor and (is_reference or _has_store_fact_request(content)):
        hints.append("store_context_available")
    if appointment:
        hints.append("appointment_context_available")
    if current_time_confirmed:
        hints.append("current_message_has_time_reference")
    if next_step_clarification:
        hints.append("current_message_asks_next_step")
    if payment_evidence:
        hints.append("payment_context_available")
    if last_assistant_action != "none":
        hints.append(f"last_assistant_action:{last_assistant_action}")
    if deposit_state == "deposit_paid":
        hints.append("structured_deposit_paid")
    if is_hard_health_risk_hold(risk_hold):
        hints.append("current_hard_health_risk")
    elif risk_hold:
        hints.append("health_risk_context")
    if location_missing and (appointment or is_reference or _has_store_fact_request(content)):
        hints.append("store_or_region_missing")
    return list(dict.fromkeys(hints))


def _binding_source(
    *,
    is_short: bool,
    is_reference: bool,
    last_assistant: str,
    store_anchor: dict[str, Any],
) -> str:
    if is_reference and store_anchor:
        return "recent_store"
    if is_short and last_assistant:
        return "last_assistant"
    return "none"


def _last_assistant_action(last_assistant: str) -> str:
    text = str(last_assistant or "")
    if not text:
        return "none"
    if "payment_collection" in text:
        return "sent_payment_collection"
    return "text_reply"


def _structured_deposit_state(state: dict[str, Any], *, sent_summary: dict[str, Any]) -> str:
    resolved = _resolved_payment_fact_for_state(state)
    if is_paid_deposit_state(resolved.get("deposit_state")):
        return "deposit_paid"
    if resolved.get("deposit_state") == "required_unpaid":
        return "required_unpaid"
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    if str(image_info.get("payment_result") or "").strip().lower() == "failed":
        return "payment_failed"
    if sent_summary.get("payment_collection_sent") or _payment_collection_sent_from_events(state):
        return "payment_link_sent"
    return "unknown"


def _payment_evidence(state: dict[str, Any], *, sent_summary: dict[str, Any], last_assistant: str) -> dict[str, Any]:
    current_text = str(state.get("normalized_content") or state.get("content") or "").strip()
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    event_count = _payment_collection_sent_count_from_events(state)
    frequency = sent_summary.get("payment_collection") if isinstance(sent_summary.get("payment_collection"), dict) else {}
    sent_count = frequency.get("total_count") or sent_summary.get("payment_collection_count") or event_count or 0
    recent_payment_texts: list[str] = []
    for item in history[-12:]:
        text = _conversation_item_text(item)
        if text and _looks_payment_related(text):
            recent_payment_texts.append(text[:600])
    evidence = {
        "sent_payment_collection": bool(sent_summary.get("payment_collection_sent") or event_count),
        "payment_collection_count": sent_count,
        "payment_collection_frequency": frequency,
        "last_assistant_payment_text": last_assistant[:600] if _looks_payment_related(last_assistant) else "",
        "current_payment_text": current_text[:600] if _looks_payment_related(current_text) else "",
        "recent_payment_texts": recent_payment_texts[-6:],
        "source_policy": "evidence_only_planner_decides_payment_state",
        "structured_payment_fact": _resolved_payment_fact_for_state(state),
    }
    return _drop_empty(evidence)


def _resolved_payment_fact_for_state(state: dict[str, Any]) -> dict[str, Any]:
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    basic_info = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    stored = basic_info.get("deposit_state")
    if isinstance(stored, dict):
        existing_state = str(stored.get("status") or stored.get("deposit_state") or "")
        existing_source = str(stored.get("source") or "")
    else:
        existing_state = str(stored or "")
        existing_source = "customer_memory" if existing_state else ""
    return resolved_payment_fact(
        orders=orders,
        image_info=state.get("image_info"),
        existing_state=existing_state,
        existing_source=existing_source,
        existing_fact=stored,
    )


def _payment_collection_sent_from_events(state: dict[str, Any]) -> bool:
    return _payment_collection_sent_count_from_events(state) > 0


def _payment_collection_sent_count_from_events(state: dict[str, Any]) -> int:
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    count = 0
    for event in events[-30:]:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type == "payment_collection_sent":
            count += 1
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        if str(facts.get("message_type") or facts.get("type") or "").strip() == "payment_collection":
            count += 1
    return count


def _deposit_signal_mappings(state: dict[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for key in ("customer_profile", "customer_basic_info", "customer_context", "appointment_cache", "request_context", "sent_message_summary"):
        value = state.get(key)
        if isinstance(value, dict):
            mappings.append(value)
    events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in events[-20:]:
        if not isinstance(event, dict):
            continue
        facts = event.get("facts")
        if isinstance(facts, dict):
            mappings.append(facts)
    return mappings


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in STRUCTURED_DEPOSIT_PAID_VALUES


def _failed_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in STRUCTURED_DEPOSIT_FAILED_VALUES


def _looks_payment_related(text: str) -> bool:
    compact = "".join(str(text or "").split())
    return bool(compact and any(term in compact for term in PAYMENT_EVIDENCE_TERMS))


def _appointment_from_current_message(content: str) -> dict[str, Any]:
    if not _has_current_time_reference(content):
        return {}
    return _appointment_from_mapping({"summary": content}, source="current_message")


def _merge_appointments(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    if not primary:
        return secondary
    if not secondary:
        return primary
    return _drop_empty(
        {
            "date": primary.get("date") or secondary.get("date"),
            "time": primary.get("time") or secondary.get("time"),
            "store_id": primary.get("store_id") or secondary.get("store_id"),
            "store_name": primary.get("store_name") or secondary.get("store_name"),
            "source": primary.get("source") or secondary.get("source"),
            "store_source": secondary.get("source") if secondary.get("store_id") or secondary.get("store_name") else "",
        }
    )


def _has_current_time_reference(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    if _extract_date(text) or _extract_time(text):
        return True
    return any(term in text for term in ("上午", "下午", "中午", "晚上", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周末"))


def _is_current_turn_time_confirmation(content: str, appointment: dict[str, Any]) -> bool:
    if not appointment:
        return False
    text = str(content or "")
    if not text:
        return False
    if _extract_date(text) or _extract_time(text):
        return True
    return any(term in text for term in VISIT_CONFIRM_TERMS) and _has_current_time_reference(text)


def _is_next_step_clarification(content: str) -> bool:
    text = "".join(str(content or "").split())
    return any(term in text for term in NEXT_STEP_TERMS)


def _mentions_visit_or_detection(content: str) -> bool:
    text = str(content or "")
    return any(term in text for term in ("到店", "检测", "检查", "评估", "适合", "明天", "今天", "后天", "上午", "下午", "晚上"))


def _is_short_followup_greeting(content: str) -> bool:
    text = "".join(str(content or "").split())
    return text in {"你好", "您好", "在吗", "有人吗", "还在吗", "hello", "hi"}


def _missing_location_slots(state: dict[str, Any], *, store_anchor: dict[str, Any]) -> list[str]:
    if isinstance(store_anchor, dict) and not store_anchor.get("ambiguous") and (store_anchor.get("store_name") or store_anchor.get("store_id")):
        return []
    has_city_or_region = _has_city_or_region_slot(state)
    return ["store"] if has_city_or_region else ["city_or_region", "store"]


def _has_city_or_region_slot(state: dict[str, Any]) -> bool:
    content = str(state.get("normalized_content") or state.get("content") or "")
    if any(word in content for word in ("市", "区", "县", "镇", "附近")) and len(content) <= 40:
        return True
    for mapping in _location_slot_mappings(state):
        for key in LOCATION_KEYS:
            if str(mapping.get(key) or "").strip():
                return True
    return False


def _location_slot_mappings(state: dict[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for key in ("request_context", "customer_basic_info", "customer_context", "appointment_cache"):
        value = state.get(key)
        if isinstance(value, dict):
            mappings.append(value)
            for nested_key in ("appointment", "appointment_info", "appointment_info_v2"):
                nested = value.get(nested_key)
                if isinstance(nested, dict):
                    mappings.append(nested)
    return mappings


def _resolved_slots(
    state: dict[str, Any],
    *,
    appointment: dict[str, Any],
    store_anchor: dict[str, Any],
    deposit_state: str,
    risk_hold: dict[str, Any],
) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    if isinstance(store_anchor, dict) and not store_anchor.get("ambiguous"):
        if store_anchor.get("store_name") or store_anchor.get("store_id"):
            slots["store"] = _store_for_context(store_anchor)
    if appointment.get("date"):
        slots["visit_date"] = appointment.get("date")
    if appointment.get("time"):
        slots["visit_time"] = appointment.get("time")
    if _has_city_or_region_slot(state):
        slots["city_or_region"] = True
    if deposit_state == "deposit_paid":
        slots["deposit"] = "paid"
    elif deposit_state != "unknown":
        slots["deposit"] = deposit_state
    if is_hard_health_risk_hold(risk_hold):
        slots["health_check"] = "required"
    elif risk_hold:
        slots["health_check"] = "advisory"
    return _drop_empty(slots)


def _blocked_actions(
    *,
    deposit_state: str,
    risk_hold: dict[str, Any],
    appointment: dict[str, Any],
    location_missing: list[str],
) -> list[str]:
    blocked: list[str] = []
    if deposit_state == "deposit_paid":
        blocked.append("payment_collection")
    if appointment and location_missing:
        blocked.append("available_time")
    if is_hard_health_risk_hold(risk_hold):
        blocked.append("payment_collection")
    return list(dict.fromkeys(blocked))


def _visible_missing_slots(
    *,
    is_reference: bool,
    content: str,
    appointment: dict[str, Any],
    location_missing: list[str],
) -> list[str]:
    if not location_missing:
        return []
    if is_reference or _has_store_fact_request(content) or appointment:
        return location_missing
    return []


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
        if source in {"appointment_cache", "appointment_context"} and _appointment_mapping_low_confidence(state, mapping):
            continue
        store = _compact_store(mapping if isinstance(mapping, dict) else {}, source=source)
        if store:
            return store
    return {}


def _appointment_mapping_low_confidence(state: dict[str, Any], mapping: Any) -> bool:
    if not isinstance(mapping, dict):
        return False
    appointment_store = _compact_store(mapping, source="appointment_context")
    if not appointment_store:
        return False
    content = str(state.get("normalized_content") or state.get("content") or "")
    if _content_mentions_store(content, appointment_store):
        return False
    current_store = _store_from_text(content, state, source="current_message")
    if _stores_conflict(appointment_store, current_store):
        return True
    recent_store = _store_from_recent_conversation(state)
    if _stores_conflict(appointment_store, recent_store):
        return True
    return False


def _content_mentions_store(content: str, store: dict[str, Any]) -> bool:
    text = str(content or "")
    store_name = str(store.get("store_name") or "").strip()
    store_id = str(store.get("store_id") or "").strip()
    if store_name and store_name in text:
        return True
    return bool(store_id and store_id in text)


def _stores_conflict(primary: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if not isinstance(candidate, dict) or not candidate:
        return False
    if candidate.get("ambiguous"):
        matched = {str(name or "").strip() for name in candidate.get("matched_store_names") or []}
        primary_name = str(primary.get("store_name") or "").strip()
        return bool(matched and primary_name and primary_name not in matched)
    primary_id = str(primary.get("store_id") or "").strip()
    candidate_id = str(candidate.get("store_id") or "").strip()
    if primary_id and candidate_id and primary_id != candidate_id:
        return True
    primary_name = str(primary.get("store_name") or "").strip()
    candidate_name = str(candidate.get("store_name") or "").strip()
    return bool(primary_name and candidate_name and primary_name != candidate_name)


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
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    name = str(basic.get("confirmed_store_name") or "").strip()
    if name and name not in seen:
        stores.append({"store_name": name, "store_id": basic.get("confirmed_store_id")})
        seen.add(name)
    for store in store_snapshot_rows():
        name = _store_name(store)
        if name and name not in seen:
            stores.append(store)
            seen.add(name)
    for name in KNOWN_STORE_NAMES:
        if name and name not in seen:
            stores.append({"store_name": name})
            seen.add(name)
    return stores


def _stores_matching_text(text: str, stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched = [store for store in stores if _store_name_matches_text(_store_name(store), str(text or ""))]
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
            "store_id": basic.get("confirmed_store_id"),
            "store_name": basic.get("confirmed_store_name"),
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
    relative_date_terms = {
        "今天",
        "明天",
        "后天",
        "本周",
        "这周",
        "下周",
        "下下周",
        "周六",
        "周日",
        "周末",
        "本月",
        "这个月",
        "下个月",
        "月初",
        "月底",
    }
    for word in TIME_REFERENCE_TERMS:
        if word in raw and word in relative_date_terms:
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


def _store_name_matches_text(name: str, text: str) -> bool:
    raw_name = str(name or "").strip()
    raw_text = str(text or "").strip()
    if raw_name and raw_text and (raw_name in raw_text or (len(raw_text) >= 4 and raw_text in raw_name)):
        return True
    normalized_name = _normalize_store_name_for_match(raw_name)
    normalized_text = _normalize_store_name_for_match(raw_text)
    return bool(
        normalized_name
        and normalized_text
        and len(normalized_name) >= 4
        and (normalized_name in normalized_text or (len(normalized_text) >= 4 and normalized_text in normalized_name))
    )


def _normalize_store_name_for_match(value: str) -> str:
    return re.sub(r"[，,。？?！!\s]", "", str(value or "")).replace("市", "").replace("百星", "")


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
