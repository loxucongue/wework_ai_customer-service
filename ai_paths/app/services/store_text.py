from __future__ import annotations

import re
from typing import Any

from app.services.store_catalog import StoreRecord
from app.services.store_text_constants import AREA_CITY_MAP, CITY_NAMES, QUERY_STORE_ALIASES, STORE_ALIASES


def needs_city_before_lookup(query: str, *, city: str, requested_name: str) -> bool:
    if city or requested_name or not query:
        return False
    generic_terms = ["门店", "店", "地址", "哪里", "附近", "停车", "导航", "位置", "怎么过去", "哪家"]
    return any(term in query for term in generic_terms)


def extract_location_preference(query: str) -> str:
    if any(term in query for term in ["机场附近", "机场周边", "离机场近", "机场近点", "高崎机场", "浦东机场", "虹桥机场", "机场"]):
        return "机场附近"
    if any(term in query for term in ["火车站附近", "离火车站近", "高铁站附近", "车站附近"]):
        return "火车站附近"
    return ""


def extract_city(query: str, stores: list[StoreRecord]) -> str:
    for city in CITY_NAMES:
        if city in query:
            return city
    for hint, city in {
        "浦东机场": "上海",
        "虹桥机场": "上海",
        "高崎机场": "厦门",
        "厦门机场": "厦门",
        "浦东": "上海",
        "虹桥": "上海",
        "枋湖": "厦门",
        "湖里": "厦门",
        "中贸": "西安",
        "小寨": "西安",
        "未央": "西安",
        "碑林": "西安",
    }.items():
        if hint in query:
            return city
    for store in stores:
        if store.name and store.name in query:
            return store.city
    for area, city in AREA_CITY_MAP.items():
        if area in query:
            return city
    return ""


def extract_store_name(query: str, stores: list[StoreRecord]) -> str:
    city = extract_city(query, stores)
    for store in stores:
        if store.name and store.name in query:
            return store.name
    if "百星" in query and city:
        return f"{city}百星"
    for alias, name in QUERY_STORE_ALIASES.items():
        if alias in query:
            return name
    generic = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,16}(?:门店|店))", query or "")
    if generic:
        matched = generic.group(1).strip()
        if _is_generic_store_phrase(matched, city):
            return ""
        if not any(term in matched for term in ["来店", "到店", "店吗", "哪家", "哪个", "不知道", "不确定"]):
            return matched
    return ""


def _is_generic_store_phrase(matched: str, city: str) -> bool:
    text = str(matched or "").strip()
    if not text:
        return True
    generic_phrases = {
        "你们门店",
        "你家门店",
        "你们店",
        "你家店",
        "附近门店",
        "附近店",
        "本地门店",
        "本地店",
    }
    if text in generic_phrases:
        return True
    if city and text in {f"{city}门店", f"{city}店"}:
        return True
    if any(term in text for term in ["附近门店", "附近店", "机场附近", "火车站附近", "高铁站附近"]):
        return True
    if text.endswith(("门店", "店")) and any(term in text for term in ["机场", "火车站", "高铁站", "车站", "附近"]):
        return True
    return False


def store_aliases(name: str) -> list[str]:
    return STORE_ALIASES.get(name, [name])


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
    if f"{city}市" in address:
        return True
    if city in {"北京", "上海", "天津", "重庆"} and city in address:
        return True
    return False


def store_matches_requested_name(store: dict[str, Any], requested_name: str, aliases: list[str]) -> bool:
    haystack = " ".join(str(store.get(key) or "") for key in ["name", "address", "city"])
    if requested_name and requested_name in haystack:
        return True
    return any(alias and alias in haystack for alias in aliases)


def asks_store_status(query: str) -> bool:
    return any(term in query for term in ["关门", "开门", "闭店", "停业", "还开", "还营业", "营业吗", "营业时间", "几点开", "几点关"])


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
        "发我",
        "发给我",
    ]
    for term in generic_terms:
        text = text.replace(term, " ")
    return [term for term in text.split() if len(term) >= 2]
