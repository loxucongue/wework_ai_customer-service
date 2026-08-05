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


class _StoreScopeRecoveryPlatformClient:
    available = True

    def list_stores(self, **_: object) -> list[dict[str, object]]:
        return [{"id": "350"}, {"id": "557"}, {"id": "not-in-snapshot"}]


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


def test_action_layer_recovers_timed_out_customer_store_scope(monkeypatch) -> None:
    snapshot = [
        {
            "store_id": "350",
            "store_name": "苏州姑苏店",
            "province": "江苏省",
            "city": "苏州市",
            "district": "姑苏区",
            "store_address": "江苏省苏州市姑苏区广济南路19号",
            "location": "120.600121,31.305249",
            "store_fact_integrity": "valid",
        },
        {
            "store_id": "557",
            "store_name": "苏州工业园二店",
            "province": "江苏省",
            "city": "苏州市",
            "district": "吴中区",
            "store_address": "江苏省苏州市吴中区星港街283号",
            "location": "120.676601,31.326341",
            "store_fact_integrity": "valid",
        },
    ]
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: snapshot)

    output = asyncio.run(
        action_nodes._recover_customer_store_scope(
            {
                "customer_id": "22089120",
                "customer_add_wechat_id": "22089120",
                "customer_context": {
                    "platform_customer_id": "14618712",
                    "customer_id": "14618712",
                    "customer_add_wechat_id": "22089120",
                    "identity": {
                        "platform_customer_id": "14618712",
                        "customer_add_wechat_id": "22089120",
                    },
                },
                "request_context": {
                    "customer_id": "22089120",
                    "external_userid": "external-1",
                    "corp_id": "corp-1",
                    "wechat": "DY258",
                },
                "customer_store_knowledge": {
                    "source": "customer_store_knowledge_timeout",
                    "stores": [],
                },
            },
            _StoreScopeRecoveryPlatformClient(),  # type: ignore[arg-type]
        )
    )

    assert output["customer_id"] == "14618712"
    assert output["customer_add_wechat_id"] == "22089120"
    assert [item["store_id"] for item in output["stores"]] == ["350", "557"]
    assert output["missing_snapshot_store_ids"] == ["not-in-snapshot"]


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

    assert output["status"] == "need_location"
    assert output["source"] == "location_evidence_v2"
    assert output["stores"] == []


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


def test_store_lookup_local_scope_missing_triggers_distance_enrichment() -> None:
    result = {
        "status": "ok",
        "geocode": {"city": "荆州市", "district": "洪湖市", "location": "113.4,29.8"},
        "exact_scope_has_store": False,
        "scope_match_level": "city_fallback",
        "candidate_stores": [
            {"store_id": "241", "city": "荆州市", "district": "荆州区"},
            {"store_id": "589", "city": "荆州市", "district": "荆州区"},
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


def test_store_lookup_city_query_augments_customer_scope_with_snapshot_city_stores(monkeypatch) -> None:
    monkeypatch.setattr(
        action_nodes,
        "_STORE_SNAPSHOT_CACHE",
        {
            "stores_by_id": {
                "241": {
                    "store_id": "241",
                    "store_name": "荆州万达店",
                    "province": "湖北省",
                    "city": "荆州市",
                    "district": "荆州区",
                    "store_address": "荆州市荆州区北京西路万达广场B座",
                },
                "589": {
                    "store_id": "589",
                    "store_name": "荆州万达二店",
                    "province": "湖北省",
                    "city": "荆州市",
                    "district": "荆州区",
                    "store_address": "湖北省荆州市荆州区北京西路万达广场写字楼B栋",
                },
            }
        },
    )
    coze = _FakeGeocodeCoze(
        {
            "荆州市": {
                "province": "湖北省",
                "city": "荆州市",
                "formatted_address": "湖北省荆州市",
                "location": "112.239,30.335",
            }
        }
    )

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "荆州市", "purpose": "existence"},
            {
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "589",
                            "store_name": "荆州万达二店",
                            "province": "湖北省",
                            "city": "荆州市",
                            "district": "荆州区",
                            "store_address": "湖北省荆州市荆州区北京西路万达广场写字楼B栋",
                        }
                    ]
                }
            },
            coze,  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "ok"
    assert output["source"] == "customer_scope_geocode"
    assert [item["store_id"] for item in output["stores"]] == ["589"]
    assert output["location_evidence"]["city"] == "荆州市"
    assert output["location_evidence"]["confirmation_mode"] == "informational_echo"


def test_store_lookup_town_without_local_store_uses_customer_scope_province_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        action_nodes,
        "_STORE_SNAPSHOT_CACHE",
        {
            "stores_by_id": {
                "185": {
                    "store_id": "185",
                    "store_name": "贵阳花溪店",
                    "province": "贵州省",
                    "city": "贵阳市",
                    "district": "花溪区",
                    "store_address": "贵阳花溪区万科大都会写字楼A座",
                },
                "581": {
                    "store_id": "581",
                    "store_name": "贵阳花溪二店",
                    "province": "贵州省",
                    "city": "贵阳市",
                    "district": "花溪区",
                    "store_address": "贵州省贵阳市经济技术开发区珠江路万科大都会万科写字楼B座",
                },
            }
        },
    )
    coze = _FakeGeocodeCoze(
        {
            "甲良镇": {
                "province": "贵州省",
                "city": "黔南布依族苗族自治州",
                "district": "荔波县",
                "township": "甲良镇",
                "formatted_address": "贵州省黔南布依族苗族自治州荔波县甲良镇",
                "location": "107.728,25.577",
            }
        }
    )

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "甲良镇", "purpose": "existence"},
            {
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "581",
                            "store_name": "贵阳花溪二店",
                            "province": "贵州省",
                            "city": "贵阳市",
                            "district": "花溪区",
                            "store_address": "贵州省贵阳市经济技术开发区珠江路万科大都会万科写字楼B座",
                        }
                    ]
                }
            },
            coze,  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "ok"
    assert output["source"] == "customer_scope_geocode"
    assert [item["store_id"] for item in output["candidate_stores"]] == ["581"]
    assert output["location_evidence"]["district"] == "荔波县"
    assert output["location_evidence"]["confirmation_mode"] == "informational_echo"


def test_store_lookup_explicit_city_district_uses_unique_poi_same_turn(monkeypatch) -> None:
    monkeypatch.setattr(
        action_nodes,
        "_STORE_SNAPSHOT_CACHE",
        {
            "stores_by_id": {
                "557": {
                    "store_id": "557",
                    "store_name": "苏州工业园二店",
                    "province": "江苏省",
                    "city": "苏州市",
                    "district": "吴中区",
                    "store_address": "苏州市工业园区星港街283号中园大厦",
                },
                "350": {
                    "store_id": "350",
                    "store_name": "苏州姑苏店",
                    "province": "江苏省",
                    "city": "苏州市",
                    "district": "姑苏区",
                    "store_address": "苏州市姑苏区广济南路19号永捷峰汇写字楼",
                },
            }
        },
    )
    coze = _FakeGeocodeCoze(
        {
            "苏州相城区": {
                "province": "江苏省",
                "city": "苏州市",
                "district": "相城区",
                "formatted_address": "江苏省苏州市相城区",
                "location": "120.642,31.369",
            }
        }
    )

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "苏州相城区", "purpose": "existence"},
            {
                "customer_store_knowledge": {
                    "source": "platform_agent.store_index+store_snapshot",
                    "stores": [
                        {
                            "store_id": "350",
                            "store_name": "苏州姑苏店",
                            "province": "江苏省",
                            "city": "苏州市",
                            "district": "姑苏区",
                            "store_address": "苏州市姑苏区广济南路19号永捷峰汇写字楼",
                        },
                        {
                            "store_id": "216",
                            "store_name": "苏州昆山店",
                            "province": "江苏省",
                            "city": "苏州市",
                            "district": "昆山市",
                            "store_address": "苏州昆山市周市镇218号万达广场金街6号门",
                        },
                    ],
                }
            },
            coze,  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "ok"
    assert output["source"] == "customer_scope_geocode"
    assert {item["store_id"] for item in output["candidate_stores"]} == {"350", "216"}
    assert output["location_evidence"]["confirmation_status"] == "confirmed"
    assert output["location_evidence"]["confirmation_mode"] == "informational_echo"


def test_planner_fact_output_filters_store_facts_to_customer_scope() -> None:
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "ok",
                "source": "customer_scope_geocode",
                "candidate_store_count": 2,
                "stores": [
                    {"store_id": "350", "store_name": "苏州姑苏店", "city": "苏州市"},
                    {"store_id": "557", "store_name": "苏州工业园二店", "city": "苏州市"},
                ],
            }
        },
        {
            "customer_store_knowledge": {
                "source": "platform_agent.store_index+store_snapshot",
                "stores": [{"store_id": "350", "store_name": "苏州姑苏店"}],
            }
        },
    )

    store_ids = {
        item["store_id"]
        for item in output["structured_facts"]["store_facts"]
    }
    assert store_ids == {"350"}
    assert output["structured_facts"]["store_facts"][0]["scope_authorized"] is True


def test_store_lookup_short_place_does_not_match_one_character_region_token() -> None:
    coze = _FakeGeocodeCoze(
        {
            "东坑": {
                "province": "江西省",
                "city": "赣州市",
                "district": "章贡区",
                "formatted_address": "江西省赣州市章贡区东坑",
                "location": "114.9,25.8",
            }
        }
    )

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "东坑", "purpose": "existence"},
            {
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "168",
                            "store_name": "中山石岐店",
                            "province": "广东省",
                            "city": "中山市",
                            "district": "东区",
                            "store_address": "中山市东区街道",
                        },
                        {
                            "store_id": "588",
                            "store_name": "攀枝花东区二店",
                            "province": "四川省",
                            "city": "攀枝花市",
                            "district": "东区",
                            "store_address": "攀枝花市东区",
                        },
                    ]
                }
            },
            coze,  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "need_location_confirmation"
    assert output["stores"] == []


def test_store_lookup_rejects_partial_multi_fragment_geocode_match() -> None:
    coze = _FakeGeocodeCoze(
        {
            "我在兆京，良乡": {
                "province": "广东省",
                "city": "梅州市",
                "district": "丰顺县",
                "township": "留隍镇",
                "formatted_address": "广东省梅州市丰顺县留隍镇良乡村",
                "location": "116.5,23.8",
            }
        }
    )

    output = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "我在兆京，良乡", "purpose": "nearby_candidates"},
            {"customer_store_knowledge": {"stores": []}},
            coze,  # type: ignore[arg-type]
        )
    )

    assert output["status"] == "geocode_query_conflict"
    assert output["candidate_stores"] == []
    assert output["query_consistency"]["matched_fragments"] == ["良乡"]
    assert output["query_consistency"]["unresolved_fragments"] == ["兆京"]


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

    assert output["status"] == "need_location"
    assert output["source"] == "location_evidence_v2"
    assert output["stores"] == []


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

    assert output["status"] == "need_location"
    assert output["source"] == "location_evidence_v2"
    assert output["missing"] == ["city_or_district"]


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
    assert output["status"] == "need_location"
    assert output["stores"] == []


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
    assert xiamen["status"] == "ok"
    assert [item["store_id"] for item in xiamen["stores"]] == ["201"]
    assert libo["query"] == "甲良镇新市场(黄江路)"
    assert libo["geocode"]["city"] == "黔南布依族苗族自治州"
    assert libo["geocode"]["district"] == "荔波县"
    assert libo["status"] == "ok"
    assert [item["store_id"] for item in libo["stores"]] == ["301"]


def test_store_lookup_uses_first_geocode_candidate_for_plain_landmark() -> None:
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

    assert output["status"] == "need_location_confirmation"
    assert output["stores"] == []
    assert output["geocode"]["city"] == "上海市"


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

    assert output["status"] == "need_location_confirmation"
    assert output["query"] == "人民广场"
    assert output["stores"] == []


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
