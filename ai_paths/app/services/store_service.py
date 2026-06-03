from __future__ import annotations

from typing import Any

from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_catalog import local_store_records
from app.services import store_format
from app.services.store_query_info import build_store_query_info
from app.services.store_recommendation import with_location_recommendation
from app.services.store_result_merge import merge_local_city_stores, sanitize_platform_result
from app.services.store_platform_context import request_context_from_customer_context, store_platform_context
from app.services import store_text


class StoreService:
    """Store lookup with platform-agent API first and a clean local fallback for tests."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client
        self._stores = local_store_records()

    def search(self, query: str, *, customer_context: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
        query_info = build_store_query_info(query, self._stores)
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
            }

        platform_error = ""
        try:
            platform_result = self._search_platform(query_info.query, customer_context or {}, limit=limit)
        except Exception as exc:
            platform_result = {}
            platform_error = f"{type(exc).__name__}: {exc}"
        if platform_result:
            platform_result = sanitize_platform_result(
                platform_result,
                requested_name=query_info.requested_name,
                city=query_info.city,
                stores_catalog=self._stores,
                limit=limit,
            )
            if query_info.city and not query_info.requested_name and platform_result.get("stores"):
                platform_result = merge_local_city_stores(
                    platform_result,
                    city=query_info.city,
                    stores_catalog=self._stores,
                    limit=limit,
                )
            if platform_result.get("stores"):
                return with_location_recommendation(platform_result, query_info.location_preference)

        candidates = self._stores
        if query_info.requested_name:
            candidates = [store for store in candidates if query_info.requested_name == store.name]
        elif query_info.city:
            candidates = [store for store in candidates if store.city == query_info.city and store.is_public]

        stores = [store_format.store_record_to_dict(store) for store in candidates[:limit]]
        result = {
            "query": query_info.query,
            "city": query_info.city,
            "requested_store": query_info.requested_name,
            "wants_parking": query_info.wants_parking,
            "wants_route": query_info.wants_route,
            "wants_status": query_info.wants_status,
            "location_preference": query_info.location_preference,
            "stores": stores,
            "source": "local_store_fallback",
            "platform_error": platform_error,
        }
        return with_location_recommendation(result, query_info.location_preference)

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
        if not self._platform_client or not self._platform_client.available:
            return {}
        platform_context = store_platform_context(customer_context)
        if platform_context.customer_id and platform_context.customer_add_wechat_id:
            rows = self._platform_client.list_stores(
                customer_id=platform_context.customer_id,
                customer_add_wechat_id=platform_context.customer_add_wechat_id,
                request_context=platform_context.request_context,
            )
            source = "platform_agent.store_index"
        else:
            rows = self._platform_client.list_store_options(request_context=platform_context.request_context)
            source = "platform_agent.option_store"
        query_info = build_store_query_info(query, self._stores)
        requested_name = query_info.requested_name
        city = query_info.city or store_text.city_for_store_name(requested_name, self._stores)
        wants_status = query_info.wants_status
        query_matches = store_text.match_rows_by_query_name(rows, query)
        candidates = [row for row in rows if store_format.is_public_store(row)]
        if requested_name:
            base_rows = rows if wants_status else candidates
            exact_candidates = [row for row in base_rows if str(row.get("name") or "") == requested_name]
            if exact_candidates:
                candidates = exact_candidates
            else:
                aliases = store_text.store_aliases(requested_name)
                candidates = [row for row in base_rows if any(alias in str(row.get("name") or "") for alias in aliases)]
            if city:
                candidates = [row for row in candidates if store_text.row_matches_city(row, city)]
        elif city:
            candidates = [row for row in candidates if store_text.row_matches_city(row, city)]
        elif query_matches:
            candidates = query_matches if wants_status else [row for row in query_matches if store_format.is_public_store(row)]
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
        return {
            "query": query,
            "city": city,
            "requested_store": requested_name,
            "wants_parking": query_info.wants_parking,
            "wants_route": query_info.wants_route,
            "wants_status": wants_status,
            "stores": stores,
            "source": source,
        }
