from __future__ import annotations

from typing import Any

from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_catalog import StoreRecord, local_store_records
from app.services.store_recommendation import with_location_recommendation
from app.services import store_text


class StoreService:
    """Store lookup with platform-agent API first and a clean local fallback for tests."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client
        self._stores = local_store_records()

    def search(self, query: str, *, customer_context: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
        query = (query or "").strip()
        city = store_text.extract_city(query, self._stores)
        requested_name = store_text.extract_store_name(query, self._stores)
        location_preference = store_text.extract_location_preference(query)
        wants_parking = any(term in query for term in ["停车", "停车场", "车位"])
        wants_route = any(term in query for term in ["导航", "路线", "怎么过去", "地址", "哪里", "位置", "发给我", "发我", "发一下"])
        wants_status = store_text.asks_store_status(query)
        if store_text.needs_city_before_lookup(query, city=city, requested_name=requested_name):
            return {
                "query": query,
                "city": "",
                "requested_store": "",
                "wants_parking": wants_parking,
                "wants_route": wants_route,
                "wants_status": wants_status,
                "location_preference": location_preference,
                "stores": [],
                "missing": ["city"],
                "source": "need_city_before_store_lookup",
            }

        platform_error = ""
        try:
            platform_result = self._search_platform(query, customer_context or {}, limit=limit)
        except Exception as exc:
            platform_result = {}
            platform_error = f"{type(exc).__name__}: {exc}"
        if platform_result:
            platform_result = self._sanitize_platform_result(platform_result, requested_name, city, limit=limit)
            if city and not requested_name and platform_result.get("stores"):
                platform_result = self._merge_local_city_stores(platform_result, city, limit=limit)
            if platform_result.get("stores"):
                return with_location_recommendation(platform_result, location_preference)

        candidates = self._stores
        if requested_name:
            candidates = [store for store in candidates if requested_name == store.name]
        elif city:
            candidates = [store for store in candidates if store.city == city and store.is_public]

        stores = [self._to_dict(store) for store in candidates[:limit]]
        result = {
            "query": query,
            "city": city,
            "requested_store": requested_name,
            "wants_parking": wants_parking,
            "wants_route": wants_route,
            "wants_status": wants_status,
            "location_preference": location_preference,
            "stores": stores,
            "source": "local_store_fallback",
            "platform_error": platform_error,
        }
        return with_location_recommendation(result, location_preference)

    def available_time(self, *, store_id: str, date: str, customer_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._platform_client or not self._platform_client.available:
            return {"source": "platform_agent_unavailable", "slots": {}, "error": "PLATFORM_AGENT_TOKEN is not configured"}
        try:
            data = self._platform_client.available_time(store_id=store_id, date=date, request_context=self._request_context(customer_context or {}))
            return {"source": "platform_agent.available_time", "date": date, "store_id": store_id, "slots": data}
        except Exception as exc:
            return {"source": "platform_agent.available_time", "date": date, "store_id": store_id, "slots": {}, "error": f"{type(exc).__name__}: {exc}"}

    def _search_platform(self, query: str, customer_context: dict[str, Any], *, limit: int) -> dict[str, Any]:
        if not self._platform_client or not self._platform_client.available:
            return {}
        customer = customer_context.get("customer") if isinstance(customer_context, dict) else {}
        if not isinstance(customer, dict):
            customer = {}
        request_context = self._request_context(customer_context)
        customer_id = customer.get("id") or customer_context.get("customer_id") or request_context.get("customer_id")
        add_wechat_id = customer.get("customer_add_wechat_id") or request_context.get("customer_add_wechat_id")
        if customer_id and add_wechat_id:
            rows = self._platform_client.list_stores(
                customer_id=customer_id,
                customer_add_wechat_id=add_wechat_id,
                request_context=request_context,
            )
            source = "platform_agent.store_index"
        else:
            rows = self._platform_client.list_store_options(request_context=request_context)
            source = "platform_agent.option_store"
        requested_name = store_text.extract_store_name(query, self._stores)
        city = store_text.extract_city(query, self._stores) or store_text.city_for_store_name(requested_name, self._stores)
        wants_status = store_text.asks_store_status(query)
        query_matches = store_text.match_rows_by_query_name(rows, query)
        candidates = [row for row in rows if self._is_public_store(row)]
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
            candidates = query_matches if wants_status else [row for row in query_matches if self._is_public_store(row)]
        stores: list[dict[str, Any]] = []
        for row in candidates:
            store = self._platform_store_to_dict(row, request_context=request_context)
            if not (store.get("address") or store.get("map_url") or wants_status):
                continue
            stores.append(store)
            if len(stores) >= limit:
                break
        return {
            "query": query,
            "city": city,
            "requested_store": requested_name,
            "wants_parking": any(term in query for term in ["停车", "停车场", "车位"]),
            "wants_route": any(term in query for term in ["导航", "路线", "怎么过去", "地址", "哪里", "位置", "发给我", "发我", "发一下"]),
            "wants_status": wants_status,
            "stores": stores,
            "source": source,
        }

    @staticmethod
    def _is_public_store(row: dict[str, Any]) -> bool:
        def int_value(key: str, default: int = 1) -> int:
            try:
                return int(row.get(key, default))
            except (TypeError, ValueError):
                return default

        return (
            int_value("status") == 1
            and int_value("shore_show") == 1
            and int_value("is_pause", 2) != 1
        )

    def _platform_store_to_dict(self, row: dict[str, Any], *, request_context: dict[str, Any]) -> dict[str, Any]:
        store_id = str(row.get("id") or "")
        info = {}
        if store_id and self._platform_client and self._platform_client.available:
            try:
                info = self._platform_client.store_info(store_id, request_context=request_context)
            except Exception:
                info = {}
        parking = info.get("parking_info") if isinstance(info, dict) else {}
        if not isinstance(parking, dict):
            parking = {}
        begin = row.get("business_hours_begin") or ""
        end = row.get("business_hours_end") or ""
        status_summary = self._status_summary(row)
        return {
            "id": store_id,
            "name": info.get("name") or row.get("name") or "",
            "city": store_text.city_from_row(row, info),
            "address": info.get("tencent_address") or row.get("tencent_address") or row.get("address") or "",
            "map_url": info.get("tencent_map_store") or row.get("tencent_map_store") or row.get("map_store") or "",
            "parking_name": parking.get("park_name") or "",
            "parking_address": parking.get("park_address") or "",
            "parking_link": parking.get("park_link") or "",
            "business_hours": f"{begin}-{end}" if begin and end else "",
            "status_code": row.get("status"),
            "shore_show_code": row.get("shore_show"),
            "schedule_status": row.get("schedule_status"),
            "plan_status": row.get("plan_status"),
            "is_pause": row.get("is_pause"),
            "pause_start": row.get("pause_start") or "",
            "pause_end": row.get("pause_end") or "",
            "is_public": self._is_public_store(row),
            "status_summary": status_summary,
        }

    def _sanitize_platform_result(
        self,
        result: dict[str, Any],
        requested_name: str,
        city: str,
        *,
        limit: int,
    ) -> dict[str, Any]:
        stores = result.get("stores") if isinstance(result, dict) else []
        if not isinstance(stores, list):
            return result
        target_city = city or store_text.city_for_store_name(requested_name, self._stores)
        aliases = store_text.store_aliases(requested_name) if requested_name else []
        clean_stores: list[dict[str, Any]] = []
        for store in stores:
            if not isinstance(store, dict):
                continue
            if target_city and not store_text.store_matches_city(store, target_city):
                continue
            if requested_name and not store_text.store_matches_requested_name(store, requested_name, aliases):
                continue
            clean_stores.append(self._merge_local_store_details(store))
            if len(clean_stores) >= limit:
                break
        output = dict(result)
        output["stores"] = clean_stores
        if target_city and not output.get("city"):
            output["city"] = target_city
        return output

    def _merge_local_city_stores(self, result: dict[str, Any], city: str, *, limit: int) -> dict[str, Any]:
        stores = [store for store in result.get("stores", []) if isinstance(store, dict)]
        seen = {
            (str(store.get("id") or ""), str(store.get("name") or ""), str(store.get("address") or ""))
            for store in stores
        }
        for record in self._stores:
            if record.city != city or not record.is_public:
                continue
            item = self._to_dict(record)
            key = (item["id"], item["name"], item["address"])
            if key in seen:
                continue
            stores.append(item)
            seen.add(key)
            if len(stores) >= limit:
                break
        output = dict(result)
        output["stores"] = stores[:limit]
        if stores and output.get("source") and "local_fallback" not in str(output.get("source")):
            output["source"] = f"{output.get('source')}+local_store_fallback"
        return output

    def _merge_local_store_details(self, store: dict[str, Any]) -> dict[str, Any]:
        name = str(store.get("name") or "").strip()
        if not name:
            return store
        aliases = store_text.store_aliases(name)
        local = next(
            (
                record
                for record in self._stores
                if record.name == name or any(alias and alias in record.name for alias in aliases)
            ),
            None,
        )
        if not local:
            return store
        merged = dict(store)
        local_data = self._to_dict(local)
        for key in ["map_url", "parking_name", "parking_address", "parking_link", "business_hours", "status_summary", "city"]:
            if not merged.get(key) and local_data.get(key):
                merged[key] = local_data[key]
        if not merged.get("address") and local_data.get("address"):
            merged["address"] = local_data["address"]
        return merged

    @staticmethod
    def _status_summary(row: dict[str, Any]) -> str:
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
            return "门店当前资料状态为正常"
        return ""

    @staticmethod
    def _to_dict(store: StoreRecord) -> dict[str, Any]:
        return {
            "id": store.id,
            "name": store.name,
            "city": store.city,
            "address": store.address,
            "map_url": store.map_url,
            "parking_name": store.parking_name,
            "parking_address": store.parking_address,
            "parking_link": store.parking_link,
            "business_hours": store.business_hours,
            "status_summary": store.status_summary,
            "is_public": store.is_public,
        }

    @staticmethod
    def _request_context(customer_context: dict[str, Any]) -> dict[str, Any]:
        value = customer_context.get("request_context") if isinstance(customer_context, dict) else {}
        return value if isinstance(value, dict) else {}
