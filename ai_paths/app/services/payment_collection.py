from __future__ import annotations

import re
from typing import Any


PAYMENT_COLLECTION_UNIT_AMOUNT = 10
PAYMENT_COLLECTION_ALLOWED_AMOUNTS = (10, 20, 30, 40)
PAYMENT_COLLECTION_MAX_AUTO_PARTICIPANTS = 4


def payment_collection_content(
    content: Any,
    *,
    state: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    remark = ""
    raw_amount = _amount_from_content(content)
    if isinstance(content, dict):
        remark = str(content.get("remark") or "").strip()
    amount = payment_collection_amount(state=state, messages=messages, fallback_amount=raw_amount)
    return {"amount": amount, "remark": remark}


def payment_collection_amount(
    *,
    state: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    fallback_amount: int | None = None,
) -> int:
    if state is None and messages is None:
        return _normalize_amount(fallback_amount)
    context = payment_collection_context(state=state, messages=messages)
    if context["over_limit"]:
        return _normalize_amount(fallback_amount)
    participants = int(context["participants"] or 1)
    return participants * PAYMENT_COLLECTION_UNIT_AMOUNT


def payment_collection_context(
    *,
    state: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = _state_payment_text(state, messages)
    participants, over_limit = payment_participants_from_text(text)
    return {
        "participants": participants,
        "amount": participants * PAYMENT_COLLECTION_UNIT_AMOUNT,
        "over_limit": over_limit,
    }


def payment_participants_from_text(text: str) -> tuple[int, bool]:
    compact = "".join(str(text or "").split())
    if not compact:
        return 1, False

    if _has_single_person_signal(compact):
        return 1, False

    companion_count = _companion_count(compact)
    if companion_count is not None:
        participants = companion_count + 1
        return min(participants, PAYMENT_COLLECTION_MAX_AUTO_PARTICIPANTS), participants > PAYMENT_COLLECTION_MAX_AUTO_PARTICIPANTS

    group_count = _group_count(compact)
    if group_count is not None:
        return min(group_count, PAYMENT_COLLECTION_MAX_AUTO_PARTICIPANTS), group_count > PAYMENT_COLLECTION_MAX_AUTO_PARTICIPANTS

    if _has_simple_companion_signal(compact):
        return 2, False
    return 1, False


def payment_amount_matches_text(messages: list[dict[str, Any]]) -> bool:
    amount = _first_payment_amount(messages)
    if amount <= PAYMENT_COLLECTION_UNIT_AMOUNT:
        return True
    text = _messages_text(messages)
    compact = "".join(text.split())
    if not compact:
        return True
    if _mentions_total_amount(compact, amount):
        return True
    if "每位10" in compact or "每人10" in compact or "一人10" in compact or "每位10元" in compact or "每人10元" in compact:
        return True
    return not bool(re.search(r"(?<!每位)(?<!每人)(?<!一人)10元?预约金", compact))


def _amount_from_content(content: Any) -> int | None:
    if not isinstance(content, dict):
        return None
    try:
        return int(float(str(content.get("amount") or "").strip()))
    except (TypeError, ValueError):
        return None


def _normalize_amount(value: int | None) -> int:
    if value in PAYMENT_COLLECTION_ALLOWED_AMOUNTS:
        return int(value)
    return PAYMENT_COLLECTION_UNIT_AMOUNT


def _state_payment_text(state: dict[str, Any] | None, messages: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    if isinstance(state, dict):
        for key in ("normalized_content", "content"):
            value = str(state.get(key) or "").strip()
            if value:
                parts.append(value)
        history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
        parts.extend(str(item) for item in history[-4:] if str(item or "").strip())
    parts.append(_messages_text(messages or []))
    return " ".join(part for part in parts if part)


def _messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, dict):
            for key in ("text", "handoff_reason", "url"):
                if content.get(key):
                    parts.append(str(content.get(key)))
        elif content:
            parts.append(str(content))
    return " ".join(parts)


def _first_payment_amount(messages: list[dict[str, Any]]) -> int:
    for item in messages:
        if isinstance(item, dict) and str(item.get("type") or "") == "payment_collection":
            content = item.get("content")
            if isinstance(content, dict):
                return _normalize_amount(_amount_from_content(content))
    return PAYMENT_COLLECTION_UNIT_AMOUNT


def _companion_count(compact: str) -> int | None:
    companion_terms = r"(朋友|家人|闺蜜|姐妹|对象|老公|老婆|妈妈|爸爸|母亲|父亲)"
    match = re.search(rf"带([一二两俩三四五六七八九\d]+)个?{companion_terms}", compact)
    if match:
        return _number_value(match.group(1))
    match = re.search(rf"和([一二两俩三四五六七八九\d]+)个?{companion_terms}一起", compact)
    if match:
        return _number_value(match.group(1))
    return None


def _group_count(compact: str) -> int | None:
    for pattern in (
        r"我们([一二两俩三四五六七八九\d]+)个?人",
        r"我们([一二两俩三四五六七八九\d]+)(个|位)?(一起|过去|到店|了解|报名|预约|参加|做)",
        r"([一二两俩三四五六七八九\d]+)个?人(一起|过去|到店|了解|报名|预约|参加|做)",
        r"([一二两俩三四五六七八九\d]+)位(一起|过去|到店|了解|报名|预约|参加|做)",
        r"我(俩|们俩)",
    ):
        match = re.search(pattern, compact)
        if match:
            value = match.group(1)
            return 2 if value in {"俩", "们俩"} else _number_value(value)
    return None


def _has_simple_companion_signal(compact: str) -> bool:
    return any(
        term in compact
        for term in (
            "带朋友",
            "朋友一起",
            "和朋友一起",
            "带家人",
            "家人一起",
            "带闺蜜",
            "闺蜜一起",
            "带姐妹",
            "姐妹一起",
        )
    )


def _has_single_person_signal(compact: str) -> bool:
    return any(term in compact for term in ("我一个人", "自己一个人", "一个人过去", "一个人到店", "一位过去", "一位到店"))


def _number_value(value: str) -> int:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "俩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    return mapping.get(text, 1)


def _mentions_total_amount(compact: str, amount: int) -> bool:
    return any(term in compact for term in (f"一共{amount}", f"共{amount}", f"合计{amount}", f"{amount}元预约金", f"{amount}元入口"))
