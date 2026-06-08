from __future__ import annotations

import re
from typing import Any

from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_catalog import StoreRecord
from app.services import store_text


def sanitize_store_display_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = re.sub(r"[（(][^）)]*医疗[^）)]*[）)]", "", value)
    value = re.sub(r"[（(][^）)]*美容[^）)]*[）)]", "", value)
    value = re.sub(r"(医学美容|医疗美容|医美机构|美容医院|医院)", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"[，、]\s*[，、]", "，", value)
    return value.strip(" ，、")


def is_public_store(row: dict[str, Any]) -> bool:
    def int_value(key: str, default: int = 1) -> int:
        try:
            return int(row.get(key, default))
        except (TypeError, ValueError):
            return default

    return int_value("status") == 1 and int_value("shore_show") == 1 and int_value("is_pause", 2) != 1


def platform_store_to_dict(
    row: dict[str, Any],
    *,
    platform_client: PlatformAgentClient | None,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    store_id = str(row.get("id") or "")
    info = {}
    if store_id and platform_client and platform_client.available:
        try:
            info = platform_client.store_info(store_id, request_context=request_context)
        except Exception:
            info = {}
    parking = info.get("parking_info") if isinstance(info, dict) else {}
    if not isinstance(parking, dict):
        parking = {}
    begin = row.get("business_hours_begin") or ""
    end = row.get("business_hours_end") or ""
    return {
        "id": store_id,
        "name": sanitize_store_display_text(info.get("name") or row.get("name") or ""),
        "city": store_text.city_from_row(row, info),
        "address": sanitize_store_display_text(
            info.get("tencent_address") or row.get("tencent_address") or row.get("address") or ""
        ),
        "map_url": info.get("tencent_map_store") or row.get("tencent_map_store") or row.get("map_store") or "",
        "parking_name": sanitize_store_display_text(parking.get("park_name") or ""),
        "parking_address": sanitize_store_display_text(parking.get("park_address") or ""),
        "parking_link": parking.get("park_link") or "",
        "business_hours": f"{begin}-{end}" if begin and end else "",
        "status_code": row.get("status"),
        "shore_show_code": row.get("shore_show"),
        "schedule_status": row.get("schedule_status"),
        "plan_status": row.get("plan_status"),
        "is_pause": row.get("is_pause"),
        "pause_start": row.get("pause_start") or "",
        "pause_end": row.get("pause_end") or "",
        "is_public": is_public_store(row),
        "status_summary": status_summary(row),
    }


def status_summary(row: dict[str, Any]) -> str:
    def int_value(key: str, default: int = -1) -> int:
        try:
            return int(row.get(key, default))
        except (TypeError, ValueError):
            return default

    status = int_value("status")
    shore_show = int_value("shore_show")
    is_pause = int_value("is_pause", 0)
    pause_start = str(row.get("pause_start") or "").strip()
    pause_end = str(row.get("pause_end") or "").strip()
    if is_pause == 1:
        if pause_start or pause_end:
            return f"门店当前有暂停标记，暂停时间：{pause_start or '未写明'}-{pause_end or '未写明'}"
        return "门店当前有暂停标记"
    if status == 0:
        return "门店当前不是正常启用状态"
    if shore_show not in (-1, 1):
        return "门店当前不是常规对外展示状态"
    if status == 1:
        return "门店当前资料状态正常"
    return ""


def store_record_to_dict(store: StoreRecord) -> dict[str, Any]:
    return {
        "id": store.id,
        "name": sanitize_store_display_text(store.name),
        "city": store.city,
        "address": sanitize_store_display_text(store.address),
        "map_url": store.map_url,
        "parking_name": sanitize_store_display_text(store.parking_name),
        "parking_address": sanitize_store_display_text(store.parking_address),
        "parking_link": store.parking_link,
        "business_hours": store.business_hours,
        "status_summary": store.status_summary,
        "is_public": store.is_public,
    }
