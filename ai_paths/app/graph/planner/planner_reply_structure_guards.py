from __future__ import annotations

from typing import Any


def renumber_reply_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["order"] = len(output) + 1
        output.append(normalized)
    return output


def has_payment_collection(messages: list[dict[str, Any]]) -> bool:
    return any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict))


def has_handoff_notice(messages: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}
        for item in messages
        if isinstance(item, dict)
    )


def remove_payment_collection_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return renumber_reply_messages(
        [item for item in messages if isinstance(item, dict) and str(item.get("type") or "") != "payment_collection"]
    )
