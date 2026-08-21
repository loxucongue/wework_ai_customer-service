from __future__ import annotations

import re
from typing import Any


_PRICE_CONTRACTS: dict[str, tuple[str, str]] = {
    "ww0873": ("199", "189"),
    "ww0601": ("199", "189"),
    "ww0743": ("268", "258"),
    "sl2491": ("268", "258"),
    "dy8832": ("268", "258"),
    "dy258": ("268", "258"),
    "sl0069": ("268", "258"),
}

_KNOWN_ACTIVITY_PRICES = ("268", "258", "199", "189")
_KNOWN_ACTIVITY_PRICE_PATTERN = re.compile(
    rf"(?<!\d)({'|'.join(_KNOWN_ACTIVITY_PRICES)})(?!\d)"
)


def wechat_price_contract(wechat: Any) -> tuple[str, str] | None:
    return _PRICE_CONTRACTS.get(str(wechat or "").strip().lower())


def enforce_wechat_price_contract(
    messages: list[dict[str, Any]],
    *,
    wechat: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Correct only known activity-price tokens in customer-visible text messages."""

    contract = wechat_price_contract(wechat)
    if contract is None:
        return [dict(message) for message in messages if isinstance(message, dict)], {
            "applied": False,
            "wechat": str(wechat or "").strip(),
            "reason": "wechat_not_configured",
            "replacement_count": 0,
        }

    activity_price, balance_after_deposit = contract
    replacements = {
        "268": activity_price,
        "199": activity_price,
        "258": balance_after_deposit,
        "189": balance_after_deposit,
    }
    output: list[dict[str, Any]] = []
    replacement_count = 0
    changed_orders: list[int] = []

    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        item = dict(message)
        if str(item.get("type") or "").strip().lower() != "text":
            output.append(item)
            continue

        content = item.get("content")
        if isinstance(content, dict):
            content_copy = dict(content)
            original = str(content_copy.get("text") or "")
            corrected, count = _correct_price_tokens(original, replacements)
            if count:
                content_copy["text"] = corrected
                item["content"] = content_copy
        elif isinstance(content, str):
            corrected, count = _correct_price_tokens(content, replacements)
            if count:
                item["content"] = corrected
        else:
            original = str(item.get("text") or "")
            corrected, count = _correct_price_tokens(original, replacements)
            if count:
                item["text"] = corrected

        if count:
            replacement_count += count
            changed_orders.append(int(item.get("order") or index))
        output.append(item)

    return output, {
        "applied": bool(replacement_count),
        "wechat": str(wechat or "").strip(),
        "activity_price": int(activity_price),
        "balance_after_deposit": int(balance_after_deposit),
        "replacement_count": replacement_count,
        "changed_orders": changed_orders,
    }


def _correct_price_tokens(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        original = match.group(1)
        replacement = replacements[original]
        if replacement != original:
            count += 1
        return replacement

    return _KNOWN_ACTIVITY_PRICE_PATTERN.sub(replace, str(text or "")), count
