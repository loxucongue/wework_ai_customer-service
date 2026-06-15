from __future__ import annotations

from dataclasses import dataclass

from app.services.store_catalog import StoreRecord
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


def build_store_query_info(query: str, stores: list[StoreRecord]) -> StoreQueryInfo:
    cleaned_query = (query or "").strip()
    city = store_text.extract_city(cleaned_query, stores)
    requested_name = store_text.extract_store_name(cleaned_query, stores)
    requested_city = store_text.city_for_store_name(requested_name, stores)
    if city and requested_city and city != requested_city:
        requested_name = ""
    return StoreQueryInfo(
        query=cleaned_query,
        city=city,
        requested_name=requested_name,
        area_or_landmark=store_text.extract_area_or_landmark(cleaned_query),
        location_granularity=store_text.location_granularity(cleaned_query, stores),
        location_preference=store_text.extract_location_preference(cleaned_query),
        wants_parking=any(term in cleaned_query for term in ["停车", "停车场", "车位"]),
        wants_route=any(
            term in cleaned_query
            for term in [
                "导航",
                "路线",
                "怎么过去",
                "地址",
                "哪里",
                "位置",
                "发给我",
                "发我",
                "发一下",
                "地铁站",
                "机场",
                "附近",
                "近吗",
            ]
        ),
        wants_status=store_text.asks_store_status(cleaned_query),
    )
