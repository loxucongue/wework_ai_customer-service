from __future__ import annotations

import re
from datetime import date, timedelta

from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES


def recent_text(state: AgentState, limit: int = 10) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def city_from_text(text: str) -> str:
    for city in CITY_NAMES:
        if city in text:
            return city
    return ""


def store_name_from_state(state: AgentState) -> str:
    for key in ["confirmed_store_name", "store_name"]:
        value = str(state.get(key) or "").strip()
        if value:
            return value
    return ""


def store_name_from_text(text: str, city: str = "") -> str:
    alias_candidates: list[str] = []
    regex_candidates: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^(用户|客户|助手|小贝)\s*[:：]\s*", "", line.strip())
        if not cleaned:
            continue
        if line_expresses_unknown_store(cleaned):
            continue
        if any(term in cleaned for term in ["百星", "思明", "二店", "徐汇", "静安", "浦东", "武侯", "渝北", "嘉定"]):
            if "百星" in cleaned:
                alias_candidates.append(f"{city or '厦门'}百星")
            elif "思明" in cleaned:
                alias_candidates.append(f"{city or '厦门'}思明")
            elif "二店" in cleaned:
                alias_candidates.append(f"{city or ''}二店".strip())
            elif "徐汇" in cleaned:
                alias_candidates.append("上海徐汇")
            elif "静安" in cleaned:
                alias_candidates.append("上海静安")
            elif "浦东" in cleaned:
                alias_candidates.append("上海浦东")
            elif "武侯" in cleaned:
                alias_candidates.append("成都武侯")
            elif "渝北" in cleaned:
                alias_candidates.append("重庆渝北")
            elif "嘉定" in cleaned:
                alias_candidates.append("嘉定")
        match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,8}(?:门店|店))", cleaned)
        if match and not any(term in match.group(1) for term in ["来店", "到店", "店吗", "哪家", "哪个", "不知道", "不确定"]):
            regex_candidates.append(match.group(1))
    if alias_candidates:
        return alias_candidates[-1]
    return regex_candidates[-1] if regex_candidates else ""


def line_expresses_unknown_store(text: str) -> bool:
    unknown_terms = ["不知道哪家", "不确定哪家", "不知道哪个店", "不确定哪个店", "不知道门店", "不确定门店", "哪家方便", "附近哪家"]
    return any(term in text for term in unknown_terms)


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


def party_size_from_text(text: str) -> int:
    if not text:
        return 0
    friend = re.search(r"带\s*(\d+)\s*个?朋友", text)
    if friend:
        return int(friend.group(1)) + 1
    people = re.search(r"(\d+)\s*个?人", text)
    if people:
        return int(people.group(1))
    chinese_people = {
        "两个人": 2,
        "二个人": 2,
        "三个人": 3,
        "四个人": 4,
        "五个人": 5,
        "我和朋友": 2,
        "带朋友": 2,
        "带闺蜜": 2,
    }
    for word, value in chinese_people.items():
        if word in text:
            return value
    return 0
