from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.platform_agent_client import PlatformAgentClient


@dataclass(frozen=True)
class StoreRecord:
    id: str
    name: str
    city: str
    address: str
    map_url: str = ""
    parking_name: str = ""
    parking_address: str = ""
    parking_link: str = ""
    business_hours: str = ""


class StoreService:
    """Store lookup with platform-agent API first and a clean local fallback for tests."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client
        self._stores = [
            StoreRecord(
                id="12",
                name="厦门思明店",
                city="厦门",
                address="厦门市思明区厦禾路1222号国骏大厦",
                map_url="https://mmapgwh.map.qq.com/shortlink/short?l=e526818bff583ca4a28cdf2eb6d0b899&tempSource=pcMap",
                parking_name="国骏大厦地下停车场",
                parking_address="福建省厦门市思明区厦禾路1168号",
                parking_link="https://mmapgwh.map.qq.com/shortlink/short?l=e526818bff583ca4a28cdf2eb6d0b899&tempSource=pcMap",
                business_hours="09:00-19:00",
            ),
            StoreRecord(
                id="385",
                name="厦门二店",
                city="厦门",
                address="厦门市湖里区湖里创新技术园嘉园大厦",
                map_url="https://mmapgwh.map.qq.com/shortlink/short?l=4c5285a342d450869ebc5ca83f86d3fe&tempSource=pcMap",
                parking_name="嘉园大厦停车场",
                parking_address="福建省厦门市湖里区安岭路与钟宅路交叉口西南60米",
                parking_link="https://mmapgwh.map.qq.com/shortlink/short?l=4a1d1e54db052935397ac7d621493b9b&tempSource=pcMap",
                business_hours="10:00-19:00",
            ),
            StoreRecord(
                id="386",
                name="厦门百星",
                city="厦门",
                address="厦门市湖里区枋湖西路189号",
                map_url="https://mmapgwh.map.qq.com/shortlink/short?l=4c5285a342d450869ebc5ca83f86d3fe&tempSource=pcMap",
                parking_name="嘉园大厦停车场",
                parking_address="福建省厦门市湖里区安岭路与钟宅路交叉口西南60米",
                parking_link="https://mmapgwh.map.qq.com/shortlink/short?l=4a1d1e54db052935397ac7d621493b9b&tempSource=pcMap",
                business_hours="10:00-19:00",
            ),
            StoreRecord(
                id="405",
                name="上海浦东二店",
                city="上海",
                address="上海市浦东新区杨高中路2108号FOR天物空间A栋",
                map_url="https://mmapgwh.map.qq.com/shortlink/short?l=14776fa967aad8a1aecf5059d4a415f4&tempSource=pcMap",
                parking_name="男龙总部园御龙宴会中心停车场",
                parking_address="上海市浦东新区南洋泾路578号(芳甸路地铁站3号口步行140米)",
                business_hours="10:00-19:00",
            ),
            StoreRecord(
                id="400",
                name="上海虹口店",
                city="上海",
                address="上海市虹口区花园路88-96号华博科技大厦",
                map_url="https://mmapgwh.map.qq.com/shortlink/short?l=f5334743dbe563dced62681169842a92&tempSource=pcMap",
                parking_name="华博科技大楼停车场",
                parking_address="上海市虹口区花园路88号华博科技大楼",
                business_hours="10:00-21:00",
            ),
            StoreRecord(
                id="322",
                name="上海嘉定店",
                city="上海",
                address="上海市嘉定区海波路366号点石辰金创意工坊",
                map_url="https://mmapgwh.map.qq.com/shortlink/short?l=a9dc89c22c641fb89df77afc09e9d049&tempSource=pcMap",
                parking_name="中星海兰苑2幢停车场",
                parking_address="上海市嘉定区海波路中星海兰苑",
                business_hours="10:00-19:00",
            ),
        ]

    def search(self, query: str, *, customer_context: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
        query = (query or "").strip()
        city = self._extract_city(query)
        requested_name = self._extract_store_name(query)
        wants_parking = any(term in query for term in ["停车", "停车场", "车位"])
        wants_route = any(term in query for term in ["导航", "路线", "怎么过去", "地址", "哪里", "位置"])
        if self._needs_city_before_lookup(query, city=city, requested_name=requested_name):
            return {
                "query": query,
                "city": "",
                "requested_store": "",
                "wants_parking": wants_parking,
                "wants_route": wants_route,
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
        if platform_result and platform_result.get("stores"):
            return platform_result

        candidates = self._stores
        if requested_name:
            candidates = [store for store in candidates if requested_name == store.name]
        elif city:
            candidates = [store for store in candidates if store.city == city]

        stores = [self._to_dict(store) for store in candidates[:limit]]
        return {
            "query": query,
            "city": city,
            "requested_store": requested_name,
            "wants_parking": wants_parking,
            "wants_route": wants_route,
            "stores": stores,
            "source": "local_store_fallback",
            "platform_error": platform_error,
        }

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
        requested_name = self._extract_store_name(query)
        city = self._extract_city(query)
        candidates = [row for row in rows if self._is_public_store(row)]
        if requested_name:
            exact_candidates = [row for row in candidates if str(row.get("name") or "") == requested_name]
            if exact_candidates:
                candidates = exact_candidates
            else:
                aliases = self._store_aliases(requested_name)
                candidates = [row for row in candidates if any(alias in str(row.get("name") or "") for alias in aliases)]
        elif city:
            candidates = [row for row in candidates if city in str(row.get("name") or "") or city in str(row.get("address") or "")]
        stores: list[dict[str, Any]] = []
        for row in candidates:
            store = self._platform_store_to_dict(row, request_context=request_context)
            if not (store.get("address") or store.get("map_url")):
                continue
            stores.append(store)
            if len(stores) >= limit:
                break
        return {
            "query": query,
            "city": city,
            "requested_store": requested_name,
            "wants_parking": any(term in query for term in ["停车", "停车场", "车位"]),
            "wants_route": any(term in query for term in ["导航", "路线", "怎么过去", "地址", "哪里", "位置"]),
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
        return {
            "id": store_id,
            "name": info.get("name") or row.get("name") or "",
            "city": self._extract_city(str(row.get("name") or row.get("address") or info.get("tencent_address") or "")),
            "address": info.get("tencent_address") or row.get("tencent_address") or row.get("address") or "",
            "map_url": info.get("tencent_map_store") or row.get("tencent_map_store") or row.get("map_store") or "",
            "parking_name": parking.get("park_name") or "",
            "parking_address": parking.get("park_address") or "",
            "parking_link": parking.get("park_link") or "",
            "business_hours": f"{begin}-{end}" if begin and end else "",
        }

    def _needs_city_before_lookup(self, query: str, *, city: str, requested_name: str) -> bool:
        if city or requested_name:
            return False
        if not query:
            return False
        generic_terms = ["门店", "店", "地址", "哪里", "附近", "停车", "导航", "位置", "怎么过去", "哪家"]
        return any(term in query for term in generic_terms)

    def _extract_city(self, query: str) -> str:
        for city in ["厦门", "上海", "重庆", "杭州", "广州", "深圳", "南京", "成都", "武汉", "长沙", "福州", "泉州"]:
            if city in query:
                return city
        for store in self._stores:
            if store.name in query:
                return store.city
        for area, city in {
            "虹口": "上海",
            "浦东": "上海",
            "嘉定": "上海",
            "思明": "厦门",
            "湖里": "厦门",
            "渝北": "重庆",
            "南岸": "重庆",
            "渝中": "重庆",
            "大坪": "重庆",
        }.items():
            if area in query:
                return city
        return ""

    def _extract_store_name(self, query: str) -> str:
        for store in self._stores:
            if store.name in query:
                return store.name
        aliases = {
            "思明": "厦门思明店",
            "厦门二店": "厦门二店",
            "二店": "厦门二店",
            "百星": "厦门百星",
            "浦东": "上海浦东二店",
            "上海二店": "上海浦东二店",
            "虹口": "上海虹口店",
            "嘉定": "上海嘉定店",
            "渝北": "重庆渝北店",
            "南岸": "重庆南岸店",
            "渝中": "重庆渝中店",
            "大坪": "重庆渝中店",
        }
        for alias, name in aliases.items():
            if alias in query:
                return name
        return ""

    @staticmethod
    def _store_aliases(name: str) -> list[str]:
        aliases = {
            "上海浦东二店": ["上海浦东二店", "浦东二店", "浦东"],
            "上海虹口店": ["上海虹口店", "虹口"],
            "上海嘉定店": ["上海嘉定店", "嘉定"],
            "厦门思明店": ["厦门思明店", "思明"],
            "厦门二店": ["厦门二店", "二店", "湖里"],
            "厦门百星": ["厦门百星", "百星"],
            "重庆渝北店": ["重庆渝北店", "渝北"],
            "重庆南岸店": ["重庆南岸店", "南岸"],
            "重庆渝中店": ["重庆渝中店", "渝中", "大坪"],
        }
        return aliases.get(name, [name])

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
        }

    @staticmethod
    def _request_context(customer_context: dict[str, Any]) -> dict[str, Any]:
        value = customer_context.get("request_context") if isinstance(customer_context, dict) else {}
        return value if isinstance(value, dict) else {}
