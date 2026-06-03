from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from app.graph import task_state


@dataclass(frozen=True)
class AppointmentQueryCallbacks:
    extract_city: Callable[[str], str]


def appointment_query_from_state(
    content: str,
    store_lookup: dict[str, Any],
    state: dict[str, Any],
    callbacks: AppointmentQueryCallbacks,
) -> dict[str, Any]:
    stores = store_lookup.get("stores") if isinstance(store_lookup, dict) else []
    store_name_hint = task_state.appointment_slot_value(state, "store_name")
    store = select_store_for_appointment(stores, store_name_hint)
    if not store:
        store = stores[0] if has_explicit_location_or_store(content, callbacks.extract_city) and isinstance(stores, list) and stores else {}
    explicit_store_id = state.get("confirmed_store_id") or state.get("store_id")
    explicit_store_name = state.get("confirmed_store_name") or state.get("store_name")
    if explicit_store_id:
        store = {"id": explicit_store_id, "name": explicit_store_name or store.get("name", "")}
    if not store and can_use_cached_appointment_store(content):
        appointment = state.get("appointment_cache") or {}
        if isinstance(appointment, dict) and appointment.get("store_id"):
            store = {"id": appointment.get("store_id"), "name": appointment.get("store_name", "")}
    date_text = extract_date_value(content) or task_state.appointment_slot_value(state, "visit_date_value")
    missing = []
    if not store.get("id"):
        missing.append("store_id")
    if not date_text:
        missing.append("date")
    return {
        "store_id": str(store.get("id") or ""),
        "store_name": str(store.get("name") or ""),
        "date": date_text,
        "missing": missing,
    }


def select_store_for_appointment(stores: Any, store_name_hint: str) -> dict[str, Any]:
    if not isinstance(stores, list) or not stores:
        return {}
    hint = str(store_name_hint or "").strip()
    if not hint:
        return {}
    normalized_hint = re.sub(r"(门店|店|吧|呀|啊)$", "", hint)
    for store in stores:
        if not isinstance(store, dict):
            continue
        name = str(store.get("name") or "")
        address = str(store.get("address") or "")
        haystack = f"{name} {address}"
        if hint and hint in haystack:
            return store
        if normalized_hint and normalized_hint in haystack:
            return store
        if "百星" in normalized_hint and "百星" in haystack:
            return store
        if "思明" in normalized_hint and "思明" in haystack:
            return store
        if "徐汇" in normalized_hint and "徐汇" in haystack:
            return store
        if "静安" in normalized_hint and "静安" in haystack:
            return store
        if "浦东" in normalized_hint and "浦东" in haystack:
            return store
    return {}


def has_explicit_location_or_store(content: str, extract_city: Callable[[str], str]) -> bool:
    if not content:
        return False
    if extract_city(content):
        return True
    return any(term in content for term in ["店", "门店", "这家", "那家", "刚刚那家", "附近", "地址", "上海", "厦门", "重庆", "成都", "北京", "广州", "深圳"])


def can_use_cached_appointment_store(content: str) -> bool:
    if not content:
        return False
    return any(term in content for term in ["原来那家", "之前那家", "上次那家", "预约的门店", "已约的", "还是那家", "改约", "改时间", "换个时间", "取消"])


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


def extract_date_value(content: str) -> str:
    explicit = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", content)
    if explicit:
        year, month, day = [int(part) for part in explicit.groups()]
        return date(year, month, day).isoformat()
    today = date.today()
    if "今天" in content:
        return today.isoformat()
    if "明天" in content:
        return (today + timedelta(days=1)).isoformat()
    if "后天" in content:
        return (today + timedelta(days=2)).isoformat()
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
    for text, target in weekday_map.items():
        if text in content:
            days = (target - today.weekday()) % 7
            if days == 0:
                days = 7
            return (today + timedelta(days=days)).isoformat()
    month_day = re.search(r"(\d{1,2})月(\d{1,2})[日号]?", content)
    if month_day:
        month, day = [int(part) for part in month_day.groups()]
        year = today.year
        candidate = date(year, month, day)
        if candidate < today:
            candidate = date(year + 1, month, day)
        return candidate.isoformat()
    return ""
