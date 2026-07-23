from __future__ import annotations

import asyncio
import json
import time

from app.graph.nodes import action_nodes
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_nodes import _customer_store_lookup
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService


class _FlakyPlatformClient:
    available = True

    def __init__(self) -> None:
        self.fail = False

    def list_stores(self, **_: object) -> list[dict[str, object]]:
        if self.fail:
            raise TimeoutError("store index timeout")
        return [{"id": "101"}]


class _RecoveringCustomerInfoPlatformClient:
    available = True

    def __init__(self) -> None:
        self.info_calls = 0

    def get_customer_info(self, **_: object) -> dict[str, object]:
        self.info_calls += 1
        if self.info_calls == 1:
            raise RuntimeError("temporary customer info error")
        return {"id": "p1", "customer_add_wechat_id": "a1"}

    def list_stores(self, **_: object) -> list[dict[str, object]]:
        return [{"id": "101"}]


class _FakeCoze:
    class settings:
        geocode_workflow_id = ""


class _FakeGeocodeCoze:
    class settings:
        geocode_workflow_id = "geo-workflow"

    def __init__(self, geocodes: dict[str, dict[str, object]]) -> None:
        self.geocodes = geocodes
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        self.calls.append((workflow_id, parameters))
        address = str(parameters.get("address") or "")
        return {"code": 0, "data": [self.geocodes.get(address, {})]}


class _AmbiguousGeocodeCoze:
    class settings:
        geocode_workflow_id = "geo-workflow"

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        assert workflow_id == "geo-workflow"
        assert parameters.get("address") == "人民广场"
        return {
            "code": 0,
            "data": [
                {
                    "province": "上海市",
                    "city": "上海市",
                    "district": "黄浦区",
                    "formatted_address": "上海市黄浦区人民广场",
                    "location": "121.475,31.232",
                },
                {
                    "province": "辽宁省",
                    "city": "大连市",
                    "district": "中山区",
                    "formatted_address": "辽宁省大连市中山区人民广场",
                    "location": "121.614,38.914",
                },
            ],
        }


class _Snapshot:
    def stores_for_scope(self, rows: list[dict[str, object]], **_: object) -> dict[str, object]:
        stores = [
            {
                "store_id": str(row.get("id") or row.get("store_id") or ""),
                "store_name": "Test Store",
                "province": "Test Province",
                "city": "Test City",
                "district": "Test District",
                "store_address": "Test Address",
            }
            for row in rows
        ]
        return {
            "stores": stores,
            "grouped_by_region": {},
            "missing_snapshot_store_ids": [],
            "snapshot_generated_at": "2026-06-25T00:00:00Z",
            "snapshot_store_count": len(stores),
            "snapshot_source": "test",
            "snapshot_refresh_error": "",
        }


def test_store_scope_uses_stale_cache_when_platform_store_index_fails() -> None:
    platform = _FlakyPlatformClient()
    service = CustomerStoreKnowledgeService(platform, _Snapshot())  # type: ignore[arg-type]
    request_context = {
        "corp_id": "corp",
        "customer_id": "input-id",
        "user_id": "u1",
        "wechat": "w1",
    }
    customer_context = {"identity": {"platform_customer_id": "p1", "customer_add_wechat_id": "a1"}}

    first = service.load(request_context=request_context, customer_context=customer_context)
    assert first["store_count"] == 1
    assert first["cache"]["store_scope_status"] == "refreshed"

    key = service._scope_cache_key("p1", "a1", {**request_context, "input_customer_id": "input-id", "platform_customer_id": "p1", "customer_id": "p1", "customer_add_wechat_id": "a1"})
    service._scope_ids_cache[key] = (time.monotonic() - 1, ["101"])
    platform.fail = True

    second = service.load(request_context=request_context, customer_context=customer_context)
    assert second["store_count"] == 1
    assert second["source"] == "platform_agent.store_index_stale_cache+store_snapshot"
    assert second["cache"]["store_scope_status"] == "stale_on_error"
    assert "store index timeout" in second["store_scope_error"]


def test_store_scope_retries_customer_info_business_error() -> None:
    platform = _RecoveringCustomerInfoPlatformClient()
    service = CustomerStoreKnowledgeService(platform, _Snapshot())  # type: ignore[arg-type]

    output = service.load(
        request_context={
            "corp_id": "corp",
            "customer_id": "input-id",
            "external_userid": "external-1",
            "user_id": "u1",
            "wechat": "w1",
        },
        customer_context={},
    )

    assert platform.info_calls == 2
    assert output["customer_id"] == "p1"
    assert output["customer_add_wechat_id"] == "a1"
    assert output["store_count"] == 1


def test_store_lookup_snapshot_fallback_reads_env_path(monkeypatch, tmp_path) -> None:
    snapshot_path = tmp_path / "store_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "stores_by_id": {
                    "301": {
                        "store_id": "301",
                        "store_name": "Test Parent Store",
                        "province": "Test Province",
                        "city": "Test City",
                        "district": "Test District",
                        "store_address": "Test City Test District Road 1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STORE_SNAPSHOT_PATH", str(snapshot_path))
    monkeypatch.setattr(action_nodes, "_STORE_SNAPSHOT_CACHE", None)

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "Test City", "purpose": "existence"},
            {"customer_store_knowledge": {"source": "missing_customer_store_scope", "stores": [], "error": "temporary"}},
            _FakeCoze(),  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "ok"
    assert output["source"] == "store_snapshot_region_fallback"
    assert output["stores"][0]["store_id"] == "301"


def test_store_lookup_cross_city_candidates_trigger_distance_enrichment() -> None:
    result = {
        "status": "ok",
        "geocode": {"city": "Test Prefecture", "district": "Test County", "location": "107.1,25.1"},
        "candidate_stores": [
            {"store_id": "301", "city": "Store City A"},
            {"store_id": "302", "city": "Store City B"},
        ],
    }

    assert action_nodes._lookup_result_needs_distance_enrichment(result)


def test_store_lookup_same_city_candidates_do_not_trigger_distance_enrichment() -> None:
    result = {
        "status": "ok",
        "geocode": {"city": "Test City", "district": "Test District", "location": "107.1,25.1"},
        "candidate_stores": [
            {"store_id": "301", "city": "Test City"},
            {"store_id": "302", "city": "Test City"},
        ],
    }

    assert not action_nodes._lookup_result_needs_distance_enrichment(result)


def test_store_lookup_uses_snapshot_region_fallback_when_scope_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        action_nodes,
        "_STORE_SNAPSHOT_CACHE",
        {
            "stores_by_id": {
                "201": {
                    "store_id": "201",
                    "store_name": "厦门思明店",
                    "province": "福建省",
                    "city": "厦门市",
                    "district": "思明区",
                    "store_address": "厦门市思明区厦禾路1222号国骏大厦",
                },
                "202": {
                    "store_id": "202",
                    "store_name": "厦门百星湖里店",
                    "province": "福建省",
                    "city": "厦门市",
                    "district": "湖里区",
                    "store_address": "福建省厦门市湖里区岐山北二路1000号萤火虫大厦",
                },
                "203": {
                    "store_id": "203",
                    "store_name": "厦门二店（停业中）",
                    "province": "福建省",
                    "city": "厦门市",
                    "district": "湖里区",
                    "store_address": "厦门市湖里区某地址",
                },
            }
        },
    )

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "厦门", "purpose": "existence"},
            {"customer_store_knowledge": {"source": "missing_customer_store_scope", "stores": [], "error": "temporary"}},
            _FakeCoze(),  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "ok"
    assert output["source"] == "store_snapshot_region_fallback"
    assert [item["store_name"] for item in output["stores"]] == ["厦门思明店", "厦门百星湖里店"]


def test_store_lookup_does_not_use_snapshot_region_fallback_for_generic_question(monkeypatch) -> None:
    monkeypatch.setattr(
        action_nodes,
        "_STORE_SNAPSHOT_CACHE",
        {
            "stores_by_id": {
                "201": {
                    "store_id": "201",
                    "store_name": "厦门思明店",
                    "province": "福建省",
                    "city": "厦门市",
                    "district": "思明区",
                    "store_address": "厦门市思明区厦禾路1222号国骏大厦",
                }
            }
        },
    )

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "你们门店在哪里", "purpose": "existence"},
            {"customer_store_knowledge": {"source": "missing_customer_store_scope", "stores": [], "error": "temporary"}},
            _FakeCoze(),  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "no_match"
    assert output["source"] == "store_snapshot_exact_name"
    assert output["missing"] == ["store_scope_unavailable"]


def test_store_lookup_strips_structured_location_label_and_prefers_text_scope() -> None:
    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "门店位置：双流人民广场", "purpose": "detail"},
            {
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "379",
                            "store_name": "成都双流店",
                            "province": "四川省",
                            "city": "成都市",
                            "district": "双流区",
                            "store_address": "成都市蛟龙港双流园区海港广场",
                        },
                        {
                            "store_id": "522",
                            "store_name": "成都双流高新店",
                            "province": "四川省",
                            "city": "成都市",
                            "district": "双流区",
                            "store_address": "成都市天府新区天府大道南段2034号三利广场3栋",
                        },
                        {
                            "store_id": "157",
                            "store_name": "杭州临平店",
                            "province": "浙江省",
                            "city": "杭州市",
                            "district": "临平区",
                            "store_address": "杭州市临平区南苑街道秀浦街",
                        },
                    ]
                }
            },
            _FakeCoze(),  # type: ignore[arg-type]
        )
    )

    assert output["raw_query"] == "门店位置：双流人民广场"
    assert output["query"] == "双流人民广场"
    assert output["status"] == "ok"
    assert [item["store_id"] for item in output["stores"]] == ["379", "522"]


def test_store_lookup_geocodes_structured_poi_message_before_matching_scope() -> None:
    coze = _FakeGeocodeCoze(
        {
            "五缘湾湿地公园-花溪": {
                "province": "福建省",
                "city": "厦门市",
                "district": "湖里区",
                "formatted_address": "福建省厦门市湖里区五缘湾湿地公园-花溪",
                "location": "118.181,24.532",
            },
            "甲良镇新市场(黄江路)": {
                "province": "贵州省",
                "city": "黔南布依族苗族自治州",
                "district": "荔波县",
                "township": "甲良镇",
                "formatted_address": "贵州省黔南布依族苗族自治州荔波县甲良镇新市场",
                "location": "107.931,25.302",
            },
        }
    )
    state = {
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "201",
                    "store_name": "厦门百星湖里店",
                    "province": "福建省",
                    "city": "厦门市",
                    "district": "湖里区",
                    "store_address": "厦门市湖里区枋湖路",
                },
                {
                    "store_id": "202",
                    "store_name": "厦门思明店",
                    "province": "福建省",
                    "city": "厦门市",
                    "district": "思明区",
                    "store_address": "厦门市思明区",
                },
                {
                    "store_id": "301",
                    "store_name": "荔波店",
                    "province": "贵州省",
                    "city": "黔南布依族苗族自治州",
                    "district": "荔波县",
                    "store_address": "荔波县",
                },
            ]
        }
    }

    xiamen = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "门店位置：五缘湾湿地公园-花溪", "purpose": "nearby_candidates"},
            state,
            coze,  # type: ignore[arg-type]
        )
    )
    libo = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "门店位置：甲良镇新市场(黄江路)", "purpose": "nearby_candidates"},
            state,
            coze,  # type: ignore[arg-type]
        )
    )

    assert [call[1]["address"] for call in coze.calls] == ["五缘湾湿地公园-花溪", "甲良镇新市场(黄江路)"]
    assert xiamen["query"] == "五缘湾湿地公园-花溪"
    assert xiamen["geocode"]["city"] == "厦门市"
    assert xiamen["geocode"]["district"] == "湖里区"
    assert [item["store_id"] for item in xiamen["stores"]] == ["201"]
    assert libo["query"] == "甲良镇新市场(黄江路)"
    assert libo["geocode"]["city"] == "黔南布依族苗族自治州"
    assert libo["geocode"]["district"] == "荔波县"
    assert [item["store_id"] for item in libo["stores"]] == ["301"]


def test_store_lookup_preserves_cross_city_ambiguity_for_plain_landmark() -> None:
    state = {
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "101",
                    "store_name": "上海黄浦店",
                    "province": "上海市",
                    "city": "上海市",
                    "district": "黄浦区",
                },
                {
                    "store_id": "201",
                    "store_name": "大连中山店",
                    "province": "辽宁省",
                    "city": "大连市",
                    "district": "中山区",
                },
            ]
        }
    }

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "人民广场", "purpose": "nearby_candidates"},
            state,
            _AmbiguousGeocodeCoze(),  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "ambiguous_location"
    assert output["source"] == "poi_to_geocode_ambiguous"
    assert output["stores"] == []
    assert output["missing"] == ["confirmed_city_or_district"]
    assert output["geocode_candidate_count"] == 2
    assert {item["city"] for item in output["geocode_candidate_regions"]} == {"上海市", "大连市"}


def test_structured_poi_keeps_first_geocode_candidate_by_contract() -> None:
    state = {
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "101",
                    "store_name": "上海黄浦店",
                    "province": "上海市",
                    "city": "上海市",
                    "district": "黄浦区",
                },
                {
                    "store_id": "201",
                    "store_name": "大连中山店",
                    "province": "辽宁省",
                    "city": "大连市",
                    "district": "中山区",
                },
            ]
        }
    }

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "门店位置：人民广场", "purpose": "nearby_candidates"},
            state,
            _AmbiguousGeocodeCoze(),  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "ok"
    assert output["query"] == "人民广场"
    assert [item["store_id"] for item in output["stores"]] == ["101"]


def test_store_tool_facts_keep_detail_fields_for_reply_model() -> None:
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "ok",
                "query": "Test City",
                "stores": [
                    {
                        "store_id": "101",
                        "store_name": "Test Store",
                        "province": "Test Province",
                        "city": "Test City",
                        "district": "Test District",
                        "store_address": "Test Address",
                        "business_hours": "09:00-19:00",
                        "parking_name": "Test Parking",
                        "parking_address": "Parking Address",
                        "parking_url": "https://example.com/parking",
                        "map_url": "https://example.com/map",
                        "location": "118.1,24.5",
                    }
                ],
            }
        },
        {},
    )
    store = output["structured_facts"]["store_facts"][0]
    assert store["store_id"] == "101"
    assert store["city"] == "Test City"
    assert store["district"] == "Test District"
    assert store["parking_name"] == "Test Parking"
    assert store["parking_address"] == "Parking Address"
    assert store["parking_url"] == "https://example.com/parking"
    assert store["map_url"] == "https://example.com/map"
    assert store["location"] == "118.1,24.5"
