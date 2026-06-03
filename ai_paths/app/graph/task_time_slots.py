from __future__ import annotations

import re
from datetime import date, timedelta


def visit_date_from_text(text: str) -> tuple[str, str] | None:
    if not text:
        return None
    today = date.today()
    relative = {
        "今天": today,
        "明天": today + timedelta(days=1),
        "后天": today + timedelta(days=2),
    }
    for label, value in relative.items():
        if label in text:
            return label, value.isoformat()
    explicit = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if explicit:
        year, month, day = [int(part) for part in explicit.groups()]
        return f"{year}-{month:02d}-{day:02d}", date(year, month, day).isoformat()
    month_day = re.search(r"(\d{1,2})月(\d{1,2})[日号]?", text)
    if month_day:
        month, day = [int(part) for part in month_day.groups()]
        candidate = date(today.year, month, day)
        if candidate < today:
            candidate = date(today.year + 1, month, day)
        return f"{month}月{day}日", candidate.isoformat()
    weekday_map = {
        "周一": 0,
        "星期一": 0,
        "周二": 1,
        "星期二": 1,
        "周三": 2,
        "星期三": 2,
        "周四": 3,
        "星期四": 3,
        "周五": 4,
        "星期五": 4,
        "周六": 5,
        "星期六": 5,
        "周日": 6,
        "星期日": 6,
        "周末": 5,
    }
    for label, target in weekday_map.items():
        if label in text:
            days = (target - today.weekday()) % 7 or 7
            return label, (today + timedelta(days=days)).isoformat()
    return None


def visit_time_from_text(text: str) -> str:
    if not text:
        return ""
    half_match = re.search(r"(上午|中午|下午|晚上)?\s*(\d{1,2})\s*点半", text)
    if half_match:
        period, hour_raw = half_match.groups()
        hour = int(hour_raw)
        if period in {"下午", "晚上"} and hour < 12:
            hour += 12
        return f"{hour:02d}:30"
    match = re.search(r"(上午|中午|下午|晚上)?\s*(\d{1,2})\s*[点:：]\s*(\d{1,2})?", text)
    if match:
        period, hour_raw, minute_raw = match.groups()
        hour = int(hour_raw)
        if period in {"下午", "晚上"} and hour < 12:
            hour += 12
        minute = int(minute_raw) if minute_raw else 0
        return f"{hour:02d}:{minute:02d}"
    chinese_hour = {
        "一点": 1,
        "两点": 2,
        "二点": 2,
        "三点": 3,
        "四点": 4,
        "五点": 5,
        "六点": 6,
        "七点": 7,
        "八点": 8,
        "九点": 9,
        "十点": 10,
    }
    for word, hour in chinese_hour.items():
        if word in text:
            if not chinese_hour_has_time_context(text, word):
                continue
            if any(period in text for period in ["下午", "晚上"]) and hour < 12:
                hour += 12
            return f"{hour:02d}:00"
    return ""


def has_time_period(text: str) -> bool:
    return any(period in text for period in ["上午", "中午", "下午", "晚上"])


def chinese_hour_has_time_context(text: str, word: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if is_chinese_hour_false_positive(compact, word):
        return False
    index = compact.find(word)
    if index < 0:
        return False
    window = compact[max(0, index - 6) : index + len(word) + 6]
    if any(period in window for period in ["上午", "中午", "下午", "晚上"]):
        return True
    if len(compact) <= len(word) + 3:
        return True
    context_terms = [
        "预约",
        "到店",
        "来店",
        "过来",
        "过去",
        "直接去",
        "直接来",
        "可约",
        "能约",
        "空位",
        "空档",
        "时间",
        "时段",
        "几点",
        "改到",
        "安排",
        "排到",
        "今天",
        "明天",
        "后天",
    ]
    return any(term in compact for term in context_terms)


def is_chinese_hour_false_positive(text: str, word: str) -> bool:
    false_patterns = [
        f"{word}也",
        f"{word}都",
        f"{word}不",
        f"{word}没",
        f"{word}用",
        f"{word}效果",
        f"{word}不好",
        f"{word}点",
        f"差{word}",
        f"好{word}",
        f"强{word}",
        f"轻{word}",
        f"重点{word}",
    ]
    return any(pattern in text for pattern in false_patterns)


def same_clock_hour(left: str, right: str) -> bool:
    try:
        left_hour = int(left.split(":", 1)[0])
        right_hour = int(right.split(":", 1)[0])
    except (TypeError, ValueError):
        return False
    return left_hour % 12 == right_hour % 12
