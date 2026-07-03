from __future__ import annotations

from typing import Any

from app.services.payment_collection import (
    payment_collection_content,
    payment_collection_context,
)


PAYMENT_TEXT_TERMS = ("预约金", "订金", "定金", "付款", "付完", "收款", "报名入口", "付款入口", "10元", "10 元")


def sanitize_sop_reply_messages(
    messages: list[dict[str, Any]],
    *,
    state: dict[str, Any] | None = None,
    conversation_messages: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize configured SOP messages before they are exposed to customers."""

    normalized = [_normalize_message(message, index) for index, message in enumerate(messages, start=1)]
    normalized = [message for message in normalized if message]
    payment_state = _payment_state(state=state, conversation_messages=conversation_messages)
    has_payment = any(message.get("type") == "payment_collection" for message in normalized)
    payment_context = payment_collection_context(state=payment_state, messages=normalized) if has_payment else {}
    if has_payment and payment_context.get("over_limit"):
        return _suppress_over_limit_payment(normalized, payment_context)

    payment_amount = int(payment_context.get("amount") or 10) if has_payment else 0
    output: list[dict[str, Any]] = []
    first_payment_index = _first_payment_index(normalized)
    for index, message in enumerate(normalized):
        item = dict(message)
        item["content"] = dict(message.get("content") if isinstance(message.get("content"), dict) else {})
        message_type = str(item.get("type") or "")
        if message_type == "text":
            text = str(item["content"].get("text") or "")
            if has_payment and index == first_payment_index - 1 and payment_amount > 10:
                text = _payment_intro_text(payment_amount)
            else:
                text = normalize_deposit_refund_text(text)
            if not text.strip():
                continue
            item["content"] = {"text": text}
        elif message_type == "payment_collection":
            item["content"] = payment_collection_content(item.get("content"), state=payment_state, messages=normalized)
        elif message_type in {"image", "video"}:
            if not str(item["content"].get("url") or "").strip():
                continue
        elif message_type == "human_handoff":
            item["type"] = "human_handoff_notice"
            item["content"] = {"handoff_reason": str(item["content"].get("handoff_reason") or item["content"].get("text") or "").strip()}
        output.append(item)

    return _renumber_messages(output), {
        "payment_context": payment_context,
        "payment_adjusted": bool(has_payment and payment_amount),
        "message_count": len(output),
    }


def normalize_deposit_refund_text(text: str) -> str:
    value = str(text or "")
    if not _mentions_deposit(value):
        return value
    replacements = (
        ("不满意订金10元直接退还", "不做退10元"),
        ("不满意定金10元直接退还", "不做退10元"),
        ("不满意预约金10元直接退还", "不做退10元"),
        ("不满意也可以退", "不做退10元"),
        ("不做的话10元预约金也退", "不做退10元"),
        ("不做的话10元订金也退", "不做退10元"),
        ("不做的话10元定金也退", "不做退10元"),
        ("不做10元也是退给您的", "不做退10元"),
        ("这个10元的预约金也是一分不少退还的", "这个10元预约金不做退10元"),
        ("10元的预约金也是一分不少退还的", "10元预约金不做退10元"),
        ("预约金也是一分不少退还的", "预约金不做退10元"),
        ("订金10元直接退还", "不做退10元"),
        ("定金10元直接退还", "不做退10元"),
    )
    for old, new in replacements:
        value = value.replace(old, new)
    value = value.replace("10元预约金可退", "不做退10元")
    value = value.replace("10 元预约金可退", "不做退10元")
    value = value.replace("预约金可退", "预约金不做退10元")
    return value


def has_forbidden_deposit_refund_text(text: str) -> bool:
    value = str(text or "")
    if not _mentions_deposit(value):
        return False
    normalized = normalize_deposit_refund_text(value)
    if normalized != value:
        return True
    compact = "".join(value.split())
    forbidden_terms = ("一分不少退", "直接退还", "不满意也可以退", "预约金可退", "订金可退", "定金可退")
    return any(term in compact for term in forbidden_terms)


def _normalize_message(message: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    message_type = str(message.get("type") or "text").strip() or "text"
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    return {
        "type": "human_handoff_notice" if message_type == "human_handoff" else message_type,
        "order": _positive_int(message.get("order"), index),
        "content": dict(content),
    }


def _payment_state(
    *,
    state: dict[str, Any] | None,
    conversation_messages: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if isinstance(state, dict):
        base = dict(state)
    else:
        base = {}
    history = base.get("conversation_history") if isinstance(base.get("conversation_history"), list) else []
    history.extend(_conversation_history(conversation_messages or []))
    base["conversation_history"] = history[-30:]
    return base


def _conversation_history(messages: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for item in messages[-30:]:
        if not isinstance(item, dict):
            continue
        text = _message_text(item.get("content"))
        if not text:
            text = _message_text(item.get("text") or item.get("message") or item.get("body"))
        if text:
            output.append(text[:240])
    return output


def _message_text(value: Any) -> str:
    if isinstance(value, dict):
        if "amount" in value:
            return f"预约金收款:{value.get('amount')}"
        for key in ("text", "content", "url", "handoff_reason", "store_id"):
            text = _message_text(value.get(key))
            if text:
                return text
        return ""
    return str(value or "").strip()


def _suppress_over_limit_payment(
    messages: list[dict[str, Any]],
    payment_context: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output: list[dict[str, Any]] = []
    inserted = False
    for message in messages:
        message_type = str(message.get("type") or "")
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        if message_type == "payment_collection" or (
            message_type == "text" and any(term in str(content.get("text") or "") for term in PAYMENT_TEXT_TERMS)
        ):
            if not inserted:
                output.append(
                    {
                        "type": "text",
                        "order": int(message.get("order") or len(output) + 1),
                        "content": {
                            "text": "同行人数我先确认一下，预约金按每位10元锁活动名额，您这次一共几位到店？"
                        },
                    }
                )
                inserted = True
            continue
        output.append(message)
    if not inserted:
        output.append(
            {
                "type": "text",
                "order": len(output) + 1,
                "content": {"text": "同行人数我先确认一下，预约金按每位10元锁活动名额，您这次一共几位到店？"},
            }
        )
    return _renumber_messages(output), {
        "payment_context": payment_context,
        "payment_suppressed": "over_limit_participants",
        "message_count": len(output),
    }


def _first_payment_index(messages: list[dict[str, Any]]) -> int:
    for index, message in enumerate(messages):
        if str(message.get("type") or "") == "payment_collection":
            return index
    return -1


def _payment_intro_text(amount: int) -> str:
    participants = max(1, amount // 10)
    return f"可以，{participants}位一共{amount}元预约金，每位10元，用来锁活动名额，到店抵扣，不做退10元。"


def _renumber_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for order, message in enumerate(sorted(messages, key=lambda item: int(item.get("order") or 0)), start=1):
        item = dict(message)
        item["order"] = order
        output.append(item)
    return output


def _mentions_deposit(text: str) -> bool:
    return any(term in str(text or "") for term in ("预约金", "订金", "定金", "10元", "10 元"))


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
