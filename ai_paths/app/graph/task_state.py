from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.graph.state import AgentState
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    CITY_NAMES,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    PROJECT_KEYWORDS,
    TRUST_KEYWORDS,
)


def build_active_task(state: AgentState, intents: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the unfinished customer task for planner/reply prompts."""
    appointment = _appointment_task(state, intents)
    if appointment:
        return appointment
    return {}


def apply_active_task_intent(
    state: AgentState,
    intents: list[dict[str, Any]],
    active_task: dict[str, Any],
) -> list[dict[str, Any]]:
    if active_task.get("type") != "appointment_visit":
        return intents
    if not _is_appointment_followup(state):
        return intents
    if _has_strong_new_non_appointment_intent(state):
        return intents

    item = {
        "intent": "appointment_intent",
        "skill": "appointment",
        "priority": 1,
        "reason": "承接未完成的到店预约任务",
    }
    appointment_skills = {"appointment", "store"}
    appointment_intents = {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel", "store_inquiry"}
    filtered = [
        intent
        for intent in intents
        if intent.get("skill") in appointment_skills or intent.get("intent") in appointment_intents
    ]
    return _prepend_unique_intent(item, filtered)


def is_active_appointment_task(state: AgentState) -> bool:
    task = state.get("active_task") or {}
    return isinstance(task, dict) and task.get("type") == "appointment_visit"


def appointment_slot_value(state: AgentState, name: str) -> str:
    task = state.get("active_task") or {}
    if not isinstance(task, dict):
        return ""
    slots = task.get("known_slots") or {}
    if not isinstance(slots, dict):
        return ""
    return str(slots.get(name) or "").strip()


def _appointment_task(state: AgentState, intents: list[dict[str, Any]]) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    recent = _recent_text(state, limit=12)
    if _has_non_appointment_interrupt(content) and not _has_current_appointment_signal(content):
        return {}
    combined = "\n".join(part for part in [recent, content] if part)
    has_appointment_intent = any(item.get("skill") == "appointment" for item in intents)
    has_store_intent = any(item.get("skill") == "store" for item in intents)
    if not (has_appointment_intent or _looks_like_appointment_context(combined) or _is_appointment_followup(state)):
        return {}

    city = _city_from_text(content) or _city_from_text(recent)
    store_name = _store_name_from_state(state) or _store_name_from_text(combined, city)
    visit_date_label, visit_date_value = _visit_date_from_text(content) or _visit_date_from_text(recent) or ("", "")
    visit_time = _visit_time_from_context(content, recent)
    party_size = _party_size_from_text(content) or _party_size_from_text(recent)

    known_slots = {
        "city": city,
        "store_name": store_name,
        "visit_date_label": visit_date_label,
        "visit_date_value": visit_date_value,
        "visit_time": visit_time,
        "party_size": party_size,
    }
    missing_slots = []
    if not city and not store_name:
        missing_slots.append("城市或门店")
    if not store_name and city:
        missing_slots.append("门店")
    if not visit_date_value:
        missing_slots.append("日期")
    if not visit_time:
        missing_slots.append("到店时间")

    status = "ready_to_check_availability" if store_name and visit_date_value else "collecting_slots"
    if store_name and visit_date_value and visit_time:
        status = "ready_to_check_availability"

    return {
        "type": "appointment_visit",
        "status": status,
        "known_slots": {key: value for key, value in known_slots.items() if value not in ("", 0, None)},
        "missing_slots": missing_slots,
        "reply_focus": "继续完成预约接待任务，复用已知门店、日期、时间和人数，不要切回项目需求询问。",
        "next_action": _appointment_next_action(status, missing_slots),
        "source": "conversation_context",
    }


def _appointment_next_action(status: str, missing_slots: list[str]) -> str:
    if status == "ready_to_confirm":
        return "按已知门店和日期查可约时间，并结合客户偏好时间继续确认。"
    if status == "ready_to_check_availability":
        return "先查已知门店和日期的可约时间，再问客户更方便的时间段。"
    if missing_slots:
        return "只追问最关键的缺失槽位；如果城市/门店也缺，优先问城市或门店：" + "、".join(missing_slots[:2])
    return "继续确认预约信息。"


def _is_appointment_followup(state: AgentState) -> bool:
    content = (state.get("normalized_content") or "").strip()
    if not content:
        return False
    if _has_non_appointment_interrupt(content) and not _has_current_appointment_signal(content):
        return False
    if any(term in content for term in APPOINTMENT_KEYWORDS):
        return True
    if _visit_date_from_text(content) or _visit_time_from_text(content) or _party_size_from_text(content):
        return True
    short_followups = [
        "是的",
        "对",
        "对的",
        "嗯",
        "好",
        "好的",
        "可以",
        "可以的",
        "行",
        "亲",
        "约好吗",
        "约好了么",
        "约好了吗",
        "可以约吗",
        "能约吗",
        "行吗",
        "可以吗",
        "就这个",
        "就这家",
        "这家吧",
        "那家吧",
        "确定",
        "确认",
        "肯定是今天",
        "今天啊",
        "就是今天",
        "现在就过来",
        "现在过来",
        "现在过去",
        "马上过来",
        "马上过去",
        "直接过去",
        "就过来",
        "下午五点",
        "下午5点",
    ]
    if any(term in content for term in short_followups):
        return _looks_like_appointment_context(_recent_text(state, limit=10))
    if len(content) <= 8 and any(term in content for term in ["今天", "明天", "后天", "下午", "上午", "晚上", "五点", "5点", "约", "过来", "过去", "位置"]):
        return _looks_like_appointment_context(_recent_text(state, limit=10))
    return False


def _has_strong_new_non_appointment_intent(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if _has_non_appointment_interrupt(content):
        return True
    strong_groups = [
        TRUST_KEYWORDS,
        PRICE_KEYWORDS,
        CAMPAIGN_KEYWORDS,
        COMPETITOR_KEYWORDS,
        AFTER_SALES_KEYWORDS,
    ]
    if any(any(term in content for term in group) for group in strong_groups):
        return True
    if any(term in content for term in PROJECT_KEYWORDS) and not any(term in content for term in APPOINTMENT_KEYWORDS):
        return True
    return False


def _has_current_appointment_signal(content: str) -> bool:
    if not content:
        return False
    return bool(
        any(term in content for term in APPOINTMENT_KEYWORDS)
        or _visit_date_from_text(content)
        or _visit_time_from_text(content)
        or _party_size_from_text(content)
    )


def _has_non_appointment_interrupt(content: str) -> bool:
    if not content:
        return False
    hard_terms = [
        "投诉",
        "退款",
        "退钱",
        "退给我",
        "骗人",
        "骗子",
        "被坑",
        "坑我",
        "太坑",
        "乱收费",
        "加钱",
        "额外收费",
        "收费不一样",
        "效果不好",
        "效果一点也不好",
        "效果一点都不好",
        "一点效果都没有",
        "一点用都没",
        "没效果",
        "没变化",
        "跟没做一样",
        "白做",
        "白花钱",
        "为什么这么慢",
        "怎么这么慢",
        "回复太慢",
        "回消息太慢",
        "没人回",
        "等这么久",
    ]
    return any(term in content for term in hard_terms)


def _looks_like_appointment_context(text: str) -> bool:
    if not text:
        return False
    appointment_terms = [
        "预约",
        "到店",
        "来店",
        "接待",
        "过来",
        "过去",
        "可约",
        "空闲",
        "时间",
        "几点",
        "位置",
        "安排位置",
        "五点",
        "5点",
        "下午",
        "上午",
        "现在过来",
        "现在过去",
        "马上过来",
        "直接过去",
    ]
    store_terms = ["门店", "店", "地址", "厦门", "上海", "重庆", "成都", "嘉定", "百星", "思明", "徐汇", "静安", "浦东"]
    strong_schedule_terms = ["安排位置", "位置", "几点", "几点呀", "现在过来", "现在过去", "马上过来", "直接过去", "可约", "空闲"]
    if any(term in text for term in strong_schedule_terms):
        return True
    return any(term in text for term in appointment_terms) and any(term in text for term in store_terms)


def _recent_text(state: AgentState, limit: int = 10) -> str:
    history = state.get("conversation_history") or []
    return "\n".join(str(item) for item in history[-limit:])


def _city_from_text(text: str) -> str:
    for city in CITY_NAMES:
        if city in text:
            return city
    return ""


def _store_name_from_state(state: AgentState) -> str:
    for key in ["confirmed_store_name", "store_name"]:
        value = str(state.get(key) or "").strip()
        if value:
            return value
    return ""


def _store_name_from_text(text: str, city: str = "") -> str:
    alias_candidates: list[str] = []
    regex_candidates: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^(用户|客户|助手|小贝)\s*[:：]\s*", "", line.strip())
        if not cleaned:
            continue
        if _line_expresses_unknown_store(cleaned):
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


def _line_expresses_unknown_store(text: str) -> bool:
    unknown_terms = ["不知道哪家", "不确定哪家", "不知道哪个店", "不确定哪个店", "不知道门店", "不确定门店", "哪家方便", "附近哪家"]
    return any(term in text for term in unknown_terms)


def _visit_date_from_text(text: str) -> tuple[str, str] | None:
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


def _visit_time_from_text(text: str) -> str:
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
            if not _chinese_hour_has_time_context(text, word):
                continue
            if any(period in text for period in ["下午", "晚上"]) and hour < 12:
                hour += 12
            return f"{hour:02d}:00"
    return ""


def _visit_time_from_context(content: str, recent: str) -> str:
    if _has_non_appointment_interrupt(content) and not _has_current_appointment_signal(content):
        return ""
    current_time = _visit_time_from_text(content)
    recent_time = _visit_time_from_text(recent)
    if (
        current_time
        and recent_time
        and not _has_time_period(content)
        and _has_time_period(recent)
        and _same_clock_hour(current_time, recent_time)
    ):
        return recent_time
    return current_time or recent_time


def _has_time_period(text: str) -> bool:
    return any(period in text for period in ["上午", "中午", "下午", "晚上"])


def _chinese_hour_has_time_context(text: str, word: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if _is_chinese_hour_false_positive(compact, word):
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


def _is_chinese_hour_false_positive(text: str, word: str) -> bool:
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


def _same_clock_hour(left: str, right: str) -> bool:
    try:
        left_hour = int(left.split(":", 1)[0])
        right_hour = int(right.split(":", 1)[0])
    except (TypeError, ValueError):
        return False
    return left_hour % 12 == right_hour % 12


def _party_size_from_text(text: str) -> int:
    if not text:
        return 0
    friend = re.search(r"带\s*(\d+)\s*个?朋友", text)
    if friend:
        return int(friend.group(1)) + 1
    party = re.search(r"(\d+)\s*个?人", text)
    if party:
        return int(party.group(1))
    return 0


def _prepend_unique_intent(item: dict[str, Any], intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intent = item.get("intent")
    skill = item.get("skill")
    kept = [existing for existing in intents if existing.get("intent") != intent and existing.get("skill") != skill]
    return [item] + kept[:2]
