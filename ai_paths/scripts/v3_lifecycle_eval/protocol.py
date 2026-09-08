"""Deterministic contracts for lifecycle-level V3 reply evaluation.

These checks are deliberately outside the production reply path.  They make
business lifecycle expectations observable without turning them into a second
sales rules engine.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


STRUCTURED_MESSAGE_TYPES = {
    "store_address",
    "payment_collection",
    "image",
    "video",
    "human_handoff",
    "human_handoff_notice",
}


def text_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "content", "message", "title", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""
    return str(value or "").strip()


def normalize_visible_messages(value: Any) -> list[dict[str, Any]]:
    """Return every customer-visible message type in a stable shape."""

    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict) and isinstance(value.get("reply_messages"), list):
        value = value["reply_messages"]
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "text").strip() or "text"
        content = item.get("content")
        if message_type == "text":
            content = text_value(content)
            if not content:
                continue
        elif not isinstance(content, dict):
            content = {"value": text_value(content)}
        output.append(
            {
                "type": message_type,
                "order": int(item.get("order") or index + 1),
                "content": content,
            }
        )
    return sorted(output, key=lambda item: item["order"])


def customer_visible_text(messages: Any) -> str:
    return " ".join(
        text_value(item.get("content"))
        for item in normalize_visible_messages(messages)
        if item.get("type") == "text"
    ).strip()


def store_card_ids(messages: Any) -> list[str]:
    output: list[str] = []
    for item in normalize_visible_messages(messages):
        if item.get("type") != "store_address":
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        store_id = str(content.get("store_id") or content.get("id") or "").strip()
        if store_id and store_id not in output:
            output.append(store_id)
    return output


def render_visible_messages(messages: Any) -> str:
    """Readable judge input which does not silently discard cards or media."""

    rendered: list[str] = []
    for item in normalize_visible_messages(messages):
        message_type = str(item.get("type") or "text")
        content = item.get("content")
        if message_type == "text":
            rendered.append(f"[文本] {text_value(content)}")
            continue
        payload = content if isinstance(content, dict) else {}
        if message_type == "store_address":
            rendered.append(
                "[门店位置卡] store_id="
                + str(payload.get("store_id") or payload.get("id") or "未记录")
            )
        elif message_type == "payment_collection":
            amount = payload.get("amount") or payload.get("deposit_amount") or "未记录"
            order = payload.get("order_id") or payload.get("order_no") or "未记录"
            rendered.append(f"[预约金/付款卡] amount={amount}; order={order}")
        elif message_type in {"image", "video"}:
            asset = payload.get("asset_id") or payload.get("content_id") or payload.get("id") or "未记录"
            rendered.append(f"[{message_type}] asset_id={asset}")
        else:
            rendered.append(f"[{message_type}] {json.dumps(payload, ensure_ascii=False, default=str)[:240]}")
    return "\n".join(rendered)


def prior_delivery_events(
    deliveries: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate prior delivered structures into the memory events production reads."""

    events: list[dict[str, Any]] = []
    for delivery_index, delivery in enumerate(deliveries):
        request_id = str(delivery.get("request_id") or f"prior-{delivery_index}")
        event_time = str(delivery.get("occurred_at") or delivery.get("created_at") or "")
        for message_index, item in enumerate(normalize_visible_messages(delivery.get("reply_messages"))):
            message_type = str(item.get("type") or "")
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            if message_type == "store_address":
                store_id = str(content.get("store_id") or content.get("id") or "").strip()
                if not store_id:
                    continue
                events.append(
                    {
                        "event_id": f"eval_store_address_sent_{request_id}_{message_index}",
                        "event_type": "store_address_sent",
                        "event_time": event_time,
                        "facts": {"store_id": store_id, "request_id": request_id},
                        "source": "reply_delivery",
                    }
                )
            elif message_type == "payment_collection":
                events.append(
                    {
                        "event_id": f"eval_payment_collection_sent_{request_id}_{message_index}",
                        "event_type": "payment_collection_sent",
                        "event_time": event_time,
                        "facts": {**content, "request_id": request_id},
                        "source": "reply_delivery",
                    }
                )
            elif message_type in {"image", "video"}:
                events.append(
                    {
                        "event_id": f"eval_asset_sent_{request_id}_{message_index}",
                        "event_type": "content_delivered",
                        "event_time": event_time,
                        "facts": {"message_type": message_type, **content, "request_id": request_id},
                        "source": "reply_delivery",
                    }
                )
    return events[-100:]


def prior_structured_summary(deliveries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    messages = [
        item
        for delivery in deliveries
        for item in normalize_visible_messages(delivery.get("reply_messages"))
        if item.get("type") in STRUCTURED_MESSAGE_TYPES
    ]
    return {
        "message_count": len(messages),
        "store_card_ids": store_card_ids(messages),
        "rendered": render_visible_messages(messages),
    }


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    return any(marker.lower() in compact for marker in markers)


def _recursive_pairs(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path.lower(), child
            yield from _recursive_pairs(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _recursive_pairs(child, f"{prefix}[{index}]")


def _has_arrival_convenience_fact(facts: dict[str, Any]) -> bool:
    """Return whether authoritative inputs explicitly support a queue/wait claim."""

    for path, value in _recursive_pairs(facts):
        if not any(marker in path for marker in ("queue", "wait", "arrival", "reception")):
            continue
        normalized = str(value or "").strip().lower()
        if normalized and normalized not in {"none", "unknown", "false", "0", "未记录", "未知"}:
            return True
    return False


def appointment_state(sample: dict[str, Any], facts: dict[str, Any]) -> str:
    if str(sample.get("appointment_id") or "").strip() or str(sample.get("appointment_time") or "").strip():
        return "confirmed"
    positive = {"confirmed", "scheduled", "booked", "appointed", "reserved", "已预约", "已排客"}
    negative = {"none", "unpaid", "required_unpaid", "not_booked", "not_scheduled", "未预约", "未排客"}
    negative_seen = False
    for path, value in _recursive_pairs(facts):
        if not any(marker in path for marker in ("appointment", "schedule", "booking", "order_state", "payment_state", "deposit_state")):
            continue
        normalized = str(value or "").strip().lower()
        if normalized in positive or any(marker in normalized for marker in positive):
            return "confirmed"
        if normalized in negative or any(marker in normalized for marker in negative):
            negative_seen = True
    return "not_confirmed" if negative_seen else "unknown"


def expected_contract(
    *,
    sample: dict[str, Any],
    facts: dict[str, Any],
    prior_deliveries: list[dict[str, Any]],
) -> dict[str, Any]:
    content = str(sample.get("content") or "")
    prior = prior_structured_summary(prior_deliveries)
    prior_events = [
        *prior_delivery_events(prior_deliveries),
        *[
            item
            for item in sample.get("source_history_events") or []
            if isinstance(item, dict)
        ],
    ]
    prior_event_types = {
        str(item.get("event_type") or "")
        for item in prior_events
        if isinstance(item, dict)
    }
    effect_delivered = "case_image_sent" in prior_event_types
    activity_delivered = "activity_intro_image_sent" in prior_event_types
    address_request = _contains_any(
        content,
        ("地址", "定位", "导航", "路线", "怎么走", "在哪", "位置", "再发", "没收到"),
    )
    store_detail = _contains_any(
        content,
        ("停车", "停车场", "营业时间", "几点开门", "几点关门", "几点下班", "几楼", "电梯"),
    )
    safety_pause = _contains_any(
        content,
        ("别联系", "别发了", "不要联系", "取消接收", "投诉", "过敏", "伤口", "怀孕", "哺乳"),
    )
    deferred = _contains_any(content, ("晚点", "改天", "考虑一下", "暂时", "没时间", "现在忙"))
    appointment = appointment_state(sample, facts)
    should_bridge_booking = bool(
        store_detail
        and prior.get("store_card_ids")
        and not address_request
        and appointment == "not_confirmed"
        and not safety_pause
        and not deferred
        and effect_delivered
        and activity_delivered
    )
    return {
        "appointment_state": appointment,
        "prior_store_card_ids": prior.get("store_card_ids") or [],
        "current_requests_address_or_navigation": address_request,
        "current_is_store_detail_question": store_detail,
        "safety_or_health_pause": safety_pause,
        "deferred": deferred,
        "effect_evidence_delivered": effect_delivered,
        "activity_offer_delivered": activity_delivered,
        "should_bridge_to_booking": should_bridge_booking,
    }


def hard_assertions(
    *,
    sample: dict[str, Any],
    facts: dict[str, Any],
    prior_deliveries: list[dict[str, Any]],
    reply_messages: Any,
) -> dict[str, Any]:
    """Apply narrow, high-confidence lifecycle failures before AI judging."""

    contract = expected_contract(sample=sample, facts=facts, prior_deliveries=prior_deliveries)
    messages = normalize_visible_messages(reply_messages)
    reply = customer_visible_text(messages)
    current_store_cards = store_card_ids(messages)
    failures: list[dict[str, str]] = []
    repeated = sorted(set(contract["prior_store_card_ids"]) & set(current_store_cards))
    if repeated and not contract["current_requests_address_or_navigation"]:
        failures.append(
            {
                "code": "repeated_delivered_store_card",
                "reason": "客户未再次索要地址或导航，却重复发送了已送达的同一门店位置卡。",
            }
        )
    if contract["should_bridge_to_booking"]:
        booking_named = _contains_any(reply, ("预约", "预约金", "登记", "锁定", "锁个", "保留名额", "留个名额"))
        next_step = _contains_any(reply, ("工作日", "周末", "哪天", "时间", "什么时候", "帮您", "给您"))
        if not (booking_named and next_step):
            failures.append(
                {
                    "code": "appointment_goal_not_explicit",
                    "reason": "门店已确认且客户尚未预约；回答门店细节后没有明确说明下一步是预约或保留名额。",
                }
            )
    unsupported_arrival_claim = _contains_any(
        reply,
        ("到店不用等", "不用等太久", "少等待", "免排队", "优先接待"),
    )
    if unsupported_arrival_claim and not _has_arrival_convenience_fact(facts):
        failures.append(
            {
                "code": "unsupported_arrival_convenience_claim",
                "reason": "没有权威排队或接待事实，却承诺了不用等、少等待、免排队或优先接待。",
            }
        )
    explicit_exit = _contains_any(
        str(sample.get("content") or ""),
        ("别联系", "别发了", "不要联系", "不要再发", "取消接收", "不再打扰"),
    )
    if explicit_exit and _contains_any(reply, ("预约", "报名", "活动", "优惠", "定金", "预约金", "到店")):
        failures.append(
            {
                "code": "explicit_exit_marketing_push",
                "reason": "客户明确停止联系后，回复仍包含营销或预约推进。",
            }
        )
    return {
        "passed": not failures,
        "failures": failures,
        "failure_codes": [item["code"] for item in failures],
        "contract": contract,
        "reply_store_card_ids": current_store_cards,
    }


def merge_ai_judge(judge: dict[str, Any], hard_result: dict[str, Any]) -> dict[str, Any]:
    merged = dict(judge)
    merged["ai_passed"] = bool(judge.get("passed"))
    failures = hard_result.get("failures") if isinstance(hard_result.get("failures"), list) else []
    merged["hard_failure_codes"] = [str(item.get("code") or "") for item in failures if isinstance(item, dict)]
    merged["passed"] = bool(merged["ai_passed"] and not failures)
    reasons = [str(item) for item in merged.get("reasons") or [] if str(item)]
    reasons.extend(str(item.get("reason") or "") for item in failures if isinstance(item, dict))
    merged["reasons"] = reasons
    return merged
