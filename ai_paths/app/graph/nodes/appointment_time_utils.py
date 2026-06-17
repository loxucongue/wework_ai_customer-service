from __future__ import annotations

import re
from typing import Any


def available_time_values(slots: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ["new", "old", "pre", "new_addon", "old_addon"]:
        _append_time_values(result, slots.get(key))
    _append_time_values(result, slots)
    return result


def _append_time_values(result: list[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = normalize_time_text(value) or value.strip()
        if text and text not in result:
            result.append(text)
        return
    if isinstance(value, list):
        for item in value:
            _append_time_values(result, item)
        return
    if isinstance(value, dict):
        for nested_key in ("time", "plan_at", "store_at", "begin", "start", "value"):
            if nested_key in value:
                _append_time_values(result, value.get(nested_key))
        for nested_value in value.values():
            if isinstance(nested_value, (list, dict)):
                _append_time_values(result, nested_value)


def normalize_time_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    exact = re.search(r"\b(\d{1,2})[:：](\d{2})\b", text)
    if exact:
        return f"{int(exact.group(1)):02d}:{exact.group(2)}"

    match = re.search(r"(上午|早上|中午|下午|晚上)?\s*(\d{1,2})\s*[点时](?:\s*(半|\d{1,2}分?))?", text)
    if not match:
        return ""
    period = match.group(1) or ""
    hour = int(match.group(2))
    minute_text = match.group(3) or ""
    minute = 30 if minute_text == "半" else 0
    if minute_text and minute_text != "半":
        minute_match = re.search(r"\d{1,2}", minute_text)
        minute = int(minute_match.group(0)) if minute_match else 0
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def target_time_status(slots: dict[str, Any], target_time: str, query: str = "") -> dict[str, Any]:
    values = available_time_values(slots)
    target = normalize_time_text(target_time) or normalize_time_text(query)
    if not target:
        return {"target_time": "", "target_time_available": None, "available_times": values}
    return {
        "target_time": target,
        "target_time_available": target in values,
        "available_times": values,
        "nearby_times": _nearby_times(values, target),
    }


def filter_times_by_preference(times: list[str], content: str) -> list[str]:
    if not times:
        return []
    exact_times = re.findall(r"\b\d{1,2}:\d{2}\b", content)
    if exact_times:
        exact = {time if len(time.split(":", 1)[0]) == 2 else f"0{time}" for time in exact_times}
        return [time for time in times if time in exact]

    normalized = normalize_time_text(content)
    if normalized:
        return [time for time in times if time == normalized]

    def hour_of(value: str) -> int:
        try:
            return int(value.split(":", 1)[0])
        except (ValueError, IndexError):
            return -1

    if "上午" in content or "早上" in content:
        return [time for time in times if 0 <= hour_of(time) < 12]
    if "中午" in content:
        return [time for time in times if 11 <= hour_of(time) < 14]
    if "下午" in content:
        return [time for time in times if 12 <= hour_of(time) < 18]
    if "晚上" in content or "6点后" in content or "六点后" in content:
        return [time for time in times if hour_of(time) >= 18]
    return times


def _nearby_times(times: list[str], target: str, *, max_items: int = 5) -> list[str]:
    target_minutes = _minutes(target)
    if target_minutes is None:
        return []
    ranked: list[tuple[int, str]] = []
    for time in times:
        minutes = _minutes(time)
        if minutes is None:
            continue
        ranked.append((abs(minutes - target_minutes), time))
    return [time for _, time in sorted(ranked)[:max_items]]


def _minutes(value: str) -> int | None:
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))
