from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes import action_nodes
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.services.driving_route_service import parse_driving_route_workflow_result, rerank_stores_by_driving_route
from app.services.store_destination_resolver import (
    _is_generic_store_detail_hint,
    _structured_current_location_query,
    resolve_active_store_destination,
)
from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_snapshot_service import StoreSnapshotService, parse_region


class _FakeGeocodeClient:
    def __init__(self, geocode: dict[str, object]) -> None:
        self.settings = SimpleNamespace(geocode_workflow_id="fake-geocode")
        self._geocode = geocode

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        assert workflow_id == "fake-geocode"
        assert parameters.get("address")
        return {"data": [self._geocode]}


class _FakeDestinationModel:
    available = True

    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.calls = 0

    async def chat_json(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        return self.output


class _ClosableDestinationModel(_FakeDestinationModel):
    async def aclose(self) -> None:
        return None


class _FailingGeocodeClient:
    settings = SimpleNamespace(geocode_workflow_id="fake-geocode")

    async def run_workflow(self, _workflow_id: str, _parameters: dict[str, object]) -> dict[str, object]:
        raise TimeoutError("map timeout")


class _FakeDrivingRouteClient:
    async def run_workflow(self, _workflow_id: str, parameters: dict[str, str]) -> dict[str, object]:
        distance, duration = {
            "104.100000,30.100000": (49_000, 3_000),
            "104.200000,30.200000": (46_000, 3_200),
        }[parameters["destination"]]
        return {
            "data": {
                "output": {
                    "paths": [{"index": 0, "distance": distance, "duration": duration}],
                }
            }
        }


def test_driving_routes_rank_by_distance_before_duration() -> None:
    parsed = parse_driving_route_workflow_result(
        {
            "data": {
                "output": {
                    "paths": [
                        {"index": 0, "distance": 49_000, "duration": 3_000},
                        {"index": 1, "distance": 46_000, "duration": 3_200},
                    ]
                }
            }
        }
    )
    assert parsed["distance_meters"] == 46_000

    reranked = asyncio.run(
        rerank_stores_by_driving_route(
            coze_client=_FakeDrivingRouteClient(),
            workflow_id="distance",
            origin_location="104.000000,30.000000",
            ranked_stores=[
                {"store_id": "1", "location": "104.100000,30.100000", "distance_km": 10.0},
                {"store_id": "2", "location": "104.200000,30.200000", "distance_km": 20.0},
            ],
        )
    )
    assert [item["store_id"] for item in reranked["ranked_stores"]] == ["2", "1"]


def test_precise_origin_with_no_local_store_delivers_global_nearest_candidates() -> None:
    stores = [_store("1", "候选一店"), _store("2", "候选二店")]
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {"source": "test", "stores": stores},
    }
    destination = {
        "request_kind": "match_location",
        "destination_query": "沈阳市沈河区青年大街109号",
        "destination_precision": "exact_address",
    }
    output = build_planner_fact_output(
        {
            "customer_store_lookup": {
                "status": "no_match",
                "destination_resolution": destination,
                "candidate_search_complete": True,
                "exact_scope_has_store": False,
                "same_city_has_store": False,
                "stores": [],
                "candidate_stores": [],
            },
            "distance_calculate": {
                "status": "ok",
                "destination_resolution": destination,
                "origin": destination["destination_query"],
                "origin_precision": "exact_address",
                "ranking_method": "haversine",
                "ranking_complete": True,
                "ranked_candidate_count": 2,
                "unranked_candidate_count": 0,
                "candidate_store_count": 2,
                "ranked_stores": [
                    {**stores[0], "distance_km": 10.0, "distance_source": "haversine"},
                    {**stores[1], "distance_km": 20.0, "distance_source": "haversine"},
                ],
            },
        },
        state,
    )
    resolution = output["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "send_multiple"
    assert resolution["delivery_store_ids"] == ["1", "2"]


def test_customer_identity_lookup_does_not_send_request_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = PlatformAgentClient(
        SimpleNamespace(
            platform_agent_base_url="https://platform.example",
            platform_agent_token="token",
            platform_agent_request_from="test",
            platform_agent_timeout_seconds=5,
            platform_agent_default_user_id=999,
            platform_agent_default_corp_id="",
            platform_agent_default_wechat="",
        )
    )
    captured: dict[str, object] = {}

    def fake_get(path: str, params: dict[str, object]) -> dict[str, object]:
        captured.update({"path": path, "params": params})
        return {"info": {"id": "customer-1", "customer_add_wechat_id": "relation-1"}}

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.get_customer_info(
        user_id=7294,
        corp_id="corp-1",
        wechat="wechat-1",
        external_userid="external-1",
    )

    assert result["id"] == "customer-1"
    assert captured == {
        "path": "/platform_agent/customer/get_customer_info",
        "params": {
            "corp_id": "corp-1",
            "wechat": "wechat-1",
            "external_userid": "external-1",
        },
    }


def test_missing_authorized_snapshot_store_is_hydrated_instead_of_dropped() -> None:
    service = object.__new__(StoreSnapshotService)
    service.load_snapshot = lambda **_kwargs: {"stores_by_id": {}, "source": "test", "store_count": 0}
    hydrated = {
        "store_id": "68",
        "store_name": "乌鲁木齐店",
        "province": "新疆维吾尔自治区",
        "city": "乌鲁木齐市",
        "district": "沙依巴克区",
        "store_address": "新疆乌鲁木齐市沙依巴克区长江路25号新疆果业大厦",
        "location": "87.60,43.79",
        "store_fact_integrity": "valid",
    }
    service._hydrate_rows = lambda rows, _context: [hydrated] if rows else []

    result = service.stores_for_scope([{"id": "68"}], request_context={"corp_id": "corp"})

    assert result["missing_snapshot_store_ids"] == ["68"]
    assert [store["store_id"] for store in result["stores"]] == ["68"]


def test_store_region_parser_normalizes_autonomous_region_short_name() -> None:
    assert parse_region("新疆乌鲁木齐市沙依巴克区长江路25号") == (
        "新疆维吾尔自治区",
        "乌鲁木齐市",
        "沙依巴克区",
    )


def test_destination_hint_is_parsed_by_model_instead_of_short_circuiting() -> None:
    model = _FakeDestinationModel(
        {
            "request_kind": "match_location",
            "destination_query": "四川省成都市简阳市大华国际",
            "destination_precision": "poi",
            "administrative_context": {
                "province": "四川省",
                "city": "成都市",
                "county_level_city": "简阳市",
            },
            "poi_query": "大华国际",
            "destination_subject": "customer",
            "named_store": "",
            "detail_kind": "none",
            "candidate_interpretations": [],
            "evidence_refs": ["current_message"],
            "superseded_location_refs": [],
            "confidence": "high",
            "needs_clarification": False,
            "geocode_before_clarification": True,
            "reason": "当前消息明确给出简阳和大华国际",
        }
    )

    resolution = asyncio.run(
        resolve_active_store_destination(
            model_client=model,
            state={"content": "简阳大华国际"},
            tool={"destination_hint": "简阳大华国际"},
        )
    )

    assert model.calls == 1
    assert resolution["resolver_status"] == "ok"
    assert resolution["destination_query"] == "四川省成都市简阳市大华国际"
    assert resolution["poi_query"] == "大华国际"


def test_invalid_primary_destination_output_uses_valid_fallback_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _FakeDestinationModel({"request_kind": "invalid"})
    fallback = _ClosableDestinationModel(
        {
            "request_kind": "match_location",
            "destination_query": "北京市",
            "destination_precision": "city",
            "administrative_context": {"province": "北京市", "city": "北京市"},
            "poi_query": "",
            "destination_subject": "customer",
            "named_store": "",
            "detail_kind": "none",
            "candidate_interpretations": [],
            "evidence_refs": ["current_message"],
            "superseded_location_refs": [],
            "confidence": "high",
            "needs_clarification": False,
            "geocode_before_clarification": True,
            "reason": "当前消息明确为北京",
        }
    )
    monkeypatch.setattr(
        "app.services.store_destination_resolver._fallback_only_model_client",
        lambda _client: fallback,
    )

    resolution = asyncio.run(
        resolve_active_store_destination(
            model_client=primary,
            state={"content": "北京"},
            tool={"destination_hint": "北京"},
        )
    )

    assert primary.calls == 1
    assert fallback.calls == 1
    assert resolution["resolver_status"] == "ok_fallback_model"
    assert resolution["destination_query"] == "北京市"


def test_destination_model_road_precision_is_normalized_to_public_contract() -> None:
    model = _FakeDestinationModel(
        {
            "request_kind": "match_location",
            "destination_query": "广州市番禺区市桥大北路",
            "destination_precision": "road",
            "administrative_context": {
                "province": "广东省",
                "city": "广州市",
                "district": "番禺区",
            },
            "poi_query": "市桥大北路",
            "destination_subject": "customer",
            "named_store": "",
            "detail_kind": "none",
            "candidate_interpretations": [],
            "evidence_refs": ["current_message"],
            "superseded_location_refs": [],
            "confidence": "high",
            "needs_clarification": False,
            "geocode_before_clarification": True,
            "reason": "当前消息给出完整道路",
        }
    )

    resolution = asyncio.run(
        resolve_active_store_destination(
            model_client=model,
            state={"content": "广州市番禺区市桥（旧称）大北路"},
            tool={"destination_hint": "广州市番禺区市桥（旧称）大北路"},
        )
    )

    assert resolution["resolver_status"] == "ok"
    assert resolution["destination_precision"] == "poi"


def test_map_timeout_returns_standard_search_incomplete_contract() -> None:
    model = _FakeDestinationModel(
        {
            "request_kind": "match_location",
            "destination_query": "四川省成都市简阳市大华国际",
            "destination_precision": "poi",
            "administrative_context": {
                "province": "四川省",
                "city": "成都市",
                "county_level_city": "简阳市",
            },
            "poi_query": "大华国际",
            "destination_subject": "customer",
            "named_store": "",
            "detail_kind": "none",
            "candidate_interpretations": [],
            "evidence_refs": ["current_message"],
            "superseded_location_refs": [],
            "confidence": "high",
            "needs_clarification": False,
            "geocode_before_clarification": True,
            "reason": "当前消息明确给出简阳和大华国际",
        }
    )
    state = {
        "content": "简阳大华国际",
        "normalized_content": "简阳大华国际",
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "store_count": 1,
            "stores": [_store("101", "成都店")],
        },
    }

    result = asyncio.run(
        action_nodes._resolve_customer_store_workflow(
            {"arguments": {"destination_hint": "简阳大华国际", "purpose": "store_search"}},
            state,
            _FailingGeocodeClient(),
            model_client=model,
        )
    )

    assert result["status"] == "search_incomplete"
    assert result["candidate_search_complete"] is False
    assert result["delivery_store_ids"] == []
    assert result["errors"] == ["TimeoutError: map timeout"]


def test_model_failure_does_not_fall_back_to_guessing_with_map() -> None:
    state = {
        "content": "大华国际",
        "normalized_content": "大华国际",
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {"source": "platform_agent.store_index", "stores": [_store("101", "成都店")]},
    }

    result = asyncio.run(
        action_nodes._resolve_customer_store_workflow(
            {"arguments": {"destination_hint": "大华国际", "purpose": "store_search"}},
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="unused")),
            model_client=None,
        )
    )

    assert result["status"] == "search_incomplete"
    assert result["candidate_search_complete"] is False
    assert result["delivery_store_ids"] == []


def test_expected_jianyang_anchor_excludes_cross_region_map_candidates() -> None:
    selected = action_nodes._first_geocode_candidate(
        [
            {"province": "辽宁省", "city": "沈阳市", "district": "浑南区", "location": "123.4,41.7"},
            {"province": "四川省", "city": "成都市", "district": "简阳市", "location": "104.5,30.4"},
            {"province": "广东省", "city": "中山市", "district": "东区", "location": "113.3,22.5"},
        ],
        expected_admin={"province": "四川省", "city": "成都市", "county_level_city": "简阳市"},
        query="四川省成都市简阳市大华国际",
    )

    assert selected["province"] == "四川省"
    assert selected["city"] == "成都市"
    assert selected["district"] == "简阳市"
    assert selected["candidate_count"] == 1
    assert selected["ambiguous_regions"] is False
    assert len(selected["all_candidate_regions"]) == 3


def test_single_geocode_candidate_region_is_not_treated_as_ambiguous() -> None:
    assert not action_nodes._geocode_ambiguous_regions(
        {
            "candidate_count": 1,
            "candidate_regions": [{"province": "广东省", "city": "广州市", "district": "天河区"}],
        }
    )


def test_different_destination_interpretations_are_ambiguous_only_when_store_results_differ() -> None:
    stores = [
        {**_store("301", "东莞店"), "province": "广东省", "city": "东莞市", "district": "南城街道"},
        {**_store("302", "江阴店"), "province": "江苏省", "city": "无锡市", "district": "江阴市"},
    ]
    results = action_nodes._different_interpretation_store_results(
        [
            (
                {"query": "广东省东莞市东坑镇", "source": "planner_normalized_candidate"},
                {"province": "广东省", "city": "东莞市", "location": "113.9,22.9"},
            ),
            (
                {"query": "江苏省无锡市江阴市东坑", "source": "planner_normalized_candidate"},
                {"province": "江苏省", "city": "无锡市", "district": "江阴市", "location": "120.2,31.9"},
            ),
        ],
        stores,
        "store_search",
    )

    assert [item["store_ids"] for item in results] == [["301"], ["302"]]


def test_ambiguous_interpretation_candidates_are_exposed_without_delivery() -> None:
    output = action_nodes._finalize_store_workflow_result(
        {
            "status": "ambiguous_location",
            "destination_resolution": {"candidate_interpretations": []},
            "customer_store_lookup": {
                "status": "ambiguous_location",
                "ambiguous_candidate_store_ids": ["301", "302"],
                "stores": [],
                "candidate_stores": [],
            },
        },
        {
            "customer_store_knowledge": {
                "source": "platform_agent.store_index",
                "store_count": 2,
                "stores": [],
            }
        },
    )

    assert output["candidate_store_ids"] == ["301", "302"]
    assert output["delivery_store_ids"] == []


def _store(store_id: str, name: str) -> dict[str, object]:
    return {
        "store_id": store_id,
        "store_name": name,
        "store_address": f"四川省成都市锦江区{name}地址",
        "province": "四川省",
        "city": "成都市",
        "district": "锦江区",
        "location": "104.080,30.650",
        "store_fact_integrity": "valid",
    }


def _state_with_previous_delivery(store_ids: list[str]) -> dict[str, object]:
    request_id = "previous-store-delivery"
    search_evidence = {
        "candidate_search_complete": True,
        "destination_fingerprint": "四川省|成都市|锦江区|成都市锦江区",
    }
    return {
        "request_context": {"interface_version": "v3"},
        "history_events": [
            {
                "event_type": "store_address_sent",
                "occurred_at": "2026-09-03T10:00:00+08:00",
                "facts": {
                    "store_id": store_id,
                    "request_id": request_id,
                    "store_search_evidence": search_evidence,
                },
            }
            for store_id in store_ids
        ],
    }


def _lookup_result(stores: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "ok",
        "raw_query": "成都市锦江区",
        "query": "成都市锦江区",
        "province": "四川省",
        "city": "成都市",
        "district": "锦江区",
        "resolved_admin_level": "district",
        "scope_match_level": "district",
        "exact_scope_has_store": True,
        "same_city_has_store": True,
        "stores": stores,
        "destination_resolution": {
            "request_kind": "match_location",
            "destination_query": "成都市锦江区",
            "destination_precision": "district",
            "administrative_context": {
                "province": "四川省",
                "city": "成都市",
                "district": "锦江区",
            },
        },
    }


def test_lookup_returns_single_store_again_when_same_card_was_previously_sent() -> None:
    store = _store("101", "成都锦江店")

    output = build_planner_fact_output(
        {"customer_store_lookup": _lookup_result([store])},
        _state_with_previous_delivery(["101"]),
    )

    resolution = output["structured_facts"]["store_resolution_fact"]
    assert resolution["status"] == "send_single"
    assert resolution["delivery_store_ids"] == ["101"]
    assert "already_delivered_store_ids" not in resolution


def test_lookup_returns_multiple_stores_again_when_same_cards_were_previously_sent() -> None:
    stores = [_store("101", "成都锦江一店"), _store("102", "成都锦江二店")]

    output = build_planner_fact_output(
        {"customer_store_lookup": _lookup_result(stores)},
        _state_with_previous_delivery(["101", "102"]),
    )

    resolution = output["structured_facts"]["store_resolution_fact"]
    assert resolution["status"] == "send_multiple"
    assert resolution["delivery_store_ids"] == ["101", "102"]
    assert "already_delivered_store_ids" not in resolution


def test_distance_ranking_returns_store_again_when_same_card_was_previously_sent() -> None:
    store = _store("101", "成都锦江店")
    destination = _lookup_result([store])["destination_resolution"]

    output = build_planner_fact_output(
        {
            "customer_store_lookup": _lookup_result([store]),
            "distance_calculate": {
                "status": "ok",
                "origin": "成都市锦江区",
                "province": "四川省",
                "city": "成都市",
                "district": "锦江区",
                "resolved_admin_level": "district",
                "scope_match_level": "district",
                "exact_scope_has_store": True,
                "same_city_has_store": True,
                "origin_precision": "exact_address",
                "ranking_complete": True,
                "ranking_method": "haversine",
                "ranked_candidate_count": 1,
                "ranked_stores": [{**store, "distance_km": 1.2, "distance_source": "haversine"}],
                "destination_resolution": destination,
            },
        },
        _state_with_previous_delivery(["101"]),
    )

    resolution = output["structured_facts"]["store_resolution_fact"]
    assert resolution["status"] == "send_single"
    assert resolution["delivery_store_ids"] == ["101"]
    assert resolution["ranking_method"] == "haversine"
    assert "already_delivered_store_ids" not in resolution


def test_full_admin_address_disambiguates_shared_address_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    stores = [
        {
            **_store("189", "重庆巴南店"),
            "store_address": "重庆市巴南区万达中心B座",
            "province": "重庆市",
            "city": "重庆市",
            "district": "巴南区",
        },
        {
            **_store("533", "银川金凤二店"),
            "store_address": "宁夏回族自治区银川市金凤区万达中心B座",
            "province": "宁夏回族自治区",
            "city": "银川市",
            "district": "金凤区",
        },
    ]
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: stores)

    matched = action_nodes._single_explicit_store_text_candidate(
        "重庆市巴南区万达中心B座是你们门店吗",
        stores,
        "store_region",
    )

    assert matched and matched["store_id"] == "189"


def test_shared_address_tail_without_admin_stays_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    stores = [
        {
            **_store("189", "重庆巴南店"),
            "store_address": "重庆市巴南区万达中心B座",
            "province": "重庆市",
            "city": "重庆市",
            "district": "巴南区",
        },
        {
            **_store("533", "银川金凤二店"),
            "store_address": "宁夏回族自治区银川市金凤区万达中心B座",
            "province": "宁夏回族自治区",
            "city": "银川市",
            "district": "金凤区",
        },
    ]
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: stores)

    matched = action_nodes._single_explicit_store_text_candidate(
        "万达中心B座附近可以直接过去吗",
        stores,
        "store_region",
    )

    assert matched is None


def test_shared_address_tail_never_produces_cross_city_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    stores = [
        {
            **_store("189", "重庆巴南店"),
            "store_address": "重庆市巴南区万达中心B座",
            "province": "重庆市",
            "city": "重庆市",
            "district": "巴南区",
        },
        {
            **_store("533", "银川金凤二店"),
            "store_address": "宁夏回族自治区银川市金凤区万达中心B座",
            "province": "宁夏回族自治区",
            "city": "银川市",
            "district": "金凤区",
        },
    ]
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: stores)
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "stores": stores,
        },
    }
    query = "我在万达中心B座附近，可以直接过去吗"

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": query,
                "customer_raw_query": query,
                "purpose": "store_region",
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )
    output = build_planner_fact_output({"customer_store_lookup": lookup}, state)
    resolution = output["structured_facts"]["store_resolution_fact"]

    assert lookup["status"] == "ambiguous_location"
    assert lookup["ambiguous_candidate_store_ids"] == ["189", "533"]
    assert lookup["stores"] == []
    assert resolution["status"] == "ambiguous_location"
    assert resolution["delivery_store_ids"] == []


def test_direct_visit_words_are_not_treated_as_location_fragments() -> None:
    geocode = {
        "province": "山东省",
        "city": "济南市",
        "district": "槐荫区",
        "formatted_address": "山东省济南市槐荫区首诺城市之光西座",
        "location": "116.96,36.65",
    }

    consistency = action_nodes._geocode_query_consistency(
        "我在首诺城市之光西座附近，可以直接过去吗",
        geocode,
    )

    assert consistency["status"] != "conflict"
    assert consistency["fragments"] == ["首诺城市之光西座"]


def test_generic_old_town_without_parent_admin_requires_confirmation() -> None:
    geocode = {"city": "洛阳市", "district": "老城区", "location": "112.47,34.68"}

    assert action_nodes._unanchored_short_place_requires_confirmation(
        query="老城区有店吗",
        state={},
        geocode=geocode,
    )
    assert not action_nodes._unanchored_short_place_requires_confirmation(
        query="洛阳老城区有店吗",
        state={},
        geocode=geocode,
    )


def test_province_plus_generic_hospital_requires_city_confirmation() -> None:
    geocode = {"province": "浙江省", "city": "嘉兴市", "district": "海宁市", "location": "120.44,30.43"}

    assert action_nodes._unanchored_short_place_requires_confirmation(
        query="浙江人民医院附近有吗",
        state={},
        geocode=geocode,
    )


def test_city_plus_generic_hospital_can_anchor_location() -> None:
    geocode = {"province": "浙江省", "city": "杭州市", "district": "拱墅区", "location": "120.15,30.29"}

    assert not action_nodes._unanchored_short_place_requires_confirmation(
        query="杭州人民医院附近有吗",
        state={},
        geocode=geocode,
    )


def test_assistant_store_location_history_strips_speaker_prefix() -> None:
    query = _structured_current_location_query("小贝: 门店位置：深圳龙华店 深圳市龙华区民治街道星河WORLD")

    assert query == "深圳龙华店 深圳市龙华区民治街道星河WORLD"


def test_combined_store_detail_request_requires_model_to_ground_historical_store_location() -> None:
    state = {
        "shared_context": {
            "current_message": {"content": "地图和营业时间发我"},
            "conversation": [
                {
                    "message_ref": "conv_001",
                    "role": "assistant",
                    "content": "小贝: 门店位置：深圳龙华店 深圳市龙华区民治街道星河WORLD",
                }
            ],
        }
    }

    resolution = asyncio.run(
        resolve_active_store_destination(
            model_client=None,
            state=state,
            tool={"query": "地图和营业时间发我"},
        )
    )

    assert resolution["resolver_status"] == "model_unavailable"
    assert resolution["destination_source"] == "recent_assistant_store_reference"
    assert resolution["destination_query"] == "深圳龙华店 深圳市龙华区民治街道星河WORLD"


def test_store_detail_hint_with_named_location_is_not_treated_as_generic() -> None:
    assert _is_generic_store_detail_hint("地图和营业时间发我")
    assert not _is_generic_store_detail_hint("深圳南山店地图和营业时间发我")


def test_explicit_store_match_never_uses_store_outside_customer_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visible = {
        **_store("101", "成都锦江店"),
        "store_address": "四川省成都市锦江区春熙路1号",
    }
    hidden = {
        **_store("999", "深圳南山店"),
        "store_address": "广东省深圳市南山区科技园1号",
        "province": "广东省",
        "city": "深圳市",
        "district": "南山区",
    }
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: [visible, hidden])

    direct_match = action_nodes._single_explicit_store_text_candidate(
        "深圳南山店地址发我",
        [visible],
        "store_detail",
    )
    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "深圳南山店",
                "customer_raw_query": "深圳南山店地址发我",
                "purpose": "store_detail",
            },
            {
                "customer_store_knowledge": {
                    "source": "platform_agent.store_index",
                    "stores": [visible],
                }
            },
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )

    assert direct_match is None
    assert lookup["status"] != "ok"
    assert lookup["stores"] == []


def test_multiple_stores_in_same_district_are_candidates_not_location_ambiguity() -> None:
    stores = [_store("101", "成都锦江一店"), _store("102", "成都锦江二店")]
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "stores": stores,
        },
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "成都市锦江区",
                "customer_raw_query": "成都市锦江区有门店吗",
                "purpose": "store_region",
                "expected_admin": {"province": "四川省", "city": "成都市", "district": "锦江区"},
                "use_resolver_admin_fallback": True,
                "allow_broad_scope_delivery": True,
                "destination_precision": "district",
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )
    resolution = build_planner_fact_output(
        {"customer_store_lookup": lookup},
        state,
    )["structured_facts"]["store_resolution_fact"]

    assert lookup["status"] == "ok"
    assert resolution["status"] == "send_multiple"
    assert resolution["delivery_store_ids"] == ["101", "102"]


def test_v3_broad_scope_caps_delivery_ids_but_keeps_all_candidates() -> None:
    stores = [_store(str(index), f"成都门店{index}") for index in range(1, 6)]
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "stores": stores,
        },
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "成都市",
                "customer_raw_query": "成都市有哪些门店",
                "purpose": "store_region",
                "expected_admin": {"province": "四川省", "city": "成都市"},
                "use_resolver_admin_fallback": True,
                "allow_broad_scope_delivery": True,
                "destination_precision": "city",
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )
    resolution = build_planner_fact_output(
        {"customer_store_lookup": lookup},
        state,
    )["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "send_multiple"
    assert resolution["delivery_store_ids"] == ["1", "2", "3"]
    assert resolution["candidate_store_ids"] == ["1", "2", "3", "4", "5"]


def test_province_scope_with_one_visible_store_emits_that_store() -> None:
    stores = [_store("101", "成都锦江店")]
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "stores": stores,
        },
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "四川省",
                "customer_raw_query": "四川省有门店吗",
                "purpose": "store_region",
                "expected_admin": {"province": "四川省"},
                "use_resolver_admin_fallback": True,
                "allow_broad_scope_delivery": True,
                "destination_precision": "province",
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )
    resolution = build_planner_fact_output(
        {"customer_store_lookup": lookup},
        state,
    )["structured_facts"]["store_resolution_fact"]

    assert resolution["status"] == "send_single"
    assert resolution["delivery_store_ids"] == ["101"]


def test_xinjiang_scope_with_one_visible_store_sends_without_clarification() -> None:
    store = {
        **_store("701", "乌鲁木齐店"),
        "province": "新疆维吾尔自治区",
        "city": "乌鲁木齐市",
        "district": "天山区",
        "store_address": "新疆维吾尔自治区乌鲁木齐市天山区人民路1号",
    }
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {"source": "platform_agent.store_index", "stores": [store]},
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "新疆维吾尔自治区",
                "customer_raw_query": "新疆",
                "purpose": "store_search",
                "expected_admin": {"province": "新疆维吾尔自治区"},
                "destination_precision": "province",
                "allow_broad_scope_delivery": True,
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )
    resolution = build_planner_fact_output({"customer_store_lookup": lookup}, state)["structured_facts"][
        "store_resolution_fact"
    ]

    assert lookup["status"] == "ok"
    assert resolution["status"] == "send_single"
    assert resolution["delivery_store_ids"] == ["701"]


def test_beijing_is_treated_as_city_scope() -> None:
    stores = [
        {
            **_store(store_id, name),
            "province": "北京市",
            "city": "北京市",
            "district": district,
            "store_address": f"北京市{district}测试路1号",
        }
        for store_id, name, district in (
            ("801", "北京朝阳店", "朝阳区"),
            ("802", "北京海淀店", "海淀区"),
        )
    ]
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {"source": "platform_agent.store_index", "stores": stores},
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "北京市",
                "customer_raw_query": "北京",
                "purpose": "store_search",
                "expected_admin": {"province": "北京市", "city": "北京市"},
                "destination_precision": "city",
                "allow_broad_scope_delivery": True,
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )
    resolution = build_planner_fact_output({"customer_store_lookup": lookup}, state)["structured_facts"][
        "store_resolution_fact"
    ]

    assert lookup["status"] == "ok"
    assert resolution["status"] == "send_multiple"
    assert resolution["delivery_store_ids"] == ["801", "802"]


def test_unavailable_customer_scope_never_falls_back_to_global_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_store = {
        **_store("999", "深圳南山店"),
        "store_address": "广东省深圳市南山区科技园1号",
        "province": "广东省",
        "city": "深圳市",
        "district": "南山区",
    }
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: [snapshot_store])
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "customer_store_knowledge_timeout",
            "stores": [],
            "error": "timeout",
        },
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "深圳南山店",
                "customer_raw_query": "深圳南山店地址发我",
                "purpose": "store_detail",
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )
    resolution = build_planner_fact_output(
        {"customer_store_lookup": lookup},
        state,
    )["structured_facts"]["store_resolution_fact"]

    assert lookup["status"] == "store_scope_unavailable"
    assert lookup["stores"] == []
    assert resolution["status"] == "search_incomplete"
    assert resolution["candidate_search_complete"] is False
    assert resolution["delivery_store_ids"] == []


def test_explicit_customer_store_address_beats_conflicting_geocode() -> None:
    store = {
        **_store("520", "成都都江堰店"),
        "store_address": "四川省成都市都江堰市幸福街道莲花社区都江堰大道211号3栋",
        "province": "四川省",
        "city": "成都市",
        "district": "都江堰市",
    }
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "stores": [store],
        },
    }
    query = "四川省成都市都江堰市幸福街道莲花社区都江堰大道211号3栋是你们门店吗"

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": query,
                "customer_raw_query": query,
                "purpose": "store_region",
            },
            state,
            _FakeGeocodeClient(
                {
                    "province": "河南省",
                    "city": "郑州市",
                    "district": "金水区",
                    "formatted_address": "河南省郑州市金水区幸福街道",
                    "location": "113.68,34.78",
                }
            ),
        )
    )
    resolution = build_planner_fact_output(
        {"customer_store_lookup": lookup},
        state,
    )["structured_facts"]["store_resolution_fact"]

    assert lookup["status"] == "ok"
    assert lookup["stores"][0]["store_id"] == "520"
    assert resolution["delivery_store_ids"] == ["520"]


def test_explicit_address_tail_cannot_override_a_different_expected_admin() -> None:
    wrong_region_store = {
        **_store("189", "重庆巴南店"),
        "province": "重庆市",
        "city": "重庆市",
        "district": "巴南区",
        "store_address": "重庆市巴南区万达中心B座",
    }
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "stores": [wrong_region_store],
        },
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "江苏省无锡市江阴市万达中心B座",
                "customer_raw_query": "万达中心B座",
                "purpose": "store_search",
                "expected_admin": {
                    "province": "江苏省",
                    "city": "无锡市",
                    "county_level_city": "江阴市",
                },
                "destination_precision": "poi",
            },
            state,
            SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id="")),
        )
    )

    assert lookup["status"] in {"no_match", "need_location", "need_location_confirmation"}
    assert lookup["stores"] == []
    assert lookup["source"] != "customer_explicit_store_text_reference"


def test_province_plus_generic_landmark_does_not_trust_geocoded_city() -> None:
    store = {
        **_store("601", "嘉兴海宁店"),
        "store_address": "浙江省嘉兴市海宁市海州路1号",
        "province": "浙江省",
        "city": "嘉兴市",
        "district": "海宁市",
    }
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {
            "source": "platform_agent.store_index",
            "stores": [store],
        },
    }
    query = "浙江人民医院附近有门店吗"

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": query,
                "customer_raw_query": query,
                "purpose": "store_region",
                "destination_needs_clarification": True,
            },
            state,
            _FakeGeocodeClient(
                {
                    "province": "浙江省",
                    "city": "嘉兴市",
                    "district": "海宁市",
                    "formatted_address": "浙江省嘉兴市海宁市人民医院",
                    "location": "120.44,30.43",
                }
            ),
        )
    )
    resolution = build_planner_fact_output(
        {"customer_store_lookup": lookup},
        state,
    )["structured_facts"]["store_resolution_fact"]

    assert lookup["status"] == "need_location_confirmation"
    assert lookup["stores"] == []
    assert resolution["delivery_store_ids"] == []


def test_model_normalized_current_address_does_not_require_second_confirmation() -> None:
    store = {
        **_store("901", "深圳龙华店"),
        "province": "广东省",
        "city": "深圳市",
        "district": "龙华区",
        "store_address": "广东省深圳市龙华区民治大道1号",
    }
    state = {
        "request_context": {"interface_version": "v3"},
        "customer_store_knowledge": {"source": "test", "stores": [store]},
    }

    lookup = asyncio.run(
        action_nodes._customer_store_lookup(
            {
                "query": "广东省深圳市龙华区民治大道",
                "customer_raw_query": "深圳市宝安区龙华（现龙华区）民治大道",
                "purpose": "store_search",
                "destination_precision": "poi",
                "destination_needs_clarification": False,
                "confirmed_by_customer": True,
                "expected_admin": {
                    "province": "广东省",
                    "city": "深圳市",
                    "district": "龙华区",
                },
            },
            state,
            _FakeGeocodeClient(
                {
                    "province": "广东省",
                    "city": "深圳市",
                    "district": "龙华区",
                    "formatted_address": "广东省深圳市龙华区民治大道",
                    "location": "114.04,22.62",
                }
            ),
        )
    )

    assert lookup["status"] == "ok"


def test_geocode_query_prefixes_missing_formal_admin_anchors() -> None:
    queries = action_nodes._store_lookup_geocode_queries(
        {
            "customer_raw_query": "北京大兴国际机场航站楼内",
            "expected_admin": {
                "province": "北京市",
                "city": "北京市",
                "district": "大兴区",
            },
        },
        "北京大兴国际机场航站楼内",
    )

    assert queries[0]["source"] == "administratively_constrained_candidate"
    assert queries[0]["query"] == "北京市大兴区北京大兴国际机场航站楼内"
    assert queries[-1]["source"] == "customer_raw"


def test_matching_expected_admin_is_not_rejected_by_inventory_substrings() -> None:
    geocode = {
        "province": "云南省",
        "city": "大理白族自治州",
        "district": "大理市",
        "formatted_address": "云南省大理白族自治州大理市大理古城南门",
        "location": "100.159741,25.685401",
    }
    unrelated_store = {
        **_store("901", "测试古城店"),
        "province": "陕西省",
        "city": "西安市",
        "district": "古城区",
        "store_address": "陕西省西安市古城区测试路1号",
    }

    assert not action_nodes._geocode_explicit_region_conflict(
        "大理古城南门游客中心",
        geocode,
        [unrelated_store],
        expected_admin={
            "province": "云南省",
            "city": "大理白族自治州",
            "district": "大理市",
        },
    )


def test_functional_zone_in_formatted_address_accepts_statutory_district() -> None:
    geocode = {
        "province": "四川省",
        "city": "成都市",
        "district": "武侯区",
        "formatted_address": "四川省成都市武侯区高新区天府软件园C区C7楼",
        "location": "104.071484,30.539677",
    }

    assert not action_nodes._geocode_explicit_region_conflict(
        "成都市高新区天府软件园C区7号楼背面",
        geocode,
        [],
        expected_admin={
            "province": "四川省",
            "city": "成都市",
            "district": "高新区",
        },
    )


def test_geocode_candidates_prefer_matching_place_subject_before_ambiguity() -> None:
    selected = action_nodes._first_geocode_candidate(
        [
            {
                "province": "上海市",
                "city": "上海市",
                "district": "松江区",
                "formatted_address": "上海市松江区松江枢纽(公交站)",
                "location": "121.228327,30.984168",
            },
            {
                "province": "上海市",
                "city": "上海市",
                "district": "长宁区",
                "formatted_address": "上海市长宁区上海虹桥",
                "location": "121.345781,31.194184",
            },
        ],
        expected_admin={"province": "上海市", "city": "上海市"},
        query="上海市上海虹桥国际枢纽中心",
    )

    assert selected["district"] == "长宁区"
    assert selected["candidate_count"] == 1
    assert selected["ambiguous_regions"] is False


def test_equal_named_geocode_candidates_remain_ambiguous() -> None:
    selected = action_nodes._first_geocode_candidate(
        [
            {
                "province": "四川省",
                "city": "成都市",
                "district": "锦江区",
                "formatted_address": "四川省成都市锦江区万达广场",
                "location": "104.1,30.6",
            },
            {
                "province": "四川省",
                "city": "成都市",
                "district": "金牛区",
                "formatted_address": "四川省成都市金牛区万达广场",
                "location": "104.0,30.7",
            },
        ],
        expected_admin={"province": "四川省", "city": "成都市"},
        query="成都市万达广场",
    )

    assert selected["candidate_count"] == 2
    assert selected["ambiguous_regions"] is True


def test_canonical_poi_geocode_overrides_weaker_raw_place_result() -> None:
    assert action_nodes._normalized_geocode_should_override_raw_sentence(
        raw_query="上海虹桥国际枢纽中心",
        normalized_query="上海虹桥站",
        raw_geocode={
            "province": "上海市",
            "city": "上海市",
            "district": "长宁区",
            "formatted_address": "上海市长宁区虹桥",
            "location": "121.412279,31.202338",
        },
        normalized_geocode={
            "province": "上海市",
            "city": "上海市",
            "district": "闵行区",
            "formatted_address": "上海市闵行区上海虹桥站",
            "location": "121.322861,31.194331",
        },
    )


def test_text_admin_parser_does_not_invent_city_from_place_name() -> None:
    dongguan = action_nodes._explicit_admin_from_query_text("东莞南城街道万科城市广场")
    market = action_nodes._explicit_admin_from_query_text("黄桥镇菜市场")

    assert "city" not in dongguan
    assert str(dongguan.get("township") or "").endswith("南城街道")
    assert "city" not in market
    assert str(market.get("township") or "").endswith("黄桥镇")


def test_county_level_city_model_hierarchy_beats_flat_text_parse() -> None:
    merged = action_nodes._merged_expected_admin(
        {
            "expected_admin": {
                "province": "安徽省",
                "city": "芜湖市",
                "county_level_city": "无为市",
                "township": "陡沟镇",
            }
        },
        query="无为市陡沟镇中心卫生院",
        stores=[],
    )

    assert merged == {
        "province": "安徽省",
        "city": "芜湖市",
        "district": "无为市",
    }


def test_street_level_model_value_does_not_become_district_constraint() -> None:
    merged = action_nodes._merged_expected_admin(
        {
            "expected_admin": {
                "province": "广东省",
                "city": "东莞市",
                "district": "南城街道",
            }
        },
        query="东莞南城街道万科城市广场",
        stores=[],
    )

    assert merged == {"province": "广东省", "city": "东莞市"}
