from __future__ import annotations

from typing import Any

from app.graph.state import AgentState
from app.graph.task_appointment_signals import (
    has_current_appointment_signal as _has_current_appointment_signal,
    has_non_appointment_interrupt as _has_non_appointment_interrupt,
    has_strong_new_non_appointment_intent as _has_strong_new_non_appointment_intent,
    is_appointment_followup as _is_appointment_followup,
    looks_like_appointment_context as _looks_like_appointment_context,
    visit_time_from_context as _visit_time_from_context,
)
from app.graph.task_slots import (
    city_from_text as _city_from_text,
    party_size_from_text as _party_size_from_text,
    recent_text as _recent_text,
    store_name_from_state as _store_name_from_state,
    store_name_from_text as _store_name_from_text,
    visit_date_from_text as _visit_date_from_text,
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


def _prepend_unique_intent(item: dict[str, Any], intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intent = item.get("intent")
    skill = item.get("skill")
    kept = [existing for existing in intents if existing.get("intent") != intent and existing.get("skill") != skill]
    return [item] + kept[:2]
