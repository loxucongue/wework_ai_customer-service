from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes import action_nodes
from app.graph.nodes.reply_generation import (
    _low_information_input_recovery,
    _verified_store_delivery_failure_recovery,
    _verified_store_recovery_observability_payload,
)
from app.graph.nodes.reply_validation import (
    _validate_parallel_appointment_confirmation_facts,
    _validate_parallel_business_hours_facts,
    _validate_parallel_registration_confirmation_facts,
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


def test_direct_visit_answer_is_not_an_appointment_completion_gate() -> None:
    state = {"normalized_content": "今天可以做吗", "evidence_join": {"structured_facts": {}}}

    _validate_parallel_appointment_confirmation_facts(
        [{"type": "text", "content": "可以的，直接到店就行。"}],
        state,
    )


def test_store_location_answer_is_not_treated_as_completed_appointment() -> None:
    state = {"normalized_content": "广州有门店吗", "evidence_join": {"structured_facts": {}}}

    _validate_parallel_appointment_confirmation_facts(
        [{"type": "text", "content": "有，广州这边能直接看。"}],
        state,
    )


@pytest.mark.parametrize(
    "reply",
    [
        "操作前后会用原相机保留对比记录，让您能直接看到自己的变化。",
        "这张案例图可以直接看出斑点改善的变化。",
        "视频里能直接看到整个操作过程。",
        "相关资料已经安排老师整理。",
    ],
)
def test_non_appointment_language_is_not_blocked_by_appointment_guard(reply: str) -> None:
    _validate_parallel_appointment_confirmation_facts(
        [{"type": "text", "content": reply}],
        {"normalized_content": "这是一次的效果吗", "evidence_join": {"structured_facts": {}}},
    )


@pytest.mark.parametrize(
    "reply",
    [
        "已预约。",
        "已经帮您约好了。",
        "已经留位。",
        "预约成功。",
        "已经排客。",
        "排客成功。",
    ],
)
def test_appointment_completion_claims_still_require_authoritative_fact(reply: str) -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        _validate_parallel_appointment_confirmation_facts(
            [{"type": "text", "content": reply}],
            {"normalized_content": "明天下午", "evidence_join": {"structured_facts": {}}},
        )


@pytest.mark.parametrize(
    "reply",
    [
        "已安排。",
        "已经帮您安排好了。",
        "明天下午准时等您。",
        "可以直接到店。",
        "今天直接过去就行。",
        "您和姐姐明天早上9点过来就好。",
    ],
)
def test_non_completion_appointment_language_is_not_a_quality_gate(reply: str) -> None:
    _validate_parallel_appointment_confirmation_facts(
        [{"type": "text", "content": reply}],
        {"normalized_content": "明天下午", "evidence_join": {"structured_facts": {}}},
    )


@pytest.mark.parametrize("reply", ["已登记。", "已经帮您登记好了。", "登记完成。"])
def test_registration_completion_claim_still_requires_authoritative_fact(reply: str) -> None:
    with pytest.raises(ValueError, match="registration_confirmation_fact_required"):
        _validate_parallel_registration_confirmation_facts(
            [{"type": "text", "content": reply}],
            {"evidence_join": {"structured_facts": {}}},
        )


@pytest.mark.parametrize(
    "reply",
    [
        "明天下午可以协调，先按这个时间作为到店意向。",
        "我先帮您协调这个时间。",
        "可以预约，您哪天方便？",
    ],
)
def test_future_coordination_and_intent_are_not_completed_appointments(reply: str) -> None:
    state = {"normalized_content": "明天下午", "evidence_join": {"structured_facts": {}}}

    _validate_parallel_appointment_confirmation_facts(
        [{"type": "text", "content": reply}],
        state,
    )
    _validate_parallel_registration_confirmation_facts(
        [{"type": "text", "content": reply}],
        state,
    )


def test_customer_arrival_time_does_not_authorize_store_opening_hours() -> None:
    state = {"normalized_content": "明天早上9点可以吗", "evidence_join": {"structured_facts": {}}}

    with pytest.raises(ValueError, match="business_hours_fact_required"):
        _validate_parallel_business_hours_facts(
            [{"type": "text", "content": "早上9点可以的，我们开门营业的。"}],
            state,
        )


def test_unrelated_store_catalog_hours_do_not_authorize_current_opening_claim() -> None:
    state = {
        "normalized_content": "明天早上9点可以吗",
        "customer_store_knowledge": {
            "stores": [{"store_id": "other-store", "business_hours": "09:00-18:00"}]
        },
        "evidence_join": {"structured_facts": {"store_facts": [], "recommended_store": {}}},
    }

    with pytest.raises(ValueError, match="business_hours_fact_required"):
        _validate_parallel_business_hours_facts(
            [{"type": "text", "content": "我们早上9点开门。"}],
            state,
        )


def test_customer_arrival_time_may_remain_a_tentative_intent_without_hours_fact() -> None:
    state = {"normalized_content": "明天早上9点可以吗", "evidence_join": {"structured_facts": {}}}

    _validate_parallel_business_hours_facts(
        [{"type": "text", "content": "早上9点我先作为您的到店时间意向，具体接待安排以门店确认为准。"}],
        state,
    )


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


def test_verified_store_recovery_delivers_every_canonical_store_card() -> None:
    stores = [
        _store(str(index), f"门店{index}", f"成都市都江堰市地址{index}")
        for index in range(1, 6)
    ]
    resolution = {
        "status": "send_multiple",
        "resolution_status": "send_multiple",
        "delivery_store_ids": [item["store_id"] for item in stores[:3]],
        "visible_candidate_ids": [item["store_id"] for item in stores],
        "candidate_store_ids": [item["store_id"] for item in stores],
        "candidate_search_complete": True,
        "ranking_method": "scope_match",
        "delivery_mode": "send_all_candidates",
    }
    state = {
        "normalized_content": "这个城市有哪些店",
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": resolution,
                "store_facts": stores,
            }
        },
        "evidence_join": {
            "shared_context": {"current_message": {"content": "这个城市有哪些店"}},
            "normalized_tool_facts": {
                "structured_facts": {
                    "store_resolution_fact": resolution,
                    "store_facts": stores,
                }
            },
        },
    }

    messages = _verified_store_delivery_failure_recovery(state)

    assert [item["content"]["store_id"] for item in messages if item["type"] == "store_address"] == [
        "1", "2", "3"
    ]


def test_verified_store_recovery_renders_large_verified_store_list_as_text() -> None:
    summaries = [
        {
            "store_id": str(index),
            "store_name": f"成都门店{index}",
            "district": f"测试区{index}",
            "store_address": f"成都市测试区{index}测试街道{index}号",
        }
        for index in range(1, 9)
    ]
    resolution = {
        "status": "send_multiple",
        "delivery_mode": "text_store_list",
        "delivery_store_ids": [],
        "text_store_summaries": summaries,
    }
    state = {
        "normalized_content": "成都有哪些门店",
        "fact_envelope": {"structured_facts": {"store_resolution_fact": resolution}},
        "evidence_join": {
            "shared_context": {"current_message": {"content": "成都有哪些门店"}},
            "normalized_tool_facts": {"structured_facts": {"store_resolution_fact": resolution}},
        },
    }

    messages = _verified_store_delivery_failure_recovery(state)

    assert len(messages) == 2
    assert {item["type"] for item in messages} == {"text"}
    visible_text = "".join(str(item["content"]) for item in messages)
    assert all(f"成都门店{index}" in visible_text for index in range(1, 9))
    assert all(f"测试区{index}" in visible_text for index in range(1, 9))
    assert all(f"成都市测试区{index}测试街道{index}号" in visible_text for index in range(1, 9))
    assert all(f"{index}. 成都门店{index}" in visible_text for index in range(1, 9))


def test_verified_store_recovery_asks_for_district_for_large_availability_scope() -> None:
    resolution = {
        "status": "need_location",
        "delivery_mode": "clarify_location",
        "city": "重庆市",
        "available_districts": ["南岸区", "巴南区", "渝北区", "九龙坡区", "江津区", "永川区", "渝中区"],
    }
    state = {
        "normalized_content": "重庆有门店吗",
        "fact_envelope": {"structured_facts": {"store_resolution_fact": resolution}},
        "evidence_join": {
            "shared_context": {"current_message": {"content": "重庆有门店吗"}},
            "normalized_tool_facts": {"structured_facts": {"store_resolution_fact": resolution}},
        },
    }

    messages = _verified_store_delivery_failure_recovery(state)

    assert len(messages) == 1
    assert messages[0]["type"] == "text"
    assert "重庆市有门店" in messages[0]["content"]
    assert "南岸区、巴南区、渝北区、九龙坡区、江津区、永川区等区域" in messages[0]["content"]
    assert "您在什么区" in messages[0]["content"]


def test_verified_store_recovery_keeps_policy_observation_but_removes_actions() -> None:
    policy_decision = {
        "primary_task": {"type": "answer_current_question"},
        "realtime_intent": {"type": "store_location"},
        "emotion_decision": {"label": "neutral"},
        "closing_decision": {"action": "none", "customer_state": "evaluating"},
    }
    messages = [{"type": "text", "order": 1, "content": "您补一下所在城市，我帮您查准。"}]

    payload = _verified_store_recovery_observability_payload(
        {
            "raw_json_output": {
                "reply_messages": [{"type": "payment_collection", "content": {"amount": 10}}],
                "action": "payment",
                "commit_actions": [{"type": "registration"}],
                "selected_content_ids": ["unsafe-content"],
                "policy_decision": policy_decision,
            }
        },
        messages,
    )

    assert payload["reply_messages"] == messages
    assert payload["policy_decision"] == policy_decision
    assert payload["action"] == "none"
    assert payload["commit_actions"] == []
    assert payload["selected_content_ids"] == []
