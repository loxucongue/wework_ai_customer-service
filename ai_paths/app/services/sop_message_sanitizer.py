from __future__ import annotations

import re
from typing import Any

from app.services.payment_collection import payment_collection_content, payment_collection_context


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
        item["content"] = _content_dict(message.get("content"))
        message_type = str(item.get("type") or "")
        if message_type == "text":
            text = str(item["content"].get("text") or "")
            if has_payment and index == first_payment_index - 1 and payment_amount > 10:
                text = _payment_intro_text(payment_amount)
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


def apply_sop_text_adjustments(
    messages: list[dict[str, Any]],
    adjustments: Any,
    message_operations: Any = None,
    removable_payment_message_orders: set[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply model-proposed text operations without changing structured SOP facts."""

    normalized = [_normalize_message(message, index) for index, message in enumerate(messages, start=1)]
    normalized = [message for message in normalized if message]
    normalized.sort(key=lambda item: int(item.get("order") or 0))
    text_by_order = {
        int(message.get("order") or 0): str(_content_dict(message.get("content")).get("text") or "").strip()
        for message in normalized
        if str(message.get("type") or "") == "text"
    }
    requested = adjustments if isinstance(adjustments, list) else []
    removable_payment_orders = set(removable_payment_message_orders or set())
    preserve_trailing_text = _has_text_after_last_structured_message(normalized)
    applied_orders: list[int] = []
    applied_operations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for item in requested[:8]:
        if not isinstance(item, dict):
            continue
        order = _positive_int(item.get("order"), 0)
        text = str(item.get("text") or "").strip()
        original = text_by_order.get(order, "")
        if not original:
            rejected.append({"order": order, "reason": "not_existing_text_message"})
            continue
        if not text or len(text) > 360:
            rejected.append({"order": order, "reason": "invalid_text_length"})
            continue
        if _numeric_tokens(text) != _numeric_tokens(original):
            rejected.append({"order": order, "reason": "numeric_facts_changed"})
            continue
        for message in normalized:
            if int(message.get("order") or 0) == order and str(message.get("type") or "") == "text":
                message["content"] = {"text": text}
                applied_orders.append(order)
                break

    for operation in message_operations if isinstance(message_operations, list) else []:
        if not isinstance(operation, dict):
            continue
        before_operation = [
            {**message, "content": dict(_content_dict(message.get("content")))}
            for message in normalized
        ]
        applied, reason = _apply_text_message_operation(
            normalized,
            operation,
            removable_payment_message_orders=removable_payment_orders,
        )
        if applied and preserve_trailing_text and not _has_text_after_last_structured_message(normalized):
            normalized[:] = before_operation
            applied = None
            reason = "trailing_action_text_required"
        if applied:
            applied_operations.append(applied)
        else:
            rejected.append({"operation": _operation_name(operation), "reason": reason or "invalid_operation"})

    return _renumber_in_current_order(normalized), {
        "requested_count": len(requested),
        "applied_orders": applied_orders,
        "applied_operations": applied_operations,
        "rejected": rejected,
    }


def _apply_text_message_operation(
    messages: list[dict[str, Any]],
    operation: dict[str, Any],
    *,
    removable_payment_message_orders: set[int] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    op = _operation_name(operation)
    if op == "replace_text":
        order = _positive_int(operation.get("order"), 0)
        text = str(operation.get("text") or "").strip()
        index = _text_index_by_order(messages, order)
        if index is None:
            return None, "not_existing_text_message"
        if not text or len(text) > 500:
            return None, "invalid_text_length"
        original = _message_text(messages[index].get("content"))
        if _numeric_tokens(text) != _numeric_tokens(original):
            return None, "numeric_facts_changed"
        messages[index]["content"] = {"text": text}
        return {"op": op, "order": order}, ""

    if op in {"insert_text_before", "insert_text_after"}:
        text = str(operation.get("text") or "").strip()
        anchor_key = "before_order" if op == "insert_text_before" else "after_order"
        order = _positive_int(operation.get(anchor_key), 0)
        anchor = _message_index_by_order(messages, order)
        if anchor is None:
            return None, "not_existing_anchor_message"
        if not text or len(text) > 360:
            return None, "invalid_text_length"
        if _numeric_tokens(text):
            return None, "numeric_facts_changed"
        insert_at = anchor if op == "insert_text_before" else anchor + 1
        messages.insert(insert_at, {"type": "text", "order": order, "content": {"text": text}})
        return {"op": op, anchor_key: order}, ""

    if op == "remove_text":
        order = _positive_int(operation.get("order"), 0)
        index = _text_index_by_order(messages, order)
        if index is None:
            return None, "not_existing_text_message"
        original = _message_text(messages[index].get("content"))
        if _numeric_tokens(original):
            return None, "numeric_facts_changed"
        if _would_remove_required_text(messages, index):
            return None, "required_text_message"
        del messages[index]
        return {"op": op, "order": order}, ""

    if op == "remove_message":
        order = _positive_int(operation.get("order"), 0)
        index = _message_index_by_order(messages, order)
        if index is None:
            return None, "not_existing_message"
        message_type = str(messages[index].get("type") or "")
        if message_type != "payment_collection":
            return None, "unsupported_message_type"
        if order not in set(removable_payment_message_orders or set()):
            return None, "payment_collection_not_removable"
        del messages[index]
        return {"op": op, "order": order, "type": message_type}, ""

    if op == "merge_text":
        orders = [_positive_int(item, 0) for item in operation.get("orders") or []]
        orders = [item for item in orders if item > 0]
        text = str(operation.get("text") or "").strip()
        if len(orders) < 2 or len(orders) > 4:
            return None, "invalid_orders"
        indices = [_text_index_by_order(messages, order) for order in orders]
        if any(index is None for index in indices):
            return None, "not_existing_text_message"
        if not text or len(text) > 700:
            return None, "invalid_text_length"
        originals = [_message_text(messages[index].get("content")) for index in indices if index is not None]
        if _numeric_tokens(text) != _numeric_tokens("".join(originals)):
            return None, "numeric_facts_changed"
        first = indices[0]
        assert first is not None
        messages[first]["content"] = {"text": text}
        for index in sorted([item for item in indices[1:] if item is not None], reverse=True):
            del messages[index]
        return {"op": op, "orders": orders}, ""

    if op == "split_text":
        order = _positive_int(operation.get("order"), 0)
        texts = [str(item or "").strip() for item in operation.get("texts") or []]
        texts = [item for item in texts if item]
        index = _text_index_by_order(messages, order)
        if index is None:
            return None, "not_existing_text_message"
        if len(texts) < 2 or len(texts) > 4 or any(len(item) > 360 for item in texts):
            return None, "invalid_text_length"
        original = _message_text(messages[index].get("content"))
        if _numeric_tokens("".join(texts)) != _numeric_tokens(original):
            return None, "numeric_facts_changed"
        replacement = [{"type": "text", "order": order, "content": {"text": text}} for text in texts]
        messages[index : index + 1] = replacement
        return {"op": op, "order": order, "count": len(texts)}, ""

    return None, "unsupported_operation"


def _operation_name(operation: dict[str, Any]) -> str:
    return str(operation.get("op") or operation.get("operation") or "").strip()


def _text_index_by_order(messages: list[dict[str, Any]], order: int) -> int | None:
    for index, message in enumerate(messages):
        if int(message.get("order") or 0) == order and str(message.get("type") or "") == "text":
            return index
    return None


def _message_index_by_order(messages: list[dict[str, Any]], order: int) -> int | None:
    for index, message in enumerate(messages):
        if int(message.get("order") or 0) == order:
            return index
    return None


def _would_remove_required_text(messages: list[dict[str, Any]], remove_index: int) -> bool:
    remaining_text_count = sum(
        1
        for index, message in enumerate(messages)
        if index != remove_index and str(message.get("type") or "") == "text"
    )
    if remaining_text_count > 0:
        return False
    return any(str(message.get("type") or "") == "payment_collection" for message in messages)


def _has_text_after_last_structured_message(messages: list[dict[str, Any]]) -> bool:
    last_structured_index = -1
    for index, message in enumerate(messages):
        if str(message.get("type") or "") != "text":
            last_structured_index = index
    if last_structured_index < 0:
        return False
    return any(
        str(message.get("type") or "") == "text"
        and bool(_message_text(message.get("content")).strip())
        for message in messages[last_structured_index + 1 :]
    )


def _renumber_in_current_order(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for order, message in enumerate(messages, start=1):
        item = dict(message)
        item["order"] = order
        output.append(item)
    return output


def _normalize_message(message: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    message_type = str(message.get("type") or "text").strip() or "text"
    content = _content_dict(message.get("content"))
    return {
        "type": "human_handoff_notice" if message_type == "human_handoff" else message_type,
        "order": _positive_int(message.get("order"), index),
        "content": dict(content),
    }


def _content_dict(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return dict(content)
    text = str(content or "").strip()
    return {"text": text} if text else {}


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
    return (
        f"可以，{participants}位一共{amount}元预约金，每位10元，用来锁活动名额，到店抵扣；"
        "未做或不满意可退，实际按付款记录核对。"
    )


def _renumber_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for order, message in enumerate(sorted(messages, key=lambda item: int(item.get("order") or 0)), start=1):
        item = dict(message)
        item["order"] = order
        output.append(item)
    return output


def _mentions_deposit(text: str) -> bool:
    return any(term in str(text or "") for term in ("预约金", "订金", "定金", "10元", "10 元"))


def _numeric_tokens(text: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:\.\d+)?", str(text or "")))


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
