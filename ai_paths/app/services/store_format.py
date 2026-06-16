from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from app.services import store_text
from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_catalog import StoreRecord


BLOCKED_STORE_TERMS = ("其他门店", "医美外协", "没有地址", "测试")


def is_public_store(row: dict[str, Any]) -> bool:
    def int_value(key: str, default: int = 1) -> int:
        try:
            return int(row.get(key, default))
        except (TypeError, ValueError):
            return default

    name = str(row.get("name") or "").strip()
    address = str(row.get("address") or row.get("tencent_address") or "").strip()
    if any(term in f"{name} {address}" for term in BLOCKED_STORE_TERMS):
        return False

    return (
        int_value("status") == 1
        and int_value("shore_show") == 1
        and int_value("is_pause", 2) != 1
    )


def platform_store_to_dict(
    row: dict[str, Any],
    *,
    platform_client: PlatformAgentClient | None,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    store_id = str(row.get("id") or "")
    info: dict[str, Any] = {}
    detail_error = ""
    detail_source = "store_index_only"
    if store_id and platform_client and platform_client.available:
        try:
            raw_info = platform_client.store_info(store_id, request_context=request_context)
            if isinstance(raw_info, dict):
                info = raw_info
                detail_source = "platform_agent.store_info"
        except Exception as exc:
            detail_error = f"{type(exc).__name__}: {exc}"

    parking = info.get("parking_info") if isinstance(info, dict) else {}
    if not isinstance(parking, dict):
        parking = {}

    begin = info.get("business_hours_begin") or row.get("business_hours_begin") or ""
    end = info.get("business_hours_end") or row.get("business_hours_end") or ""
    address = info.get("tencent_address") or info.get("address") or row.get("tencent_address") or row.get("address") or ""
    map_url = _normalize_map_url(
        info.get("tencent_map_store")
        or info.get("map_store")
        or row.get("tencent_map_store")
        or row.get("map_store")
        or ""
    )

    output = {
        "id": store_id,
        "name": info.get("name") or row.get("name") or "",
        "city": store_text.city_from_row(row, info),
        "address": address,
        "map_url": map_url,
        "parking_name": parking.get("park_name") or "",
        "parking_address": parking.get("park_address") or "",
        "parking_link": _normalize_map_url(parking.get("park_link") or ""),
        "business_hours": f"{begin}-{end}" if begin and end else "",
        "status_code": row.get("status"),
        "shore_show_code": row.get("shore_show"),
        "schedule_status": row.get("schedule_status"),
        "plan_status": row.get("plan_status"),
        "is_pause": row.get("is_pause"),
        "pause_start": row.get("pause_start") or "",
        "pause_end": row.get("pause_end") or "",
        "is_public": is_public_store({**row, **({"name": info.get("name")} if info.get("name") else {})}),
        "status_summary": status_summary(row),
        "detail_source": detail_source,
        "detail_error": detail_error,
        "has_detail": detail_source == "platform_agent.store_info",
        "address_source": "platform_agent.store_info" if address and detail_source == "platform_agent.store_info" else "store_index",
    }
    if any(term in f"{output['name']} {output['address']}" for term in BLOCKED_STORE_TERMS):
        output["is_public"] = False
    return output


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
            return f"门店当前有暂停标记，暂停时间：{pause_start or '未填写'}-{pause_end or '未填写'}"
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
        "name": store.name,
        "city": store.city,
        "address": store.address,
        "map_url": _normalize_map_url(store.map_url),
        "parking_name": store.parking_name,
        "parking_address": store.parking_address,
        "parking_link": _normalize_map_url(store.parking_link),
        "business_hours": store.business_hours,
        "status_summary": store.status_summary,
        "is_public": store.is_public and not any(term in f"{store.name} {store.address}" for term in BLOCKED_STORE_TERMS),
        "detail_source": "local_store_fallback",
        "detail_error": "",
        "has_detail": False,
        "address_source": "local_store_fallback",
    }


def _normalize_map_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        link_id = (query.get("l") or [""])[0]
        if parsed.netloc and parsed.path and link_id:
            return text
        if link_id:
            return f"https://mmapgwh.map.qq.com/shortlink/short?l={link_id}&tempSource=pcMap"
        if "mmapgwh.map.qq.com/shortlink/short" in text:
            return ""
        return text
    if text.startswith("l="):
        link_id = text.split("=", 1)[1].split("&", 1)[0].strip()
        if link_id:
            return f"https://mmapgwh.map.qq.com/shortlink/short?l={link_id}&tempSource=pcMap"
    if len(text) >= 16 and all(ch.isalnum() or ch in "-_" for ch in text):
        return f"https://mmapgwh.map.qq.com/shortlink/short?l={text}&tempSource=pcMap"
    return text
