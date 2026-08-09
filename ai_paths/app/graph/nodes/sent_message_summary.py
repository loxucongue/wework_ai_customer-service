from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def sent_message_summary_for_model(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize sent message facts without deciding the next business action."""

    payment_events = _unique_payment_events(state.get("history_events"))
    visible_payment_records = _visible_payment_records(state.get("conversation_history"))
    payment_frequency = _payment_frequency(
        payment_events,
        visible_payment_records=visible_payment_records,
        now=now,
    )
    case_image_delivery = _case_image_delivery(state.get("history_events"))
    store_address_delivery = _store_address_delivery(state.get("history_events"))
    store_anchor_fact = _store_anchor_fact(store_address_delivery)
    activity_intro_image_sent = False
    store_ids: list[str] = []

    for event in state.get("history_events") or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        if event_type == "activity_intro_image_sent":
            activity_intro_image_sent = True
        if event_type == "store_address_sent":
            store_id = str(facts.get("store_id") or "").strip()
            if store_id:
                store_ids.append(store_id)

    for item in state.get("conversation_history") or []:
        text = _conversation_text(item)
        if "activity_intro_image" in text or "anniversary-268.jpg" in text:
            activity_intro_image_sent = True
        if "store_address" in text or "门店位置卡" in text:
            for match in re.finditer(r"(?:store_id|门店ID)\s*[=:：]\s*(\d+)", text, flags=re.IGNORECASE):
                store_ids.append(match.group(1))

    total_count = int(payment_frequency.get("total_count") or 0)
    output: dict[str, Any] = {
        "payment_collection_sent": total_count > 0,
        "payment_collection_count": total_count,
        "payment_collection": payment_frequency,
        "case_image_sent": bool(case_image_delivery),
        "case_image_delivery": case_image_delivery,
        "activity_intro_image_sent": activity_intro_image_sent,
        "store_address_sent_by_store_id": list(dict.fromkeys(store_ids)),
        "store_address_delivery": store_address_delivery,
        "store_anchor_fact": store_anchor_fact,
    }
    return {key: value for key, value in output.items() if value not in (False, 0, [], {}, None, "")}


def latest_single_store_card_anchor_id(state: dict[str, Any]) -> str:
    """Return the latest single-card store id from authoritative delivery events."""

    anchor = store_anchor_fact_for_model(state)
    if str(anchor.get("status") or "") != "eligible":
        return ""
    return str(anchor.get("store_id") or "").strip()


def store_anchor_fact_for_model(state: dict[str, Any]) -> dict[str, Any]:
    """Return structural store-card evidence without deciding customer acceptance."""

    return _store_anchor_fact(_store_address_delivery(state.get("history_events")))


def _store_anchor_fact(delivery: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(delivery, dict) or not delivery:
        return {"status": "none", "source": "history_events"}
    store_ids = [
        str(item or "").strip()
        for item in delivery.get("latest_batch_store_ids") or []
        if str(item or "").strip()
    ]
    confidence = str(delivery.get("batch_confidence") or "").strip()
    if confidence != "high":
        status = "unverified"
    elif len(store_ids) == 1:
        status = "eligible"
    elif len(store_ids) > 1:
        status = "ambiguous"
    else:
        status = "none"
    return _drop_empty(
        {
            "status": status,
            "store_id": store_ids[0] if status == "eligible" else "",
            "store_ids": store_ids,
            "batch_count": len(store_ids),
            "confidence": confidence or "none",
            "last_sent_at": str(delivery.get("last_sent_at") or "").strip(),
            "request_id": str(delivery.get("request_id") or "").strip(),
            "source": "latest_store_address_delivery",
            "usage": "evidence_only_planner_decides_customer_store_acceptance",
        }
    )


def _store_address_delivery(raw_events: Any) -> dict[str, Any]:
    """Expose the latest store-card delivery batch without deciding sales intent."""

    deliveries: list[tuple[int, dict[str, Any], datetime | None, str, str]] = []
    for index, event in enumerate(raw_events if isinstance(raw_events, list) else []):
        if not isinstance(event, dict) or str(event.get("event_type") or "").strip() != "store_address_sent":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        store_id = str(facts.get("store_id") or facts.get("id") or "").strip()
        if not store_id:
            continue
        deliveries.append(
            (
                index,
                event,
                _event_datetime(event),
                store_id,
                str(facts.get("request_id") or "").strip(),
            )
        )
    if not deliveries:
        return {}

    latest = max(
        deliveries,
        key=lambda item: (
            item[2].timestamp() if item[2] is not None else float("-inf"),
            item[0],
        ),
    )
    latest_request_id = latest[4]
    if latest_request_id:
        latest_batch = [item for item in deliveries if item[4] == latest_request_id]
        batch_confidence = "high"
    else:
        # Legacy events do not prove whether adjacent cards belonged to one response.
        latest_batch = [latest]
        batch_confidence = "partial"

    store_ids = list(dict.fromkeys(item[3] for item in sorted(latest_batch, key=lambda item: item[0])))
    latest_at = latest[2]
    return _drop_empty(
        {
            "total_events": len(deliveries),
            "latest_batch_store_ids": store_ids,
            "unique_latest_store_id": store_ids[0] if len(store_ids) == 1 and batch_confidence == "high" else "",
            "latest_batch_count": len(store_ids),
            "last_sent_at": latest_at.isoformat() if latest_at is not None else "",
            "request_id": latest_request_id,
            "batch_confidence": batch_confidence,
            "source": "history_events",
            "decision_policy": "evidence_only_model_decides_store_binding",
        }
    )


def _case_image_delivery(raw_events: Any) -> dict[str, Any]:
    """Expose case-image delivery facts without deciding whether another image is needed."""

    events = [
        (index, event, _event_datetime(event))
        for index, event in enumerate(raw_events if isinstance(raw_events, list) else [])
        if isinstance(event, dict) and str(event.get("event_type") or "").strip() == "case_image_sent"
    ]
    if not events:
        return {}
    _, latest, latest_at = max(
        events,
        key=lambda item: (
            item[2].timestamp() if item[2] is not None else float("-inf"),
            item[0],
        ),
    )
    facts = latest.get("facts") if isinstance(latest.get("facts"), dict) else {}
    document_ids = [str(item) for item in facts.get("document_ids") or [] if str(item or "").strip()]
    image_urls = [str(item) for item in facts.get("image_urls") or [] if str(item or "").strip()]
    timestamped_count = sum(1 for _, _, event_at in events if event_at is not None)
    return {
        "total_events": len(events),
        "last_sent_at": latest_at.isoformat() if latest_at is not None else "",
        "last_document_count": len(document_ids),
        "last_image_count": len(image_urls),
        "last_document_ids": document_ids,
        "last_image_urls": image_urls,
        "time_confidence": "high" if timestamped_count == len(events) else "partial",
        "source": "history_events",
        "decision_policy": "evidence_only_model_decides_case_image_send",
    }


def _unique_payment_events(raw_events: Any) -> list[dict[str, Any]]:
    events = raw_events if isinstance(raw_events, list) else []
    output: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict) or not _is_payment_event(event):
            continue
        event_id = str(event.get("event_id") or event.get("id") or "").strip()
        if event_id:
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
        output.append({**event, "_source_index": index})
    return output


def _is_payment_event(event: dict[str, Any]) -> bool:
    if str(event.get("event_type") or "").strip() == "payment_collection_sent":
        return True
    facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
    return str(facts.get("message_type") or facts.get("type") or "").strip() == "payment_collection"


def _payment_frequency(
    events: list[dict[str, Any]],
    *,
    visible_payment_records: list[dict[str, Any]],
    now: datetime | None,
) -> dict[str, Any]:
    current = _as_shanghai(now or datetime.now(tz=SHANGHAI_TZ))
    parsed_events: list[tuple[dict[str, Any], datetime | None]] = [
        (event, _event_datetime(event)) for event in events
    ]
    timestamped = [(event, event_at) for event, event_at in parsed_events if event_at is not None]
    unknown_time_count = len(parsed_events) - len(timestamped)

    if events:
        total_count = len(events)
        if unknown_time_count:
            today_count: int | None = None
            prior_count: int | None = None
            count_confidence = "partial" if timestamped else "unknown"
        else:
            today_count = sum(1 for _, event_at in timestamped if event_at.date() == current.date())
            prior_count = total_count - today_count
            count_confidence = "high"
        latest_event, latest_at = max(
            parsed_events,
            key=lambda item: (
                item[1].timestamp() if item[1] is not None else float("-inf"),
                int(item[0].get("_source_index") or 0),
            ),
        )
        last_amount = _payment_amount(latest_event)
        source = "history_events"
    else:
        total_count = len(visible_payment_records)
        today_count = None
        prior_count = None
        count_confidence = "unknown" if total_count else "none"
        latest = visible_payment_records[-1] if visible_payment_records else {}
        latest_at = None
        last_amount = _numeric_amount(latest.get("amount"))
        unknown_time_count = total_count
        source = "conversation_history_fallback" if total_count else "none"

    last_sent_at = latest_at.isoformat() if latest_at is not None else ""
    customer_turns_since_last_card = _customer_turns_since_last_card(visible_payment_records)
    output: dict[str, Any] = {
        "today_count": today_count,
        "prior_count": prior_count,
        "total_count": total_count,
        "last_sent_at": last_sent_at,
        "last_amount": last_amount,
        "customer_turns_since_last_card": customer_turns_since_last_card,
        "recent_history_count": len(visible_payment_records),
        "unknown_time_count": unknown_time_count,
        "count_confidence": count_confidence,
        "source": source,
        "decision_policy": "evidence_only_model_decides_repeat_send",
    }
    return {
        key: value
        for key, value in output.items()
        if value not in ("", [], {}, None) or key in {"today_count", "prior_count"}
    }


def _visible_payment_records(raw_history: Any) -> list[dict[str, Any]]:
    history = raw_history if isinstance(raw_history, list) else []
    records: list[dict[str, Any]] = []
    for index, item in enumerate(history):
        if not _is_visible_payment_collection(item):
            continue
        records.append(
            {
                "index": index,
                "amount": _payment_amount_from_history_item(item),
                "customer_turns_after": sum(1 for later in history[index + 1 :] if _is_customer_message(later)),
            }
        )
    return records


def _is_visible_payment_collection(item: Any) -> bool:
    if _is_customer_message(item):
        return False
    if isinstance(item, dict):
        if str(item.get("type") or item.get("message_type") or "").strip() == "payment_collection":
            return True
        content = item.get("content")
        if isinstance(content, dict) and str(content.get("type") or "").strip() == "payment_collection":
            return True
    text = _conversation_text(item)
    return bool(
        re.search(r"\bpayment_collection\b", text, flags=re.IGNORECASE)
        or re.search(r"预约金收款\s*[:：]\s*(?:10|20|30|40)(?:\.0)?\b", text)
        or re.search(r"付款给\s*[:：]\s*\S+", text)
    )


def _customer_turns_since_last_card(records: list[dict[str, Any]]) -> int | None:
    if not records:
        return None
    return max(0, int(records[-1].get("customer_turns_after") or 0))


def _is_customer_message(item: Any) -> bool:
    if isinstance(item, dict):
        role = str(item.get("role") or item.get("direction") or "").strip().lower()
        return role in {"user", "customer", "inbound"}
    text = str(item or "").strip()
    return text.startswith(("用户:", "用户：", "客户:", "客户："))


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    for key in ("event_time", "created_at", "timestamp", "time"):
        parsed = _parse_datetime(event.get(key))
        if parsed is not None:
            return _as_shanghai(parsed)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return _parse_datetime(int(raw))
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI_TZ)


def _payment_amount(event: dict[str, Any]) -> int | None:
    facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
    for value in (facts.get("amount"), facts.get("prepay"), event.get("amount")):
        amount = _numeric_amount(value)
        if amount is not None:
            return amount
    return None


def _payment_amount_from_history_item(item: Any) -> int | None:
    if isinstance(item, dict):
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        amount = _numeric_amount(content.get("amount"))
        if amount is not None:
            return amount
    text = _conversation_text(item)
    match = re.search(r"(?:amount|金额)\s*[=:：]\s*(10|20|30|40)\b", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _numeric_amount(value: Any) -> int | None:
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _conversation_text(item: Any) -> str:
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or content.get("content") or "")
        return str(content or item.get("text") or "")
    return str(item or "")


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
