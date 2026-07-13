from __future__ import annotations

import re
import unicodedata
from typing import Any

_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


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
        for key in value.keys():
            if isinstance(key, str):
                text = normalize_time_text(key)
                if text and text not in result:
                    result.append(text)
        for nested_key in ("time", "plan_at", "store_at", "begin", "start", "value"):
            if nested_key in value:
                _append_time_values(result, value.get(nested_key))
        for nested_value in value.values():
            if isinstance(nested_value, (list, dict)):
                _append_time_values(result, nested_value)


def normalize_time_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    exact = re.search(r"(?<!\d)(\d{1,2})[:：.](\d{2})(?!\d)", text)
    if exact:
        return f"{int(exact.group(1)):02d}:{exact.group(2)}"

    chinese_match = re.search(
        r"(上午|早上|中午|下午|晚上)?\s*([零〇一二两三四五六七八九十]{1,3})\s*[点时](?!点)"
        r"(?:\s*(半|一刻|三刻|\d{1,2}分?|[零〇一二两三四五六七八九十]{1,3}分?))?",
        text,
    )
    if chinese_match:
        period = chinese_match.group(1) or ""
        hour = _parse_chinese_number(chinese_match.group(2))
        minute_text = chinese_match.group(3) or ""
        minute = _parse_time_minute(minute_text)
        if hour is None or minute is None:
            return ""
        return _format_time(hour, minute, period)

    match = re.search(
        r"(上午|早上|中午|下午|晚上)?\s*(\d{1,2})\s*[点时](?!点)"
        r"(?:\s*(半|一刻|三刻|\d{1,2}分?|[零〇一二两三四五六七八九十]{1,3}分?))?",
        text,
    )
    if not match:
        return ""
    period = match.group(1) or ""
    hour = int(match.group(2))
    minute_text = match.group(3) or ""
    minute = _parse_time_minute(minute_text)
    if minute is None:
        return ""
    return _format_time(hour, minute, period)


def _format_time(hour: int, minute: int, period: str) -> str:
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return ""
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def _parse_time_minute(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return 0
    if text == "半":
        return 30
    if text == "一刻":
        return 15
    if text == "三刻":
        return 45
    digit_match = re.search(r"\d{1,2}", text)
    if digit_match:
        minute = int(digit_match.group(0))
        return minute if 0 <= minute <= 59 else None
    text = text.removesuffix("分钟").removesuffix("分")
    minute = _parse_chinese_number(text)
    if minute is None:
        return None
    return minute if 0 <= minute <= 59 else None


def _parse_chinese_number(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1:
        return _CHINESE_DIGITS.get(text)
    if text[0] in {"零", "〇"} and len(text) == 2:
        return _CHINESE_DIGITS.get(text[1])
    return None


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
    normalized_content = unicodedata.normalize("NFKC", str(content or ""))
    exact_times = re.findall(r"\b\d{1,2}:\d{2}\b", normalized_content)
    if exact_times:
        exact = {time if len(time.split(":", 1)[0]) == 2 else f"0{time}" for time in exact_times}
        return [time for time in times if time in exact]

    normalized = normalize_time_text(normalized_content)
    if normalized:
        return [time for time in times if time == normalized]

    def hour_of(value: str) -> int:
        try:
            return int(value.split(":", 1)[0])
        except (ValueError, IndexError):
            return -1

    if "上午" in normalized_content or "早上" in normalized_content:
        return [time for time in times if 0 <= hour_of(time) < 12]
    if "中午" in normalized_content:
        return [time for time in times if 11 <= hour_of(time) < 14]
    if "下午" in normalized_content:
        return [time for time in times if 13 <= hour_of(time) < 18]
    if "晚上" in normalized_content or "6点后" in normalized_content or "六点后" in normalized_content:
        return [time for time in times if hour_of(time) >= 18]
    return times


def summarize_available_slots(slots: dict[str, Any], content: str, *, target_time: str = "") -> dict[str, Any]:
    values = available_time_values(slots)
    status = target_time_status(slots, target_time, content)
    target = str(status.get("target_time") or "").strip()
    target_available = status.get("target_time_available")
    nearby = [str(item) for item in (status.get("nearby_times") or []) if str(item or "").strip()]
    preference = _time_preference(content)
    preferred = filter_times_by_preference(values, content)
    if not preferred and not preference:
        preferred = values

    recommended = ""
    backups: list[str] = []
    if target and target_available is True:
        recommended = target
        backups = [time for time in preferred if time != target][:1]
    elif target and target_available is False:
        recommended = nearby[0] if nearby else (preferred[0] if preferred else "")
        backups = [time for time in nearby[1:] if time != recommended][:1]
        if not backups:
            backups = [time for time in preferred if time != recommended][:1]
    else:
        recommended = preferred[0] if preferred else ""
        backups = [time for time in preferred[1:] if time != recommended][:1]

    return {
        "recommended_slot": recommended,
        "backup_slots": backups,
        "slot_count": len(values),
        "preference": preference,
        "target_time": target,
        "target_time_available": target_available,
        "nearby_times": nearby[:2],
    }


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


def _time_preference(content: str) -> str:
    text = str(content or "")
    if "上午" in text or "早上" in text:
        return "morning"
    if "中午" in text:
        return "noon"
    if "下午" in text:
        return "afternoon"
    if "晚上" in text or "6点后" in text or "六点后" in text:
        return "evening"
    if normalize_time_text(text):
        return "specific_time"
    return ""


def _minutes(value: str) -> int | None:
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(value or "").strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))
