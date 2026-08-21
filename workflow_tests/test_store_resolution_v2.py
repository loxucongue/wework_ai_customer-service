from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_nodes import (
    _customer_store_lookup,
    _distance_calculate,
    _distance_origin_geocode_from_lookup,
    _distance_origin_is_broad_lookup_scope,
    _filter_invalid_planned_tools,
    _geocode_explicit_region_conflict,
    _lookup_result_allows_distance_calculate,
    _region_equal,
    _strip_admin_suffix,
)
from app.graph.planner.planner_schema_normalizer import normalize_tools
from app.services.store_resolution_v2 import (
    _region_or_text_mentioned,
    _region_alias,
    build_location_evidence,
    resolution_status_for_location,
)
from app.services.driving_route_service import (
    parse_driving_route_workflow_result,
    rerank_stores_by_driving_route,
)


def test_autonomous_prefecture_and_county_level_city_share_region_scope() -> None:
    assert _region_equal("黔南布依族苗族自治州", "黔南市") is True
    assert _region_equal("黔南布依族苗族自治州", "贵阳市") is False


def test_autonomous_prefecture_prefix_is_recognized_in_customer_text() -> None:
    assert _region_or_text_mentioned("黔南布依族苗族自治州", "我在黔南荔波县") is True
    assert _region_or_text_mentioned("黔南布依族苗族自治州", "我在贵阳") is False
from app.services.store_snapshot_service import StoreSnapshotService, parse_geocode_workflow_response


class _GeocodeClient:
    def __init__(
        self,
        results: dict[str, dict[str, object]],
        *,
        routes: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.settings = SimpleNamespace(
            geocode_workflow_id="geo",
            distance_workflow_id="route" if routes is not None else "",
        )
        self.results = results
        self.routes = routes or {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        self.calls.append((workflow_id, dict(parameters)))
        if workflow_id == "route":
            return self.routes.get(
                str(parameters.get("destination") or ""),
                {"data": json.dumps({"output": None})},
            )
        return {"data": self.results.get(str(parameters.get("address") or ""), {})}


@pytest.mark.parametrize(
    ("official_name", "expected_alias"),
    [
        ("浦东新区", "浦东"),
        ("滨海新区", "滨海"),
        ("雄安新区", "雄安"),
        ("两江新区", "两江"),
        ("高新区", "高新"),
        ("荆州市", "荆州"),
        ("荆州区", "荆州"),
    ],
)
def test_administrative_aliases_preserve_useful_place_roots(
    official_name: str,
    expected_alias: str,
) -> None:
    assert _strip_admin_suffix(official_name) == expected_alias
    assert _region_alias(official_name) == expected_alias


def _store(store_id: str, name: str, *, location: str, district: str = "龙湾区") -> dict[str, object]:
    return {
        "store_id": store_id,
        "store_name": name,
        "province": "浙江省",
        "city": "温州市",
        "district": district,
        "store_address": f"浙江省温州市{district}{name}",
        "location": location,
        "store_fact_integrity": "valid",
    }


def test_location_evidence_combines_recent_confirmed_region_and_current_road() -> None:
    state = {
        "normalized_content": "滨海路",
        "conversation_history": ["用户: 我在温州龙湾", "小贝: 好的亲"],
    }
    evidence = build_location_evidence(
        state,
        raw_text="浙江省温州市龙湾区滨海路",
        query="浙江省温州市龙湾区滨海路",
        geocode={
            "province": "浙江省",
            "city": "温州市",
            "district": "龙湾区",
            "formatted_address": "浙江省温州市龙湾区滨海路",
            "location": "120.82,27.91",
        },
    )

    assert evidence["confirmation_status"] == "confirmed"
    assert evidence["city"] == "温州市"
    assert evidence["district"] == "龙湾区"
    assert evidence["detail"] == "滨海路"
    assert "current_message" in evidence["source_message_refs"]
    assert "conv_1" in evidence["source_message_refs"]
    assert resolution_status_for_location(evidence) == ""


def test_distance_origin_prefers_platform_location_card_coordinates_over_text_geocode() -> None:
    origin = _distance_origin_geocode_from_lookup(
        {
            "customer_store_lookup": {
                "status": "ok",
                "query": "杭州市富阳区灵璟闻涛府",
                "geocode": {
                    "location": "120.000000,30.000000",
                    "city": "杭州市",
                    "district": "富阳区",
                },
                "location_evidence": {
                    "normalized_query": "杭州市富阳区灵璟闻涛府",
                    "longitude": 119.900001,
                    "latitude": 30.100002,
                    "province": "浙江省",
                    "city": "杭州市",
                    "district": "富阳区",
                },
            }
        }
    )

    assert origin["location"] == "119.900001,30.100002"
    assert origin["origin_source"] == "platform_location_card"


def test_location_evidence_reads_normalized_location_card_coordinates_field() -> None:
    evidence = build_location_evidence(
        {
            "normalized_content": "定位卡片：萤火虫大厦",
            "location_card": {
                "title": "萤火虫大厦",
                "address": "福建省厦门市湖里区岐山北二路1000号",
                "coordinates": "24.535414,118.152077",
            },
        },
        raw_text="福建省厦门市湖里区岐山北二路1000号 萤火虫大厦",
        query="福建省厦门市湖里区岐山北二路1000号 萤火虫大厦",
        geocode={
            "province": "福建省",
            "city": "厦门市",
            "location": "114.000000,30.000000",
        },
    )

    assert evidence["confirmation_mode"] == "authoritative_location_card"
    assert evidence["longitude"] == 118.152077
    assert evidence["latitude"] == 24.535414


def test_province_only_location_requires_more_location() -> None:
    evidence = build_location_evidence(
        {"normalized_content": "湖北省"},
        raw_text="湖北省",
        query="湖北省",
        geocode={
            "province": "湖北省",
            "city": "武汉市",
            "district": "武昌区",
            "location": "114.30,30.59",
        },
    )

    assert evidence["confirmation_status"] == "incomplete"
    assert resolution_status_for_location(evidence) == "need_location"


def test_unique_county_level_place_with_detail_is_confirmed() -> None:
    evidence = build_location_evidence(
        {"normalized_content": "昆山正仪镇南桥"},
        raw_text="昆山正仪镇南桥",
        query="昆山正仪镇南桥",
        geocode={
            "province": "江苏省",
            "city": "苏州市",
            "district": "昆山市",
            "township": "正仪镇",
            "formatted_address": "江苏省苏州市昆山市正仪镇南桥公交站",
            "location": "120.853701,31.370878",
            "candidate_count": 1,
        },
    )

    assert evidence["confirmation_status"] == "confirmed"
    assert evidence["district"] == "昆山市"
    assert evidence["detail"] == "正仪镇南桥"
    assert resolution_status_for_location(evidence) == ""


def test_distance_tool_requires_successful_lookup_candidates() -> None:
    assert not _lookup_result_allows_distance_calculate(
        {
            "customer_store_lookup": {
                "status": "need_location_confirmation",
                "candidate_stores": [],
            }
        }
    )
    assert _lookup_result_allows_distance_calculate(
        {
            "customer_store_lookup": {
                "status": "ok",
                "candidate_stores": [{"store_id": "350"}],
            }
        }
    )


def test_distance_origin_allows_city_level_lookup_as_approximate_origin() -> None:
    assert _distance_origin_is_broad_lookup_scope(
        {
            "customer_store_lookup": {
                "status": "ok",
                "resolved_admin_level": "city",
                "geocode": {
                    "province": "Guangdong",
                    "city": "Guangzhou",
                    "district": "",
                    "location": "113.33,23.13",
                },
                "location_evidence": {
                    "province": "Guangdong",
                    "city": "Guangzhou",
                    "district": "",
                    "township": "",
                    "detail": "is",
                    "confirmation_mode": "informational_echo",
                },
            }
        },
        candidate_count=4,
    ) is False


def test_parent_city_alias_does_not_create_false_district_conflict() -> None:
    stores = [
        {
            "store_id": "589",
            "store_name": "荆州万达二店",
            "province": "湖北省",
            "city": "荆州市",
            "district": "荆州区",
            "store_address": "湖北省荆州市荆州区万达广场",
        }
    ]
    geocode = {
        "province": "湖北省",
        "city": "荆州市",
        "district": "洪湖市",
        "location": "113.475984,29.827256",
    }

    assert not _geocode_explicit_region_conflict(
        "湖北省荆州市洪湖市",
        geocode,
        stores,
    )
    assert _geocode_explicit_region_conflict(
        "湖北省荆州市荆州区",
        geocode,
        stores,
    )


def test_city_and_district_without_province_can_match_with_informational_echo() -> None:
    evidence = build_location_evidence(
        {"normalized_content": "嘉兴秀洲区"},
        raw_text="嘉兴秀洲区",
        query="嘉兴秀洲区",
        geocode={
            "province": "浙江省",
            "city": "嘉兴市",
            "district": "秀洲区",
            "location": "120.69,30.76",
        },
    )

    assert evidence["confirmation_status"] == "confirmed"
    assert evidence["confirmation_mode"] == "informational_echo"
    assert evidence["confirmation_required_before_match"] is False
    assert resolution_status_for_location(evidence) == ""


def test_explicit_city_with_unique_geocode_can_match_without_blocking_confirmation() -> None:
    evidence = build_location_evidence(
        {"normalized_content": "武汉市"},
        raw_text="武汉市",
        query="武汉市",
        geocode={
            "province": "湖北省",
            "city": "武汉市",
            "district": "武昌区",
            "location": "114.30,30.59",
            "candidate_count": 1,
        },
    )

    assert evidence["confirmation_status"] == "confirmed"
    assert evidence["confirmation_mode"] == "informational_echo"
    assert resolution_status_for_location(evidence) == ""


def test_explicit_wuhan_high_tech_district_can_match_same_turn() -> None:
    evidence = build_location_evidence(
        {"normalized_content": "武汉市东湖高新区"},
        raw_text="武汉市东湖高新区",
        query="武汉市东湖高新区",
        geocode={
            "province": "湖北省",
            "city": "武汉市",
            "district": "洪山区",
            "formatted_address": "湖北省武汉市东湖新技术开发区",
            "location": "114.43,30.50",
            "candidate_count": 1,
        },
    )

    assert evidence["confirmation_status"] == "confirmed"
    assert evidence["confirmation_mode"] == "informational_echo"
    assert resolution_status_for_location(evidence) == ""


def test_lookup_explicit_wuhan_high_tech_district_returns_store_same_turn() -> None:
    query = "武汉市东湖高新区"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "wh-1",
                    "store_name": "武汉光谷店",
                    "province": "湖北省",
                    "city": "武汉市",
                    "district": "洪山区",
                    "store_address": "湖北省武汉市洪山区光谷大道1号",
                    "location": "114.44,30.50",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient(
        {
            query: {
                "province": "湖北省",
                "city": "武汉市",
                "district": "洪山区",
                "formatted_address": "湖北省武汉市东湖新技术开发区",
                "location": "114.43,30.50",
                "candidate_count": 1,
            }
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "nearby_candidates",
                "location_specificity": "confirmed_region",
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ok"
    assert result["candidate_store_count"] == 1
    assert result["candidate_stores"][0]["store_id"] == "wh-1"
    assert result["location_evidence"]["confirmation_mode"] == "informational_echo"


def test_conflicting_guangzhou_huizhou_cities_require_confirmation() -> None:
    query = "广州惠州"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "gz-1",
                    "store_name": "广州店",
                    "province": "广东省",
                    "city": "广州市",
                    "district": "天河区",
                    "store_address": "广东省广州市天河区测试路1号",
                    "location": "113.33,23.13",
                    "store_fact_integrity": "valid",
                },
                {
                    "store_id": "hz-1",
                    "store_name": "惠州店",
                    "province": "广东省",
                    "city": "惠州市",
                    "district": "惠城区",
                    "store_address": "广东省惠州市惠城区测试路1号",
                    "location": "114.42,23.09",
                    "store_fact_integrity": "valid",
                },
            ]
        },
    }
    client = _GeocodeClient(
        {
            query: [
                {
                    "province": "广东省",
                    "city": "广州市",
                    "district": "天河区",
                    "location": "113.33,23.13",
                },
                {
                    "province": "广东省",
                    "city": "惠州市",
                    "district": "惠城区",
                    "location": "114.42,23.09",
                },
            ]
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "nearby_candidates",
                "location_specificity": "specific_place",
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "need_location_confirmation"
    assert result["candidate_stores"] == []
    assert result["location_evidence"]["confirmation_required_before_match"] is True


def test_store_tool_normalization_keeps_customer_confirmation_fact() -> None:
    assert normalize_tools(
        [
            {
                "name": "customer_store_lookup",
                "query": "浙江省嘉兴市秀洲区",
                "purpose": "existence",
                "confirmed_by_customer": True,
            }
        ]
    ) == [
        {
            "name": "customer_store_lookup",
            "purpose": "existence",
            "query": "浙江省嘉兴市秀洲区",
            "confirmed_by_customer": True,
        }
    ]


def test_action_layer_skips_store_lookup_rejected_as_stale_context() -> None:
    tool_results: dict[str, object] = {}
    tool_calls: list[dict[str, object]] = []
    tools = _filter_invalid_planned_tools(
        [{"name": "customer_store_lookup", "query": "浙江省温州市龙湾区"}],
        {
            "tool_policy_violations": [
                {
                    "subtype": "customer_store_lookup",
                    "missing": "store_lookup_not_relevant_to_current_turn",
                }
            ]
        },
        tool_results,
        tool_calls,
    )

    assert tools == []
    assert tool_results["customer_store_lookup"]["error"].endswith(
        "store_lookup_not_relevant_to_current_turn"
    )
    assert tool_calls[0]["skipped"] is True


def test_customer_confirmation_accepts_recent_assistant_location_proposal() -> None:
    evidence = build_location_evidence(
        {
            "normalized_content": "对",
            "conversation_history": [
                "用户: 嘉兴秀洲区",
                "小贝: 您是在浙江省嘉兴市秀洲区这边对吗？",
            ],
        },
        raw_text="浙江省嘉兴市秀洲区",
        query="浙江省嘉兴市秀洲区",
        geocode={
            "province": "浙江省",
            "city": "嘉兴市",
            "district": "秀洲区",
            "location": "120.69,30.76",
        },
        confirmed_by_customer=True,
    )

    assert evidence["confirmation_status"] == "confirmed"
    assert resolution_status_for_location(evidence) == ""


def test_confirmed_district_lookup_returns_scope_candidates() -> None:
    state = {
        "normalized_content": "浙江省温州市龙湾区",
        "customer_store_knowledge": {
            "stores": [
                _store("1", "温州龙湾一店", location="120.80,27.90"),
                _store("2", "温州龙湾二店", location="120.85,27.92"),
            ]
        },
    }
    client = _GeocodeClient(
        {
            "浙江省温州市龙湾区": {
                "province": "浙江省",
                "city": "温州市",
                "district": "龙湾区",
                "formatted_address": "浙江省温州市龙湾区",
                "location": "120.82,27.91",
            }
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": "浙江省温州市龙湾区", "purpose": "existence"},
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ok"
    assert result["location_evidence"]["confirmation_status"] == "confirmed"
    assert [item["store_id"] for item in result["stores"]] == ["1", "2"]


def test_distance_calculate_uses_haversine_without_route_workflow() -> None:
    stores = [
        _store("1", "近店", location="120.821,27.911"),
        _store("2", "远店", location="121.20,28.20"),
    ]
    client = _GeocodeClient(
        {
            "浙江省温州市龙湾区滨海路": {
                "province": "浙江省",
                "city": "温州市",
                "district": "龙湾区",
                "formatted_address": "浙江省温州市龙湾区滨海路",
                "location": "120.82,27.91",
            }
        }
    )

    result = asyncio.run(
        _distance_calculate(
            {
                "name": "distance_calculate",
                "origin": "浙江省温州市龙湾区滨海路",
                "candidate_source": "customer_store_lookup",
            },
            {"normalized_content": "滨海路"},
            client,  # type: ignore[arg-type]
            {"customer_store_lookup": {"candidate_stores": stores}},
        )
    )

    assert result["status"] == "ok"
    assert result["ranking_method"] == "haversine"
    assert [item["store_id"] for item in result["ranked_stores"]] == ["1", "2"]
    assert all(item.get("distance_source") == "haversine" for item in result["ranked_stores"])
    assert all("origin" not in parameters and "destination" not in parameters for _, parameters in client.calls)


def test_route_parser_uses_one_path_and_ignores_top_level_sums() -> None:
    result = parse_driving_route_workflow_result(
        {
            "data": json.dumps(
                {
                    "output": {
                        "distance": 63000,
                        "duration": 9000,
                        "paths": [
                            {"index": 0, "distance": "20000", "duration": 3100},
                            {"index": 1, "distance": "22000", "duration": 2800},
                            {"index": 2, "distance": "21000", "duration": 3100},
                        ],
                    }
                }
            )
        }
    )

    assert result == {
        "status": "ok",
        "route_index": 1,
        "distance_meters": 22000,
        "duration_seconds": 2800,
        "path_count": 3,
    }


def test_route_parser_accepts_v5_cost_duration_and_outputoutput_alias() -> None:
    result = parse_driving_route_workflow_result(
        {
            "data": {
                "outputoutput": {
                    "paths": [
                        {"distance": "9300", "cost": {"duration": "1200"}},
                    ]
                }
            }
        }
    )

    assert result["status"] == "ok"
    assert result["distance_meters"] == 9300
    assert result["duration_seconds"] == 1200


def test_distance_calculate_reranks_haversine_shortlist_by_driving_time() -> None:
    stores = [
        _store("1", "直线近但驾车慢", location="120.821,27.911"),
        _store("2", "驾车更快", location="120.822,27.912"),
        _store("3", "第三家", location="120.900,27.990"),
    ]

    def route(*paths: tuple[int, int]) -> dict[str, object]:
        normalized = [
            {"index": index, "distance": str(distance), "duration": duration}
            for index, (distance, duration) in enumerate(paths)
        ]
        return {
            "data": json.dumps(
                {
                    "output": {
                        "distance": sum(distance for distance, _ in paths),
                        "duration": sum(duration for _, duration in paths),
                        "paths": normalized,
                    }
                }
            )
        }

    client = _GeocodeClient(
        {
            "浙江省温州市龙湾区滨海路": {
                "province": "浙江省",
                "city": "温州市",
                "district": "龙湾区",
                "formatted_address": "浙江省温州市龙湾区滨海路",
                "location": "120.82,27.91",
            }
        },
        routes={
            "120.821000,27.911000": route((1000, 500), (1200, 450)),
            "120.822000,27.912000": route((1500, 180), (1400, 220)),
            "120.900000,27.990000": route((12000, 900), (11000, 950)),
        },
    )

    result = asyncio.run(
        _distance_calculate(
            {
                "name": "distance_calculate",
                "origin": "浙江省温州市龙湾区滨海路",
                "candidate_source": "customer_store_lookup",
                "ranking_claim_level": "relative_near",
            },
            {"normalized_content": "滨海路"},
            client,  # type: ignore[arg-type]
            {"customer_store_lookup": {"candidate_stores": stores}},
        )
    )

    assert result["status"] == "ok"
    assert result["ranking_method"] == "driving_route"
    assert result["route_ranking_complete"] is True
    assert result["route_candidate_count"] == 3
    assert result["route_success_count"] == 3
    assert [item["store_id"] for item in result["ranked_stores"]] == ["2", "1", "3"]
    assert result["ranked_stores"][0]["driving_duration_seconds"] == 180
    route_calls = [parameters for workflow, parameters in client.calls if workflow == "route"]
    assert len(route_calls) == 3
    assert {parameters["origin"] for parameters in route_calls} == {"120.820000,27.910000"}


def test_distance_calculate_falls_back_to_complete_haversine_when_one_route_fails() -> None:
    stores = [
        _store("1", "近店", location="120.821,27.911"),
        _store("2", "远店", location="120.900,27.990"),
    ]
    client = _GeocodeClient(
        {
            "浙江省温州市龙湾区滨海路": {
                "province": "浙江省",
                "city": "温州市",
                "district": "龙湾区",
                "location": "120.82,27.91",
            }
        },
        routes={
            "120.821000,27.911000": {
                "data": json.dumps(
                    {"output": {"paths": [{"distance": "1000", "duration": 200}]}}
                )
            }
        },
    )

    result = asyncio.run(
        _distance_calculate(
            {
                "name": "distance_calculate",
                "origin": "浙江省温州市龙湾区滨海路",
                "candidate_source": "customer_store_lookup",
            },
            {"normalized_content": "滨海路"},
            client,  # type: ignore[arg-type]
            {"customer_store_lookup": {"candidate_stores": stores}},
        )
    )

    assert result["ranking_method"] == "haversine"
    assert result["route_status"] == "fallback_haversine"
    assert [item["store_id"] for item in result["ranked_stores"]] == ["1", "2"]
    assert all("driving_duration_seconds" not in item for item in result["ranked_stores"])


def test_driving_route_only_calls_first_eight_haversine_candidates() -> None:
    stores = [
        {
            **_store(str(index), f"门店{index}", location=f"120.{820 + index:03d},27.91"),
            "distance_km": float(index),
            "distance_source": "haversine",
        }
        for index in range(1, 11)
    ]
    routes = {
        f"120.{820 + index:03d}000,27.910000": {
            "data": json.dumps(
                {
                    "output": {
                        "paths": [
                            {
                                "distance": str(index * 1000),
                                "duration": 1000 - index * 10,
                            }
                        ]
                    }
                }
            )
        }
        for index in range(1, 9)
    }
    client = _GeocodeClient({}, routes=routes)

    result = asyncio.run(
        rerank_stores_by_driving_route(
            coze_client=client,
            workflow_id="route",
            origin_location="120.820,27.910",
            ranked_stores=stores,
        )
    )

    route_calls = [parameters for workflow, parameters in client.calls if workflow == "route"]
    assert len(route_calls) == 8
    assert result["ranking_method"] == "driving_route_shortlist"
    assert result["route_ranking_complete"] is False
    assert [item["store_id"] for item in result["ranked_stores"][:8]] == [
        "8",
        "7",
        "6",
        "5",
        "4",
        "3",
        "2",
        "1",
    ]
    assert [item["store_id"] for item in result["ranked_stores"][8:]] == ["9", "10"]


def test_distance_calculate_reuses_confirmed_lookup_coordinates() -> None:
    stores = [
        {
            "store_id": "589",
            "store_name": "荆州万达二店",
            "province": "湖北省",
            "city": "荆州市",
            "district": "荆州区",
            "store_address": "湖北省荆州市荆州区万达广场",
            "location": "112.239,30.335",
            "store_fact_integrity": "valid",
        }
    ]
    client = _GeocodeClient(
        {
            "湖北省荆州市荆州区": {
                "province": "湖北省",
                "city": "荆州市",
                "district": "荆州区",
                "location": "112.239,30.335",
            }
        }
    )
    lookup = {
        "status": "ok",
        "query": "湖北省荆州市洪湖市",
        "geocode": {
            "province": "湖北省",
            "city": "荆州市",
            "district": "洪湖市",
            "location": "113.475984,29.827256",
        },
        "candidate_stores": stores,
    }

    result = asyncio.run(
        _distance_calculate(
            {
                "name": "distance_calculate",
                "origin": "湖北省荆州市洪湖市",
                "candidate_source": "customer_store_lookup",
            },
            {"normalized_content": "洪湖市", "customer_store_knowledge": {"stores": stores}},
            client,  # type: ignore[arg-type]
            {"customer_store_lookup": lookup},
        )
    )

    assert result["status"] == "ok"
    assert result["origin_geocode"]["district"] == "洪湖市"
    assert result["ranked_stores"][0]["distance_km"] > 100
    assert client.calls == []


def test_planner_fact_output_emits_single_v2_delivery_contract() -> None:
    stores = [
        {**_store("1", "近店", location="120.821,27.911"), "distance_km": 0.2, "distance_source": "haversine"},
        {**_store("2", "远店", location="121.20,28.20"), "distance_km": 50.0, "distance_source": "haversine"},
    ]
    state = {
        "customer_store_knowledge": {"stores": stores},
        "guardrail_result": {},
    }

    output = build_planner_fact_output(
        {
            "distance_calculate": {
                "status": "ok",
                "origin": "浙江省温州市龙湾区滨海路",
                "province": "浙江省",
                "city": "温州市",
                "district": "龙湾区",
                "resolved_admin_level": "district",
                "ranking_method": "haversine",
                "ranked_stores": stores,
                "candidate_store_count": 2,
            }
        },
        state,
    )
    resolution = output["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "send_single"
    assert resolution["recommended_store_id"] == "1"
    assert resolution["delivery_store_ids"] == ["1"]
    assert resolution["ranking_method"] == "haversine"
    assert resolution["customer_claim_level"] == "relative_near"


def test_planner_fact_output_preserves_driving_route_ranking_fact() -> None:
    stores = [
        {
            **_store("2", "驾车更快", location="120.822,27.912"),
            "distance_km": 0.3,
            "distance_source": "driving_route",
            "driving_distance_meters": 1500,
            "driving_duration_seconds": 180,
        },
        {
            **_store("1", "直线更近", location="120.821,27.911"),
            "distance_km": 0.2,
            "distance_source": "driving_route",
            "driving_distance_meters": 1000,
            "driving_duration_seconds": 500,
        },
    ]
    output = build_planner_fact_output(
        {
            "distance_calculate": {
                "status": "ok",
                "origin": "浙江省温州市龙湾区滨海路",
                "province": "浙江省",
                "city": "温州市",
                "district": "龙湾区",
                "resolved_admin_level": "district",
                "ranking_method": "driving_route",
                "ranking_complete": True,
                "route_status": "ok",
                "route_candidate_count": 2,
                "route_success_count": 2,
                "route_ranking_complete": True,
                "route_shortlist_size": 2,
                "ranked_stores": stores,
                "candidate_store_count": 2,
                "ranked_candidate_count": 2,
            }
        },
        {"customer_store_knowledge": {"stores": stores}, "guardrail_result": {}},
    )
    resolution = output["structured_facts"]["store_resolution_fact"]
    recommended = output["structured_facts"]["recommended_store"]

    assert resolution["recommended_store_id"] == "2"
    assert resolution["ranking_method"] == "driving_route"
    assert resolution["route_ranking_complete"] is True
    assert resolution["route_candidate_count"] == 2
    assert recommended["reason"] == "driving_route_rank_1"


def test_city_fallback_distance_ranking_delivers_top_three_store_options() -> None:
    stores = [
        {**_store(str(store_id), f"store-{store_id}", location=location, district=district), "distance_km": distance, "distance_source": "haversine"}
        for store_id, location, district, distance in (
            (218, "114.320528,30.388406", "Jiangxia", 12.0),
            (344, "114.275676,30.588601", "Jianghan", 15.0),
            (149, "114.339542,30.557202", "Wuchang", 18.0),
            (590, "114.412893,30.493390", "Guanggu", 24.0),
        )
    ]
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "ok",
                "raw_query": "Wuhan Zhuankou",
                "query": "Wuhan Zhuankou",
                "province": "Hubei",
                "city": "Wuhan",
                "district": "Caidian",
                "resolved_admin_level": "district",
                "scope_match_level": "city_fallback",
                "exact_scope_has_store": False,
                "stores": stores,
            },
            "distance_calculate": {
                "status": "ok",
                "origin": "Wuhan Zhuankou",
                "province": "Hubei",
                "city": "Wuhan",
                "district": "Caidian",
                "resolved_admin_level": "district",
                "scope_match_level": "city_fallback",
                "exact_scope_has_store": False,
                "ranking_method": "haversine",
                "ranked_stores": stores,
                "candidate_store_count": 4,
            },
        },
        {"customer_store_knowledge": {"stores": stores}, "guardrail_result": {}},
    )
    resolution = output["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "send_multiple"
    assert resolution["recommended_store_id"] == "218"
    assert resolution["delivery_store_ids"] == ["218", "344"]
    assert resolution["distance_tie_threshold_km"] == 5.0
    assert resolution["visible_candidate_ids"] == ["218", "344", "149", "590"]
    assert resolution["ranking_method"] == "haversine"
    assert resolution["customer_claim_level"] == "relative_near"


def test_completed_province_search_without_store_does_not_ask_for_district() -> None:
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "no_match",
                "raw_query": "湖北省",
                "query": "湖北省",
                "province": "湖北省",
                "resolved_admin_level": "province",
                "scope_match_level": "none",
                "exact_scope_has_store": False,
                "same_city_has_store": False,
                "stores": [],
                "candidate_stores": [],
                "missing": [],
            }
        },
        {
            "customer_store_knowledge": {
                "stores": [
                    {
                        "store_id": "900",
                        "store_name": "外省门店",
                        "province": "湖南省",
                        "city": "长沙市",
                        "district": "岳麓区",
                        "store_address": "湖南省长沙市岳麓区测试路1号",
                        "store_fact_integrity": "valid",
                    }
                ]
            },
            "guardrail_result": {},
        },
    )
    resolution = output["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "no_valid_candidate"
    assert resolution["candidate_search_complete"] is True
    assert resolution["candidate_search_scope"] == "province"
    assert resolution["coverage_status"] == "no_store_in_province"
    assert resolution["clarification_required"] is False
    assert resolution["recommendation_final_for_destination"] is True
    assert resolution["delivery_store_ids"] == []


def test_city_scope_delivers_all_same_city_stores_without_cross_city_candidate() -> None:
    stores = [
        {
            "store_id": str(index),
            "store_name": f"武汉门店{index}",
            "province": "湖北省",
            "city": "武汉市",
            "district": district,
            "store_address": f"湖北省武汉市{district}测试路{index}号",
            "store_fact_integrity": "valid",
        }
        for index, district in enumerate(
            ("江汉区", "江岸区", "武昌区", "洪山区", "汉阳区", "硚口区"),
            start=1,
        )
    ]
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "ok",
                "raw_query": "武汉市",
                "query": "武汉市",
                "province": "湖北省",
                "city": "武汉市",
                "resolved_admin_level": "city",
                "scope_match_level": "city",
                "exact_scope_has_store": True,
                "same_city_has_store": True,
                "allow_broad_scope_delivery": True,
                "stores": stores,
                "candidate_stores": stores,
                "missing": [],
            }
        },
        {"customer_store_knowledge": {"stores": stores}, "guardrail_result": {}},
    )
    resolution = output["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "send_multiple"
    assert resolution["delivery_store_ids"] == ["1", "2", "3", "4", "5", "6"]
    assert resolution["coverage_status"] == "same_city_available"
    assert resolution["recommendation_final_for_destination"] is True


def test_empty_distance_result_does_not_erase_location_confirmation_contract() -> None:
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "need_location_confirmation",
                "raw_query": "东坑",
                "query": "东坑",
                "location_evidence": {
                    "raw_text": "东坑",
                    "confirmation_status": "needs_confirmation",
                },
                "stores": [],
                "candidate_stores": [],
                "missing": ["confirmed_location"],
            },
            "distance_calculate": {
                "status": "no_candidate_stores",
                "origin": "东坑",
                "candidate_stores": [],
                "error": "no_candidate_stores",
            },
        },
        {"customer_store_knowledge": {"stores": []}, "guardrail_result": {}},
    )

    resolution = output["structured_facts"]["store_resolution_fact"]
    assert resolution["status"] == "need_location_confirmation"
    assert resolution["location_evidence"]["raw_text"] == "东坑"


def test_snapshot_keeps_platform_region_and_does_not_use_parking_for_membership() -> None:
    service = StoreSnapshotService(Settings(geocode_workflow_id=""), platform_client=None)
    service._geocode_store_address = lambda _: {  # type: ignore[method-assign]
        "province": "广东省",
        "city": "广州市",
        "district": "番禺区",
        "formatted_address": "广东省广州市番禺区万达广场",
        "location": "113.35,23.00",
    }
    store = service._store_from_row(
        {"id": "129", "name": "广州番禺店", "status": 1, "shore_show": 1},
        detail={
            "tencent_address": "四川省南充市南部县万达广场",
            "parking_info": {"park_address": "广东省广州市番禺区兴南大道368号"},
        },
        detail_source="test",
    )

    assert store["province"] == "四川省"
    assert store["city"] == "南充市"
    assert store["platform_region"]["city"] == "南充市"
    assert store["parking_address"].startswith("广东省广州市")
    assert store["geocode_source"] == ""


def test_store_tool_normalization_keeps_bounded_location_candidates() -> None:
    tools = normalize_tools(
        [
            {
                "name": "customer_store_lookup",
                "query": "\u9632\u6210\u6e2f",
                "purpose": "existence",
                "location_specificity": "typo_or_alias",
                "location_candidates": [
                    {
                        "query": "\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a\u9632\u57ce\u6e2f\u5e02",
                        "reason": "\u7591\u4f3c\u540c\u97f3\u9519\u522b\u5b57",
                        "confidence": "high",
                        "requires_confirmation": True,
                    },
                    "\u5e7f\u897f\u9632\u57ce\u6e2f",
                    "\u5e7f\u4e1c\u9632\u57ce\u6e2f",
                    "\u8d85\u51fa\u4e0a\u9650\u7684\u7b2c\u56db\u6761",
                ],
            }
        ]
    )

    assert len(tools[0]["location_candidates"]) == 3
    assert tools[0]["location_candidates"][0]["requires_confirmation"] is True
    assert tools[0]["location_specificity"] == "typo_or_alias"


def test_generic_landmark_without_region_does_not_call_geocode() -> None:
    query = "火车站附近"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {"stores": []},
    }
    client = _GeocodeClient(
        {
            query: {
                "province": "甘肃省",
                "city": "陇南市",
                "district": "徽县",
                "formatted_address": "甘肃省陇南市徽县火车站",
                "location": "106.08,33.77",
            }
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "existence",
                "location_specificity": "generic_landmark_without_region",
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "need_location"
    assert result["stores"] == []
    assert result["source"] == "planner_location_specificity"
    assert client.calls == []


def test_ambiguous_place_without_region_does_not_call_geocode() -> None:
    query = "东坑"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {"stores": []},
    }
    client = _GeocodeClient({query: {"location": "113.94,22.99"}})

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "existence",
                "location_specificity": "ambiguous_place_without_region",
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ambiguous_location"
    assert result["stores"] == []
    assert client.calls == []


def test_generic_landmark_nearby_lookup_does_not_require_distance_tool() -> None:
    from app.graph.planner.brain_v2_normalizer import _distance_tool_violations

    violations = _distance_tool_violations(
        [
            {
                "name": "customer_store_lookup",
                "purpose": "nearby_candidates",
                "query": "火车站附近",
                "location_specificity": "generic_landmark_without_region",
            }
        ]
    )

    assert violations == []


def test_typo_location_requires_model_generated_candidate_for_repair() -> None:
    from app.graph.planner.brain_v2_normalizer import _tool_policy_violations

    violations = _tool_policy_violations(
        [
            {
                "name": "customer_store_lookup",
                "purpose": "existence",
                "query": "防成港",
                "location_specificity": "typo_or_alias",
            }
        ],
        {
            "normalized_content": "防成港",
            "conversation_history": ["小贝: 您在哪个城市哪个区呀，我帮您匹配门店"],
        },
    )

    assert any(item["missing"] == "store_lookup_typo_candidate_required" for item in violations)


def test_store_lookup_never_crosses_province_via_same_district_text_fallback() -> None:
    liaoning = "\u8fbd\u5b81\u7701"
    shenyang = "\u6c88\u9633\u5e02"
    heping = "\u548c\u5e73\u533a"
    tianjin = "\u5929\u6d25\u5e02"
    state = {
        "normalized_content": liaoning + shenyang + heping,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "tj-1",
                    "store_name": "\u5929\u6d25\u548c\u5e73\u5e97",
                    "province": tianjin,
                    "city": tianjin,
                    "district": heping,
                    "store_address": tianjin + heping + "\u6d4b\u8bd5\u8def1\u53f7",
                    "location": "117.20,39.12",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient(
        {
            liaoning + shenyang + heping: {
                "province": liaoning,
                "city": shenyang,
                "district": heping,
                "formatted_address": liaoning + shenyang + heping,
                "location": "123.42,41.79",
            }
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": liaoning + shenyang + heping, "purpose": "existence"},
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "no_match"
    assert result["stores"] == []
    assert result["source"] != "customer_scope_text_match"


def test_store_lookup_uses_normalized_typo_candidate_but_requires_customer_confirmation() -> None:
    raw = "\u9632\u6210\u6e2f"
    corrected = "\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a\u9632\u57ce\u6e2f\u5e02"
    state = {
        "normalized_content": raw,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "gx-1",
                    "store_name": "\u5357\u5b81\u5e97",
                    "province": "\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a",
                    "city": "\u5357\u5b81\u5e02",
                    "district": "\u9752\u79c0\u533a",
                    "store_address": "\u5e7f\u897f\u5357\u5b81\u5e02\u9752\u79c0\u533a\u6d4b\u8bd5\u8def1\u53f7",
                    "location": "108.37,22.82",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient(
        {
            raw: {},
            corrected: {
                "province": "\u5e7f\u897f\u58ee\u65cf\u81ea\u6cbb\u533a",
                "city": "\u9632\u57ce\u6e2f\u5e02",
                "district": "\u6e2f\u53e3\u533a",
                "formatted_address": corrected + "\u6e2f\u53e3\u533a",
                "location": "108.35,21.69",
            },
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": raw,
                "purpose": "nearby_candidates",
                "location_candidates": [
                    {
                        "query": corrected,
                        "reason": "\u7591\u4f3c\u9519\u522b\u5b57",
                        "confidence": "high",
                        "requires_confirmation": True,
                    }
                ],
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "need_location_confirmation"
    assert result["query"] == corrected
    assert result["stores"] == []
    assert result["normalization_evidence"]["selected_source"] == "planner_normalized_candidate"
    assert result["normalization_evidence"]["requires_confirmation"] is True


def test_store_lookup_blocks_explicit_district_when_first_poi_conflicts() -> None:
    query = "\u5e7f\u5dde\u756a\u79ba\u4e07\u8fbe"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "gz-1",
                    "store_name": "\u5e7f\u5dde\u756a\u79ba\u5e97",
                    "province": "\u5e7f\u4e1c\u7701",
                    "city": "\u5e7f\u5dde\u5e02",
                    "district": "\u756a\u79ba\u533a",
                    "store_address": "\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u756a\u79ba\u533a\u4e07\u8fbe\u5e7f\u573a",
                    "location": "113.35,23.00",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient(
        {
            query: {
                "province": "\u5e7f\u4e1c\u7701",
                "city": "\u5e7f\u5dde\u5e02",
                "district": "\u6d77\u73e0\u533a",
                "formatted_address": "\u5e7f\u4e1c\u7701\u5e7f\u5dde\u5e02\u6d77\u73e0\u533a\u4e07\u8fbe\u5e7f\u573a",
                "location": "113.32,23.08",
            }
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": query, "purpose": "nearby_candidates"},
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "geocode_query_conflict"
    assert result["stores"] == []
    assert result["normalization_evidence"]["attempts"][0]["reason"] == "explicit_region_conflict"


def test_store_lookup_uses_exact_visible_district_when_geocode_is_unavailable() -> None:
    query = "贵阳市花溪区"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "501",
                    "store_name": "贵阳花溪店",
                    "province": "贵州省",
                    "city": "贵阳市",
                    "district": "花溪区",
                    "store_address": "贵州省贵阳市花溪区清溪路",
                    "location": "106.67,26.41",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient({query: {}})

    result = asyncio.run(
        _customer_store_lookup(
            {"name": "customer_store_lookup", "query": query, "purpose": "existence"},
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ok"
    assert result["source"] == "customer_scope_exact_text_region"
    assert [item["store_id"] for item in result["candidate_stores"]] == ["501"]
    assert result["location_evidence"]["city"] == "贵阳市"
    assert result["location_evidence"]["district"] == "花溪区"
    assert result["location_evidence"]["confirmation_status"] == "confirmed"


def test_geocode_multiple_regions_uses_first_but_preserves_ambiguity() -> None:
    from app.graph.nodes.action_nodes import _first_geocode_candidate

    result = _first_geocode_candidate(
        [
            {
                "province": "广东省",
                "city": "东莞市",
                "district": "东坑镇",
                "location": "113.94,22.99",
            },
            {
                "province": "江西省",
                "city": "赣州市",
                "district": "章贡区",
                "location": "114.94,25.83",
            },
        ]
    )

    assert result["province"] == "广东省"
    assert result["candidate_count"] == 2
    assert result["ambiguous_regions"] is True
    assert result["candidate_regions"] == [
        {"province": "广东省", "city": "东莞市", "district": "东坑镇"},
        {"province": "江西省", "city": "赣州市", "district": "章贡区"},
    ]


def test_lookup_blocks_unconfirmed_cross_region_poi_results() -> None:
    query = "东坑"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "dg-1",
                    "store_name": "东莞店",
                    "province": "广东省",
                    "city": "东莞市",
                    "district": "东坑镇",
                    "store_address": "广东省东莞市东坑镇测试路1号",
                    "location": "113.94,22.99",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient(
        {
            query: [
                {
                    "province": "广东省",
                    "city": "东莞市",
                    "district": "东坑镇",
                    "location": "113.94,22.99",
                },
                {
                    "province": "江西省",
                    "city": "赣州市",
                    "district": "章贡区",
                    "location": "114.94,25.83",
                },
            ]
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "nearby_candidates",
                "location_specificity": "specific_place",
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "need_location_confirmation"
    assert result["stores"] == []
    assert result["geocode_candidate_count"] == 2
    assert len(result["geocode_candidate_regions"]) == 2


def test_structured_location_card_title_does_not_conflict_with_matching_full_address() -> None:
    query = "双流人民广场，四川省成都市双流区"
    state = {
        "normalized_content": "定位卡片：双流人民广场",
        "request_context": {
            "msgtype": "location",
            "location_title": "双流人民广场",
            "location_address": "四川省成都市双流区",
        },
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "401",
                    "store_name": "成都双流店",
                    "province": "四川省",
                    "city": "成都市",
                    "district": "双流区",
                    "store_address": "四川省成都市双流区蛟龙港",
                    "location": "103.95,30.58",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient(
        {
            query: {
                "province": "四川省",
                "city": "成都市",
                "district": "双流区",
                "formatted_address": "四川省成都市双流区",
                "location": "103.92,30.57",
            }
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "nearby_candidates",
                "location_specificity": "specific_place",
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ok"
    assert result["candidate_store_count"] == 1
    assert result["location_evidence"]["confirmation_status"] == "confirmed"


def test_explicit_full_region_can_use_consistent_first_poi_candidate() -> None:
    query = "广东省东莞市东坑镇"
    state = {
        "normalized_content": query,
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "dg-1",
                    "store_name": "东莞店",
                    "province": "广东省",
                    "city": "东莞市",
                    "district": "东坑镇",
                    "store_address": "广东省东莞市东坑镇测试路1号",
                    "location": "113.94,22.99",
                    "store_fact_integrity": "valid",
                }
            ]
        },
    }
    client = _GeocodeClient(
        {
            query: [
                {
                    "province": "广东省",
                    "city": "东莞市",
                    "district": "东坑镇",
                    "location": "113.94,22.99",
                },
                {
                    "province": "江西省",
                    "city": "赣州市",
                    "district": "章贡区",
                    "location": "114.94,25.83",
                },
            ]
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "nearby_candidates",
                "location_specificity": "confirmed_region",
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ok"
    assert result["candidate_store_count"] == 1
    assert result["location_evidence"]["confirmation_status"] == "confirmed"
    assert result["location_evidence"]["geocode_ambiguous_regions"] is True


def test_snapshot_geocode_parser_keeps_all_region_candidates_for_audit() -> None:
    result = parse_geocode_workflow_response(
        {
            "data": [
                {
                    "province": "广东省",
                    "city": "东莞市",
                    "district": "东坑镇",
                    "location": "113.94,22.99",
                },
                {
                    "province": "江西省",
                    "city": "赣州市",
                    "district": "章贡区",
                    "location": "114.94,25.83",
                },
            ]
        }
    )

    assert result["province"] == "广东省"
    assert result["candidate_count"] == 2
    assert result["ambiguous_regions"] is True
    assert len(result["candidate_regions"]) == 2
