from __future__ import annotations

from typing import Any

from app.graph.state import AgentState
from app.graph.task_slots import (
    city_from_text as _city_from_text,
    has_time_period as _has_time_period,
    party_size_from_text as _party_size_from_text,
    recent_text as _recent_text,
    same_clock_hour as _same_clock_hour,
    store_name_from_state as _store_name_from_state,
    store_name_from_text as _store_name_from_text,
    visit_date_from_text as _visit_date_from_text,
    visit_time_from_text as _visit_time_from_text,
)
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
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


def _prepend_unique_intent(item: dict[str, Any], intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intent = item.get("intent")
    skill = item.get("skill")
    kept = [existing for existing in intents if existing.get("intent") != intent and existing.get("skill") != skill]
    return [item] + kept[:2]
