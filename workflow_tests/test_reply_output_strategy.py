from __future__ import annotations

import asyncio

import pytest

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.contextual_short_message import short_message_context_for_model
from app.graph.nodes.current_turn_context import build_current_turn_context
from app.graph.nodes.layer_nodes import create_background_context_layer
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.appointment_time_utils import normalize_time_text, summarize_available_slots
from app.graph.nodes.profile_nodes import _profile_conversation_history
from app.graph.nodes.reply_validation import validate_reply_consistency, validated_model_messages
from app.graph.planner.brain_v2 import _current_known_store_for_planner, _planner_payload_for_model, _should_suppress_planner_memory
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.graph.planner.brain_v2_prompts import PLANNER_SYSTEM_PROMPT
from app.schemas import ChatResponse, ReplyMessage
from app.services.workflow_compat import workflow_response_from_chat


def test_contextual_short_message_keeps_planner_history() -> None:
    assert _should_suppress_planner_memory({"normalized_content": "可以"}) is False
    assert _should_suppress_planner_memory({"normalized_content": "人呢"}) is False


def test_short_message_context_marks_renne_as_contextual() -> None:
    context = short_message_context_for_model(content="人呢", conversation_history=[], sent_message_summary={})

    assert context["is_contextual_short_message"] is True
    assert context["needs_recent_context"] is True


def test_current_turn_context_binds_renne_to_deposit_push() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "人呢",
            "conversation_history": [
                "用户: 我在广州这边",
                "小贝: 广州白云三店（7月4日）上午11点名额已帮您预留好～",
                "用户: 10元预约金到底抵扣吗，不能退还10元，现在确认钱包领吗？",
                "小贝: 现在为您生成付款入口～",
                "小贝: payment_collection amount=10",
            ],
            "customer_store_knowledge": {
                "stores": [{"store_id": "562", "store_name": "广州白云三店", "city": "广州市"}]
            },
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
            "customer_context": {
                "appointment_info": {
                    "store_id": "562",
                    "store_name": "广州白云三店",
                    "appointment_time": "2026-07-04 11:00",
                }
            },
        }
    )

    assert context["open_task"] == "deposit_push"
    assert context["binding_source"] == "open_task"
    assert context["last_assistant_action"] == "sent_payment_collection"
    assert context["deposit_state"] == "payment_link_sent"
    assert context["confirmed_store"]["store_name"] == "广州白云三店"
    assert context["confirmed_appointment"]["time"] == "11:00"
    assert "不要重新问城市或项目" in context["reply_anchor"]


def test_current_turn_context_allows_greeting_without_context() -> None:
    context = build_current_turn_context({"normalized_content": "人呢", "conversation_history": []})

    assert context["is_contextual_short_message"] is True
    assert context["open_task"] == "none"
    assert context["binding_source"] == "none"
    assert "reply_anchor" not in context


def test_current_turn_context_store_anchor_prefers_recent_store_over_profile() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "这家地址发我",
            "conversation_history": ["小贝: 给您推荐广州白云三店 门店ID=562，离您更方便一些"],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "562", "store_name": "广州白云三店", "city": "广州市"},
                    {"store_id": "999", "store_name": "广州天河店", "city": "广州市"},
                ]
            },
            "customer_basic_info": {"preferred_store_id": "999", "preferred_store_name": "广州天河店"},
        }
    )

    assert context["open_task"] == "store_followup"
    assert context["current_store_anchor"]["store_id"] == "562"
    assert context["current_store_anchor"]["source"] == "recent_store_address_message"


def test_current_turn_context_marks_ambiguous_recent_stores() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "这家",
            "conversation_history": ["小贝: 广州白云三店和广州天河店都可以看，您选方便的"],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "562", "store_name": "广州白云三店", "city": "广州市"},
                    {"store_id": "999", "store_name": "广州天河店", "city": "广州市"},
                ]
            },
        }
    )

    assert context["current_store_anchor"]["ambiguous"] is True
    assert set(context["current_store_anchor"]["matched_store_names"]) == {"广州白云三店", "广州天河店"}


def test_planner_payload_keeps_context_for_low_information_message() -> None:
    payload = _planner_payload_for_model(
        {
            "normalized_content": "人呢",
            "conversation_history": [f"history-{index}" for index in range(35)],
            "customer_profile": {"decision_stage": "预约推进"},
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
            "customer_context": {"appointment_info": {"store_name": "广州白云三店"}},
            "customer_store_knowledge": {},
        }
    )

    assert payload["conversation_history"] == [f"history-{index}" for index in range(5, 35)]
    assert payload["customer_profile"]["decision_stage"] == "预约推进"
    assert payload["history_events"]
    assert payload["customer_context"]["appointment_info"]["store_name"] == "广州白云三店"
    assert payload["current_turn_context"]["open_task"] == "deposit_push"
    assert payload["current_turn_context"]["binding_source"] == "open_task"
    assert payload["current_turn_context"]["confirmed_store"]["store_name"] == "广州白云三店"


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


def test_deposit_push_without_payment_auto_appends_payment_collection() -> None:
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
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text", "payment_collection"]
    assert plan["planner_reply_messages"][1]["content"]["amount"] == 10
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_payment_entry_phrase_without_card_auto_appends_payment_collection() -> None:
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
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text", "payment_collection"]
    assert plan["planner_reply_messages"][1]["content"] == {"amount": 10, "remark": ""}
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_previous_payment_entry_explanation_does_not_auto_append_payment_collection() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "刚刚那个是什么"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "price",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": "刚刚发的是10元预约金入口，到店抵扣。"}}],
            "tool_calls": [],
        },
    )
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_accompany_deposit_direct_reply_missing_card_is_repaired() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "那我可以带朋友一起过去了解一下吗"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "risk",
            "next_step": "send_deposit",
            "reply_messages": [
                {"type": "text", "content": {"text": "当然可以，朋友一起过来了解完全没问题～"}},
                {
                    "type": "text",
                    "content": {
                        "text": "我马上为您生成10元预约金入口，锁住明天上午11点的名额，到店直接抵扣，不做退10元"
                    },
                },
            ],
            "tool_calls": [],
        },
    )

    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text", "text", "payment_collection"]
    assert plan["planner_reply_messages"][2]["content"] == {"amount": 20, "remark": ""}
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


@pytest.mark.parametrize(
    ("content", "expected_amount"),
    [
        ("报名", 10),
        ("可以带朋友一起过去吗", 20),
        ("带两个朋友一起过去", 30),
        ("我们四个人一起过去", 40),
    ],
)
def test_payment_collection_amount_follows_participant_count(content: str, expected_amount: int) -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": content, "content": content},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": "我给您发预约金入口，锁活动名额。"}}],
            "tool_calls": [],
        },
    )
    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert payment["content"]["amount"] == expected_amount


def test_payment_collection_over_four_people_requires_confirmation() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "带四个朋友一起过去", "content": "带四个朋友一起过去"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": "我给您发预约金入口，锁活动名额。"}}],
            "tool_calls": [],
        },
    )
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]
    assert any(item.get("missing") == "payment_participant_count_confirm_required" for item in plan["tool_policy_violations"])


def test_payment_refund_wording_is_normalized_before_reply_validation() -> None:
    messages = validated_model_messages(
        {
            "reply_messages": [
                {
                    "type": "text",
                    "content": {
                        "text": _u(
                            r"\u4e24\u4f4d\u4e00\u8d77\u62a5\u540d\uff0c\u9884\u7ea6\u91d1\u517120\u5143\uff0c\u5230\u5e97\u53ef\u62b5\u6263\uff0c\u4e0d\u505a\u9000\u8fd820\u5143\u3002"
                        )
                    },
                },
                {"type": "payment_collection", "content": {"amount": 20}},
            ]
        },
        {"normalized_content": _u(r"\u6211\u548c\u670b\u53cb\u4e24\u4e2a\u4eba\u62a5\u540d")},
    )

    text = messages[0]["content"]
    assert _u(r"\u4e0d\u505a\u900010\u5143") in text
    assert _u(r"\u9000\u8fd820\u5143") not in text
    validate_reply_consistency(messages, {"normalized_content": _u(r"\u6211\u548c\u670b\u53cb\u4e24\u4e2a\u4eba\u62a5\u540d")})


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


def test_generic_store_question_does_not_inherit_recent_store_card() -> None:
    known = _current_known_store_for_planner(
        {
            "normalized_content": "你们门店在哪里",
            "conversation_history": ["小贝: 给您推荐广州白云三店 门店ID=562，离您更方便一些"],
            "customer_store_knowledge": {
                "stores": [{"store_id": "562", "store_name": "广州白云三店", "city": "广州市"}]
            },
        }
    )

    assert known == {}


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


def test_generic_store_lookup_must_not_fill_query_from_history_store() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u4f60\u4eec\u95e8\u5e97\u5728\u54ea\u91cc"),
            "conversation_history": [
                _u(r"\u7528\u6237: \u6211\u5728\u5e7f\u5dde\u767d\u4e91\u9644\u8fd1"),
                _u(r"\u5c0f\u8d1d: \u6309\u60a8\u8fd9\u4e2a\u4f4d\u7f6e\uff0c\u4f18\u5148\u770b\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97\u3002"),
            ],
            "customer_store_knowledge": {"stores": [{"city": "广州市", "store_name": "广州白云三店"}]},
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
            "tool_calls": [{"name": "customer_store_lookup", "query": "广州市白云区白云三店", "purpose": "detail"}],
        },
    )
    assert any(item.get("missing") == "store_lookup_query_over_anchors_history" for item in plan["tool_policy_violations"])


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


def test_reply_validation_keeps_handoff_notice_type() -> None:
    messages = validated_model_messages(
        {
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "您有心脏病和高血压，这个要到店先做检测，看下适不适合再安排。"},
                },
                {
                    "type": "human_handoff_notice",
                    "order": 2,
                    "content": {"handoff_reason": "健康高风险：心脏病/高血压，需到店检测后确认适配性"},
                },
            ]
        },
        {},
    )

    assert [item["type"] for item in messages] == ["text", "human_handoff_notice"]
    validate_reply_consistency(messages, {})


def test_reply_validation_normalizes_old_human_handoff_to_notice() -> None:
    messages = validated_model_messages(
        {
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "我先帮您把情况记录清楚，您是在我们哪家门店做的？"}},
                {"type": "human_handoff", "order": 2, "content": {"handoff_reason": "客户要求退款"}},
            ]
        },
        {},
    )

    assert [item["type"] for item in messages] == ["text", "human_handoff_notice"]


def test_reply_validation_rejects_old_handoff_visible_wording() -> None:
    messages = validated_model_messages(
        {
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "这个需要专业同事确认，我帮您同步处理。"}},
                {"type": "human_handoff_notice", "order": 2, "content": {"handoff_reason": "客户要求退款"}},
            ]
        },
        {},
    )

    with pytest.raises(ValueError, match="human_handoff_notice_customer_text_not_resolved"):
        validate_reply_consistency(messages, {})


def test_planner_normalizes_old_handoff_to_notice() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我要退款"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_COMPLAINT_REFUND",
            "conversion_stage": "objection_resolution",
            "customer_type": "risk",
            "main_blocker": "risk",
            "next_step": "solve_blocker",
            "reply_messages": [
                {"type": "text", "content": {"text": "我先帮您把情况记录清楚，您是在我们哪家门店做的？"}},
                {"type": "human_handoff", "content": {"handoff_reason": "客户要求退款"}},
            ],
            "tool_calls": [],
        },
    )

    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text", "human_handoff_notice"]


def test_reply_validation_allows_payment_collection_after_previous_send() -> None:
    validate_reply_consistency(
        [
            {
                "type": "text",
                "order": 1,
                "content": {"text": "可以的，我这边给您发10元预约金入口，先帮您锁活动名额。"},
            },
            {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": "锁活动名额"}},
        ],
        {
            "conversion_stage": "deposit_push",
            "next_step": "send_deposit",
            "sent_message_summary": {"payment_collection_sent": True, "payment_collection_count": 1},
            "history_events": [{"event_type": "payment_collection_sent"}],
        },
    )


def test_reply_validation_rejects_group_payment_text_mismatch() -> None:
    with pytest.raises(ValueError, match="payment_collection_amount_text_mismatch"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": {"text": "可以，我给您发10元预约金入口，先帮您锁活动名额。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 20, "remark": ""}},
            ],
            {"conversion_stage": "deposit_push", "next_step": "send_deposit"},
        )


def test_reply_validation_rejects_group_payment_wrong_total_amount() -> None:
    with pytest.raises(ValueError, match="payment_collection_amount_text_mismatch"):
        validate_reply_consistency(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {
                        "text": _u(
                            r"\u4e24\u4f4d\u670b\u53cb\u4e00\u8d77\u62a5\u540d\uff0c\u5171\u970020\u5143\u9884\u7ea6\u91d1\uff0c\u9a6c\u4e0a\u4e3a\u60a8\u751f\u621020\u5143\u9884\u7ea6\u91d1\u5165\u53e3\u3002"
                        )
                    },
                },
                {"type": "payment_collection", "order": 2, "content": {"amount": 30, "remark": ""}},
            ],
            {
                "normalized_content": _u(r"\u6211\u5e26\u4e24\u4e2a\u670b\u53cb\u4e00\u8d77\u62a5\u540d"),
                "conversion_stage": "deposit_push",
                "next_step": "send_deposit",
            },
        )


def test_reply_validation_allows_group_payment_text_with_per_person_wording() -> None:
    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": {"text": "可以，2位一共20元预约金，每位10元，到店抵扣。"}},
            {"type": "payment_collection", "order": 2, "content": {"amount": 20, "remark": ""}},
        ],
        {"conversion_stage": "deposit_push", "next_step": "send_deposit"},
    )


def test_reply_validation_rejects_payment_collection_when_participants_over_limit() -> None:
    with pytest.raises(ValueError, match="payment_participant_count_confirm_required"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": {"text": "可以，我给您发10元预约金入口，先帮您锁活动名额。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            {
                "normalized_content": "我带四个朋友一起过去",
                "conversion_stage": "deposit_push",
                "next_step": "send_deposit",
            },
        )


def test_reply_validation_allows_over_limit_participant_confirmation_without_payment() -> None:
    validate_reply_consistency(
        [
            {
                "type": "text",
                "order": 1,
                "content": {"text": "可以，多人同行我先帮您确认实际到店人数和名额。您这边一共几位到店？"},
            }
        ],
        {
            "normalized_content": "我带四个朋友一起过去",
            "conversion_stage": "deposit_push",
            "next_step": "send_deposit",
        },
    )


def test_reply_validation_rejects_over_limit_text_promising_entry_without_payment() -> None:
    with pytest.raises(ValueError, match="payment_participant_count_confirm_required"):
        validate_reply_consistency(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "可以，我马上发入口，您确认一下一共几位到店。"},
                }
            ],
            {
                "normalized_content": "我带四个朋友一起过去",
                "conversion_stage": "deposit_push",
                "next_step": "send_deposit",
            },
        )


def test_reply_validation_rejects_over_limit_text_with_high_payment_amount_without_card() -> None:
    with pytest.raises(ValueError, match="payment_participant_count_confirm_required"):
        validate_reply_consistency(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "您带4位朋友一共5人，预约金需要50元，每人10元锁名额。"},
                }
            ],
            {
                "normalized_content": "我带四个朋友一起过去",
                "conversion_stage": "deposit_push",
                "next_step": "send_deposit",
            },
        )


def test_workflow_response_keeps_payment_collection_amount() -> None:
    response = ChatResponse(
        request_id="payment-amount-test",
        reply_messages=[ReplyMessage(type="payment_collection", order=1, content={"amount": 30, "remark": ""})],
    )
    body = workflow_response_from_chat(response)
    assert body["data"]["reply_messages"][0]["content"]["amount"] == 30


def test_workflow_response_outputs_handoff_notice() -> None:
    response = ChatResponse(
        request_id="handoff-notice-test",
        reply_messages=[
            ReplyMessage(type="human_handoff_notice", order=1, content={"handoff_reason": "健康高风险，需到店检测"})
        ],
    )
    body = workflow_response_from_chat(response)
    assert body["data"]["reply_messages"][0]["type"] == "human_handoff_notice"
    assert body["data"]["reply_messages"][0]["content"]["handoff_reason"] == "健康高风险，需到店检测"


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


def test_distance_fact_output_hides_customer_visible_numbers() -> None:
    output = build_planner_fact_output(
        {
            "distance_calculate": {
                "origin": "厦门机场",
                "status": "ok",
                "ranked_stores": [
                    {
                        "store_id": "227",
                        "store_name": "厦门思明店",
                        "address": "厦门市思明区示例路1号",
                        "distance_km": 3.2,
                        "distance_meters": 3200,
                        "duration_seconds": 600,
                        "distance_source": "amap",
                    }
                ],
            }
        },
        {},
    )
    structured = output["fact_envelope"]["structured_facts"]
    assert structured["recommended_store"]["reason"] == "distance_calculate_rank_1"
    assert "distance_km" not in structured["recommended_store"]
    assert "distance_meters" not in structured["store_facts"][0]
    assert "duration_seconds" not in structured["store_facts"][0]


def test_reply_validation_allows_distance_rank_without_numeric_value() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": {"text": "按您这个位置，优先看厦门思明店，这家更近一些。"}}],
        {
            "fact_envelope": {
                "structured_facts": {
                    "store_facts": [{"store_id": "227", "store_name": "厦门思明店"}],
                    "recommended_store": {
                        "store_id": "227",
                        "store_name": "厦门思明店",
                        "reason": "distance_calculate_rank_1",
                    },
                }
            }
        },
    )


@pytest.mark.parametrize(
    "text",
    [
        "按您这个位置，厦门思明店距离您3.2公里。",
        "按您这个位置，厦门思明店过去大概15分钟。",
        "按您这个位置，厦门思明店车程约10分钟。",
    ],
)
def test_reply_validation_rejects_customer_visible_distance_values(text: str) -> None:
    with pytest.raises(ValueError, match="distance_value_not_customer_visible"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": text}}],
            {
                "fact_envelope": {
                    "structured_facts": {
                        "store_facts": [{"store_id": "227", "store_name": "厦门思明店"}],
                        "recommended_store": {
                            "store_id": "227",
                            "store_name": "厦门思明店",
                            "reason": "distance_calculate_rank_1",
                        },
                    }
                }
            },
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


class _TraceLogger:
    class _Span:
        def __init__(self) -> None:
            self.value: dict[str, object] = {"entry": {}}

        def __enter__(self) -> dict[str, object]:
            return self.value

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

    def node(self, state: dict[str, object], name: str, input_snapshot: dict[str, object]) -> "_TraceLogger._Span":
        return self._Span()


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


def test_reply_payload_can_offer_deposit_after_payment_collection_was_sent() -> None:
    payload = reply_user_payload_for_model(
        {
            "content": "我想报名",
            "normalized_content": "我想报名",
            "planner_stage": "S3",
            "planner_sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "price",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "fact_envelope": {"structured_facts": {}},
            "history_events": [{"event_type": "payment_collection_sent", "facts": {}}],
            "conversation_history": [],
        }
    )

    assert payload["sent_message_summary"]["payment_collection_sent"] is True
    categories = {item["category"] for item in payload["sop_progress"]["next_candidates"]}
    assert "deposit_push" in categories


def test_reply_payload_keeps_context_for_low_information_message() -> None:
    payload = reply_user_payload_for_model(
        {
            "normalized_content": "人呢",
            "conversation_history": [f"history-{index}" for index in range(15)],
            "customer_profile": {"decision_stage": "预约推进"},
            "customer_basic_info": {"preferred_store_name": "广州白云三店"},
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
            "customer_context": {"appointment": {"store_name": "广州白云三店"}},
            "fact_envelope": {"structured_facts": {"appointment_facts": [{"store_name": "广州白云三店"}]}},
        }
    )

    assert payload["conversation_history"] == [f"history-{index}" for index in range(15)]
    assert payload["customer_profile"]["decision_stage"] == "预约推进"
    assert payload["customer_basic_info"]["preferred_store_name"] == "广州白云三店"
    assert payload["history_events"]
    assert payload["fact_envelope"]["structured_facts"]["appointment_facts"]
    assert payload["current_turn_context"]["open_task"] == "deposit_push"
    assert payload["current_turn_context"]["binding_source"] == "open_task"
    assert payload["current_turn_context"]["confirmed_store"]["store_name"] == "广州白云三店"


def test_planner_prompt_treats_payment_sent_as_context_not_hard_dedupe() -> None:
    assert "payment_collection_sent 不是硬去重" in PLANNER_SYSTEM_PROMPT
    assert "不要求客户必须说没收到或再发" in PLANNER_SYSTEM_PROMPT
    assert "你还没付/支付失败/刚才没付款" in PLANNER_SYSTEM_PROMPT
    assert "要交钱吗/预约金怎么抵扣/能不能退/是不是额外收费/尾款多少" in PLANNER_SYSTEM_PROMPT
    assert "可以同轮进入 deposit_push 并输出 payment_collection" in PLANNER_SYSTEM_PROMPT
    assert "已经发送过 payment_collection 后，只有客户明确说没收到" not in PLANNER_SYSTEM_PROMPT
    assert "只解释规则，不发 payment_collection" not in PLANNER_SYSTEM_PROMPT


def test_reply_prompt_uses_handoff_notice_and_direct_resolution() -> None:
    from app.prompts.reply_synthesizer import REPLY_SYSTEM_PROMPT

    assert "human_handoff_notice" in REPLY_SYSTEM_PROMPT
    assert "到店先做皮肤检测/专业检测" in REPLY_SYSTEM_PROMPT
    assert "确认是不是在我们门店做的" in REPLY_SYSTEM_PROMPT
    assert "专业同事确认/核对" not in REPLY_SYSTEM_PROMPT


def test_profile_conversation_history_prefers_fetched_50_messages() -> None:
    calls: list[dict[str, object]] = []

    async def fetcher(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "status": "ok",
            "messages": [
                {"direction": "customer" if index % 2 else "staff", "content": f"message-{index}"}
                for index in range(60)
            ],
        }

    async def run() -> tuple[list[str], dict[str, object]]:
        return await _profile_conversation_history(
            {
                "conversation_history": ["old"] * 10,
                "request_context": {
                    "corp_id": "corp",
                    "customer_id": "internal",
                    "external_userid": "external",
                    "user_id": "user",
                    "wechat": "wechat",
                },
            },
            fetcher,
        )

    history, meta = asyncio.run(run())

    assert calls[0]["limit"] == 50
    assert calls[0]["customer_id"] == "external"
    assert len(history) == 50
    assert "message-10" in history[0]
    assert meta["message_count"] == 60
    assert meta["used_message_count"] == 50


def test_profile_conversation_history_falls_back_when_fetch_params_missing() -> None:
    async def fetcher(**kwargs: object) -> dict[str, object]:
        raise AssertionError("fetcher should not be called when required identity is missing")

    async def run() -> tuple[list[str], dict[str, object]]:
        return await _profile_conversation_history(
            {"conversation_history": [f"history-{index}" for index in range(12)], "request_context": {"corp_id": "corp"}},
            fetcher,
        )

    history, meta = asyncio.run(run())

    assert history == [f"history-{index}" for index in range(12)]
    assert meta["status"] == "skipped"
    assert meta["reason"] == "missing_required_fields"


def test_background_context_replaces_request_history_with_platform_30_messages() -> None:
    calls: list[dict[str, object]] = []

    async def fetcher(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "status": "ok",
            "messages": [
                {"direction": "customer" if index % 2 else "staff", "content": {"text": f"message-{index}"}}
                for index in range(35)
            ],
        }

    node = create_background_context_layer(
        trace_logger=_TraceLogger(),
        memory_store=None,
        customer_context_service=None,
        customer_store_knowledge_service=None,
        conversation_fetcher=fetcher,
    )
    output = asyncio.run(
        node(
            {
                "conversation_history": ["request-history"],
                "request_context": {
                    "corp_id": "corp",
                    "customer_id": "internal",
                    "external_userid": "external",
                    "user_id": "user",
                    "wechat": "wechat",
                },
                "trace": [],
            }
        )
    )

    assert calls[0]["limit"] == 30
    assert calls[0]["customer_id"] == "external"
    assert len(output["conversation_history"]) == 30
    assert output["conversation_history"][0] == "用户: message-5"
    assert output["conversation_history"][1] == "小贝: message-6"
    assert output["conversation_fetch"]["status"] == "ok"
    assert output["conversation_fetch"]["used_message_count"] == 30


def test_background_context_keeps_request_history_when_platform_fetch_fails() -> None:
    async def fetcher(**kwargs: object) -> dict[str, object]:
        return {"status": "failed", "error": "timeout", "messages": []}

    node = create_background_context_layer(
        trace_logger=_TraceLogger(),
        memory_store=None,
        customer_context_service=None,
        customer_store_knowledge_service=None,
        conversation_fetcher=fetcher,
    )
    output = asyncio.run(
        node(
            {
                "conversation_history": ["用户: 原始历史"],
                "request_context": {
                    "corp_id": "corp",
                    "customer_id": "internal",
                    "external_userid": "external",
                    "user_id": "user",
                    "wechat": "wechat",
                },
                "trace": [],
            }
        )
    )

    assert output["conversation_history"] == ["用户: 原始历史"]
    assert output["conversation_fetch"]["status"] == "failed"
    assert output["conversation_fetch"]["used_message_count"] == 1


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
