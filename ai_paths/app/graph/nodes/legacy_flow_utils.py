from __future__ import annotations

import re
from typing import Any, Callable


def compact_memory(memory: dict[str, Any]) -> dict[str, Any]:
    if not memory:
        return {}
    return {
        "portrait_keys": list((memory.get("portrait") or {}).keys())[:12],
        "basic_info": memory.get("basic_info") or {},
        "history_events_count": len(memory.get("history_events") or []),
        "updated_at": memory.get("updated_at", ""),
    }


def extract_price_digits(content: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", content or "")


def available_slot_list(slots_value: Any, dedupe_strings: Callable[[list[str]], list[str]]) -> list[str]:
    if isinstance(slots_value, dict):
        values: list[str] = []
        for key in ["new", "new_addon", "old", "old_addon", "pre"]:
            raw = slots_value.get(key)
            if isinstance(raw, list):
                values.extend(str(item) for item in raw if item)
        return dedupe_strings(values)
    if isinstance(slots_value, list):
        return dedupe_strings([str(item) for item in slots_value if item])
    return []


def parking_text(store: dict[str, Any]) -> str:
    parking_name = str(store.get("parking_name") or "").strip()
    parking_address = str(store.get("parking_address") or "").strip()
    if parking_name and parking_address:
        return f"停车：{parking_name}，{parking_address}"
    if parking_name:
        return f"停车：{parking_name}"
    if parking_address:
        return f"停车：{parking_address}"
    return ""
