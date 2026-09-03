from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes import action_nodes
from app.graph.nodes.reply_generation import _appointment_time_failure_recovery, _low_information_input_recovery
from app.graph.nodes.reply_validation import (
    _validate_parallel_appointment_confirmation_facts,
    _validate_unconfirmed_store_availability_claim,
)
from app.services.store_destination_resolver import _structured_current_location_query


def _store(
    store_id: str,
    name: str,
    address: str,
    *,
    province: str = "四川省",
    city: str = "成都市",
    district: str = "都江堰市",
) -> dict[str, str]:
    return {
        "store_id": store_id,
        "store_name": name,
        "name": name,
        "store_address": address,
        "address": address,
        "province": province,
        "city": city,
        "district": district,
    }


def test_unique_explicit_address_tail_beats_broad_geocode(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store("520", "成都都江堰店", "四川省成都市都江堰市幸福街道莲花社区都江堰大道211号3栋")
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: [store])

    matched = action_nodes._single_explicit_store_text_candidate(
        "我在幸福街道莲花社区都江堰大道211号3栋附近，可以直接过去吗",
        [store],
        "store_region",
    )

    assert matched and matched["store_id"] == "520"


def test_generic_place_does_not_become_explicit_store_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    stores = [
        _store("1", "广州天河店", "广东省广州市天河区体育西路101号"),
        _store("2", "广州海珠店", "广东省广州市海珠区万达广场A座", district="海珠区"),
    ]
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: stores)

    matched = action_nodes._single_explicit_store_text_candidate("广东万达附近有店吗", stores, "nearby_candidates")

    assert matched is None


def test_full_admin_address_disambiguates_shared_address_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    chongqing = _store("189", "重庆巴南店", "重庆市巴南区万达中心B座", province="重庆市", city="重庆市", district="巴南区")
    yinchuan = _store(
        "533",
        "银川金凤二店",
        "宁夏回族自治区银川市金凤区万达中心B座",
        province="宁夏回族自治区",
        city="银川市",
        district="金凤区",
    )
    stores = [chongqing, yinchuan]
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: stores)

    matched = action_nodes._single_explicit_store_text_candidate(
        "重庆市巴南区万达中心B座是你们门店吗",
        stores,
        "store_region",
    )

    assert matched and matched["store_id"] == "189"


def test_shared_address_tail_without_admin_stays_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    chongqing = _store("189", "重庆巴南店", "重庆市巴南区万达中心B座", province="重庆市", city="重庆市", district="巴南区")
    yinchuan = _store(
        "533",
        "银川金凤二店",
        "宁夏回族自治区银川市金凤区万达中心B座",
        province="宁夏回族自治区",
        city="银川市",
        district="金凤区",
    )
    stores = [chongqing, yinchuan]
    monkeypatch.setattr(action_nodes, "_snapshot_store_values", lambda: stores)

    matched = action_nodes._single_explicit_store_text_candidate(
        "万达中心B座附近可以直接过去吗",
        stores,
        "store_region",
    )

    assert matched is None


def test_explicit_address_tail_inside_direct_visit_sentence_is_location_fragment() -> None:
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


def test_assistant_store_location_history_strips_speaker_prefix() -> None:
    query = _structured_current_location_query("小贝: 门店位置：深圳龙华店 深圳市龙华区民治街道星河WORLD")

    assert query == "深圳龙华店 深圳市龙华区民治街道星河WORLD"


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


def test_unmatched_store_query_cannot_claim_nearby_store_exists() -> None:
    state = {
        "shared_context": {"current_message": {"content": "广东万达附近有店吗"}},
        "tool_results": {"customer_store_lookup": {"status": "need_location_confirmation", "candidate_stores": []}},
    }

    with pytest.raises(ValueError, match="store_availability_fact_required"):
        _validate_unconfirmed_store_availability_claim(
            [{"type": "text", "content": "有的，这边附近有门店，可以过来看。"}],
            state,
        )


def test_unmatched_store_query_may_ask_for_location_detail() -> None:
    state = {
        "shared_context": {"current_message": {"content": "广东万达附近有店吗"}},
        "tool_results": {"customer_store_lookup": {"status": "need_location_confirmation", "candidate_stores": []}},
    }

    _validate_unconfirmed_store_availability_claim(
        [{"type": "text", "content": "可以帮您查附近是否有门店，但需要先补一下城市、区县或附近地标。"}],
        state,
    )


def test_direct_visit_question_cannot_be_confirmed_without_appointment_fact() -> None:
    state = {"normalized_content": "今天可以做吗", "evidence_join": {"structured_facts": {}}}

    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        _validate_parallel_appointment_confirmation_facts(
            [{"type": "text", "content": "可以的，直接到店就行。"}],
            state,
        )


def test_store_card_does_not_authorize_direct_visit_wording() -> None:
    state = {"normalized_content": "广州有门店吗", "evidence_join": {"structured_facts": {}}}

    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        _validate_parallel_appointment_confirmation_facts(
            [{"type": "text", "content": "有，广州这边能直接看。"}],
            state,
        )


def test_appointment_time_failure_recovery_asks_for_store_scope_without_promising_slot() -> None:
    state = {"normalized_content": "下午三点可以过去", "evidence_join": {"structured_facts": {}}}

    messages = _appointment_time_failure_recovery(state)

    assert messages
    content = messages[0]["content"]
    assert "先确认门店和接待安排" in content
    assert "可以过去" not in content


def test_low_information_input_recovery_only_handles_standalone_symbol_input() -> None:
    assert _low_information_input_recovery({"content": "？？？", "evidence_join": {"structured_facts": {}}})
    assert not _low_information_input_recovery(
        {
            "content": "？？？",
            "conversation_history": ["小贝: 您看哪个门店方便？"],
            "evidence_join": {"structured_facts": {}},
        }
    )
    assert not _low_information_input_recovery({"content": "多少钱", "evidence_join": {"structured_facts": {}}})
