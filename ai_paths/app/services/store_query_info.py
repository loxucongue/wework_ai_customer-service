from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services import store_text


@dataclass(frozen=True)
class StoreQueryInfo:
    query: str
    city: str
    requested_name: str
    area_or_landmark: str
    location_granularity: str
    location_preference: str
    wants_parking: bool
    wants_route: bool
    wants_status: bool


def build_store_query_info(query: str, stores: list[Any] | None = None) -> StoreQueryInfo:
    cleaned_query = str(query or "").strip()
    store_refs = list(stores or [])
    city = store_text.extract_city(cleaned_query, store_refs)
    requested_name = store_text.extract_store_name(cleaned_query, store_refs)
    requested_city = store_text.city_for_store_name(requested_name, store_refs)
    if city and requested_city and city != requested_city:
        requested_name = ""

    wants_parking = any(term in cleaned_query for term in ("停车", "停车场", "车位"))
    wants_route = any(
        term in cleaned_query
        for term in (
            "导航",
            "路线",
            "怎么去",
            "怎么过去",
            "地址",
            "哪里",
            "位置",
            "发给我",
            "发我",
            "地铁",
            "机场",
            "附近",
            "最近",
            "离我近",
        )
    )

    return StoreQueryInfo(
        query=cleaned_query,
        city=city,
        requested_name=requested_name,
        area_or_landmark=store_text.extract_area_or_landmark(cleaned_query),
        location_granularity=store_text.location_granularity(cleaned_query, store_refs),
        location_preference=store_text.extract_location_preference(cleaned_query),
        wants_parking=wants_parking,
        wants_route=wants_route,
        wants_status=store_text.asks_store_status(cleaned_query),
    )
