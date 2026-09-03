from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes import action_nodes
from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.services.store_destination_resolver import (
    _is_generic_store_detail_hint,
    _structured_current_location_query,
    resolve_active_store_destination,
)


class _FakeGeocodeClient:
    def __init__(self, geocode: dict[str, object]) -> None:
        self.settings = SimpleNamespace(geocode_workflow_id="fake-geocode")
        self._geocode = geocode

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        assert workflow_id == "fake-geocode"
        assert parameters.get("address")
        return {"data": [self._geocode]}


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
            {"query": query, "customer_raw_query": query, "purpose": "store_region"},
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


def test_combined_store_detail_request_reuses_historical_store_location() -> None:
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

    assert resolution["resolver_status"] == "deterministic_destination_evidence"
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


def test_ordinary_province_scope_does_not_emit_city_store_cards() -> None:
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

    assert resolution["status"] in {"need_location", "ambiguous_location"}
    assert resolution["delivery_store_ids"] == []


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
            {"query": query, "customer_raw_query": query, "purpose": "store_region"},
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
            {"query": query, "customer_raw_query": query, "purpose": "store_region"},
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
