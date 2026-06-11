from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Callable

from app.graph.task_state import appointment_slot_value


def appointment_query_from_state(
    content: str,
    store_lookup: dict[str, Any],
    state: dict[str, Any],
    extract_city: Callable[[str], str],
) -> dict[str, Any]:
    stores = store_lookup.get("stores") if isinstance(store_lookup, dict) else []
    store_name_hint = appointment_slot_value(state, "store_name")
    store = select_store_for_appointment(stores, store_name_hint)
    if not store and has_explicit_location_or_store(content, extract_city) and isinstance(stores, list) and stores:
        store = stores[0]

    explicit_store_id = state.get("confirmed_store_id") or state.get("store_id")
    explicit_store_name = state.get("confirmed_store_name") or state.get("store_name")
    if explicit_store_id:
        store = {"id": explicit_store_id, "name": explicit_store_name or store.get("name", "")}

    if not store and can_use_cached_appointment_store(content):
        appointment = state.get("appointment_cache") or {}
        if isinstance(appointment, dict) and appointment.get("store_id"):
            store = {
                "id": appointment.get("store_id"),
                "name": appointment.get("store_name", ""),
            }

    date_text = extract_date_value(content) or appointment_slot_value(state, "visit_date_value")
    missing: list[str] = []
    if not str(store.get("id") or "").strip():
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
    normalized_hint = re.sub(r"(门店|店名|店)$", "", hint)
    area_aliases = ["百星", "思明", "徐汇", "静安", "浦东", "渝北", "南岸", "渝中", "中贸"]
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
        for alias in area_aliases:
            if alias in normalized_hint and alias in haystack:
                return store
    return {}


def has_explicit_location_or_store(content: str, extract_city: Callable[[str], str]) -> bool:
    if not content:
        return False
    if extract_city(content):
        return True
    return any(
        term in content
        for term in ["店", "门店", "这家", "那家", "附近", "地址", "上海", "厦门", "重庆", "成都", "北京", "广州", "深圳"]
    )


def can_use_cached_appointment_store(content: str) -> bool:
    if not content:
        return False
    return any(
        term in content
        for term in ["原来那家", "之前那家", "上次那家", "预约的门店", "已经约的", "还是那家", "改约", "改时间", "取消"]
    )


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
