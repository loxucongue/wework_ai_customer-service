from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_nodes import (
    _customer_store_lookup,
    _distance_calculate,
    _filter_invalid_planned_tools,
    _geocode_explicit_region_conflict,
    _lookup_result_allows_distance_calculate,
)
from app.graph.planner.planner_schema_normalizer import normalize_tools
from app.services.store_resolution_v2 import build_location_evidence, resolution_status_for_location
from app.services.store_snapshot_service import StoreSnapshotService


class _GeocodeClient:
    def __init__(self, results: dict[str, dict[str, object]]) -> None:
        self.settings = SimpleNamespace(geocode_workflow_id="geo", distance_workflow_id="route")
        self.results = results
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        self.calls.append((workflow_id, dict(parameters)))
        return {"data": self.results.get(str(parameters.get("address") or ""), {})}


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


def test_city_and_district_without_province_requires_confirmation() -> None:
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

    assert evidence["confirmation_status"] == "needs_confirmation"
    assert resolution_status_for_location(evidence) == "need_location_confirmation"


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
