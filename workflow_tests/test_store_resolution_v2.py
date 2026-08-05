from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_nodes import _customer_store_lookup, _distance_calculate, _filter_invalid_planned_tools
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
