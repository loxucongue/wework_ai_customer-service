from __future__ import annotations

import re
from typing import Any


def sent_message_summary_for_model(state: dict[str, Any]) -> dict[str, Any]:
    """Summarize previously sent special message types for model context."""

    payment_sent = False
    payment_count = 0
    activity_intro_image_sent = False
    store_ids: list[str] = []

    for event in state.get("history_events") or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        if event_type == "payment_collection_sent":
            payment_sent = True
            payment_count += 1
        if event_type == "activity_intro_image_sent":
            activity_intro_image_sent = True
        if event_type == "store_address_sent":
            store_id = str(facts.get("store_id") or "").strip()
            if store_id:
                store_ids.append(store_id)

    for item in state.get("conversation_history") or []:
        text = str(item or "")
        if "payment_collection" in text or "预约金收款卡" in text or "付款入口" in text:
            payment_sent = True
            payment_count += 1
        if "activity_intro_image" in text or "anniversary-268.jpg" in text:
            activity_intro_image_sent = True
        if "store_address" in text or "门店位置卡" in text:
            for match in re.finditer(r"(?:store_id|门店ID)\s*[=:：]\s*(\d+)", text, flags=re.IGNORECASE):
                store_ids.append(match.group(1))

    unique_store_ids = list(dict.fromkeys(store_ids))
    output = {
        "payment_collection_sent": payment_sent,
        "payment_collection_count": payment_count,
        "activity_intro_image_sent": activity_intro_image_sent,
        "store_address_sent_by_store_id": unique_store_ids,
    }
    return {key: value for key, value in output.items() if value not in (False, 0, [], {}, None, "")}
