from __future__ import annotations

import re
from typing import Any


def available_time_values(slots: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ["new", "old", "pre", "new_addon", "old_addon"]:
        values = slots.get(key) or []
        if isinstance(values, list):
            for value in values:
                text = str(value).strip()
                if text and text not in result:
                    result.append(text)
    return result


def filter_times_by_preference(times: list[str], content: str) -> list[str]:
    if not times:
        return []
    exact_times = re.findall(r"\b\d{1,2}:\d{2}\b", content)
    if exact_times:
        exact = {time if len(time.split(":", 1)[0]) == 2 else f"0{time}" for time in exact_times}
        return [time for time in times if time in exact]

    def hour_of(value: str) -> int:
        try:
            return int(value.split(":", 1)[0])
        except (ValueError, IndexError):
            return -1

    if "上午" in content:
        return [time for time in times if 0 <= hour_of(time) < 12]
    if "中午" in content:
        return [time for time in times if 11 <= hour_of(time) < 14]
    if "下午" in content:
        return [time for time in times if 12 <= hour_of(time) < 18]
    if "晚上" in content or "6点后" in content or "六点后" in content:
        return [time for time in times if hour_of(time) >= 18]
    return times
