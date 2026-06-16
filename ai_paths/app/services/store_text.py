from __future__ import annotations

import re
from typing import Any, Iterable

from app.services.store_text_constants import (
    AREA_CITY_MAP,
    CITY_NAMES,
    EXTERNAL_LOCATION_NAMES,
    QUERY_STORE_ALIASES,
    STORE_ALIASES,
)


STORE_ROUTE_TERMS = (
    "地址",
    "位置",
    "导航",
    "路线",
    "怎么去",
    "怎么过去",
    "发我",
    "发给我",
    "地铁",
    "停车",
    "停车场",
    "停车位",
)

STORE_STATUS_TERMS = (
    "营业",
    "营业时间",
    "几点开门",
    "几点关门",
    "今天开吗",
    "还开着吗",
    "还在吗",
    "搬走",
    "关门",
    "停业",
)

LOCATION_PREFERENCE_TERMS = (
    "最近",
    "近一点",
    "离我近",
    "方便一点",
    "近不近",
    "就近",
)

LANDMARK_SUFFIXES = (
    "机场",
    "高铁站",
    "高铁",
    "火车站",
    "地铁站",
    "地铁口",
    "科技园",
    "软件园",
    "产业园",
    "商圈",
    "广场",
    "大厦",
    "中心",
    "城",
    "口岸",
    "码头",
    "大学城",
    "会展中心",
    "万象城",
    "万达",
)

SPECIAL_LANDMARKS = tuple(
    sorted(
        set(AREA_CITY_MAP.keys())
        | {
            "高崎机场",
            "高崎国际机场",
            "厦门高崎机场",
            "厦门高崎国际机场",
            "南山科技园",
            "深圳湾口岸",
            "宝安机场",
            "虹桥机场",
            "浦东机场",
            "西安高新",
        },
        key=len,
        reverse=True,
    )
)


def city_from_row(row: dict[str, Any], info: dict[str, Any] | None = None) -> str:
    sources = [
        _text(row.get("city")),
        _text((info or {}).get("city")),
        _text((info or {}).get("district")),
        _text((info or {}).get("region")),
        _text(row.get("address")),
        _text(row.get("tencent_address")),
        _text((info or {}).get("address")),
        _text((info or {}).get("tencent_address")),
        _text(row.get("name")),
        _text((info or {}).get("name")),
    ]
    return _infer_city_from_texts(sources)


def extract_city(query: str, store_refs: Iterable[Any] | None = None) -> str:
    text = _text(query)
    direct = _infer_city_from_texts([text])
    if direct:
        return direct
    requested_name = extract_store_name(text, store_refs)
    if requested_name:
        city = city_for_store_name(requested_name, store_refs)
        if city:
            return city
    return ""


def extract_store_name(query: str, store_refs: Iterable[Any] | None = None) -> str:
    text = _text(query)
    if not text:
        return ""

    for alias, canonical in QUERY_STORE_ALIASES.items():
        if alias and alias in text:
            return canonical

    for ref in store_refs or []:
        name = _store_name(ref)
        if not name:
            continue
        aliases = store_aliases(name)
        if any(alias and alias in text for alias in aliases):
            return name
    return ""


def city_for_store_name(name: str, store_refs: Iterable[Any] | None = None) -> str:
    store_name = _text(name)
    if not store_name:
        return ""
    for ref in store_refs or []:
        if _store_name(ref) != store_name:
            continue
        city = _store_city(ref)
        if city:
            return city
    return _infer_city_from_texts([store_name])


def location_granularity(query: str, store_refs: Iterable[Any] | None = None) -> str:
    text = _text(query)
    if extract_store_name(text, store_refs):
        return "store_name"
    city = extract_city(text, store_refs)
    landmark = extract_area_or_landmark(text)
    if city and landmark:
        return "area_or_landmark"
    if city:
        return "city_only"
    if landmark:
        return "area_or_landmark"
    return "unknown"


def extract_location_preference(query: str) -> str:
    text = _text(query)
    if any(term in text for term in LOCATION_PREFERENCE_TERMS):
        return "nearest"
    return ""


def extract_area_or_landmark(query: str) -> str:
    text = _text(query)
    if not text:
        return ""

    for landmark in SPECIAL_LANDMARKS:
        if landmark and landmark in text:
            return landmark

    match = re.search(
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}(?:%s))" % "|".join(map(re.escape, LANDMARK_SUFFIXES)),
        text,
    )
    if match:
        return match.group(1)

    for area in sorted(AREA_CITY_MAP.keys(), key=len, reverse=True):
        if area and area in text:
            return area
    return ""


def asks_store_status(query: str) -> bool:
    text = _text(query)
    return any(term in text for term in STORE_STATUS_TERMS)


def needs_city_before_lookup(query: str, *, city: str, requested_name: str) -> bool:
    text = _text(query)
    if requested_name or city:
        return False
    if any(term in text for term in STORE_ROUTE_TERMS + STORE_STATUS_TERMS + LOCATION_PREFERENCE_TERMS):
        return True
    if "门店" in text or "店" in text or "地址" in text:
        return True
    return False


def row_matches_city(row: dict[str, Any], city: str) -> bool:
    return bool(city) and city_from_row(row) == city


def store_matches_city(store: dict[str, Any], city: str) -> bool:
    text = " ".join(
        filter(
            None,
            [
                _text(store.get("city")),
                _text(store.get("name")),
                _text(store.get("address")),
            ],
        )
    )
    inferred = _infer_city_from_texts([text])
    return bool(city) and inferred == city


def store_aliases(name: str) -> list[str]:
    store_name = _text(name)
    if not store_name:
        return []
    aliases = STORE_ALIASES.get(store_name, [])
    values = [store_name, *_normalize_aliases(aliases)]
    normalized_name = _normalize_store_name(store_name)
    if normalized_name and normalized_name not in values:
        values.append(normalized_name)
    return list(dict.fromkeys(filter(None, values)))


def match_rows_by_query_name(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    requested = extract_store_name(query, rows)
    if not requested:
        return []
    aliases = store_aliases(requested)
    return [row for row in rows if _row_matches_requested_name(row, requested, aliases)]


def store_matches_requested_name(store: dict[str, Any], requested_name: str, aliases: list[str]) -> bool:
    return _row_matches_requested_name(store, requested_name, aliases)


def _row_matches_requested_name(ref: Any, requested_name: str, aliases: list[str]) -> bool:
    name = _store_name(ref)
    if not name:
        return False
    if name == requested_name:
        return True
    normalized_name = _normalize_store_name(name)
    requested_normalized = _normalize_store_name(requested_name)
    if normalized_name and normalized_name == requested_normalized:
        return True
    return any(alias and alias in name for alias in aliases)


def _infer_city_from_texts(texts: Iterable[str]) -> str:
    values = [value for value in (_text(item) for item in texts) if value]
    merged = " ".join(values)
    for city in CITY_NAMES:
        if city in merged:
            return city
    for area, city in sorted(AREA_CITY_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if area in merged:
            return city
    for city in EXTERNAL_LOCATION_NAMES:
        if city in merged:
            return city
    return ""


def _normalize_aliases(values: Iterable[str]) -> list[str]:
    aliases: list[str] = []
    for value in values:
        text = _text(value)
        if not text:
            continue
        aliases.append(text)
        normalized = _normalize_store_name(text)
        if normalized and normalized not in aliases:
            aliases.append(normalized)
    return aliases


def _store_name(ref: Any) -> str:
    if isinstance(ref, dict):
        return _text(ref.get("name"))
    return _text(getattr(ref, "name", ""))


def _store_city(ref: Any) -> str:
    if isinstance(ref, dict):
        city = _text(ref.get("city"))
        if city:
            return city
        return _infer_city_from_texts([_text(ref.get("address")), _text(ref.get("name"))])
    city = _text(getattr(ref, "city", ""))
    if city:
        return city
    return _infer_city_from_texts([_text(getattr(ref, "address", "")), _text(getattr(ref, "name", ""))])


def _normalize_store_name(value: str) -> str:
    text = _text(value)
    if not text:
        return ""
    text = re.sub(r"(BEIFACE|贝颜|百星)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(门店|体验店|门诊部|门诊|机构)", "", text)
    return text.strip()


def _text(value: Any) -> str:
    return str(value or "").strip()
