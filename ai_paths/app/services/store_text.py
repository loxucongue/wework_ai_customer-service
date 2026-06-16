from __future__ import annotations

from typing import Any

from app.services.store_catalog import StoreRecord
from app.services.store_text_constants import (
    AREA_CITY_MAP,
    CITY_NAMES,
    EXTERNAL_LOCATION_NAMES,
    QUERY_STORE_ALIASES,
    STORE_ALIASES,
)


def needs_city_before_lookup(query: str, *, city: str, requested_name: str) -> bool:
    if city or requested_name or not query:
        return False
    return True


def extract_location_preference(query: str) -> str:
    text = query or ""
    if any(term in text for term in ["南山科技园", "科技园"]):
        return "南山科技园"
    if any(term in text for term in ["高崎机场", "厦门机场", "厦门高崎", "机场附近", "机场周边", "离机场近", "机场"]):
        if "厦门" in text or "高崎" in text:
            return "厦门高崎机场"
        return "机场附近"
    if any(term in text for term in ["蔡塘地铁站", "蔡塘站", "蔡塘"]):
        return "蔡塘地铁站"
    if any(term in text for term in ["火车站附近", "离火车站近", "高铁站附近", "火车站", "高铁站"]):
        return "火车站附近"
    return ""


def extract_area_or_landmark(query: str) -> str:
    text = query or ""
    preference = extract_location_preference(text)
    if preference:
        return preference
    for suffix in ("机场", "火车站", "高铁站", "地铁站", "商圈", "广场", "大厦", "医院", "学校", "科技园", "产业园", "园区"):
        index = text.find(suffix)
        if index >= 0:
            start = max(0, index - 8)
            return text[start : index + len(suffix)].strip()
    for area in sorted(AREA_CITY_MAP, key=len, reverse=True):
        if area in text:
            return area
    return ""


def location_granularity(query: str, stores: list[StoreRecord]) -> str:
    text = (query or "").strip()
    if not text:
        return "unknown"
    if extract_store_name(text, stores):
        return "store_name"
    if extract_area_or_landmark(text):
        return "area_or_landmark"
    if extract_city(text, stores):
        return "city_only"
    return "unknown"


def extract_city(query: str, stores: list[StoreRecord]) -> str:
    text = query or ""
    for city in CITY_NAMES:
        if city in text:
            return city
    for store in stores:
        if store.name and store.name in text:
            return store.city
    for area, city in sorted(AREA_CITY_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if area in text:
            return city
    for location in EXTERNAL_LOCATION_NAMES:
        if location in text:
            return location
    return ""


def extract_store_name(query: str, stores: list[StoreRecord]) -> str:
    text = query or ""
    city = extract_city(text, stores)
    for store in stores:
        if store.name and store.name in text:
            return store.name
    for alias, name in QUERY_STORE_ALIASES.items():
        if alias not in text:
            continue
        alias_city = city_for_store_name(name, stores)
        if city and alias_city and city != alias_city:
            continue
        return name
    return ""


def store_aliases(name: str) -> list[str]:
    return STORE_ALIASES.get(name, [name] if name else [])


def city_for_store_name(name: str, stores: list[StoreRecord]) -> str:
    if not name:
        return ""
    for store in stores:
        if store.name == name:
            return store.city
    for city in CITY_NAMES:
        if city in name:
            return city
    return ""


def row_matches_city(row: dict[str, Any], city: str) -> bool:
    name = str(row.get("name") or "")
    city_field = str(row.get("city") or row.get("city_name") or "")
    address = " ".join(str(row.get(key) or "") for key in ["address", "tencent_address"])
    return text_matches_city(name=name, city_field=city_field, address=address, city=city)


def store_matches_city(store: dict[str, Any], city: str) -> bool:
    return text_matches_city(
        name=str(store.get("name") or ""),
        city_field=str(store.get("city") or ""),
        address=str(store.get("address") or ""),
        city=city,
    )


def city_from_row(row: dict[str, Any], info: dict[str, Any]) -> str:
    city_field = str(row.get("city") or row.get("city_name") or info.get("city") or info.get("city_name") or "")
    if city_field:
        for city in CITY_NAMES:
            if city in city_field:
                return city
    name = str(info.get("name") or row.get("name") or "")
    address = str(info.get("tencent_address") or row.get("tencent_address") or row.get("address") or "")
    for city in CITY_NAMES:
        if text_matches_city(name=name, city_field="", address=address, city=city):
            return city
    return ""


def text_matches_city(*, name: str, city_field: str, address: str, city: str) -> bool:
    if not city:
        return False
    if city_field and city in city_field:
        return True
    if name.startswith(city) or f"{city}店" in name:
        return True
    if f"{city}市" in address or city in address:
        return True
    return False


def store_matches_requested_name(store: dict[str, Any], requested_name: str, aliases: list[str]) -> bool:
    haystack = " ".join(str(store.get(key) or "") for key in ["name", "address", "city"])
    if requested_name and requested_name in haystack:
        return True
    return any(alias and alias in haystack for alias in aliases)


def asks_store_status(query: str) -> bool:
    return any(term in (query or "") for term in ["关门", "开门", "闭店", "停业", "还开", "还营业", "营业吗", "营业时间", "几点开", "几点关"])


def match_rows_by_query_name(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = store_hint_terms(query)
    if not terms:
        return []
    matched: list[dict[str, Any]] = []
    for row in rows:
        haystack = " ".join(str(row.get(key) or "") for key in ["name", "address", "tencent_address"])
        if any(term in haystack for term in terms):
            matched.append(row)
    return matched


def store_hint_terms(query: str) -> list[str]:
    text = query or ""
    generic_terms = [
        "这边",
        "那边",
        "附近",
        "关门",
        "开门",
        "闭店",
        "停业",
        "营业",
        "营业时间",
        "了吗",
        "吗",
        "是不是",
        "还有",
        "还在",
        "还开",
        "门店",
        "店",
        "地址",
        "哪里",
        "位置",
        "导航",
        "停车",
        "现在",
        "目前",
        "今天",
        "明天",
        "几点",
    ]
    for term in generic_terms:
        text = text.replace(term, " ")
    return [term for term in text.split() if len(term) >= 2]
