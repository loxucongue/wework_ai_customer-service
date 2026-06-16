from __future__ import annotations

from typing import Any

from app.services import store_format, store_text
from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_platform_context import request_context_from_customer_context, store_platform_context
from app.services.store_query_info import StoreQueryInfo, build_store_query_info


class StoreService:
    """Store lookup backed only by real platform APIs."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client

    def search(
        self,
        query: str,
        *,
        customer_context: dict[str, Any] | None = None,
        limit: int = 8,
        planner_distance_origin: str = "",
    ) -> dict[str, Any]:
        query_info = build_store_query_info(query, [])
        if store_text.needs_city_before_lookup(
            query_info.query,
            city=query_info.city,
            requested_name=query_info.requested_name,
        ):
            return {
                "query": query_info.query,
                "city": "",
                "requested_store": "",
                "wants_parking": query_info.wants_parking,
                "wants_route": query_info.wants_route,
                "wants_status": query_info.wants_status,
                "location_preference": query_info.location_preference,
                "stores": [],
                "missing": ["city"],
                "source": "need_city_before_store_lookup",
                "data_authority": "none",
                "store_data_authority": "none",
            }

        if not self._platform_client or not self._platform_client.available:
            platform_result = _store_lookup_unavailable(query_info, "platform_agent_unavailable")
        else:
            try:
                platform_result = self._search_platform(query_info.query, customer_context or {}, limit=limit)
            except Exception as exc:
                platform_result = _store_lookup_unavailable(query_info, f"{type(exc).__name__}: {exc}")

        return _with_planner_distance_origin(
            _apply_location_gate(
                platform_result,
                query_info=query_info,
            ),
            planner_distance_origin=planner_distance_origin,
        )

    def available_time(self, *, store_id: str, date: str, customer_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._platform_client or not self._platform_client.available:
            return {"source": "platform_agent_unavailable", "slots": {}, "error": "PLATFORM_AGENT_TOKEN is not configured"}
        try:
            data = self._platform_client.available_time(
                store_id=store_id,
                date=date,
                request_context=request_context_from_customer_context(customer_context or {}),
            )
            return {"source": "platform_agent.available_time", "date": date, "store_id": store_id, "slots": data}
        except Exception as exc:
            return {"source": "platform_agent.available_time", "date": date, "store_id": store_id, "slots": {}, "error": f"{type(exc).__name__}: {exc}"}

    def _search_platform(self, query: str, customer_context: dict[str, Any], *, limit: int) -> dict[str, Any]:
        assert self._platform_client is not None

        base_query_info = build_store_query_info(query, [])
        platform_context = store_platform_context(customer_context)
        missing_params: list[str] = []
        if not platform_context.customer_id:
            missing_params.append("customer_id")
        if not platform_context.customer_add_wechat_id:
            missing_params.append("customer_add_wechat_id")
        if missing_params:
            return {
                "query": query,
                "city": base_query_info.city,
                "requested_store": base_query_info.requested_name,
                "wants_parking": base_query_info.wants_parking,
                "wants_route": base_query_info.wants_route,
                "wants_status": base_query_info.wants_status,
                "area_or_landmark": base_query_info.area_or_landmark,
                "location_granularity": base_query_info.location_granularity,
                "stores": [],
                "missing": missing_params,
                "source": "platform_agent.store_index_missing_params",
                "data_authority": "none",
                "store_data_authority": "none",
                "platform_error": "store/index requires customer_id and customer_add_wechat_id",
                "needs_handoff": True,
                "unsupported_claims": ["无法获取真实门店数据，不能提供门店名称、地址或距离"],
            }

        rows = self._platform_client.list_stores(
            customer_id=platform_context.customer_id,
            customer_add_wechat_id=platform_context.customer_add_wechat_id,
            request_context=platform_context.request_context,
        )
        query_info = build_store_query_info(query, rows)
        requested_name = query_info.requested_name
        city = query_info.city or store_text.city_for_store_name(requested_name, rows)
        wants_status = query_info.wants_status
        query_matches = store_text.match_rows_by_query_name(rows, query)
        candidates = [row for row in rows if store_format.is_public_store(row)]

        if requested_name:
            base_rows = rows if wants_status else candidates
            exact_candidates = [row for row in base_rows if str(row.get("name") or "").strip() == requested_name]
            if exact_candidates:
                candidates = exact_candidates
            else:
                aliases = store_text.store_aliases(requested_name)
                candidates = [row for row in base_rows if store_text.store_matches_requested_name(row, requested_name, aliases)]
            if city:
                candidates = [row for row in candidates if store_text.row_matches_city(row, city)]
        elif city:
            candidates = [row for row in candidates if store_text.row_matches_city(row, city)]
        elif query_matches:
            candidates = query_matches if wants_status else [row for row in query_matches if store_format.is_public_store(row)]

        if query_info.area_or_landmark and candidates:
            candidates = sorted(
                candidates,
                key=lambda row: 0 if _row_mentions_location(row, query_info.area_or_landmark) else 1,
            )

        stores: list[dict[str, Any]] = []
        for row in candidates:
            store = store_format.platform_store_to_dict(
                row,
                platform_client=self._platform_client,
                request_context=platform_context.request_context,
            )
            if not (store.get("address") or store.get("map_url") or wants_status):
                continue
            stores.append(store)
            if len(stores) >= limit:
                break

        source = "platform_agent.store_index" if stores else "platform_agent.store_index_no_match"
        return {
            "query": query,
            "city": city,
            "requested_store": requested_name,
            "wants_parking": query_info.wants_parking,
            "wants_route": query_info.wants_route,
            "wants_status": wants_status,
            "location_preference": query_info.location_preference,
            "area_or_landmark": query_info.area_or_landmark,
            "location_granularity": query_info.location_granularity,
            "stores": stores,
            "source": source,
            "data_authority": "platform",
            "store_data_authority": "platform",
            "platform_customer_id": str(platform_context.customer_id or ""),
            "customer_add_wechat_id": str(platform_context.customer_add_wechat_id or ""),
        }


def _apply_location_gate(result: dict[str, Any], *, query_info: StoreQueryInfo) -> dict[str, Any]:
    output = dict(result or {})
    output["area_or_landmark"] = query_info.area_or_landmark
    output["location_granularity"] = query_info.location_granularity
    stores = output.get("stores") if isinstance(output.get("stores"), list) else []
    output["city_store_count"] = len(stores)
    output["has_city_store_candidates"] = bool(stores)
    if query_info.location_granularity != "city_only":
        if query_info.location_granularity in {"area_or_landmark", "store_name"} and stores:
            output["distance_lookup_required"] = True
        return output

    if not stores:
        output["needs_area_or_landmark"] = False
        return output

    gated = dict(output)
    gated["stores"] = []
    gated.pop("recommended_store", None)
    gated.pop("recommendation_reason", None)
    gated["missing"] = list(dict.fromkeys([*(gated.get("missing") or []), "area_or_landmark"]))
    gated["needs_area_or_landmark"] = True
    source = str(gated.get("source") or "").strip()
    gated["source"] = f"{source}+city_only_gate" if source else "city_only_gate"
    return gated


def _with_planner_distance_origin(result: dict[str, Any], *, planner_distance_origin: str) -> dict[str, Any]:
    output = dict(result or {})
    planned_origin = str(planner_distance_origin or "").strip()
    if planned_origin:
        output["planned_distance_origin"] = planned_origin
    if not output.get("distance_lookup_required"):
        return output
    origin = _qualified_distance_origin(
        planned_origin or str(output.get("area_or_landmark") or "").strip(),
        city=str(output.get("city") or "").strip(),
    )
    if origin:
        output["distance_origin"] = origin
    return output


def _qualified_distance_origin(origin: str, *, city: str) -> str:
    value = str(origin or "").strip()
    if not value:
        return ""
    if not city or city in value:
        return value
    return f"{city}{value}"


def _row_mentions_location(row: dict[str, Any], location: str) -> bool:
    needle = str(location or "").strip()
    if not needle:
        return False
    haystack = " ".join(
        filter(
            None,
            [
                str(row.get("name") or "").strip(),
                str(row.get("address") or "").strip(),
                str(row.get("tencent_address") or "").strip(),
            ],
        )
    )
    return needle in haystack


def _store_lookup_unavailable(query_info: StoreQueryInfo, platform_error: str) -> dict[str, Any]:
    return {
        "query": query_info.query,
        "city": query_info.city,
        "requested_store": query_info.requested_name,
        "wants_parking": query_info.wants_parking,
        "wants_route": query_info.wants_route,
        "wants_status": query_info.wants_status,
        "location_preference": query_info.location_preference,
        "area_or_landmark": query_info.area_or_landmark,
        "location_granularity": query_info.location_granularity,
        "stores": [],
        "source": "store_lookup_unavailable",
        "data_authority": "none",
        "store_data_authority": "none",
        "platform_error": platform_error,
        "needs_handoff": True,
        "unsupported_claims": ["无法获取真实门店数据，不能提供门店名称、地址或距离"],
    }
