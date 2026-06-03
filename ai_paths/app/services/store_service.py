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
    status_summary: str = "本地门店资料未包含暂停状态"
    is_public: bool = True


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
            StoreRecord(
                id="fallback-xian-xiaozhai",
                name="西安小寨店",
                city="西安",
                address="陕西省西安市雁塔区小寨西路232号置地时代MOMOPARK7号楼",
                parking_name="MOMOPARK艺术购物中心地下停车场",
                business_hours="10:00-19:00",
                status_summary="当前资料未显示暂停营业状态",
            ),
            StoreRecord(
                id="fallback-xian-weiyang",
                name="西安未央店",
                city="西安",
                address="西安市未央区凤城二路经发大厦A座",
                parking_name="西安经发大厦A座地下停车场",
                business_hours="10:00-19:00",
                status_summary="当前资料未显示暂停营业状态",
            ),
            StoreRecord(
                id="fallback-xian-beilin",
                name="西安碑林店",
                city="西安",
                address="西安市碑林区体育馆东路宏信国际花园2号楼",
                parking_name="香港宏信国际花园停车场",
                business_hours="10:00-19:00",
                status_summary="当前资料未显示暂停营业状态",
            ),
            StoreRecord(
                id="20",
                name="西安中贸店",
                city="西安",
                address="",
                business_hours="10:00-20:00",
                status_summary="当前资料不是正常对外接待状态，建议以门店最新确认为准",
                is_public=False,
            ),
        ]

    def search(self, query: str, *, customer_context: dict[str, Any] | None = None, limit: int = 3) -> dict[str, Any]:
        query = (query or "").strip()
        city = self._extract_city(query)
        requested_name = self._extract_store_name(query)
        location_preference = self._extract_location_preference(query)
        wants_parking = any(term in query for term in ["停车", "停车场", "车位"])
        wants_route = any(term in query for term in ["导航", "路线", "怎么过去", "地址", "哪里", "位置", "发给我", "发我", "发一下"])
        wants_status = self._asks_store_status(query)
        if self._needs_city_before_lookup(query, city=city, requested_name=requested_name):
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
                return self._with_location_recommendation(platform_result, location_preference)

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
        return self._with_location_recommendation(result, location_preference)

    def _with_location_recommendation(self, result: dict[str, Any], location_preference: str) -> dict[str, Any]:
        if not location_preference:
            return result
        stores = result.get("stores") if isinstance(result, dict) else []
        if not isinstance(stores, list) or not stores:
            return result
        city = str(result.get("city") or "").strip()
        if location_preference == "机场附近" and city and city != "厦门":
            return result
        ranked = sorted(
            [store for store in stores if isinstance(store, dict)],
            key=lambda store: self._location_preference_rank(store, location_preference),
        )
        if not ranked:
            return result
        best_rank = self._location_preference_rank(ranked[0], location_preference)
        if best_rank >= 50:
            return result
        recommended = ranked[0]
        output = dict(result)
        output["stores"] = ranked
        output["location_preference"] = location_preference
        output["recommended_store"] = recommended
        output["recommendation_reason"] = self._recommendation_reason(recommended, location_preference)
        return output

    @staticmethod
    def _location_preference_rank(store: dict[str, Any], location_preference: str) -> int:
        text = " ".join(str(store.get(key) or "") for key in ["name", "city", "address", "parking_name", "parking_address"])
        if location_preference == "机场附近":
            if any(term in text for term in ["机场", "高崎"]):
                return 0
            if any(term in text for term in ["百星", "枋湖"]):
                return 1
            if any(term in text for term in ["湖里", "安岭", "钟宅"]):
                return 2
            if any(term in text for term in ["思明", "厦禾", "国骏"]):
                return 8
        return 99

    @staticmethod
    def _recommendation_reason(store: dict[str, Any], location_preference: str) -> str:
        name = str(store.get("name") or "这家门店").strip()
        address = str(store.get("address") or "").strip()
        if location_preference == "机场附近":
            if any(term in f"{name} {address}" for term in ["湖里", "枋湖", "百星", "安岭", "钟宅"]):
                return f"客户偏好机场附近，按当前门店地址看，{name}在湖里区方向，比思明区门店更贴近机场区域。"
            return f"客户偏好机场附近，按当前门店地址看，可优先对比{name}。"
        return f"按客户位置偏好，可优先对比{name}。"

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
        city = self._extract_city(query) or self._city_for_store_name(requested_name)
        wants_status = self._asks_store_status(query)
        query_matches = self._match_rows_by_query_name(rows, query)
        candidates = [row for row in rows if self._is_public_store(row)]
        if requested_name:
            base_rows = rows if wants_status else candidates
            exact_candidates = [row for row in base_rows if str(row.get("name") or "") == requested_name]
            if exact_candidates:
                candidates = exact_candidates
            else:
                aliases = self._store_aliases(requested_name)
                candidates = [row for row in base_rows if any(alias in str(row.get("name") or "") for alias in aliases)]
            if city:
                candidates = [row for row in candidates if self._row_matches_city(row, city)]
        elif city:
            candidates = [row for row in candidates if self._row_matches_city(row, city)]
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
            "city": self._city_from_row(row, info),
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

    def _needs_city_before_lookup(self, query: str, *, city: str, requested_name: str) -> bool:
        if city or requested_name:
            return False
        if not query:
            return False
        generic_terms = ["门店", "店", "地址", "哪里", "附近", "停车", "导航", "位置", "怎么过去", "哪家"]
        return any(term in query for term in generic_terms)

    @staticmethod
    def _extract_location_preference(query: str) -> str:
        if any(term in query for term in ["机场附近", "机场周边", "离机场近", "机场近", "高崎机场", "厦门机场", "机场"]):
            return "机场附近"
        if any(term in query for term in ["火车站附近", "离火车站近", "高铁站附近"]):
            return "火车站附近"
        return ""

    def _extract_city(self, query: str) -> str:
        for city in ["厦门", "上海", "重庆", "杭州", "广州", "深圳", "南京", "成都", "武汉", "长沙", "福州", "泉州", "西安"]:
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
            "中贸": "西安",
            "小寨": "西安",
            "未央": "西安",
            "碑林": "西安",
        }.items():
            if area in query:
                return city
        return ""

    def _extract_store_name(self, query: str) -> str:
        city = self._extract_city(query)
        for store in self._stores:
            if store.name in query:
                return store.name
        if "百星" in query and city:
            return f"{city}百星"
        aliases = {
            "中贸": "西安中贸店",
            "小寨": "西安小寨店",
            "未央": "西安未央店",
            "碑林": "西安碑林店",
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
            "西安中贸店": ["西安中贸店", "中贸"],
            "西安小寨店": ["西安小寨店", "小寨"],
            "西安未央店": ["西安未央店", "未央"],
            "西安碑林店": ["西安碑林店", "碑林"],
        }
        if name.endswith("百星"):
            return [name, "百星"]
        return aliases.get(name, [name])

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
        target_city = city or self._city_for_store_name(requested_name)
        aliases = self._store_aliases(requested_name) if requested_name else []
        clean_stores: list[dict[str, Any]] = []
        for store in stores:
            if not isinstance(store, dict):
                continue
            if target_city and not self._store_matches_city(store, target_city):
                continue
            if requested_name and not self._store_matches_requested_name(store, requested_name, aliases):
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
        aliases = self._store_aliases(name)
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

    def _city_for_store_name(self, name: str) -> str:
        if not name:
            return ""
        for store in self._stores:
            if store.name == name:
                return store.city
        for city in ["厦门", "上海", "重庆", "杭州", "广州", "深圳", "南京", "成都", "武汉", "长沙", "福州", "泉州", "西安"]:
            if city in name:
                return city
        return ""

    def _row_matches_city(self, row: dict[str, Any], city: str) -> bool:
        name = str(row.get("name") or "")
        city_field = str(row.get("city") or row.get("city_name") or "")
        address = " ".join(str(row.get(key) or "") for key in ["address", "tencent_address"])
        return self._text_matches_city(name=name, city_field=city_field, address=address, city=city)

    def _store_matches_city(self, store: dict[str, Any], city: str) -> bool:
        return self._text_matches_city(
            name=str(store.get("name") or ""),
            city_field=str(store.get("city") or ""),
            address=str(store.get("address") or ""),
            city=city,
        )

    def _city_from_row(self, row: dict[str, Any], info: dict[str, Any]) -> str:
        city_field = str(row.get("city") or row.get("city_name") or info.get("city") or info.get("city_name") or "")
        if city_field:
            for city in ["厦门", "上海", "重庆", "杭州", "广州", "深圳", "南京", "成都", "武汉", "长沙", "福州", "泉州", "西安"]:
                if city in city_field:
                    return city
        name = str(info.get("name") or row.get("name") or "")
        address = str(info.get("tencent_address") or row.get("tencent_address") or row.get("address") or "")
        for city in ["厦门", "上海", "重庆", "杭州", "广州", "深圳", "南京", "成都", "武汉", "长沙", "福州", "泉州", "西安"]:
            if self._text_matches_city(name=name, city_field="", address=address, city=city):
                return city
        return ""

    @staticmethod
    def _text_matches_city(*, name: str, city_field: str, address: str, city: str) -> bool:
        if not city:
            return False
        if city_field and city in city_field:
            return True
        if name.startswith(city) or f"{city}店" in name:
            return True
        if f"{city}市" in address:
            return True
        if city in {"北京", "上海", "天津", "重庆"} and f"{city}" in address:
            return True
        return False

    def _store_matches_requested_name(self, store: dict[str, Any], requested_name: str, aliases: list[str]) -> bool:
        haystack = " ".join(str(store.get(key) or "") for key in ["name", "address", "city"])
        if requested_name and requested_name in haystack:
            return True
        return any(alias and alias in haystack for alias in aliases)

    @staticmethod
    def _asks_store_status(query: str) -> bool:
        return any(term in query for term in ["关门", "开门", "闭店", "停业", "还开", "还营业", "营业吗", "营业时间", "几点开", "几点关"])

    def _match_rows_by_query_name(self, rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        terms = self._store_hint_terms(query)
        if not terms:
            return []
        matched: list[dict[str, Any]] = []
        for row in rows:
            haystack = " ".join(str(row.get(key) or "") for key in ["name", "address", "tencent_address"])
            if any(term in haystack for term in terms):
                matched.append(row)
        return matched

    @staticmethod
    def _store_hint_terms(query: str) -> list[str]:
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
