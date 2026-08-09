from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.sent_message_summary import sent_message_summary_for_model


_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_NAME_BEFORE_MOBILE_RE = re.compile(r"([\u4e00-\u9fff]{2,6})\s*1[3-9]\d{9}")
_PAYMENT_TYPES = {"payment_collection", "payment", "pay_card", "reservation_card"}


def build_conversation_state(state: dict[str, Any]) -> dict[str, Any]:
    """Build one evidence-backed fact snapshot for Planner and Reply.

    This function only extracts or combines facts. It does not choose a sales
    stage, objection, or next action.
    """

    turns = _dedupe_turns(state.get("conversation_turns"), state.get("conversation_history"))
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    sent = sent_message_summary_for_model(state)
    customer_fields = _customer_fields(basic, turns)
    delivery_progress = _delivery_progress(state, sent)
    payment_context = _payment_context(state)
    visit_context = _visit_context(state, basic)
    sop_progress = _sop_progress(state)
    payment_card_cooldown = _payment_card_cooldown(
        turns,
        state.get("history_events"),
        has_current_customer_message=bool(str(state.get("normalized_content") or "").strip()),
    )

    return _drop_empty(
        {
            "customer_fields": customer_fields,
            "delivery_progress": delivery_progress,
            "payment_context": payment_context,
            "visit_context": visit_context,
            "sop_progress": sop_progress,
            "payment_card_cooldown": payment_card_cooldown,
            "evidence_policy": "facts_only_model_owns_business_semantics",
        }
    )


def known_customer_field_names(conversation_state: Any) -> list[str]:
    snapshot = conversation_state if isinstance(conversation_state, dict) else {}
    fields = snapshot.get("customer_fields") if isinstance(snapshot.get("customer_fields"), dict) else {}
    return [
        key
        for key in ("name", "mobile", "location")
        if isinstance(fields.get(key), dict) and str(fields[key].get("status") or "") == "known"
    ]


def payment_card_cooldown_active(conversation_state: Any) -> bool:
    snapshot = conversation_state if isinstance(conversation_state, dict) else {}
    cooldown = snapshot.get("payment_card_cooldown") if isinstance(snapshot.get("payment_card_cooldown"), dict) else {}
    return bool(cooldown.get("active"))


def conversation_state_for_guard(state: dict[str, Any]) -> dict[str, Any]:
    existing = state.get("conversation_state")
    if isinstance(existing, dict) and existing:
        return existing
    return build_conversation_state(state)


def _customer_fields(basic: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    registration = basic.get("registration_state") if isinstance(basic.get("registration_state"), dict) else {}
    name = str(basic.get("customer_name") or "").strip()
    mobile = re.sub(r"\D", "", str(basic.get("phone") or ""))
    name_refs: list[str] = ["customer_memory"] if name else []
    mobile_refs: list[str] = ["customer_memory"] if len(mobile) == 11 else []

    for turn in turns:
        if str(turn.get("role") or "") != "customer":
            continue
        content = str(turn.get("content") or "").strip()
        ref = str(turn.get("message_ref") or "").strip()
        phone_match = _MOBILE_RE.search(content)
        if phone_match:
            mobile = phone_match.group(1)
            mobile_refs = [ref] if ref else []
            name_match = _NAME_BEFORE_MOBILE_RE.search(content)
            if name_match:
                name = name_match.group(1)
                name_refs = [ref] if ref else []

    city = str(basic.get("city") or "").strip()
    district = str(basic.get("district") or basic.get("area_or_landmark") or "").strip()
    output: dict[str, Any] = {
        "name": _field_fact(
            name,
            name_refs,
            known=bool(name) or bool(registration.get("customer_name_collected")),
        ),
        "mobile": _field_fact(
            mobile if len(mobile) == 11 else "",
            mobile_refs,
            known=len(mobile) == 11 or bool(registration.get("phone_collected")),
        ),
    }
    if city or district:
        output["location"] = _drop_empty(
            {
                "status": "known",
                "city": city,
                "district": district,
                "evidence_ids": ["customer_memory"],
            }
        )
    return output


def _field_fact(value: str, refs: list[str], *, known: bool) -> dict[str, Any]:
    return _drop_empty(
        {
            "status": "known" if known else "unknown",
            "value": value,
            "evidence_ids": list(dict.fromkeys(ref for ref in refs if ref)),
        }
    )


def _delivery_progress(state: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    evidence = state.get("sop_progress_evidence") if isinstance(state.get("sop_progress_evidence"), dict) else {}
    completed_categories = {
        str(item or "").strip()
        for item in evidence.get("completed_categories") or []
        if str(item or "").strip()
    }
    case_delivery = sent.get("case_image_delivery") if isinstance(sent.get("case_image_delivery"), dict) else {}
    store_delivery = sent.get("store_address_delivery") if isinstance(sent.get("store_address_delivery"), dict) else {}
    payment = sent.get("payment_collection") if isinstance(sent.get("payment_collection"), dict) else {}
    return {
        "case_images": _drop_empty(
            {
                "status": "sent" if sent.get("case_image_sent") else "not_sent",
                "last_sent_at": case_delivery.get("last_sent_at"),
                "count": case_delivery.get("total_events"),
            }
        ),
        "activity_intro": {
            "status": "completed"
            if sent.get("activity_intro_image_sent") or "activity_intro" in completed_categories
            else "not_completed"
        },
        "store_cards": _drop_empty(
            {
                "status": "sent" if sent.get("store_address_sent_by_store_id") else "not_sent",
                "store_ids": sent.get("store_address_sent_by_store_id") or [],
                "last_sent_at": store_delivery.get("last_sent_at"),
            }
        ),
        "payment_card": _drop_empty(
            {
                "status": "sent" if sent.get("payment_collection_sent") else "not_sent",
                "count": payment.get("total_count"),
                "last_sent_at": payment.get("last_sent_at"),
            }
        ),
    }


def _payment_context(state: dict[str, Any]) -> dict[str, Any]:
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = customer_context.get("orders") if isinstance(customer_context.get("orders"), list) else []
    platform_paid = any(
        isinstance(order, dict) and _positive_number(order.get("prepay_paid") or order.get("paid_amount"))
        for order in orders
    )
    stored = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    deposit = stored.get("deposit_state")
    deposit_status = str(
        deposit.get("status") if isinstance(deposit, dict) else deposit or ""
    ).strip().lower()
    if platform_paid or deposit_status in {"paid", "deposit_paid", "paid_by_platform_transfer_event"}:
        platform_state = "paid"
    elif orders:
        platform_state = "unpaid"
    else:
        platform_state = "unknown"
    vision_verified = str(image_info.get("payment_result") or "").lower() == "success"
    return {
        "customer_claim": "claimed" if deposit_status == "customer_claimed_paid" else "none",
        "vision_evidence": "verified" if vision_verified else "none",
        "platform_state": platform_state,
        "source_policy": "vision_and_platform_are_distinct_facts",
    }


def _visit_context(state: dict[str, Any], basic: dict[str, Any]) -> dict[str, Any]:
    appointment = basic.get("appointment_state") if isinstance(basic.get("appointment_state"), dict) else {}
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    created_appointment = (
        tool_results.get("create_order_plan")
        if isinstance(tool_results.get("create_order_plan"), dict)
        else {}
    )
    confirmed = next(
        (
            item
            for item in (created_appointment, appointment_cache, appointment)
            if str(item.get("status") or "").lower() in {"confirmed", "created", "reused", "booked"}
        ),
        None,
    )
    if confirmed:
        return _drop_empty(
            {
                "state": "confirmed",
                "date_text": confirmed.get("appointment_time") or confirmed.get("time"),
                "source": confirmed.get("source") or "appointment_fact",
                "evidence_ids": [str(confirmed.get("appointment_id") or confirmed.get("order_id") or "appointment_fact")],
            }
        )
    intent_date = str(basic.get("intent_date") or "").strip()
    intent_time = str(basic.get("intent_time") or "").strip()
    if intent_date or intent_time:
        return {
            "state": "tentative",
            "date_text": " ".join(item for item in (intent_date, intent_time) if item),
            "source": "customer_memory",
            "evidence_ids": ["customer_memory"],
        }
    return {"state": "none"}


def _sop_progress(state: dict[str, Any]) -> dict[str, Any]:
    evidence = state.get("sop_progress_evidence") if isinstance(state.get("sop_progress_evidence"), dict) else {}
    unfinished = [item for item in evidence.get("unfinished_sops") or [] if isinstance(item, dict)]
    first = unfinished[0] if unfinished else {}
    return {
        "completed_stages": list(
            dict.fromkeys(str(item) for item in evidence.get("completed_categories") or [] if str(item or "").strip())
        ),
        "earliest_unfinished_stage": str(first.get("mainline_stage") or first.get("stage") or ""),
        "earliest_unfinished_pack_id": str(first.get("id") or first.get("pack_id") or ""),
    }


def _payment_card_cooldown(
    turns: list[dict[str, Any]],
    raw_events: Any,
    *,
    has_current_customer_message: bool,
) -> dict[str, Any]:
    last_assistant_batch: list[dict[str, Any]] = []
    index = len(turns) - 1
    while index >= 0 and str(turns[index].get("role") or "") == "customer":
        index -= 1
    while index >= 0:
        turn = turns[index]
        role = str(turn.get("role") or "")
        if role != "assistant":
            break
        last_assistant_batch.append(turn)
        index -= 1
    active = any(_turn_is_payment_card(turn) for turn in last_assistant_batch)
    evidence_ids = [
        str(turn.get("message_ref") or "")
        for turn in last_assistant_batch
        if _turn_is_payment_card(turn) and str(turn.get("message_ref") or "")
    ]
    if not active and not last_assistant_batch and not has_current_customer_message:
        events = [event for event in raw_events if isinstance(event, dict)] if isinstance(raw_events, list) else []
        latest_visible = next(
            (
                event
                for event in reversed(events)
                if str(event.get("event_type") or "")
                in {"payment_collection_sent", "case_image_sent", "store_address_sent", "activity_intro_image_sent"}
            ),
            None,
        )
        active = bool(latest_visible and str(latest_visible.get("event_type") or "") == "payment_collection_sent")
        if active:
            evidence_ids = [str(latest_visible.get("event_id") or "payment_collection_sent")]
    return {
        "active": active,
        "reason": "previous_assistant_batch_had_payment_collection" if active else "",
        "evidence_ids": evidence_ids,
        "allowed_alternatives": ["explain_existing", "manual_transfer", "continue_without_card"] if active else [],
    }


def _turn_is_payment_card(turn: dict[str, Any]) -> bool:
    message_type = str(turn.get("message_type") or turn.get("msgtype") or "").strip().lower()
    if message_type in _PAYMENT_TYPES:
        return True
    content = str(turn.get("content") or "").lower()
    return "payment_collection" in content or "预约金收款" in content or "付款给：" in content


def _dedupe_turns(raw_turns: Any, raw_history: Any) -> list[dict[str, Any]]:
    turns = [dict(item) for item in raw_turns if isinstance(item, dict)] if isinstance(raw_turns, list) else []
    if not turns:
        history = raw_history if isinstance(raw_history, list) else []
        for index, item in enumerate(history, start=1):
            text = str(item or "").strip()
            role = "customer" if text.startswith(("用户:", "客户:")) else "assistant"
            content = text.split(":", 1)[1].strip() if ":" in text else text
            turns.append({"message_ref": f"history_{index:03d}", "role": role, "content": content})
    output: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    last_signature = ""
    for turn in turns:
        ref = str(turn.get("message_ref") or "").strip()
        if ref and ref in seen_refs:
            continue
        signature = "|".join(
            (
                str(turn.get("role") or ""),
                re.sub(r"\s+", "", str(turn.get("content") or "")),
                str(turn.get("message_type") or ""),
            )
        )
        if signature and signature == last_signature:
            continue
        if ref:
            seen_refs.add(ref)
        output.append(turn)
        last_signature = signature
    return output


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
