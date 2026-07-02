from __future__ import annotations

import pytest

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.appointment_time_utils import normalize_time_text, summarize_available_slots
from app.graph.nodes.reply_validation import validate_reply_consistency, validated_model_messages
from app.graph.planner.brain_v2 import _current_known_store_for_planner, _should_suppress_planner_memory
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2


def test_contextual_short_message_keeps_planner_history() -> None:
    assert _should_suppress_planner_memory({"normalized_content": "可以"}) is False


def test_need_tools_transition_is_standardized() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "想看效果"},
        {
            "decision": "need_tools",
            "stage": "S1",
            "sub_rule_id": "S1_CASE_REQUEST",
            "conversion_stage": "objection_resolution",
            "customer_type": "effect",
            "main_blocker": "effect",
            "next_step": "solve_blocker",
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈，我帮您看下效果参考"}}],
            "tool_calls": [{"name": "kb_search", "kb_name": "case_studies", "query": "淡斑效果"}],
        },
    )
    assert plan["planner_reply_messages"] == [{"type": "text", "order": 1, "content": {"text": "稍等一下哈"}}]


def test_deposit_push_without_payment_marks_repair_violation() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "报名"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "unknown",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": "好的，现在为您发入口"}}],
            "tool_calls": [],
        },
    )
    assert any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_payment_entry_phrase_without_card_marks_repair_violation() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "怎么交预约金"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "objection_resolution",
            "customer_type": "price",
            "main_blocker": "none",
            "next_step": "solve_blocker",
            "reply_messages": [
                {
                    "type": "text",
                    "content": {
                        "text": _u(
                            r"\u9a6c\u4e0a\u4e3a\u60a8\u53d1\u9001\u62a5\u540d\u5165\u53e3\uff5e"
                        )
                    },
                }
            ],
            "tool_calls": [],
        },
    )
    assert any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_generic_store_question_does_not_inherit_appointment_store() -> None:
    known = _current_known_store_for_planner(
        {
            "normalized_content": _u(r"\u95e8\u5e97\u5728\u54ea\u91cc"),
            "customer_context": {"appointment": {"store_id": "458", "store_name": "store-from-appointment"}},
        }
    )
    assert known == {}


def test_appointment_question_can_use_appointment_store() -> None:
    known = _current_known_store_for_planner(
        {
            "normalized_content": _u(r"\u9884\u7ea6\u8bb0\u5f55\u91cc\u7684\u95e8\u5e97\u660e\u5929\u80fd\u6539\u7ea6\u5417"),
            "customer_context": {"appointment": {"store_id": "458", "store_name": "store-from-appointment"}},
        }
    )
    assert known["store_id"] == "458"
    assert known["source"] == "appointment_context"


def test_current_preferred_store_overrides_old_appointment_store() -> None:
    known = _current_known_store_for_planner(
        {
            "normalized_content": _u(r"\u898110\uff1a30\u5de6\u53f3\u5427"),
            "customer_basic_info": {
                "city": _u(r"\u5e7f\u5dde\u5e02"),
                "preferred_store_id": "562",
                "preferred_store_name": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"),
            },
            "customer_context": {"appointment": {"store_id": "458", "store_name": _u(r"\u897f\u5b89\u5357\u95e8\u5e97")}},
        }
    )
    assert known["store_id"] == "562"
    assert known["source"] == "customer_profile"


def test_recent_store_address_message_sets_current_known_store() -> None:
    known = _current_known_store_for_planner(
        {
            "normalized_content": _u(r"\u53d1\u4e2a\u4f4d\u7f6e"),
            "conversation_history": ["小贝: store_address store_id=467"],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"}
                ]
            },
        }
    )
    assert known["store_id"] == "467"
    assert known["store_name"] == "重庆百星渝中店"


def test_direct_store_address_text_requires_store_lookup() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": _u(r"\u53d1\u4e2a\u4f4d\u7f6e")},
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "reply_messages": [
                {
                    "type": "text",
                    "content": {
                        "text": _u(
                            r"\u91cd\u5e86\u767e\u661f\u6e1d\u4e2d\u5e97\u5730\u5740\u662f\u745e\u5929\u8def10\u53f7\u5609\u9675\u4e2d\u5fc3\uff0c\u60a8\u76f4\u63a5\u5bfc\u822a\u8fc7\u53bb\u5c31\u884c\u3002"
                        )
                    },
                }
            ],
            "tool_calls": [],
        },
    )
    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"][0]["name"] == "customer_store_lookup"


def test_direct_store_parking_text_requires_store_lookup() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": _u(r"\u8fd9\u5bb6\u80fd\u505c\u8f66\u5417")},
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_PARKING",
            "conversion_stage": "store_match",
            "customer_type": "unknown",
            "main_blocker": "logistics",
            "next_step": "confirm_time",
            "reply_messages": [
                {
                    "type": "text",
                    "content": {"text": _u(r"\u8fd9\u5bb6\u697c\u4e0b\u5c31\u6709\u5730\u4e0b\u505c\u8f66\u573a\uff0c\u505c\u8f66\u65b9\u4fbf\u3002")},
                }
            ],
            "tool_calls": [],
        },
    )
    assert any(item.get("missing") == "store_detail_tool_required" for item in plan["tool_policy_violations"])


def test_generic_store_lookup_query_requires_city_or_store_name() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u95e8\u5e97\u5728\u54ea\u91cc"),
            "customer_store_knowledge": {"stores": [{"city": "重庆市", "store_name": "重庆百星渝中店"}]},
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_CITY_ONLY",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "ok"}}],
            "tool_calls": [{"name": "customer_store_lookup", "query": _u(r"\u95e8\u5e97\u5728\u54ea\u91cc"), "purpose": "existence"}],
        },
    )
    assert any(item.get("missing") == "location_query_missing_city_or_region" for item in plan["tool_policy_violations"])


def test_nearby_store_lookup_requires_distance_calculate() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "airport nearby", "customer_store_knowledge": {"stores": [{"city": "Xiamen"}]}},
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "ok"}}],
            "tool_calls": [{"name": "customer_store_lookup", "query": "Xiamen airport", "purpose": "nearby_candidates"}],
        },
    )
    assert any(item.get("missing") == "distance_calculate_required" for item in plan["tool_policy_violations"])


def test_nearby_store_lookup_with_distance_calculate_passes_distance_policy() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "airport nearby", "customer_store_knowledge": {"stores": [{"city": "Xiamen"}]}},
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "ok"}}],
            "tool_calls": [
                {"name": "customer_store_lookup", "query": "Xiamen airport", "purpose": "nearby_candidates"},
                {"name": "distance_calculate", "origin": "Xiamen airport", "candidate_source": "customer_store_lookup"},
            ],
        },
    )
    assert not any(item.get("missing") == "distance_calculate_required" for item in plan["tool_policy_violations"])


def test_available_time_rejects_scope_only_store_id() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "appointment tomorrow afternoon",
            "customer_store_knowledge": {"stores": [{"store_id": "227", "store_name": "store-b"}]},
        },
        {
            "decision": "need_tools",
            "stage": "S3",
            "sub_rule_id": "S3_APPOINTMENT_TIME",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "reply_messages": [{"type": "text", "content": {"text": "ok"}}],
            "tool_calls": [{"name": "available_time", "store_id": "227", "date": "2026-07-02"}],
        },
    )
    assert any(item.get("missing") == "available_time_invalid_store_id" for item in plan["tool_policy_violations"])


def test_available_time_allows_request_store_id() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "appointment tomorrow afternoon",
            "store_id": "227",
        },
        {
            "decision": "need_tools",
            "stage": "S3",
            "sub_rule_id": "S3_APPOINTMENT_TIME",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "reply_messages": [{"type": "text", "content": {"text": "ok"}}],
            "tool_calls": [{"name": "available_time", "store_id": "227", "date": "2026-07-02"}],
        },
    )
    assert not any(item.get("missing") == "available_time_invalid_store_id" for item in plan["tool_policy_violations"])


def test_available_time_allows_recent_store_address_id() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "appointment tomorrow afternoon",
            "conversation_history": ["小贝: store_address store_id=524"],
        },
        {
            "decision": "need_tools",
            "stage": "S3",
            "sub_rule_id": "S3_APPOINTMENT_TIME",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "reply_messages": [{"type": "text", "content": {"text": "ok"}}],
            "tool_calls": [{"name": "available_time", "store_id": "524", "date": "2026-07-02"}],
        },
    )
    assert not any(item.get("missing") == "available_time_invalid_store_id" for item in plan["tool_policy_violations"])


def test_direct_reply_answer_with_next_step_marks_two_text_violation() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "可以带朋友一起去吗"},
        {
            "decision": "direct_reply",
            "stage": "S1",
            "sub_rule_id": "S1_PROJECT_DIRECTION",
            "conversion_stage": "interest_capture",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "ask_intent",
            "reply_messages": [{"type": "text", "content": {"text": "可以带朋友一起到店哦，您方便今天还是明天过来？"}}],
            "tool_calls": [],
        },
    )
    assert any(item.get("missing") == "two_text_required" for item in plan["tool_policy_violations"])


def test_reply_validation_requires_payment_when_promising_entry() -> None:
    with pytest.raises(ValueError, match="payment_collection_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "好的，我重新发您10元预约金入口"}}],
            {"conversion_stage": "deposit_push", "next_step": "send_deposit"},
        )


def test_reply_validation_requires_payment_when_promising_signup_entry() -> None:
    with pytest.raises(ValueError, match="payment_collection_required"):
        validate_reply_consistency(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {
                        "text": _u(r"\u9a6c\u4e0a\u4e3a\u60a8\u53d1\u9001\u62a5\u540d\u5165\u53e3\uff5e")
                    },
                }
            ],
            {"conversion_stage": "objection_resolution", "next_step": "solve_blocker"},
        )


def test_reply_validation_rejects_parking_without_fact() -> None:
    with pytest.raises(ValueError, match="parking_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "这家楼下可以停车，您直接导航过去。"}}],
            {"fact_envelope": {"structured_facts": {}}},
        )


def test_reply_validation_rejects_store_address_without_fact() -> None:
    with pytest.raises(ValueError, match="unsupported_store_address_message"):
        validate_reply_consistency(
            [{"type": "store_address", "order": 1, "content": {"store_id": "467"}}],
            {"fact_envelope": {"structured_facts": {"store_facts": []}}},
        )


def test_reply_validation_allows_store_address_from_store_fact() -> None:
    validate_reply_consistency(
        [{"type": "store_address", "order": 1, "content": {"store_id": "227"}}],
        {"fact_envelope": {"structured_facts": {"store_facts": [{"store_id": "227", "store_name": "厦门思明店"}]}}},
    )


def test_reply_validation_requires_store_card_when_promising_navigation() -> None:
    with pytest.raises(ValueError, match="store_address_message_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "重庆百星渝中店地址我发您，您可以直接点开导航过去。"}}],
            {"fact_envelope": {"structured_facts": {"store_facts": [{"store_id": "467", "store_name": "重庆百星渝中店"}]}}},
        )


def test_reply_validation_requires_store_card_when_customer_requests_location() -> None:
    with pytest.raises(ValueError, match="store_address_message_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "重庆百星渝中店地址是瑞天路10号嘉陵中心A馆。"}}],
            {
                "content": "发个位置",
                "normalized_content": "发个位置",
                "fact_envelope": {"structured_facts": {"store_facts": [{"store_id": "467", "store_name": "重庆百星渝中店"}]}},
            },
        )


def test_reply_validation_requires_store_card_for_combined_address_location_request() -> None:
    with pytest.raises(ValueError, match="store_address_message_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "广州白云三店地址是白云大道北349号。"}}],
            {
                "content": _u(r"\u4f60\u5730\u5740\u548c\u5b9a\u4f4d\u53d1\u7ed9\u6211\u4e0b"),
                "normalized_content": _u(r"\u4f60\u5730\u5740\u548c\u5b9a\u4f4d\u53d1\u7ed9\u6211\u4e0b"),
                "fact_envelope": {"structured_facts": {"store_facts": [{"store_id": "562", "store_name": "广州白云三店"}]}},
            },
        )


def test_reply_validation_rejects_confirmed_appointment_without_fact() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "已为您锁定西安南门店，明天9:30准时等您。"}}],
            {"fact_envelope": {"structured_facts": {"appointment_facts": []}}},
        )


def test_full_width_colon_time_is_detected_as_available_target() -> None:
    assert normalize_time_text(_u(r"\u898110\uff1a30\u5de6\u53f3\u5427")) == "10:30"
    summary = summarize_available_slots(
        {"new": ["09:00", "09:30", "10:00", "10:30", "11:00"]},
        _u(r"\u898110\uff1a30\u5de6\u53f3\u5427"),
    )
    assert summary["target_time"] == "10:30"
    assert summary["target_time_available"] is True
    assert summary["recommended_slot"] == "10:30"


def test_reply_validation_rejects_available_time_claim_without_slots() -> None:
    with pytest.raises(ValueError, match="available_time_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "厦门思明店明天下午有空，您看几点方便？"}}],
            {
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [{"type": "available_time", "store": "12", "date": "2026-06-26", "slots": {}}]
                    }
                }
            },
        )


def test_reply_validation_allows_available_time_claim_with_slots() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": {"text": "厦门思明店明天下午有空，15:30或16:00都可以。"}}],
        {
            "fact_envelope": {
                "structured_facts": {
                    "appointment_facts": [
                        {"type": "available_time", "store": "12", "date": "2026-06-26", "slots": {"afternoon": ["15:30", "16:00"]}}
                    ]
                }
            }
        },
    )


def test_reply_validation_rejects_activity_image_when_case_fact_available() -> None:
    state = {
        "planner_sub_rule_id": "S1_CASE_REQUEST",
        "customer_type": "effect",
        "main_blocker": "effect",
        "business_rules": {"offer": {"activity_intro_image_url": "https://example.com/activity.jpg"}},
        "fact_envelope": {
            "structured_facts": {
                "case_facts": [{"document_id": "doc-1", "image_url": "https://example.com/case.jpg"}]
            }
        },
    }
    with pytest.raises(ValueError, match="case_context_must_not_use_activity_intro_image"):
        validate_reply_consistency(
            [{"type": "image", "order": 1, "content": {"url": "https://example.com/activity.jpg"}}],
            state,
        )


def test_reply_validation_allows_case_image_when_case_fact_available() -> None:
    validate_reply_consistency(
        [{"type": "image", "order": 1, "content": {"url": "https://example.com/case.jpg"}}],
        {
            "planner_sub_rule_id": "S1_CASE_REQUEST",
            "customer_type": "effect",
            "main_blocker": "effect",
            "business_rules": {"offer": {"activity_intro_image_url": "https://example.com/activity.jpg"}},
            "fact_envelope": {
                "structured_facts": {
                    "case_facts": [{"document_id": "doc-1", "image_url": "https://example.com/case.jpg"}]
                }
            },
        },
    )


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def test_reply_validation_rejects_schedule_lookup_promise_without_available_time_fact() -> None:
    messages = validated_model_messages(
        {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {
                        "text": _u(
                            r"\u6211\u5e2e\u60a8\u67e5\u4e00\u4e0b\u53ef\u7ea6\u65f6\u95f4\uff0c\u660e\u5929\u53ef\u4ee5\u8fc7\u6765\u3002"
                        )
                    },
                }
            ]
        },
        {"reply_mode": "sop_sequence"},
    )
    with pytest.raises(ValueError, match="unfinished_appointment_lookup_promise"):
        validate_reply_consistency(
            messages,
            {
                "fact_envelope": {
                    "structured_facts": {
                        "store_facts": [{"store_id": "227", "store_name": "store-a"}],
                        "appointment_facts": [],
                    }
                }
            },
        )


def test_reply_validation_rejects_unfinished_lookup_sync_phrase() -> None:
    messages = validated_model_messages(
        {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {
                        "text": _u(
                            r"\u897f\u5b89\u5357\u95e8\u5e97\u660e\u5929\u4e0b\u5348\u6863\u671f\u8fd8\u5728\u6838\u5bf9\u4e2d\uff0c\u6211\u5e2e\u60a8\u786e\u8ba4\u597d\u9a6c\u4e0a\u540c\u6b65\u3002"
                        )
                    },
                }
            ]
        },
        {"reply_mode": "normal_answer"},
    )
    with pytest.raises(ValueError, match="unfinished_tool_promise_after_tool_execution"):
        validate_reply_consistency(
            messages,
            {
                "planner_decision": "need_tools",
                "fact_envelope": {
                    "structured_facts": {
                        "store_facts": [{"store_id": "458", "store_name": "store-a"}],
                        "appointment_facts": [],
                    }
                },
            },
        )


def test_reply_payload_uses_sop_sequence_for_store_mainline() -> None:
    payload = reply_user_payload_for_model(
        {
            "content": "Xiamen",
            "planner_stage": "S2",
            "planner_sub_rule_id": "S2_STORE_LOCATION",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "fact_envelope": {
                "structured_facts": {
                    "store_facts": [
                        {"store_id": "12", "store_name": "store-a", "city": "Xiamen"},
                        {"store_id": "227", "store_name": "store-b", "city": "Xiamen"},
                    ]
                }
            },
            "sent_message_summary": {},
            "history_events": [],
        }
    )
    assert payload["reply_mode"] == "sop_sequence"
    categories = {item["category"] for item in payload["sop_progress"]["next_candidates"]}
    assert "store_address" in categories
    assert "effect_case" in categories


def test_reply_payload_keeps_parking_as_normal_answer() -> None:
    payload = reply_user_payload_for_model(
        {
            "content": "parking?",
            "planner_stage": "S2",
            "planner_sub_rule_id": "S2_STORE_PARKING",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "fact_envelope": {
                "structured_facts": {
                    "store_facts": [
                        {
                            "store_id": "227",
                            "store_name": "store-b",
                            "parking_name": "parking-lot",
                        }
                    ]
                }
            },
            "sent_message_summary": {},
            "history_events": [],
        }
    )
    assert payload["reply_mode"] == "normal_answer"
