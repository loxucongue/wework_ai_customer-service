from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Callable

from app.graph.task_state import appointment_slot_value
from app.graph.nodes.store_context import current_real_store_from_state, known_store_name_from_history


def appointment_query_from_state(
    content: str,
    store_lookup: dict[str, Any],
    state: dict[str, Any],
    extract_city: Callable[[str], str],
) -> dict[str, Any]:
    stores = store_lookup.get("stores") if isinstance(store_lookup, dict) else []
    store_name_hint = appointment_slot_value(state, "store_name") or known_store_name_from_history(state)
    hard_store = _confirmed_store_from_state(state)
    current_store = current_real_store_from_state(state)
    explicit_current_location = has_explicit_location_or_store(content, extract_city)
    needs_distance_lookup = bool(store_lookup.get("distance_lookup_required")) if isinstance(store_lookup, dict) else False
    current_lookup_store = _store_from_current_lookup(store_lookup, store_name_hint)
    if needs_distance_lookup and not current_lookup_store:
        store = {}
    elif explicit_current_location and current_lookup_store:
        store = current_lookup_store
    elif explicit_current_location:
        store = {}
    else:
        store = hard_store or (current_store if _should_prefer_current_store(content, current_store) else {})
    if not store and not explicit_current_location:
        store = select_store_for_appointment(stores, store_name_hint)
    if not store and not explicit_current_location:
        current_id = str(current_store.get("id") or "").strip()
        current_name = str(current_store.get("name") or "").strip()
        if current_id or current_name:
            if isinstance(stores, list) and stores:
                for item in stores:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id") or "").strip()
                    item_name = str(item.get("name") or "").strip()
                    if current_id and item_id == current_id:
                        store = item
                        break
                    if current_name and item_name and (item_name == current_name or current_name in item_name or item_name in current_name):
                        store = item
                        break
            if not store and (current_id or current_name):
                store = current_store
    if (
        not needs_distance_lookup
        and not store
        and has_explicit_location_or_store(content, extract_city)
        and isinstance(stores, list)
        and stores
    ):
        store = stores[0]

    if not store and not explicit_current_location and can_use_cached_appointment_store(content):
        appointment = state.get("appointment_cache") or {}
        if isinstance(appointment, dict) and appointment.get("store_id"):
            store = {
                "id": appointment.get("store_id"),
                "name": appointment.get("store_name", ""),
            }

    date_text = (
        extract_date_value(content)
        or _state_slot_value(state, "visit_date", "appointment_date", "date")
        or appointment_slot_value(state, "visit_date_value")
        or _history_date_value(state)
        or _known_info_date_value(state)
    )
    time_text = (
        extract_time_value(content)
        or _state_slot_value(state, "visit_time", "appointment_time", "time")
        or appointment_slot_value(state, "visit_time")
        or _history_time_value(state)
        or _known_info_time_value(state)
    )
    time_preference = extract_time_preference(content) or extract_time_preference(str(time_text or ""))
    missing: list[str] = []
    if not str(store.get("id") or "").strip():
        missing.append("store_id")
    if not date_text:
        missing.append("date")
    return {
        "store_id": str(store.get("id") or ""),
        "store_name": str(store.get("name") or ""),
        "date": date_text,
        "time": time_text,
        "time_text": time_text,
        "time_preference": time_preference,
        "store_source": str(store.get("source") or ""),
        "missing": missing,
    }


def _confirmed_store_from_state(state: dict[str, Any]) -> dict[str, Any]:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    candidates: list[tuple[str, str, str]] = []
    for source in (state, request_context):
        candidates.append(
            (
                str(source.get("confirmed_store_id") or source.get("store_id") or "").strip(),
                str(source.get("confirmed_store_name") or source.get("store_name") or "").strip(),
                "hard_state",
            )
        )
    # Current sales intent should win over historical appointment/order facts.
    # The profile analyzer writes the matched store here after a store_address card is sent.
    customer_basic_info = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    candidates.append(
        (
            str(customer_basic_info.get("preferred_store_id") or customer_basic_info.get("confirmed_store_id") or "").strip(),
            str(customer_basic_info.get("preferred_store_name") or customer_basic_info.get("confirmed_store_name") or "").strip(),
            "customer_basic_info",
        )
    )
    customer_profile = state.get("customer_profile") if isinstance(state.get("customer_profile"), dict) else {}
    candidates.append(
        (
            str(customer_profile.get("preferred_store_id") or customer_profile.get("confirmed_store_id") or "").strip(),
            str(customer_profile.get("preferred_store_name") or customer_profile.get("confirmed_store_name") or "").strip(),
            "customer_profile",
        )
    )
    appointment = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    candidates.append(
        (
            str(appointment.get("store_id") or "").strip(),
            str(appointment.get("store_name") or "").strip(),
            "appointment_cache",
        )
    )
    customer_context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    appointment_context = customer_context.get("appointment") if isinstance(customer_context.get("appointment"), dict) else {}
    candidates.append(
        (
            str(appointment_context.get("store_id") or "").strip(),
            str(appointment_context.get("store_name") or "").strip(),
            "customer_context",
        )
    )
    for store_id, store_name, source in candidates:
        if store_id or store_name:
            return {"id": store_id, "name": store_name, "source": source}
    return {}


def _state_slot_value(state: dict[str, Any], *keys: str) -> str:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    appointment = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    for source in (state, request_context, appointment):
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def extract_time_preference(content: str) -> str:
    text = str(content or "")
    if any(term in text for term in ("早上", "上午")):
        return "morning"
    if "中午" in text:
        return "noon"
    if "下午" in text:
        return "afternoon"
    if "晚上" in text:
        return "evening"
    return ""


def _history_date_value(state: dict[str, Any]) -> str:
    for text in _recent_customer_texts(state):
        value = extract_date_value(text)
        if value:
            return value
    return ""


def _history_time_value(state: dict[str, Any]) -> str:
    for text in _recent_customer_texts(state):
        value = extract_time_value(text)
        if value:
            return value
    return ""


def _known_info_date_value(state: dict[str, Any]) -> str:
    for text in _planner_known_texts(state):
        value = extract_date_value(text)
        if value:
            return value
    return ""


def _known_info_time_value(state: dict[str, Any]) -> str:
    for text in _planner_known_texts(state):
        value = extract_time_value(text)
        if value:
            return value
    return ""


def _recent_customer_texts(state: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in reversed(state.get("conversation_history") or []):
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role and role not in {"user", "customer"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
        else:
            text = str(item or "").strip()
            if text.startswith(("小贝：", "小贝:", "客服：", "客服:", "AI回复：", "AI回复:", "助手：", "助手:")):
                continue
            if text.startswith(("客户：", "客户:", "用户：", "用户:")):
                text = text.split("：", 1)[-1] if "：" in text else text.split(":", 1)[-1]
        if text:
            texts.append(text)
    return texts[:10]


def _planner_known_texts(state: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    primary = state.get("primary_task") if isinstance(state.get("primary_task"), dict) else {}
    values = primary.get("known_info") if isinstance(primary, dict) else []
    if isinstance(values, list):
        texts.extend(str(item or "").strip() for item in values if str(item or "").strip())
    secondary = state.get("secondary_tasks") if isinstance(state.get("secondary_tasks"), list) else []
    for task in secondary:
        if not isinstance(task, dict):
            continue
        values = task.get("known_info")
        if isinstance(values, list):
            texts.extend(str(item or "").strip() for item in values if str(item or "").strip())
    return texts[:12]


def select_store_for_appointment(stores: Any, store_name_hint: str) -> dict[str, Any]:
    if not isinstance(stores, list) or not stores:
        return {}
    hint = str(store_name_hint or "").strip()
    if not hint:
        return {}
    normalized_hint = re.sub(r"(门店|店名|店)$", "", hint)
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
    return {}


def _store_from_current_lookup(store_lookup: dict[str, Any], store_name_hint: str) -> dict[str, Any]:
    if not isinstance(store_lookup, dict):
        return {}
    recommended = store_lookup.get("recommended_store") if isinstance(store_lookup.get("recommended_store"), dict) else {}
    if recommended and (recommended.get("id") or recommended.get("store_id") or recommended.get("name")):
        return recommended
    stores = store_lookup.get("stores") if isinstance(store_lookup.get("stores"), list) else []
    if not stores:
        return {}
    requested = str(store_lookup.get("requested_store") or "").strip() or str(store_name_hint or "").strip()
    if requested:
        selected = select_store_for_appointment(stores, requested)
        if selected:
            return selected
    if len(stores) == 1 and not store_lookup.get("distance_lookup_required"):
        first = stores[0]
        return first if isinstance(first, dict) else {}
    return {}


def has_explicit_location_or_store(content: str, extract_city: Callable[[str], str]) -> bool:
    if not content:
        return False
    if extract_city(content):
        return True
    if re.search(r"[\u4e00-\u9fa5A-Za-z0-9]{2,24}店", content):
        return True
    return any(
        term in content
        for term in ["门店", "这家", "那家", "附近", "地址"]
    )


def can_use_cached_appointment_store(content: str) -> bool:
    if not content:
        return False
    return any(
        term in content
        for term in ["原来那家", "之前那家", "上次那家", "预约的门店", "已经约的", "还是那家", "改约", "改时间", "取消"]
    )


def extract_date_value(content: str) -> str:
    explicit = re.search(r"(20\d{2})(?:[-/.年])(\d{1,2})(?:[-/.月])(\d{1,2})", content)
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
        "周天": 6,
        "星期日": 6,
        "星期天": 6,
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


def extract_time_value(content: str) -> str:
    explicit = re.search(r"(\d{1,2})[:：](\d{2})", content)
    if explicit:
        return f"{int(explicit.group(1)):02d}:{explicit.group(2)}"
    hour_match = re.search(r"(上午|下午|晚上|中午)?\s*(\d{1,2}|[一二两三四五六七八九十十一十二])\s*点", content)
    if not hour_match:
        if "上午" in content:
            return "10:00"
        if "中午" in content:
            return "12:00"
        if "下午" in content:
            return "14:00"
        if "晚上" in content:
            return "18:00"
        return ""
    prefix = hour_match.group(1) or ""
    hour = _hour_number(hour_match.group(2))
    if hour is None:
        return ""
    if prefix in {"下午", "晚上"} and hour < 12:
        hour += 12
    if prefix == "中午" and hour < 11:
        hour += 12
    return f"{hour:02d}:00"


def _hour_number(value: str) -> int | None:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    mapping = {
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
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    return mapping.get(text)


def _should_prefer_current_store(content: str, current_store: dict[str, Any]) -> bool:
    if not (str(current_store.get("id") or "").strip() or str(current_store.get("name") or "").strip()):
        return False
    text = str(content or "").strip()
    if not text:
        return False
    if re.search(r"1[3-9]\d{9}", text):
        return True
    if extract_date_value(text) or extract_time_value(text):
        return True
    return any(
        term in text
        for term in (
            "预约",
            "登记",
            "报名",
            "安排",
            "交10",
            "付10",
            "预约金",
            "定金",
            "订金",
            "到店",
            "过来",
            "过去",
        )
    )
