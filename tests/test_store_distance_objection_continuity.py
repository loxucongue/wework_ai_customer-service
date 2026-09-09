from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.chat_runtime import _store_search_evidence_from_state  # noqa: E402
from app.graph.nodes.action_module_outputs import (  # noqa: E402
    _reuse_already_delivered_store_delivery,
)
from app.graph.nodes.current_turn_context import build_current_turn_context  # noqa: E402
from app.graph.nodes.reply_admission import validate_model_led_reply_admission  # noqa: E402
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model  # noqa: E402
from app.graph.nodes.turn_evidence_view import turn_evidence_for_model  # noqa: E402
from app.prompts.reply_synthesizer import (  # noqa: E402
    PARALLEL_REPLY_SYSTEM_PROMPT,
    _compact_reply_status,
    _render_knowledge_evidence,
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


def test_distance_prompt_reframes_without_repeating_negative_objection() -> None:
    assert "回复不得再用“距离、远、路程、折腾、麻烦、不方便”复述顾虑" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "即使候选原文有也不得照搬" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "轻承接后马上转到技术、效果、案例和是否值得" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "正向社会证明可说“专程过来/花一两个小时过来”" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "无真实登记、订单或付款事实，不得说“我已留名额”" in PARALLEL_REPLY_SYSTEM_PROMPT


def test_terminal_distance_objection_rejects_negative_restatement_and_same_city_requery() -> None:
    state = {
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "semantic_route": {
                "current_friction": {
                    "status": "explicit",
                    "checkpoint_code": "cp9",
                    "checkpoint_tag_name": "店太远了，不愿意过来",
                }
            },
            "sales_recall": {
                "sequence_candidates": [
                    {
                        "sequence_id": "45",
                        "sequence_name": "距离异议转价值",
                        "checkpoint_name": "店太远了，不愿意过来",
                    }
                ]
            },
            "shared_context": {
                "authoritative_facts": {
                    "sent_messages": {
                        "latest_store_recommendation": {
                            "store_search_evidence": {
                                "city": "长沙市",
                                "candidate_search_complete": True,
                                "recommendation_final_for_destination": True,
                                "clarification_would_change_result": False,
                                "recommended_store_id": "160",
                            }
                        }
                    }
                }
            },
        },
        "reply_sales_judgment": {
            "next_sales_action": {
                "type": "deliver_value",
                "target_stage": "appointment",
            }
        },
    }

    with pytest.raises(ValueError, match="terminal_store_distance_objection_restates_negative"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": "理解您觉得有点远，来回确实挺折腾的。"}],
            state,
        )

    with pytest.raises(ValueError, match="terminal_store_distance_objection_restates_negative"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": "理解您觉得过来一趟不太方便。"}],
            state,
        )

    with pytest.raises(ValueError, match="terminal_store_distance_objection_restates_negative"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": "不少客户一开始也担心路程。"}],
            state,
        )

    with pytest.raises(ValueError, match="terminal_store_distance_objection_same_city_requery"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": "那没关系呀，您告诉我大概在哪个位置，我再看看。"}],
            state,
        )

    validate_model_led_reply_admission(
        [
            {
                "type": "text",
                "content": "那没关系呀，我们不少客户会专程花一两个小时过来，主要还是看中技术和效果。您平时还有其他方便去的城市吗？",
            }
        ],
        state,
    )


def test_distance_repair_hints_remove_same_city_loop_and_negative_restatement() -> None:
    from app.graph.nodes.reply_nodes import _reply_repair_hint

    restatement = _reply_repair_hint("terminal_store_distance_objection_restates_negative")
    requery = _reply_repair_hint("terminal_store_distance_objection_same_city_requery")

    assert "不得再次出现‘距离、远、路程、折腾、麻烦、不方便’" in restatement
    assert "删除询问地铁站、路口、楼栋、具体位置" in requery


def test_distance_candidate_examples_are_adapted_before_reply_prompt() -> None:
    rendered = _render_knowledge_evidence(
        {
            "sequence_candidates": [
                {
                    "sequence_id": "11",
                    "sequence_name": "到店受阻-店太远了，不愿意过来",
                    "checkpoint_name": "店太远了，不愿意过来",
                    "steps": [
                        {
                            "step_id": "53",
                            "action_name": "共情引导",
                            "objective": "确实距离不算近，来回赶路挺折腾的。",
                        }
                    ],
                }
            ],
            "candidates": [
                {
                    "source_id": "225",
                    "script_id": "225",
                    "script_name": "换位思考",
                    "checkpoint_type": {"name": "到店受阻"},
                    "checkpoint_tag": {"name": "店太远了，不愿意过来"},
                    "paragraphs": [
                        {
                            "paragraph_no": 1,
                            "messages": [
                                {"type": "text", "content": "距离不是问题，来回折腾但效果值得。"},
                                {"type": "video", "url": "https://example.com/effect.mp4"},
                            ],
                        }
                    ],
                }
            ],
        }
    )

    assert "序列 11" in rendered
    assert "话术ID=225" in rendered
    assert "客户会专程到店、看重技术与效果、值得了解" in rendered
    assert "确实距离不算近" not in rendered
    assert "距离不是问题，来回折腾" not in rendered


def test_same_city_store_list_does_not_resend_an_already_delivered_card() -> None:
    resolution = {
        "status": "send_single",
        "outcome": "resolved",
        "city": "长沙市",
        "delivery_store_ids": ["160"],
        "delivery_mode": "store_cards",
        "destination_resolution": {
            "request_kind": "list",
            "administrative_context": {"city": "长沙市"},
        },
    }

    reused = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert reused["status"] == "reuse_confirmed_store"
    assert reused["delivery_store_ids"] == []
    assert reused["already_delivered_store_ids"] == ["160"]
    assert reused["reason"] == "already_delivered_store_same_destination:list"


def test_legacy_visible_store_card_prevents_parking_detail_resend_without_event() -> None:
    store = {
        "store_id": "218",
        "store_name": "武汉江夏店",
        "store_address": "湖北省武汉市江夏区文化大道侨亚国际广场",
    }
    resolution = {
        "status": "send_single",
        "outcome": "resolved",
        "city": "武汉市",
        "delivery_store_ids": ["218"],
        "delivery_mode": "send_recommended",
        "destination_resolution": {
            "request_kind": "store_detail",
            "detail_kind": "parking",
            "administrative_context": {"city": "武汉市"},
        },
    }

    reused = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            # A stale recommendation from another city must not override exact
            # proof that this current store card was already shown.
            "history_events": _history_events(),
            "conversation_history": [
                "用户: 我在武汉",
                "小贝: 门店位置：武汉江夏店\n湖北省武汉市江夏区文化大道侨亚国际广场",
            ],
        },
        resolution,
        available_stores=[store],
    )

    assert reused["status"] == "reuse_confirmed_store"
    assert reused["delivery_store_ids"] == []
    assert reused["already_delivered_store_ids"] == ["218"]
    assert reused["reason"] == "already_delivered_store_non_address_detail:parking"


def test_customer_mention_of_store_name_and_address_is_not_delivery_evidence() -> None:
    store = {
        "store_id": "218",
        "store_name": "武汉江夏店",
        "store_address": "湖北省武汉市江夏区文化大道侨亚国际广场",
    }
    resolution = {
        "status": "send_single",
        "outcome": "resolved",
        "city": "武汉市",
        "delivery_store_ids": ["218"],
        "destination_resolution": {
            "request_kind": "store_detail",
            "detail_kind": "parking",
            "administrative_context": {"city": "武汉市"},
        },
    }

    unchanged = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": [],
            "conversation_history": [
                "用户: 武汉江夏店，湖北省武汉市江夏区文化大道侨亚国际广场，可以停车吗",
            ],
        },
        resolution,
        available_stores=[store],
    )

    assert unchanged == resolution


def test_same_city_store_list_filters_only_previously_delivered_cards() -> None:
    resolution = {
        "status": "send_multiple",
        "outcome": "resolved",
        "city": "长沙市",
        "delivery_store_ids": ["160", "324", "448"],
        "delivery_mode": "send_all_candidates",
        "destination_resolution": {
            "request_kind": "list",
            "administrative_context": {"city": "长沙市"},
        },
    }

    filtered = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
        available_stores=[
            {
                "store_id": store_id,
                "store_name": name,
                "store_address": f"长沙市{district}{name}地址",
                "province": "湖南省",
                "city": "长沙市",
                "district": district,
                "store_fact_integrity": "valid",
            }
            for store_id, name, district in (
                ("160", "长沙岳麓店", "岳麓区"),
                ("324", "长沙雨花店", "雨花区"),
                ("448", "长沙望城店", "望城区"),
            )
        ],
    )

    assert filtered["status"] == "send_multiple"
    assert filtered["delivery_store_ids"] == []
    assert filtered["already_delivered_store_ids"] == ["160"]
    assert filtered["delivery_mode"] == "text_store_list"
    assert len(filtered["text_store_summaries"]) == 3
    assert filtered["reason"] == "already_delivered_store_cards_replaced_with_text_list"


def test_new_city_is_allowed_even_if_a_store_id_was_delivered_before() -> None:
    resolution = {
        "status": "send_single",
        "city": "武汉市",
        "delivery_store_ids": ["160"],
        "destination_resolution": {
            "request_kind": "match_location",
            "administrative_context": {"city": "武汉市"},
        },
    }

    unchanged = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert unchanged == resolution


def test_same_city_finer_location_cannot_replace_a_terminal_recommendation() -> None:
    resolution = {
        "status": "send_single",
        "city": "长沙市",
        "delivery_store_ids": ["999"],
        "destination_resolution": {
            "request_kind": "match_location",
            "destination_precision": "poi",
            "administrative_context": {"city": "长沙市"},
        },
    }

    reused = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert reused["status"] == "reuse_confirmed_store"
    assert reused["delivery_store_ids"] == []
    assert reused["already_delivered_store_ids"] == ["160"]
    assert reused["reason"] == "already_delivered_store_terminal_destination:match_location"


def test_explicit_address_request_can_repeat_an_already_delivered_card() -> None:
    resolution = {
        "status": "send_single",
        "city": "长沙市",
        "delivery_store_ids": ["160"],
        "destination_resolution": {
            "request_kind": "store_detail",
            "detail_kind": "address",
            "administrative_context": {"city": "长沙市"},
        },
    }

    unchanged = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert unchanged == resolution


def test_explicit_address_rerequest_reuses_latest_card_when_no_new_location_exists() -> None:
    resolution = {
        "status": "need_location_confirmation",
        "outcome": "need_clarification",
        "clarification_required": True,
        "clarification_would_change_result": True,
        "delivery_store_ids": [],
        "destination_resolution": {
            "request_kind": "store_detail",
            "detail_kind": "address",
            "needs_clarification": True,
        },
    }

    reused = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert reused["status"] == "send_single"
    assert reused["delivery_store_ids"] == ["160"]
    assert reused["clarification_required"] is False
    assert reused["reason"] == "explicit_address_rerequest_reuses_latest_delivered_store"


def test_explicit_address_rerequest_survives_later_incomplete_distance_result() -> None:
    resolution = {
        "status": "search_incomplete",
        "outcome": "search_incomplete",
        "delivery_store_ids": [],
        "destination_resolution": {
            "request_kind": "store_detail",
            "detail_kind": "address",
        },
    }

    reused = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert reused["status"] == "send_single"
    assert reused["delivery_store_ids"] == ["160"]


def test_degraded_store_detail_without_a_concrete_field_does_not_reuse_stale_anchor() -> None:
    resolution = {
        "status": "search_incomplete",
        "outcome": "search_incomplete",
        "delivery_store_ids": [],
        "destination_resolution": {
            "request_kind": "store_detail",
            "detail_kind": "none",
            "resolver_status": "invalid_model_output",
        },
    }

    unchanged = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert unchanged == resolution


def test_store_detail_survives_incomplete_lookup_without_repeating_card() -> None:
    resolution = {
        "status": "search_incomplete",
        "outcome": "search_incomplete",
        "delivery_store_ids": [],
        "requested_detail_available": True,
        "destination_resolution": {
            "request_kind": "store_detail",
            "detail_kind": "parking",
        },
    }

    reused = _reuse_already_delivered_store_delivery(
        {
            "request_context": {"interface_version": "v3"},
            "history_events": _history_events(),
        },
        resolution,
    )

    assert reused["status"] == "reuse_confirmed_store"
    assert reused["delivery_store_ids"] == []
    assert reused["already_delivered_store_ids"] == ["160"]
    assert reused["requested_detail_available"] is True
    assert reused["reason"] == "store_detail_reuses_latest_delivered_store:parking"
