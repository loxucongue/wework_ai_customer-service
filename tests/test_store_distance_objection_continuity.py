from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.chat_runtime import _store_search_evidence_from_state  # noqa: E402
from app.graph.nodes.current_turn_context import build_current_turn_context  # noqa: E402
from app.graph.nodes.reply_admission import validate_model_led_reply_admission  # noqa: E402
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model  # noqa: E402
from app.graph.nodes.turn_evidence_view import turn_evidence_for_model  # noqa: E402
from app.prompts.reply_synthesizer import (  # noqa: E402
    PARALLEL_REPLY_SYSTEM_PROMPT,
    _compact_reply_status,
)
from app.prompts.v3_semantic_router import (  # noqa: E402
    V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT,
    _current_status_block,
)
from app.services.run_observability import build_v3_run_observability  # noqa: E402
from app.services.v3_semantic_router_service import (  # noqa: E402
    _apply_terminal_store_recommendation_guard,
    _store_tool_plan,
)


def _history_events() -> list[dict[str, object]]:
    return [
        {
            "event_type": "store_address_sent",
            "occurred_at": "2026-09-06T15:04:00+08:00",
            "facts": {
                "store_id": "160",
                "request_id": "location-recommendation",
                "store_search_evidence": {
                    "raw_place": "岳麓区",
                    "normalized_query": "湖南省长沙市岳麓区",
                    "city": "长沙市",
                    "district": "岳麓区",
                    "resolved_admin_level": "district",
                    "candidate_search_complete": True,
                    "recommended_store_id": "160",
                    "delivery_store_ids": ["160"],
                    "ranking_method": "scope_match",
                    "distance_ranking_available": False,
                    "location_evidence": {
                        "confirmation_mode": "model_grounded_administrative_scope"
                    },
                },
            },
        },
        {
            "event_type": "store_address_sent",
            "occurred_at": "2026-09-06T17:47:00+08:00",
            "facts": {
                "store_id": "160",
                "request_id": "parking-detail",
                "store_search_evidence": {
                    "raw_place": "可以停车吗",
                    "normalized_query": "长沙岳麓店",
                    "city": "长沙市",
                    "district": "岳麓区",
                    "candidate_search_complete": True,
                    "recommended_store_id": "160",
                    "delivery_store_ids": ["160"],
                    "ranking_method": "scope_match",
                    "location_evidence": {
                        "confirmation_mode": "model_grounded_named_store"
                    },
                },
            },
        },
    ]


def test_detail_delivery_does_not_replace_latest_location_recommendation() -> None:
    summary = sent_message_summary_for_model({"history_events": _history_events()})

    assert summary["store_address_delivery"]["request_id"] == "parking-detail"
    assert summary["latest_store_recommendation"]["request_id"] == "location-recommendation"
    assert summary["recent_store_search_evidence"]["raw_place"] == "岳麓区"


def test_legacy_recommendation_is_exposed_as_terminal_cross_turn_fact() -> None:
    sent_summary = sent_message_summary_for_model({"history_events": _history_events()})
    context = build_current_turn_context(
        {
            "content": "太远了",
            "normalized_content": "太远了",
            "conversation_history": [],
            "history_events": _history_events(),
        },
        sent_message_summary=sent_summary,
    )
    model_evidence = turn_evidence_for_model(context)
    recommendation = model_evidence["store_evidence"]["latest_store_recommendation"]

    assert recommendation["query"] == "湖南省长沙市岳麓区"
    assert recommendation["candidate_search_complete"] is True
    assert recommendation["recommendation_final_for_destination"] is True
    assert recommendation["clarification_would_change_result"] is False
    assert recommendation["ranking_method"] == "scope_match"


def test_new_store_delivery_persists_recommendation_boundary_fields() -> None:
    state = {
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": {
                    "raw_place": "岳麓区",
                    "city": "长沙市",
                    "district": "岳麓区",
                    "candidate_search_complete": True,
                    "recommendation_final_for_destination": True,
                    "clarification_would_change_result": False,
                    "ranking_method": "scope_match",
                    "destination_resolution": {
                        "request_kind": "match_location",
                        "destination_precision": "district",
                    },
                },
            }
        }
    }

    evidence = _store_search_evidence_from_state(state)

    assert evidence["recommendation_final_for_destination"] is True
    assert evidence["clarification_would_change_result"] is False
    assert evidence["request_kind"] == "match_location"
    assert evidence["destination_precision"] == "district"


def test_router_and_reply_receive_same_terminal_store_boundary() -> None:
    sent_summary = sent_message_summary_for_model({"history_events": _history_events()})
    facts = {"sent_messages": sent_summary}

    router_status = _current_status_block({"authoritative_facts": facts})
    reply_status = _compact_reply_status(facts)
    reply_status_text = str(reply_status)

    for rendered in (router_status, reply_status_text):
        assert "湖南省长沙市岳麓区" in rendered
        assert "scope_match" in rendered
        assert "True" in rendered
    assert "clarification_would_change_result=否" in router_status
    assert "不再追问同城更细地址或承诺更近门店" in reply_status_text

    assert "不再追问同城地铁站、路口、楼栋或更细地址" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不得客观断言门店确实远或近" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "只有客户给出不同城市才重新查店" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "不再问同城地铁站/路口/楼栋" in V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT


def test_run_observability_separates_latest_delivery_and_recommendation() -> None:
    output = build_v3_run_observability(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
            "semantic_route": {"store_query": {"required": False}},
        }
    )
    workflow = output["store_workflow"]

    assert workflow["latest_delivery"]["request_id"] == "parking-detail"
    assert workflow["latest_recommendation"]["query"] == "湖南省长沙市岳麓区"
    assert workflow["latest_recommendation"]["recommendation_final_for_destination"] is True
    assert workflow["latest_recommendation"]["same_city_refinement_useful"] is False


def test_terminal_recommendation_suppresses_unsourced_distance_requery() -> None:
    sent_summary = sent_message_summary_for_model({"history_events": _history_events()})
    route = {
        "current_friction": {"checkpoint_code": "distance", "status": "explicit"},
        "checkpoint": {"primary_code": "distance"},
        "store_query": {
            "required": True,
            "purpose": "distance_compare",
            "location_evidence_refs": ["current_message"],
            "destination_hint": "",
        },
    }

    guarded = _apply_terminal_store_recommendation_guard(
        route,
        shared_context={"authoritative_facts": {"sent_messages": sent_summary}},
    )

    assert guarded["store_query"]["required"] is False
    assert guarded["store_query"]["purpose"] == "existing_store_recommendation_final"
    assert guarded["store_query"]["suppressed_reason"] == "distance_objection_without_new_destination"
    assert route["store_query"]["required"] is True


def test_explicit_new_destination_is_not_suppressed() -> None:
    sent_summary = sent_message_summary_for_model({"history_events": _history_events()})
    route = {
        "current_friction": {"checkpoint_code": "distance", "status": "explicit"},
        "checkpoint": {"primary_code": "distance"},
        "store_query": {
            "required": True,
            "purpose": "store_search",
            "location_evidence_refs": ["current_message"],
            "destination_hint": "武汉市",
        },
    }

    guarded = _apply_terminal_store_recommendation_guard(
        route,
        shared_context={"authoritative_facts": {"sent_messages": sent_summary}},
    )

    assert guarded["store_query"]["required"] is True
    assert guarded["store_query"]["destination_hint"] == "武汉市"
    tool_plan = _store_tool_plan(guarded)
    assert tool_plan["decision"] == "use_tools"
    assert tool_plan["tool_calls"][0]["name"] == "resolve_customer_store"
    assert tool_plan["tool_calls"][0]["arguments"]["destination_hint"] == "武汉市"


def test_model_led_admission_rejects_unexecuted_slot_reservation_claim() -> None:
    state = {
        "evidence_join": {
            "schema_version": "v3_evidence_join_v1",
            "normalized_tool_facts": {"structured_facts": {}},
            "content_candidates": [],
        }
    }

    with pytest.raises(ValueError, match="registration_confirmation_fact_required"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": "您方便的时候再来，我帮您把活动名额留着。"}],
            state,
        )
