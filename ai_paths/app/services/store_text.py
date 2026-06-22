from __future__ import annotations

import re
from typing import Any, Iterable

from app.services.store_text_constants import CITY_NAMES, EXTERNAL_LOCATION_NAMES


STORE_ROUTE_TERMS = (
    "地址",
    "位置",
    "定位",
    "导航",
    "路线",
    "怎么去",
    "怎么过去",
    "发我",
    "发给我",
    "地图",
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
    "高新区",
    "开发区",
    "经开区",
    "新区",
    "县级市",
    "县城",
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
    "区",
    "县",
)

SPECIAL_LANDMARKS = tuple(
    sorted(
        {
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
            "宁乡县城",
            "岳麓区",
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

    after_city = _area_after_explicit_city(text)
    if after_city:
        return after_city

    match = re.search(
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}(?:%s))" % "|".join(map(re.escape, LANDMARK_SUFFIXES)),
        text,
    )
    if match:
        return _clean_location_phrase(match.group(1))

    return ""


def city_for_area_or_landmark(area_or_landmark: str) -> str:
    _ = area_or_landmark
    return ""


def looks_like_location_fragment(query: str) -> bool:
    value = _clean_location_phrase(query)
    value = re.sub(r"(这边|附近|周边|这块|那里|那边|这附近)$", "", value)
    value = value.strip()
    if not re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9]{2,12}", value):
        return False
    generic_or_business_terms = (
        "你好",
        "您好",
        "在吗",
        "好的",
        "可以",
        "嗯嗯",
        "祛斑",
        "淡斑",
        "斑",
        "痘",
        "价格",
        "收费",
        "多少钱",
        "预约",
        "报名",
        "效果",
        "技术",
        "怎么做",
        "门店",
        "地址",
        "位置",
    )
    return not any(term in value for term in generic_or_business_terms)


def asks_store_status(query: str) -> bool:
    text = _text(query)
    return any(term in text for term in STORE_STATUS_TERMS)


def needs_city_before_lookup(query: str, *, city: str, requested_name: str, area_or_landmark: str = "") -> bool:
    text = _text(query)
    if requested_name or city or area_or_landmark:
        return False
    if any(term in text for term in STORE_ROUTE_TERMS + STORE_STATUS_TERMS + LOCATION_PREFERENCE_TERMS):
        return True
    return "门店" in text or "店" in text or "地址" in text


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
    values = [store_name]
    normalized_name = _normalize_store_name(store_name)
    if normalized_name and normalized_name not in values and normalized_name not in CITY_NAMES and len(normalized_name) >= 3:
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


def _clean_location_phrase(value: str) -> str:
    text = _text(value)
    text = re.sub(r"^(我在|我住在|住在|在|离)", "", text)
    return text.strip()


def _area_after_explicit_city(text: str) -> str:
    value = _text(text)
    if not value:
        return ""
    for city in CITY_NAMES:
        index = value.find(city)
        if index < 0:
            continue
        tail = value[index + len(city) :]
        tail = _clean_location_phrase(tail)
        tail = re.sub(r"^(市|城区|这边|附近|周边|的|有|离)", "", tail)
        tail = re.split(
            r"(?:这边|附近|周边|哪里|在哪|地址|位置|门店|店|有店|有门店|怎么去|导航|路线|最近|近一点|多少钱|收费|价格)",
            tail,
            maxsplit=1,
        )[0]
        tail = re.sub(r"[，,。.!！?？\s]+", "", tail)
        tail = re.sub(r"(这边|附近|周边|这块|那里|那边)$", "", tail)
        if re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9]{2,20}", tail):
            return _clean_location_phrase(tail)
    return ""


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
    # Platform addresses commonly contain the actual city as "XX市".
    # Prefer this before area keywords such as "龙岗/福田", otherwise streets
    # in other cities (e.g. 常德市龙岗路) can be misclassified as Shenzhen.
    for value in values:
        match = re.search(r"([\u4e00-\u9fa5]{2,8})市", value)
        if match:
            return match.group(1)
    for city in EXTERNAL_LOCATION_NAMES:
        if city in merged:
            return city
    return ""


def _store_name(ref: Any) -> str:
    if isinstance(ref, dict):
        return _text(ref.get("name"))
    return _text(getattr(ref, "name", ""))


def _store_city(ref: Any) -> str:
    if isinstance(ref, dict):
        city = _text(ref.get("city"))
        if city:
            return city
        return _infer_city_from_texts([
            _text(ref.get("address")),
            _text(ref.get("tencent_address")),
            _text(ref.get("name")),
        ])
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
    text = str(value or "").strip()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    text = re.sub(r"[，,。.;；:：、/\\|｜\-—_（）()【】\[\]{}<>《》\"'“”‘’!?！？~～]+", "", text)
    return text
