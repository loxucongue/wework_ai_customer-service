from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes import action_nodes
from app.graph.nodes.reply_generation import _appointment_time_failure_recovery
from app.graph.nodes.reply_validation import (
    _validate_parallel_appointment_confirmation_facts,
    _validate_unconfirmed_store_availability_claim,
)


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
        [],
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


def test_appointment_time_failure_recovery_asks_for_store_scope_without_promising_slot() -> None:
    state = {"normalized_content": "下午三点可以过去", "evidence_join": {"structured_facts": {}}}

    messages = _appointment_time_failure_recovery(state)

    assert messages
    content = messages[0]["content"]
    assert "先确认门店和接待安排" in content
    assert "可以过去" not in content
