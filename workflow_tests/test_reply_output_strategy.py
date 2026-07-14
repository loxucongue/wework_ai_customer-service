from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.graph.nodes.action_module_outputs import build_planner_fact_output
from app.graph.nodes.action_nodes import _snapshot_stores_for_exact_query
from app.graph.nodes.common import repair_mojibake_text
from app.graph.nodes.contextual_short_message import short_message_context_for_model
from app.graph.nodes.conversation_history_fetch import platform_messages_to_history
from app.graph.nodes.current_turn_context import build_current_turn_context
from app.graph.nodes.layer_nodes import create_background_context_layer
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.nodes.appointment_time_utils import normalize_time_text, summarize_available_slots
from app.graph.nodes.profile_nodes import _profile_conversation_history
from app.graph.nodes.reply_nodes import (
    _ensure_required_handoff_notice,
    _maybe_build_required_payment_collection_fallback,
    _normalize_planner_reply_messages,
    _preserve_planner_store_address_actions,
    _suppress_stale_handoff_notice,
)
from app.graph.nodes.reply_validation import collect_reply_soft_warnings, validate_reply_consistency, validated_model_messages
from app.graph.planner.brain_v2 import _current_known_store_for_planner, _planner_payload_for_model, _should_suppress_planner_memory
from app.graph.planner.brain_v2_normalizer import _clean_scoped_location_query, build_planner_plan_v2
from app.graph.planner.brain_v2_prompts import PLANNER_SYSTEM_PROMPT
from app.schemas import ChatResponse, ReplyMessage
from app.services.workflow_compat import workflow_response_from_chat


def test_contextual_short_message_keeps_planner_history() -> None:
    assert _should_suppress_planner_memory({"normalized_content": "可以"}) is False
    assert _should_suppress_planner_memory({"normalized_content": "人呢"}) is False


def test_recent_exact_snapshot_store_name_hydrates_store_id_for_appointment_turn() -> None:
    store = _current_known_store_for_planner(
        {
            "normalized_content": "明天下午三点可以吗？",
            "conversation_history": [
                "用户: 我想去厦门思明店。",
                "小贝: 好的，厦门思明店可以继续看时间。",
            ],
        }
    )

    assert store["store_id"] == "12"
    assert store["store_name"] == "厦门思明店"
    assert store["source"] == "recent_conversation"


def test_reply_payload_and_postprocess_preserve_planner_scope_verified_store_card() -> None:
    state = {
        "normalized_content": "厦门思明区都有哪些店？",
        "planner_decision": "direct_reply",
        "planner_reply_messages": [
            {"type": "text", "order": 1, "content": {"text": "思明区这边目前有1家门店。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "12"}},
        ],
        "customer_store_knowledge": {
            "source": "platform_scope",
            "stores": [
                {
                    "store_id": "12",
                    "store_name": "厦门思明店",
                    "province": "福建省",
                    "city": "厦门市",
                    "district": "思明区",
                }
            ],
        },
    }

    payload = reply_user_payload_for_model(state)
    assert payload["planner_structured_actions"] == [
        {
            "type": "store_address",
            "content": {"store_id": "12"},
            "source": "planner_scope_verified",
        }
    ]

    messages, preserved = _preserve_planner_store_address_actions(
        [{"type": "text", "order": 1, "content": "思明区这边目前有1家门店。"}],
        state,
    )

    assert preserved is True
    assert messages[-1] == {"type": "store_address", "order": 2, "content": {"store_id": "12"}}


def test_short_message_context_marks_renne_as_contextual() -> None:
    context = short_message_context_for_model(content="人呢", conversation_history=[], sent_message_summary={})

    assert context["is_contextual_short_message"] is True
    assert context["needs_recent_context"] is True


def test_input_mojibake_repair_decodes_utf8_as_gbk_text() -> None:
    original = "门店在哪？"
    garbled = original.encode("utf-8").decode("gbk", errors="replace")

    repaired, info = repair_mojibake_text(garbled)

    assert info["applied"] is True
    assert "门店在哪" in repaired


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

    assert "open_task" not in context
    assert context["binding_source"] == "last_assistant"
    assert "short_message" in context["context_hints"]
    assert "payment_context_available" in context["context_hints"]
    assert "last_assistant_action:sent_payment_collection" in context["context_hints"]
    assert context["last_assistant_action"] == "sent_payment_collection"
    assert context["deposit_state"] == "payment_link_sent"
    assert context["payment_evidence"]["sent_payment_collection"] is True
    assert context["confirmed_store"]["store_name"] == "广州白云三店"
    assert context["confirmed_appointment"]["time"] == "11:00"
    assert "evidence_summary" not in context
    assert "reply_anchor" not in context
    assert context["turn_evidence"]["source_policy"] == "evidence_only_planner_decides_business_action"
    assert context["turn_evidence"]["history_evidence"]["is_short_message"] is True
    assert context["turn_evidence"]["payment_evidence"]["link_sent_evidence"] is True
    assert "reply_anchor" not in context["turn_evidence"]


def test_sent_message_summary_separates_today_and_prior_without_double_counting() -> None:
    events = [
        {
            "event_id": f"payment-{index}",
            "event_type": "payment_collection_sent",
            "event_time": f"2026-07-{index + 1:02d}T02:00:00+00:00",
            "facts": {"amount": 10},
        }
        for index in range(5)
    ]
    events.extend(
        [
            {
                "event_id": "payment-today",
                "event_type": "payment_collection_sent",
                "event_time": "2026-07-13T02:00:00+00:00",
                "facts": {"amount": 20},
            },
            {
                "event_id": "payment-today",
                "event_type": "payment_collection_sent",
                "event_time": "2026-07-13T02:00:00+00:00",
                "facts": {"amount": 20},
            },
        ]
    )
    summary = sent_message_summary_for_model(
        {
            "history_events": events,
            "conversation_history": [
                "小贝: payment_collection amount=20",
                "用户: 我看到了",
                "小贝: 这20元到店抵扣。",
                "用户: 好",
            ],
        },
        now=datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    frequency = summary["payment_collection"]
    assert frequency["today_count"] == 1
    assert frequency["prior_count"] == 5
    assert frequency["total_count"] == 6
    assert frequency["last_amount"] == 20
    assert frequency["customer_turns_since_last_card"] == 2
    assert frequency["count_confidence"] == "high"
    assert summary["payment_collection_count"] == 6


def test_sent_message_summary_keeps_unknown_time_distinct_from_zero_today() -> None:
    summary = sent_message_summary_for_model(
        {
            "history_events": [
                {
                    "event_id": "payment-unknown-time",
                    "event_type": "payment_collection_sent",
                    "facts": {"amount": 10},
                }
            ]
        },
        now=datetime(2026, 7, 13, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    frequency = summary["payment_collection"]
    assert frequency["today_count"] is None
    assert frequency["prior_count"] is None
    assert frequency["total_count"] == 1
    assert frequency["count_confidence"] == "unknown"


def test_sent_message_summary_exposes_authoritative_case_image_delivery_evidence() -> None:
    summary = sent_message_summary_for_model(
        {
            "history_events": [
                {
                    "event_id": "case-image-1",
                    "event_type": "case_image_sent",
                    "created_at": "2026-07-14T02:30:00+00:00",
                    "facts": {
                        "document_ids": ["case-doc-1", "case-doc-2"],
                        "image_urls": ["https://example.com/case-1.jpg", "https://example.com/case-2.jpg"],
                    },
                }
            ]
        }
    )

    assert summary["case_image_sent"] is True
    delivery = summary["case_image_delivery"]
    assert delivery["total_events"] == 1
    assert delivery["last_sent_at"] == "2026-07-14T10:30:00+08:00"
    assert delivery["last_document_count"] == 2
    assert delivery["last_image_count"] == 2
    assert delivery["source"] == "history_events"
    assert delivery["decision_policy"] == "evidence_only_model_decides_case_image_send"


def test_current_turn_context_does_not_label_payment_explanation_as_sent_card() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "嗯",
            "conversation_history": [
                "小贝: payment_collection amount=10",
                "用户: 这个到店抵扣对吧",
                "小贝: 对的，10元到店直接抵扣，不是另外加收。",
            ],
            "history_events": [
                {
                    "event_id": "payment-yesterday",
                    "event_type": "payment_collection_sent",
                    "event_time": "2026-07-12T02:00:00+00:00",
                    "facts": {"amount": 10},
                }
            ],
        }
    )

    assert context["last_assistant_action"] == "text_reply"
    assert "last_assistant_action:text_reply" in context["context_hints"]
    assert context["payment_evidence"]["sent_payment_collection"] is True
    assert context["payment_evidence"]["payment_collection_frequency"]["total_count"] == 1


def test_contextual_short_open_task_recovers_planner_no_reply() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "人呢",
            "content": "人呢",
            "conversation_history": [
                "用户: 我明天上午11点过去",
                "小贝: 厦门思明店明天上午11点名额我先帮您预留，预约金入口也发您了。",
                "小贝: payment_collection amount=10",
            ],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
        },
        {
            "decision": "no_reply",
            "stage": "S4",
            "sub_rule_id": "S4_HESITATION",
            "conversion_stage": "deposit_push",
            "customer_type": "unknown",
            "main_blocker": "trust",
            "next_step": "no_action",
            "reply_messages": [],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "no_reply"
    assert plan["planner_reply_messages"] == []
    assert "current_turn_context_guard" not in plan["reply_strategy"]


def test_current_turn_context_allows_greeting_without_context() -> None:
    context = build_current_turn_context({"normalized_content": "人呢", "conversation_history": []})

    assert context["is_contextual_short_message"] is True
    assert "open_task" not in context
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

    assert "open_task" not in context
    assert "reference_message" in context["context_hints"]
    assert "store_context_available" in context["context_hints"]
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


def test_current_turn_context_ignores_overbroad_store_alias_when_recent_store_is_specific() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "这家地址发我一下",
            "conversation_history": ["用户: 厦门思明附近有门店吗", "小贝: 厦门思明店更方便。"],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "12", "store_name": "厦门思明店", "city": "厦门市"},
                    {"store_id": "126", "store_name": "厦门百星湖里店", "city": "厦门市"},
                ]
            },
        }
    )

    assert "open_task" not in context
    assert context["current_store_anchor"]["store_name"] == "厦门思明店"
    assert context["current_store_anchor"]["source"] == "recent_conversation"
    assert not context["current_store_anchor"].get("ambiguous")


def test_platform_history_sorts_by_timestamp_before_taking_latest() -> None:
    history = platform_messages_to_history(
        [
            {"direction": "customer", "content": "message-3", "created_at": "2026-07-06T10:00:03+08:00"},
            {"direction": "staff", "content": "message-1", "created_at": "2026-07-06T10:00:01+08:00"},
            {"direction": "customer", "content": "message-2", "created_at": "2026-07-06T10:00:02+08:00"},
        ],
        limit=2,
    )

    assert history == ["用户: message-2", "用户: message-3"]


def test_platform_history_preserves_order_when_timestamp_missing() -> None:
    history = platform_messages_to_history(
        [
            {"direction": "customer", "content": "message-1"},
            {"direction": "staff", "content": "message-2"},
            {"direction": "customer", "content": "message-3"},
        ],
        limit=2,
    )

    assert history == ["小贝: message-2", "用户: message-3"]


def test_current_turn_context_post_deposit_time_confirmation_missing_store() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "明天就可以",
            "conversation_history": [
                "用户: 我已经付款了，预约金已付",
                "小贝: 好的，您明天还是后天方便到店检测？",
            ],
            "customer_profile": {"deposit_state": "已支付"},
        }
    )

    assert "open_task" not in context
    assert context["deposit_state"] == "deposit_paid"
    assert "structured_deposit_paid" in context["context_hints"]
    assert "current_message_has_time_reference" in context["context_hints"]
    assert "store_or_region_missing" in context["context_hints"]
    assert context["confirmed_appointment"]["date"] == "明天"
    assert context["missing_slots"] == ["city_or_region", "store"]
    assert "available_time" in context["blocked_actions"]
    assert "payment_collection" in context["blocked_actions"]
    assert "recommended_next_action" not in context


def test_current_turn_context_plain_paid_phrase_binds_next_step_without_card() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "付完然后呢",
            "conversation_history": [
                "用户: 我报名，朋友一起",
                "小贝: 2位一共20元预约金入口发您，每位10元，到店抵扣。",
                "用户: 已经付了",
            ],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 20}}],
        }
    )

    assert context.get("deposit_state") != "deposit_paid"
    assert "open_task" not in context
    assert context["payment_evidence"]["sent_payment_collection"] is True
    assert context["payment_evidence"]["recent_payment_texts"]
    assert "current_message_asks_next_step" in context["context_hints"]
    assert "payment_context_available" in context["context_hints"]
    assert "payment_collection" not in context.get("blocked_actions", [])
    assert "recommended_next_action" not in context


def test_current_turn_context_does_not_treat_unpaid_history_as_paid_deposit() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "明天就可以",
            "conversation_history": [
                "用户: 我还没付预约金，刚才支付失败了",
                "小贝: 那您明天还是后天方便到店检测？",
            ],
        }
    )

    assert context.get("deposit_state") != "deposit_paid"
    assert "open_task" not in context


def test_current_turn_context_payment_retry_does_not_become_paid_deposit() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "没收到，再发一下",
            "conversation_history": [
                "用户: 我报名，朋友一起",
                "小贝: 2位一共20元预约金入口发您，每位10元，到店抵扣。",
                "用户: 已经付了",
            ],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 20}}],
        }
    )

    assert context.get("deposit_state") != "deposit_paid"
    assert "open_task" not in context
    assert context["payment_evidence"]["sent_payment_collection"] is True
    assert "payment_context_available" in context["context_hints"]


def test_current_turn_context_history_health_risk_is_advisory_for_short_message() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "你好",
            "conversation_history": [
                "用户: 我有心脏病和高血压，这个能做吗",
                "小贝: 您有心脏病和高血压，这个要到店先做检测，让门店专业人员看下适不适合再安排。",
                '小贝: human_handoff_notice {"handoff_reason":"健康高风险"}',
            ],
        }
    )

    assert "open_task" not in context
    assert context["resolved_slots"]["health_check"] == "advisory"
    assert "payment_collection" not in context.get("blocked_actions", [])
    assert "recommended_next_action" not in context


def test_current_turn_context_old_health_risk_does_not_shadow_store_question() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "门店在哪",
            "conversation_history": [
                "用户: 我有心脏病和高血压，这个能做吗",
                "小贝: 这个要到店先做检测，确认适合再安排。",
                '小贝: human_handoff_notice {"handoff_reason":"健康高风险"}',
                "用户: 好的",
                "小贝: 您想看哪个城市的门店？",
                "用户: 我在厦门",
                "小贝: 厦门有湖里和思明门店，您在哪个区方便？",
            ],
        }
    )

    assert context.get("resolved_slots", {}).get("health_check") != "advisory"
    assert "payment_collection" not in context.get("blocked_actions", [])
    assert "recommended_next_action" not in context


def test_current_turn_context_current_health_risk_still_hard_blocks_payment() -> None:
    context = build_current_turn_context(
        {
            "normalized_content": "我有心脏病和高血压，明天下午可以到店检测吗",
            "conversation_history": ["小贝: 您明天上午还是下午方便？"],
        }
    )

    assert "open_task" not in context
    assert "current_hard_health_risk" in context["context_hints"]
    assert context["resolved_slots"]["health_check"] == "required"
    assert "payment_collection" in context["blocked_actions"]
    assert "recommended_next_action" not in context


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

    assert payload["conversation_history"] == [f"history-{index}" for index in range(15, 35)]
    assert payload["customer_profile"]["decision_stage"] == "预约推进"
    assert payload["history_events"]
    assert payload["customer_context"]["appointment_info"]["store_name"] == "广州白云三店"
    assert "open_task" not in payload["current_turn_context"]
    assert payload["current_turn_context"]["binding_source"] in {"last_assistant", "none"}
    assert "payment_context_available" in payload["current_turn_context"]["context_hints"]
    assert payload["current_turn_context"]["confirmed_store"]["store_name"] == "广州白云三店"
    assert payload["turn_evidence"]["source_policy"] == "evidence_only_planner_decides_business_action"
    assert payload["turn_evidence"]["store_evidence"]["candidates"]


def test_planner_payload_does_not_send_long_sales_strategy_to_planner() -> None:
    payload = _planner_payload_for_model(
        {
            "normalized_content": "明天可以",
            "conversation_history": [],
            "customer_profile": {
                "decision_stage": "预约推进",
                "deposit_state": "已支付",
                "main_objection": "担心效果",
                "next_sales_strategy": "很长的销售策略不应该进 planner",
            },
        }
    )

    assert payload["customer_profile"]["decision_stage"] == "预约推进"
    assert payload["customer_profile"]["deposit_state"] == "已支付"
    assert "next_sales_strategy" not in payload["customer_profile"]


def test_reply_payload_exposes_turn_evidence_without_dropping_current_turn_context() -> None:
    payload = reply_user_payload_for_model(
        {
            "normalized_content": "这家地址发我",
            "conversation_history": ["小贝: 给您推荐广州白云三店 门店ID=562"],
            "customer_store_knowledge": {"stores": [{"store_id": "562", "store_name": "广州白云三店"}]},
        }
    )

    assert payload["current_turn_context"]["current_store_anchor"]["store_id"] == "562"
    assert "open_task" not in payload["current_turn_context"]
    assert "evidence_summary" not in payload["current_turn_context"]
    assert payload["current_turn_context"]["source_policy"] == "reply_evidence_only_planner_decides_business_action"
    assert payload["turn_evidence"]["store_evidence"]["unique_recent_store"]["store_id"] == "562"
    assert payload["turn_evidence"]["source_policy"] == "evidence_only_planner_decides_business_action"


def test_planner_tool_policy_flags_post_deposit_time_confirmation_missing_store() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "明天就可以",
            "conversation_history": [
                "用户: 我已经付款了，预约金已付",
                "小贝: 好的，您明天还是后天方便到店检测？",
            ],
            "customer_profile": {"deposit_state": "已支付"},
        },
        {
            "decision": "need_tools",
            "stage": "S3",
            "sub_rule_id": "S3_APPOINTMENT_TIME",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "reply_messages": [{"type": "text", "content": {"text": "我帮您查一下明天档期"}}],
            "tool_calls": [{"name": "available_time", "store_id": "", "date": "2026-07-07"}],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert all(item["type"] != "payment_collection" for item in plan["planner_reply_messages"])
    assert any(item.get("subtype") == "available_time" for item in plan["tool_policy_violations"])


def test_planner_removes_payment_collection_after_customer_says_paid() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "付完然后呢",
            "conversation_history": [
                "用户: 我报名，朋友一起",
                "小贝: 2位一共20元预约金入口发您，每位10元，到店抵扣。",
                "用户: 已经付了",
            ],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 20}}],
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "time",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "customer_claimed_paid",
            "reply_messages": [
                {"type": "text", "content": {"text": "收到，付完就给您锁名额了。"}},
                {"type": "payment_collection", "content": {"amount": 20, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["conversion_stage"] == "time_confirm"
    assert plan["next_step"] == "confirm_time"
    assert plan["payment_state"] == "customer_claimed_paid"
    assert plan["payment_decision"]["action"] == "after_paid_next_step"
    assert all(item["type"] != "payment_collection" for item in plan["planner_reply_messages"])
    assert plan["reply_strategy"]["payment_action_guard"] == "payment_card_removed_by_payment_action"


def test_planner_legacy_card_payload_becomes_send_now_decision_when_model_omits_state() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "付完然后呢",
            "conversation_history": [
                "用户: 我报名，朋友一起",
                "小贝: 2位一共20元预约金入口发您，每位10元，到店抵扣。",
                "用户: 已经付了",
            ],
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "time",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "unknown",
            "reply_messages": [
                {"type": "text", "content": {"text": "收到，接下来帮您安排到店时间。"}},
                {"type": "payment_collection", "content": {"amount": 20, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["payment_state"] == "needs_payment"
    assert plan["payment_action"] == "send_now"
    assert plan["payment_decision"]["action"] == "send_now"
    assert plan["payment_decision"]["amount"] == 20
    assert plan["conversion_stage"] == "deposit_push"
    assert plan["next_step"] == "send_deposit"
    assert any(item["type"] == "payment_collection" for item in plan["planner_reply_messages"])


def test_planner_removes_payment_collection_after_structured_paid_state() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "下一步呢",
            "current_turn_context": {
                "deposit_state": "deposit_paid",
                "turn_evidence": {
                    "payment_evidence": {
                        "structured_payment_state": "deposit_paid",
                        "source_policy": "evidence_only_planner_decides_payment_state",
                    }
                },
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "time",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "unknown",
            "reply_messages": [
                {"type": "text", "content": {"text": "收到，接下来帮您安排到店时间。"}},
                {"type": "payment_collection", "content": {"amount": 20, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["payment_state"] == "customer_claimed_paid"
    assert plan["payment_decision"]["action"] == "after_paid_next_step"
    assert plan["conversion_stage"] == "time_confirm"
    assert plan["next_step"] == "confirm_time"
    assert all(item["type"] != "payment_collection" for item in plan["planner_reply_messages"])


def test_planner_does_not_auto_append_payment_collection_after_customer_says_paid() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "已经付了，下一步呢",
            "conversation_history": [
                "小贝: 10元预约金入口发您，用于锁活动名额，到店抵扣。",
                "小贝: payment_collection amount=10",
            ],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "time",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "customer_claimed_paid",
            "reply_messages": [{"type": "text", "content": {"text": "我继续帮您处理后续安排。"}}],
            "tool_calls": [],
        },
    )

    assert all(item["type"] != "payment_collection" for item in plan["planner_reply_messages"])
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_planner_keeps_payment_collection_when_customer_requests_resend() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "没收到，再发一下",
            "conversation_history": [
                "用户: 我报名，朋友一起",
                "小贝: 2位一共20元预约金入口发您，每位10元，到店抵扣。",
                "用户: 已经付了",
            ],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 20}}],
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "high_intent",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "resend_requested",
            "reply_messages": [
                {"type": "text", "content": {"text": "我再给您发一次，2位一共20元预约金，每位10元，到店抵扣。"}},
                {"type": "payment_collection", "content": {"amount": 20, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert any(item["type"] == "payment_collection" for item in plan["planner_reply_messages"])


def test_planner_payment_action_offer_resend_removes_same_turn_payment_card() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "你好",
            "conversation_history": [
                "用户: 我报名",
                "小贝: 我把10元预约金入口发您，到店抵扣，未做或不满意可退。",
                "小贝: payment_collection amount=10",
            ],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 10}}],
        },
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_DEPOSIT_FOLLOWUP",
            "conversion_stage": "deposit_push",
            "customer_type": "high_intent",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "link_sent",
            "payment_action": "offer_resend",
            "reply_messages": [
                {"type": "text", "content": {"text": "在的，我在。您是继续确认到店安排，还是需要我继续帮您处理？"}},
                {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["payment_action"] == "offer_resend"
    assert plan["payment_decision"]["action"] == "none"
    assert plan["conversion_stage"] == "time_confirm"
    assert plan["next_step"] == "confirm_time"
    assert all(item["type"] != "payment_collection" for item in plan["planner_reply_messages"])
    assert plan["reply_strategy"]["payment_action_guard"] == "payment_card_removed_by_payment_action"
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_planner_payment_action_send_now_auto_appends_payment_card() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "发吧，我现在付"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "high_intent",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "needs_payment",
            "payment_action": "send_now",
            "reply_messages": [{"type": "text", "content": {"text": "好的，我给您发10元预约金入口，到店抵扣，未做或不满意可退。"}}],
            "tool_calls": [],
        },
    )

    assert any(item["type"] == "payment_collection" for item in plan["planner_reply_messages"])
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])


def test_planner_send_now_is_not_downgraded_by_short_message_guard() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "你好",
            "conversation_history": [
                "用户: 我报名",
                "小贝: 我把10元预约金入口发您，到店抵扣，未做或不满意可退。",
                "小贝: payment_collection amount=10",
            ],
            "current_turn_context": {
                "is_contextual_short_message": True,
                "payment_evidence": {
                    "sent_payment_collection": True,
                    "recent_payment_texts": ["小贝: payment_collection amount=10"],
                },
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "unknown",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "link_sent",
            "payment_action": "send_now",
            "reply_messages": [
                {"type": "text", "content": {"text": "你好，我把10元预约金入口再发您。"}},
                {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["payment_action"] == "send_now"
    assert plan["conversion_stage"] == "deposit_push"
    assert plan["next_step"] == "send_deposit"
    assert any(item["type"] == "payment_collection" for item in plan["planner_reply_messages"])
    assert not any(item.get("missing") == "payment_collection_blocked_by_payment_action" for item in plan["tool_policy_violations"])


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


def test_effect_question_direct_reply_is_not_forced_to_case_tool_by_normalizer() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "会不会没效果"},
        {
            "decision": "direct_reply",
            "stage": "S1",
            "sub_rule_id": "S1_PROJECT_DIRECTION",
            "conversion_stage": "objection_resolution",
            "customer_type": "effect",
            "main_blocker": "effect",
            "next_step": "solve_blocker",
            "reply_messages": [{"type": "text", "content": {"text": "淡斑效果因人而异，到店检测后看。"}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["required_tools"] == [{"name": "no_tool", "purpose": "Planner did not request external tools"}]
    assert plan["planner_reply_messages"] == [
        {"type": "text", "order": 1, "content": {"text": "淡斑效果因人而异，到店检测后看。"}}
    ]


def test_specific_spot_can_do_question_without_effect_marker_is_not_forced_by_normalizer() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "雀斑能不能做"},
        {
            "decision": "direct_reply",
            "stage": "S1",
            "sub_rule_id": "S1_PROJECT_DIRECTION",
            "conversion_stage": "interest_capture",
            "customer_type": "unknown",
            "main_blocker": "none",
            "next_step": "ask_intent",
            "reply_messages": [{"type": "text", "content": {"text": "雀斑可以做。"}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["required_tools"] == [{"name": "no_tool", "purpose": "Planner did not request external tools"}]


def test_planner_effect_marker_without_case_tool_stays_model_decision() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "雀斑能不能做"},
        {
            "decision": "direct_reply",
            "stage": "S1",
            "sub_rule_id": "S1_PROJECT_EFFECT",
            "conversion_stage": "objection_resolution",
            "customer_type": "effect",
            "main_blocker": "effect",
            "next_step": "solve_blocker",
            "reply_messages": [{"type": "text", "content": {"text": "雀斑可以做。"}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["required_tools"] == [{"name": "no_tool", "purpose": "Planner did not request external tools"}]


def test_deposit_push_without_payment_action_does_not_auto_append_payment_collection() -> None:
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
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]
    assert any(item.get("missing") == "payment_decision_required" for item in plan["tool_policy_violations"])


def test_payment_entry_phrase_without_structured_action_stays_model_semantics() -> None:
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
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]
    assert not any(item.get("missing") == "payment_decision_required" for item in plan["tool_policy_violations"])


def test_future_payment_entry_after_store_confirmation_is_not_current_send_action() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我想报名"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "conversion_stage": "store_match",
            "next_step": "lookup_store",
            "payment_action": "confirm_next_step",
            "payment_decision": {"action": "none"},
            "reply_messages": [
                {"type": "text", "content": {"text": "先确认您想去的门店，确认后我再给您安排报名入口。"}}
            ],
            "tool_calls": [],
        },
    )

    assert not any(item.get("missing") == "payment_decision_required" for item in plan["tool_policy_violations"])


def test_resending_store_card_is_not_treated_as_payment_entry() -> None:
    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": {"text": "银川兴庆店地址我再发您卡片。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "369"}},
        ],
        {
            "payment_action": "confirm_next_step",
            "payment_decision": {"action": "after_paid_next_step"},
            "fact_envelope": {
                "structured_facts": {
                    "store_facts": [{"store_id": "369", "store_name": "银川兴庆店", "address": "测试路1号"}]
                }
            },
        },
    )


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
            "payment_action": "send_now",
            "reply_messages": [
                {"type": "text", "content": {"text": "当然可以，朋友一起过来了解完全没问题～"}},
                {
                    "type": "text",
                    "content": {
                        "text": "我马上为您生成10元预约金入口，锁住明天上午11点的名额，到店直接抵扣，未做或不满意可退"
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
            "payment_action": "send_now",
            "reply_messages": [{"type": "text", "content": {"text": "我给您发预约金入口，锁活动名额。"}}],
            "tool_calls": [],
        },
    )
    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert payment["content"]["amount"] == expected_amount


def test_payment_decision_single_registration_overrides_noisy_history_amount() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "那我报名",
            "conversation_history": ["用户: 想淡斑", "小贝: 明天方便到店看一下吗？"],
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "high_intent",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_decision": {
                "action": "send_now",
                "party_size": 1,
                "amount": 10,
                "source": "current_message",
                "confidence": "high",
                "basis": ["客户当前只说自己报名"],
            },
            "reply_messages": [{"type": "text", "content": {"text": "报名可以，我把10元预约金入口发您，到店抵扣。"}}],
            "tool_calls": [],
        },
    )
    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert plan["payment_decision"]["action"] == "send_now"
    assert plan["payment_decision"]["party_size"] == 1
    assert payment["content"]["amount"] == 10


def test_payment_decision_friend_together_auto_appends_twenty_yuan_card() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我朋友也一起过去"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_decision": {
                "action": "send_now",
                "party_size": 2,
                "amount": 20,
                "source": "current_message",
                "confidence": "high",
                "basis": ["客户当前说朋友一起"],
            },
            "reply_messages": [{"type": "text", "content": {"text": "可以，2位一共20元预约金，每位10元，到店抵扣。"}}],
            "tool_calls": [],
        },
    )
    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert payment["content"]["amount"] == 20


def test_payment_decision_two_friends_auto_appends_thirty_yuan_card() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我带两个朋友一起"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_decision": {
                "action": "send_now",
                "party_size": 3,
                "amount": 30,
                "source": "current_message",
                "confidence": "high",
                "basis": ["客户当前说带两个朋友，本人加两位朋友"],
            },
            "reply_messages": [{"type": "text", "content": {"text": "可以，3位一共30元预约金，每位10元，到店抵扣。"}}],
            "tool_calls": [],
        },
    )
    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert payment["content"]["amount"] == 30


def test_payment_decision_after_paid_next_step_removes_card() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "付完然后呢"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_DEPOSIT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "unknown",
            "main_blocker": "none",
            "next_step": "confirm_time",
            "payment_decision": {
                "action": "after_paid_next_step",
                "source": "current_message",
                "confidence": "high",
                "basis": ["客户声称已付后询问下一步"],
            },
            "reply_messages": [
                {"type": "text", "content": {"text": "我继续帮您确认到店安排。"}},
                {"type": "payment_collection", "content": {"amount": 20}},
            ],
            "tool_calls": [],
        },
    )
    assert all(item["type"] != "payment_collection" for item in plan["planner_reply_messages"])
    assert plan["payment_decision"]["action"] == "after_paid_next_step"


def test_payment_decision_resend_inherits_last_payment_amount() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "没收到，再发一下",
            "conversation_history": ["小贝: 2位一共20元预约金入口已发", "小贝: payment_collection amount=20"],
            "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 20}}],
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "high_intent",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_decision": {
                "action": "resend",
                "source": "last_payment_collection",
                "confidence": "high",
                "basis": ["客户当前要求重发入口"],
            },
            "reply_messages": [{"type": "text", "content": {"text": "我再发您一次，2位一共20元预约金，每位10元，到店抵扣。"}}],
            "tool_calls": [],
        },
    )
    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert plan["payment_decision"]["action"] == "resend"
    assert payment["content"]["amount"] == 20


def test_payment_collection_amount_inherits_recent_twenty_yuan_context() -> None:
    state = {
        "normalized_content": _u(r"\u4eba\u5462"),
        "conversation_history": [
            _u(r"\u5c0f\u8d1d: 2\u4f4d\u4e00\u517120\u5143\u9884\u7ea6\u91d1\u5165\u53e3\u5df2\u53d1"),
            "小贝: payment_collection amount=20",
        ],
        "history_events": [{"event_type": "payment_collection_sent", "facts": {"amount": 20}}],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "price",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [
                {
                    "type": "text",
                    "content": {
                        "text": _u(r"\u5728\u7684\uff0c\u6211\u521a\u521a\u7ed9\u60a8\u53d1\u7684\u662f20\u5143\u53cc\u4eba\u9884\u7ea6\u91d1\u5165\u53e3")
                    },
                },
                {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert payment["content"]["amount"] == 20


def test_payment_collection_amount_infers_recent_companion_confirmation() -> None:
    state = {
        "normalized_content": _u(r"\u53ef\u4ee5\uff0c\u53d1\u9884\u7ea6\u91d1\u5165\u53e3"),
        "content": _u(r"\u53ef\u4ee5\uff0c\u53d1\u9884\u7ea6\u91d1\u5165\u53e3"),
        "conversation_history": [
            _u(r"\u7528\u6237: \u670b\u53cb\u53ef\u4ee5\u4e00\u8d77\u8fc7\u53bb\u5417"),
            _u(r"\u5c0f\u8d1d: \u53ef\u4ee5\uff0c\u670b\u53cb\u4e5f\u80fd\u4e00\u8d77\u53bb\u3002"),
        ],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [
                {
                    "type": "text",
                    "content": {"text": _u(r"\u53ef\u4ee5\uff0c10\u5143\u9884\u7ea6\u91d1\u5165\u53e3\u53d1\u60a8")},
                },
                {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    payment = [item for item in plan["planner_reply_messages"] if item["type"] == "payment_collection"][0]
    assert payment["content"]["amount"] == 20


def test_reply_validation_rejects_text_twenty_yuan_with_ten_yuan_card() -> None:
    with pytest.raises(ValueError, match="payment_collection_amount_text_mismatch"):
        validate_reply_consistency(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": _u(r"\u6211\u5e2e\u60a8\u53d120\u5143\u53cc\u4eba\u9884\u7ea6\u5165\u53e3")},
                },
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            {"normalized_content": _u(r"\u4eba\u5462")},
        )


def test_reply_validation_rejects_text_ten_yuan_entry_with_twenty_yuan_card() -> None:
    with pytest.raises(ValueError, match="payment_collection_amount_text_mismatch"):
        validate_reply_consistency(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": _u(r"\u6211\u53d1\u4f6010\u5143\u9884\u7ea6\u5165\u53e3\uff0c\u5148\u9501\u540d\u989d")},
                },
                {"type": "payment_collection", "order": 2, "content": {"amount": 20, "remark": ""}},
            ],
            {"normalized_content": _u(r"\u670b\u53cb\u4e00\u8d77\u8fc7\u53bb")},
        )


def test_planner_reply_normalizes_ten_yuan_entry_text_for_twenty_yuan_card() -> None:
    messages = _normalize_planner_reply_messages(
        [
            {
                "type": "text",
                "order": 1,
                "content": {"text": _u(r"\u6211\u53d1\u4f6010\u5143\u9884\u7ea6\u5165\u53e3\uff0c\u5148\u9501\u540d\u989d")},
            },
            {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
        ],
        state={
            "normalized_content": _u(r"\u53ef\u4ee5\uff0c\u53d1\u9884\u7ea6\u91d1\u5165\u53e3"),
            "conversation_history": [
                _u(r"\u7528\u6237: \u670b\u53cb\u53ef\u4ee5\u4e00\u8d77\u8fc7\u53bb\u5417"),
                _u(r"\u5c0f\u8d1d: \u53ef\u4ee5\uff0c\u670b\u53cb\u4e5f\u80fd\u4e00\u8d77\u53bb"),
            ],
        },
    )

    assert messages[0]["content"]["text"] == _u(r"\u6211\u53d1\u4f602\u4f4d\u4e00\u517120\u5143\u9884\u7ea6\u91d1\u5165\u53e3\uff0c\u5148\u9501\u540d\u989d")
    assert messages[1]["content"]["amount"] == 20


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


def test_legacy_non_refund_wording_is_repaired_before_customer_delivery() -> None:
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
    assert _u(r"\u4e0d\u505a\u9000\u8fd820\u5143") in text
    with pytest.raises(ValueError, match="legacy_deposit_refund_policy"):
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


def test_store_lookup_exact_snapshot_name_is_allowed_without_extra_city() -> None:
    stores = _snapshot_stores_for_exact_query(_u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"))

    assert stores
    assert str(stores[0].get("store_id") or stores[0].get("id") or "") == "562"
    city_suffix_stores = _snapshot_stores_for_exact_query(_u(r"\u5e7f\u5dde\u5e02\u767d\u4e91\u4e09\u5e97"))
    assert city_suffix_stores
    assert str(city_suffix_stores[0].get("store_id") or city_suffix_stores[0].get("id") or "") == "562"

    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u8fd9\u5bb6\u5730\u5740\u53d1\u6211\u4e00\u4e0b"),
            "conversation_history": [_u(r"\u5c0f\u8d1d: \u7ed9\u60a8\u63a8\u8350\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97")],
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_ADDRESS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"),
                }
            ],
        },
    )

    assert not any(
        item.get("missing") == "location_query_missing_city_or_region" for item in plan["tool_policy_violations"]
    )


def test_store_detail_reference_uses_recent_snapshot_store_name() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u8fd9\u5bb6\u5730\u5740\u53d1\u6211\u4e00\u4e0b"),
            "conversation_history": [
                _u(r"\u5c0f\u8d1d: \u5e7f\u5dde\u767d\u4e91\u4e09\u5e97\u660e\u5929\u4e0a\u534811\u70b9\u53ef\u4ee5\u7ea6"),
                *[f"history filler {index}" for index in range(10)],
            ],
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "logistics",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u6211\u53d1\u60a8\u5730\u5740")}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])
    assert not any(
        item.get("missing") == "location_query_missing_city_or_region" for item in plan["tool_policy_violations"]
    )


def test_store_detail_reference_rewrites_region_query_to_recent_store_anchor() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u8fd9\u5bb6\u5730\u5740\u53d1\u6211\u4e00\u4e0b"),
            "conversation_history": [
                _u(r"\u5c0f\u8d1d: \u5e7f\u5dde\u767d\u4e91\u4e09\u5e97\u660e\u5929\u4e0a\u534811\u70b9\u53ef\u4ee5\u7ea6"),
                *[f"history filler {index}" for index in range(10)],
            ],
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_ADDRESS_PARKING_HOURS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": _u(r"\u5e7f\u5dde\u5e02\u767d\u4e91\u533a"),
                }
            ],
        },
    )

    assert plan["planner_tool_calls"][0]["query"] == _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97")


def test_generic_store_question_with_unique_history_store_allows_lookup() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u4f60\u4eec\u95e8\u5e97\u5728\u54ea\u91cc"),
            "conversation_history": [_u(r"\u5c0f\u8d1d: \u7ed9\u60a8\u63a8\u8350\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97")],
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_LOCATION",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"),
                }
            ],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [
        {"name": "customer_store_lookup", "purpose": "detail", "query": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97")}
    ]


def test_generic_store_question_with_payment_task_allows_recent_store_query() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u95e8\u5e97\u5728\u54ea"),
            "conversation_history": [
                _u(r"\u7528\u6237: \u6211\u660e\u5929\u4e0a\u534811\u70b9\u8fc7\u53bb"),
                _u(r"\u5c0f\u8d1d: \u5e7f\u5dde\u767d\u4e91\u4e09\u5e97\u660e\u5929\u4e0a\u534811\u70b9\u540d\u989d\u5df2\u7ecf\u5e2e\u60a8\u9884\u7559"),
                _u(r"\u5c0f\u8d1d: payment_collection amount=10"),
            ],
            "customer_store_knowledge": {
                "stores": [{"store_id": "562", "store_name": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"), "city": _u(r"\u5e7f\u5dde\u5e02")}]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_LOCATION",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"),
                }
            ],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [
        {"name": "customer_store_lookup", "purpose": "detail", "query": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97")}
    ]


def test_contextual_store_question_with_payment_task_is_not_forced_to_lookup_by_normalizer() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u95e8\u5e97\u5728\u54ea"),
            "conversation_history": [
                _u(r"\u7528\u6237: \u6211\u660e\u5929\u4e0a\u534811\u70b9\u8fc7\u53bb"),
                _u(r"\u5c0f\u8d1d: \u5e7f\u5dde\u767d\u4e91\u4e09\u5e97\u660e\u5929\u4e0a\u534811\u70b9\u540d\u989d\u5df2\u7ecf\u5e2e\u60a8\u9884\u7559"),
                _u(r"\u5c0f\u8d1d: payment_collection amount=10"),
            ],
            "customer_store_knowledge": {
                "stores": [{"store_id": "562", "store_name": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"), "city": _u(r"\u5e7f\u5dde\u5e02")}]
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_DEPOSIT_PUSH",
            "conversion_stage": "deposit_push",
            "customer_type": "price",
            "main_blocker": "price",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u95e8\u5e97\u6211\u5e2e\u60a8\u6838\u5bf9\u4e0b")}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []
    assert "current_turn_context_guard" not in plan["reply_strategy"]


def test_generic_store_question_with_profile_only_flags_tool_for_repair() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u95e8\u5e97\u5728\u54ea"),
            "customer_basic_info": {
                "preferred_store_id": "23",
                "preferred_store_name": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
                "city": _u(r"\u53a6\u95e8"),
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_LOCATION",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
                }
            ],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [
        {
            "name": "customer_store_lookup",
            "purpose": "detail",
            "query": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
        }
    ]
    assert any(
        item.get("missing") == "store_lookup_query_over_anchors_history"
        for item in plan["tool_policy_violations"]
    )


def test_preferred_store_is_candidate_not_current_known_store() -> None:
    state = {
        "normalized_content": _u(r"\u660e\u5929\u53ef\u4ee5\u53bb\u5417"),
        "customer_basic_info": {
            "city": _u(r"\u5e7f\u5dde\u5e02"),
            "preferred_store_id": "562",
            "preferred_store_name": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"),
        },
    }
    known = _current_known_store_for_planner(state)
    payload = _planner_payload_for_model(state)

    assert known == {}
    assert payload["store_candidate"]["store_id"] == "562"
    assert payload["store_candidate"]["source"] == "customer_profile"
    assert "confirmed_store" not in payload.get("current_turn_context", {})


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


def test_relative_month_followup_uses_recent_confirmed_store() -> None:
    state = {
        "normalized_content": "那我下个月再去也可以吧？",
        "conversation_history": [
            "用户: 我想去厦门思明店。",
            "小贝: 好的，厦门思明店可以继续看时间。",
        ],
    }

    known = _current_known_store_for_planner(state)
    payload = _planner_payload_for_model(state)

    assert known["store_id"] == "12"
    assert known["store_name"] == "厦门思明店"
    assert payload["turn_evidence"]["appointment_evidence"]["date"] == "下个月"


def test_multi_store_recent_conversation_marks_current_known_store_ambiguous() -> None:
    known = _current_known_store_for_planner(
        {
            "normalized_content": "这家地址发我",
            "conversation_history": [
                "用户: 厦门思明店可以吗",
                "小贝: 可以。",
                "用户: 厦门湖里店也行吗",
                "小贝: 湖里店也可以。",
            ],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "12", "store_name": "厦门思明店", "city": "厦门市"},
                    {"store_id": "126", "store_name": "厦门百星湖里店", "city": "厦门市"},
                ]
            },
        }
    )

    assert known["ambiguous"] is True
    assert set(known["matched_store_names"]) == {"厦门思明店", "厦门百星湖里店"}
    assert known["source"] == "recent_conversation"


def test_ambiguous_store_reference_allows_unique_real_store_lookup_tool() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "这家地址发我",
            "conversation_history": [
                "用户: 厦门思明店可以吗",
                "小贝: 可以。",
                "用户: 厦门湖里店也行吗",
                "小贝: 湖里店也可以。",
            ],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "12", "store_name": "厦门思明店", "city": "厦门市"},
                    {"store_id": "126", "store_name": "厦门百星湖里店", "city": "厦门市"},
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_ADDRESS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "payment_decision": {"action": "none", "source": "none", "confidence": "low"},
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{"name": "customer_store_lookup", "purpose": "detail", "query": "厦门思明店"}],
            "handoff": {"needed": False, "reason": ""},
        },
    )

    assert not any(
        item.get("missing") == "store_lookup_query_over_ambiguous_reference"
        for item in plan["tool_policy_violations"]
    )


def test_explicit_current_store_address_allows_lookup_over_ambiguous_history() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "厦门思明店地址发我",
            "conversation_history": [
                "用户: 厦门思明店可以吗",
                "小贝: 可以。",
                "用户: 厦门湖里店也行吗",
                "小贝: 湖里店也可以。",
            ],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "12", "store_name": "厦门思明店", "city": "厦门市"},
                    {"store_id": "126", "store_name": "厦门百星湖里店", "city": "厦门市"},
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_ADDRESS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "payment_decision": {"action": "none", "source": "none", "confidence": "low"},
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{"name": "customer_store_lookup", "purpose": "detail", "query": "厦门思明店"}],
            "handoff": {"needed": False, "reason": ""},
        },
    )

    assert not any(
        item.get("missing") == "store_lookup_query_over_ambiguous_reference"
        for item in plan["tool_policy_violations"]
    )


def test_payment_entry_request_does_not_trigger_ambiguous_store_lookup_guard() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "预约金入口发我",
            "conversation_history": ["用户: 厦门思明店明天上午可以", "小贝: 可以先锁名额。"],
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "12", "store_name": "厦门思明店", "city": "厦门市"},
                    {"store_id": "126", "store_name": "厦门百星湖里店", "city": "厦门市"},
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_ADDRESS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "payment_decision": {"action": "send_now", "source": "model", "confidence": "high"},
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{"name": "customer_store_lookup", "purpose": "detail", "query": "厦门思明店"}],
            "handoff": {"needed": False, "reason": ""},
        },
    )

    assert not any(
        item.get("missing") == "store_lookup_query_over_ambiguous_reference"
        for item in plan["tool_policy_violations"]
    )


def test_store_detail_direct_reply_with_appointment_anchor_becomes_lookup_tool() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "我昨天没去，你把门店地址发我一下",
            "appointment_cache": {"store_id": "369", "store_name": "银川兴庆店", "status": "pending"},
            "customer_basic_info": {"preferred_store_id": "467", "preferred_store_name": "重庆百星渝中店"},
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "369", "store_name": "银川兴庆店", "city": "银川市"},
                    {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市"},
                ]
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_ADDRESS_PARKING_HOURS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "payment_decision": {"action": "none", "source": "none", "confidence": "high"},
            "reply_messages": [
                {"type": "text", "content": {"text": "你说的是重庆渝中那家店对吗？"}},
                {"type": "text", "content": {"text": "我可以再发地址和导航给你"}},
            ],
            "tool_calls": [],
            "handoff": {"needed": False, "reason": ""},
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [
        {"name": "customer_store_lookup", "purpose": "detail", "query": "银川兴庆店"}
    ]
    assert not any(item.get("missing") == "store_detail_tool_required" for item in plan["tool_policy_violations"])


def test_current_scoped_store_lookup_query_is_not_overwritten_by_old_appointment_anchor() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "渝中区门店地址给我发一下",
            "appointment_cache": {"store_id": "369", "store_name": "银川兴庆店", "status": "pending"},
            "customer_basic_info": {
                "preferred_store_id": "467",
                "preferred_store_name": "重庆百星渝中店",
                "city": "重庆市",
                "area_or_landmark": "渝中区",
            },
            "customer_store_knowledge": {
                "stores": [
                    {"store_id": "369", "store_name": "银川兴庆店", "city": "银川市", "district": "兴庆区"},
                    {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_ADDRESS_PARKING_HOURS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "payment_decision": {"action": "none", "source": "none", "confidence": "high"},
            "reply_messages": [{"type": "text", "content": {"text": "我先帮您看一下"}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": "重庆市渝中区 重庆百星渝中店",
                }
            ],
            "handoff": {"needed": False, "reason": ""},
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [
        {"name": "customer_store_lookup", "purpose": "detail", "query": "重庆市渝中区 重庆百星渝中店"}
    ]


def test_store_detail_clarification_without_anchor_can_direct_reply() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "门店地址发我一下", "customer_store_knowledge": {"stores": []}},
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_ADDRESS_PARKING_HOURS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "payment_decision": {"action": "none", "source": "none", "confidence": "low"},
            "reply_messages": [{"type": "text", "content": {"text": "您说的是哪家门店？"}}],
            "tool_calls": [],
            "handoff": {"needed": False, "reason": ""},
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert not any(item.get("missing") == "store_detail_tool_required" for item in plan["tool_policy_violations"])


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
    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []
    assert any(item.get("missing") == "store_detail_tool_required" for item in plan["tool_policy_violations"])


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
    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [
        {"name": "customer_store_lookup", "purpose": "existence", "query": _u(r"\u95e8\u5e97\u5728\u54ea\u91cc")}
    ]
    assert any(
        item.get("missing") == "location_query_missing_city_or_region"
        for item in plan["tool_policy_violations"]
    )


def test_generic_store_lookup_rewrites_noncanonical_history_query_to_anchor() -> None:
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
    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [{"name": "customer_store_lookup", "purpose": "detail", "query": "广州市白云区白云三店"}]
    assert any(
        item.get("missing") == "store_lookup_query_over_anchors_history"
        for item in plan["tool_policy_violations"]
    )


def test_generic_store_direct_reply_is_not_rewritten_by_normalizer() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u4f60\u4eec\u95e8\u5e97\u5728\u54ea\u91cc"),
            "customer_basic_info": {
                "preferred_store_name": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
                "city": _u(r"\u53a6\u95e8"),
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_LOCATION",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [
                {"type": "text", "content": {"text": _u(r"\u60a8\u73b0\u5728\u60f3\u770b\u54ea\u4e2a\u57ce\u5e02\u6216\u533a\u57df\u7684\u95e8\u5e97\uff1f")}}
            ],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []
    assert plan["planner_reply_messages"] == [
        {
            "type": "text",
            "order": 1,
            "content": {"text": _u(r"\u60a8\u73b0\u5728\u60f3\u770b\u54ea\u4e2a\u57ce\u5e02\u6216\u533a\u57df\u7684\u95e8\u5e97\uff1f")},
        }
    ]
    assert "current_turn_context_guard" not in plan["reply_strategy"]


def test_scoped_city_store_question_is_left_to_planner_not_forced_by_normalizer() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "厦门有门店吗",
            "customer_store_knowledge": {"stores": [{"city": "厦门市", "district": "思明区", "store_name": "厦门思明店"}]},
        },
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_LOCATION_NEEDS_SCOPE",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "您想看哪个城市或区域的门店？"}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []


def test_scoped_city_store_question_does_not_override_legal_planner_tool_query() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "厦门有门店吗",
            "customer_store_knowledge": {
                "stores": [
                    {"city": "厦门市", "district": "思明区", "store_name": "厦门思明店"},
                    {"city": "厦门市", "district": "湖里区", "store_name": "厦门百星湖里店"},
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S4",
            "sub_rule_id": "S4_HESITATION",
            "conversion_stage": "deposit_push",
            "customer_type": "price",
            "main_blocker": "price",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{"name": "customer_store_lookup", "query": "厦门百星湖里店", "purpose": "detail"}],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["conversion_stage"] == "deposit_push"
    assert plan["next_step"] == "send_deposit"
    assert plan["planner_tool_calls"] == [{"name": "customer_store_lookup", "purpose": "detail", "query": "厦门百星湖里店"}]


def test_scoped_nearby_store_question_is_left_to_planner_not_forced_by_normalizer() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "厦门思明附近有门店吗",
            "customer_store_knowledge": {"stores": [{"city": "厦门市", "district": "思明区", "store_name": "厦门思明店"}]},
        },
        {
            "decision": "direct_reply",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_LOCATION_NEEDS_SCOPE",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "您想看哪个城市或区域的门店？"}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []


def test_scoped_nearby_landmark_preserves_landmark_in_distance_origin() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "哪家离厦门机场近一点",
            "customer_store_knowledge": {
                "stores": [
                    {"city": "厦门市", "district": "思明区", "store_name": "厦门思明店"},
                    {"city": "厦门市", "district": "湖里区", "store_name": "厦门百星湖里店"},
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{"name": "customer_store_lookup", "purpose": "nearby_candidates", "query": "厦门"}],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [{"name": "customer_store_lookup", "purpose": "nearby_candidates", "query": "厦门"}]
    assert any(item.get("missing") == "distance_calculate_required" for item in plan["tool_policy_violations"])


def test_scoped_location_query_cleaning_keeps_store_name_tokens() -> None:
    assert _clean_scoped_location_query(_u(r"\u53a6\u95e8\u6709\u95e8\u5e97\u5417")) == _u(r"\u53a6\u95e8")
    assert _clean_scoped_location_query(_u(r"\u54ea\u5bb6\u79bb\u53a6\u95e8\u673a\u573a\u8fd1\u4e00\u70b9")) == _u(r"\u53a6\u95e8\u673a\u573a")
    assert _clean_scoped_location_query(_u(r"\u53a6\u95e8\u601d\u660e\u5e97\u5730\u5740")) == _u(r"\u53a6\u95e8\u601d\u660e\u5e97")


def test_generic_store_question_uses_contextual_anchor_with_open_task() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "你们门店在哪里",
            "current_turn_context": {
                "open_task": "appointment_confirm",
                "current_store_anchor": {"store_name": "厦门百星湖里店", "source": "appointment_context"},
            },
            "customer_store_knowledge": {"stores": [{"city": "厦门市", "district": "湖里区", "store_name": "厦门百星湖里店"}]},
        },
        {
            "decision": "need_tools",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{"name": "customer_store_lookup", "query": "厦门百星湖里店", "purpose": "detail"}],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [{"name": "customer_store_lookup", "purpose": "detail", "query": "厦门百星湖里店"}]


def test_generic_store_question_without_scope_does_not_override_deposit_direct_reply() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "你们门店在哪里",
            "current_turn_context": {
                "open_task": "deposit_push",
                "current_store_anchor": {"store_name": "厦门百星湖里店", "source": "appointment_context"},
            },
            "customer_store_knowledge": {"stores": [{"city": "厦门市", "district": "湖里区", "store_name": "厦门百星湖里店"}]},
        },
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_DEPOSIT_PUSH",
            "conversion_stage": "deposit_push",
            "customer_type": "price",
            "main_blocker": "price",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": "我把10元预约金入口发您。"}}],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []
    assert "current_turn_context_guard" not in plan["reply_strategy"]


def test_generic_store_reply_must_not_use_history_store_without_facts() -> None:
    messages = [{"type": "text", "order": 1, "content": {"text": "广州白云三店我帮您核对一下。"}}]
    state = {
        "normalized_content": _u(r"\u4f60\u4eec\u95e8\u5e97\u5728\u54ea\u91cc"),
        "customer_store_knowledge": {"stores": [{"city": "广州市", "store_name": "广州白云三店"}]},
    }

    validate_reply_consistency(messages, state)
    warnings = collect_reply_soft_warnings(messages, state)

    assert any("store_context_over_anchor_for_generic_question" in item["detail"] for item in warnings)


def test_generic_store_reply_must_not_use_store_name_from_history_text() -> None:
    messages = [{"type": "text", "order": 1, "content": {"text": "广州白云三店是当前为您匹配的门店。"}}]
    state = {
        "normalized_content": _u(r"\u4f60\u4eec\u95e8\u5e97\u5728\u54ea\u91cc"),
        "conversation_history": [
            _u(r"\u7528\u6237: \u6211\u5728\u5e7f\u5dde\u767d\u4e91\u9644\u8fd1"),
            _u(r"\u5c0f\u8d1d: \u6309\u60a8\u8fd9\u4e2a\u4f4d\u7f6e\uff0c\u4f18\u5148\u770b\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97\u3002"),
        ],
    }

    validate_reply_consistency(messages, state)
    warnings = collect_reply_soft_warnings(messages, state)

    assert any("store_context_over_anchor_for_generic_question" in item["detail"] for item in warnings)


def test_generic_store_reply_can_ask_current_city_or_district() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": {"text": "您在哪个城市或哪个区？我按您方便的位置帮您查附近门店。"}}],
        {
            "normalized_content": _u(r"\u4f60\u4eec\u95e8\u5e97\u5728\u54ea\u91cc"),
            "customer_store_knowledge": {"stores": [{"city": "广州市", "store_name": "广州白云三店"}]},
        },
    )


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


def test_distance_calculate_rejects_bare_city_origin() -> None:
    plan = build_planner_plan_v2(
        {"content": "厦门哪家方便", "normalized_content": "厦门哪家方便"},
        {
            "decision": "need_tools",
            "stage": "S2",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "tool_calls": [
                {"name": "customer_store_lookup", "query": "厦门"},
                {"name": "distance_calculate", "origin": "厦门", "candidate_source": "customer_store_lookup"},
            ],
        },
    )

    assert any(
        item.get("missing") == "distance_origin_too_broad_for_ranking"
        for item in plan["tool_policy_violations"]
    )


def test_direct_store_lookup_action_requires_tool_or_verified_store_card() -> None:
    plan = build_planner_plan_v2(
        {"content": "广告不是说集美有吗", "normalized_content": "广告不是说集美有吗"},
        {
            "decision": "direct_reply",
            "stage": "S2",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "appointment_decision": {"action": "lookup_store", "commitment_level": "none"},
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "这是平台同城展示定位，我先给您看厦门实际门店。"}}
            ],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "store_lookup_action_requires_tool_or_store_card"
        for item in plan["tool_policy_violations"]
    )


def test_direct_store_lookup_next_step_requires_tool_or_verified_store_card() -> None:
    plan = build_planner_plan_v2(
        {"content": "广告不是说集美有吗", "normalized_content": "广告不是说集美有吗"},
        {
            "decision": "direct_reply",
            "stage": "S2",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "appointment_decision": {"action": "none", "commitment_level": "none"},
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "这是平台同城展示定位，我先给您看厦门实际门店。"}}
            ],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "store_lookup_action_requires_tool_or_store_card"
        for item in plan["tool_policy_violations"]
    )


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


def test_available_time_rejects_preferred_store_candidate_id() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "appointment tomorrow afternoon",
            "customer_basic_info": {"preferred_store_id": "227", "preferred_store_name": "store-b"},
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


def test_available_time_allows_store_id_from_structured_active_order() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "明天下午有哪些时间",
            "current_date": "2026-07-12",
            "current_known_store": {"store_id": "369", "store_name": "银川兴庆店", "source": "appointment_context"},
            "customer_context": {
                "orders": [{"id": "order-101", "status": "pending", "store_id": "369"}]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S3",
            "conversion_stage": "time_confirm",
            "next_step": "confirm_time",
            "reply_messages": [{"type": "text", "content": {"text": "我看一下。"}}],
            "tool_calls": [{"name": "available_time", "store_id": "369", "date": "2026-07-13"}],
        },
    )

    assert not any(item.get("missing") == "available_time_invalid_store_id" for item in plan["tool_policy_violations"])


def test_create_work_order_tool_keeps_transaction_arguments_after_dedupe() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "我朋友也一起，发卡吧",
            "confirmed_store_id": "369",
        },
        {
            "decision": "need_tools",
            "payment_decision": {"action": "send_now", "party_size": 2, "amount": 20},
            "order_decision": {"action": "create_work", "store_id": "369", "amount": 20},
            "reply_messages": [{"type": "text", "content": {"text": "我先给您开单。"}}],
            "tool_calls": [
                {
                    "name": "create_work_order",
                    "store_id": "369",
                    "category_id": "819",
                    "prepay": 20,
                    "store_confirmation_source": "current_message",
                }
            ],
        },
    )

    tool = next(item for item in plan["planner_tool_calls"] if item.get("name") == "create_work_order")
    assert tool["prepay"] == 20
    assert tool["store_confirmation_source"] == "current_message"
    assert not any(item.get("missing", "").startswith("create_work_order_missing") for item in plan["tool_policy_violations"])
    assert not any(item.get("missing") == "payment_collection_required" for item in plan["tool_policy_violations"])
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]


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


def test_direct_reply_time_availability_claim_requires_available_time_tool() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "5点有空吧"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "payment_state": "link_sent",
            "payment_action": "confirm_next_step",
            "reply_messages": [{"type": "text", "content": {"text": "5点可以，我先帮你按今天晚点安排。"}}],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "available_time_required_for_availability_claim"
        for item in plan["tool_policy_violations"]
    )


def test_available_time_inherits_recent_relative_date_and_need_tools_uses_standard_transition() -> None:
    plan = build_planner_plan_v2(
        {
            "current_date": "2026-07-12",
            "normalized_content": "下午有哪些时间",
            "conversation_history": ["小贝: 明天可以帮您看档期。"],
            "customer_context": {
                "orders": [
                    {
                        "id": "order-101",
                        "status": "waiting_schedule",
                        "store_id": "369",
                        "prepay_required": 10,
                        "prepay_paid": 10,
                    }
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "payment_state": "customer_claimed_paid",
            "payment_action": "confirm_next_step",
            "appointment_decision": {
                "action": "check_availability",
                "commitment_level": "tentative",
                "basis": ["查询明天下午档期"],
            },
            "reply_messages": [
                {"type": "text", "content": {"text": "好的，我给您定明天下午。"}}
            ],
            "tool_calls": [
                {"name": "available_time", "store_id": "369", "date": "2026-07-12"}
            ],
        },
    )

    available_time = next(
        item for item in plan["planner_tool_calls"] if item.get("name") == "available_time"
    )
    assert available_time["date"] == "2026-07-13"
    assert plan["planner_reply_messages"] == [
        {"type": "text", "order": 1, "content": {"text": "稍等一下哈"}}
    ]


def test_case_confidence_across_two_messages_is_not_misread_as_pending_lookup() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我就怕做了没效果"},
        {
            "decision": "direct_reply",
            "stage": "S1",
            "sub_rule_id": "S1_CASE_OR_IMAGE",
            "conversion_stage": "objection_resolution",
            "customer_type": "effect",
            "main_blocker": "effect",
            "next_step": "solve_blocker",
            "payment_state": "unknown",
            "payment_action": "none",
            "reply_messages": [
                {"type": "text", "content": {"text": "这类通常是能看到改善的。"}},
                {"type": "text", "content": {"text": "我先把案例和到店检测流程接上。"}},
            ],
            "tool_calls": [],
        },
    )

    assert not any(
        item.get("missing") == "direct_reply_promises_unfinished_lookup"
        for item in plan["tool_policy_violations"]
    )


def test_direct_reply_cannot_say_customer_can_go_before_store_and_schedule_are_known() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "明天去可以吗"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "payment_state": "customer_claimed_paid",
            "payment_action": "confirm_next_step",
            "appointment_decision": {
                "action": "ask_store",
                "commitment_level": "none",
                "basis": ["门店未确认"],
            },
            "reply_messages": [
                {"type": "text", "content": {"text": "可以先去，不过要先确认门店。"}}
            ],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "available_time_required_for_availability_claim"
        for item in plan["tool_policy_violations"]
    )


def test_confirmed_appointment_decision_requires_real_appointment_fact() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "明天可以去吗",
            "customer_basic_info": {"preferred_store_id": "12", "preferred_store_name": "厦门思明店"},
        },
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "none",
            "next_step": "confirm_time",
            "payment_state": "customer_claimed_paid",
            "payment_action": "confirm_next_step",
            "payment_decision": {"action": "after_paid_next_step", "source": "recent_history", "confidence": "high"},
            "appointment_decision": {
                "action": "confirm_existing",
                "commitment_level": "confirmed",
                "basis": ["preferred_store_12", "tomorrow"],
            },
            "reply_messages": [
                {"type": "text", "content": {"text": "可以，明天能去厦门思明店。"}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["appointment_decision"]["commitment_level"] == "confirmed"
    assert any(
        item.get("missing") == "available_time_required_for_confirmed_appointment_decision"
        for item in plan["tool_policy_violations"]
    )


def test_confirmed_appointment_decision_allows_request_confirmed_appointment_fact() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "明天可以去吗",
            "confirmed_store_id": "12",
            "appointment_time": "2026-07-10 11:00",
        },
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "none",
            "next_step": "confirm_time",
            "payment_state": "customer_claimed_paid",
            "payment_action": "confirm_next_step",
            "payment_decision": {"action": "after_paid_next_step", "source": "recent_history", "confidence": "high"},
            "appointment_decision": {
                "action": "confirm_existing",
                "commitment_level": "confirmed",
                "basis": ["request_confirmed_appointment"],
            },
            "reply_messages": [
                {"type": "text", "content": {"text": "可以，明天11点这个到店安排我这边看到了。"}},
            ],
            "tool_calls": [],
        },
    )

    assert not any(
        item.get("missing") == "available_time_required_for_confirmed_appointment_decision"
        for item in plan["tool_policy_violations"]
    )


def test_direct_reply_hold_wording_requires_appointment_fact() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我还有我朋友一起哦"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "confirm_time",
            "payment_state": "link_sent",
            "payment_action": "confirm_next_step",
            "reply_messages": [
                {"type": "text", "content": {"text": "可以，带朋友一起来没问题。"}},
                {"type": "text", "content": {"text": "我把你们按今天晚点先留着。"}},
            ],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "available_time_required_for_availability_claim"
        for item in plan["tool_policy_violations"]
    )


def test_direct_reply_remember_time_wording_requires_appointment_fact() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "5点有空吧"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "payment_state": "link_sent",
            "payment_action": "confirm_next_step",
            "reply_messages": [{"type": "text", "content": {"text": "可以，5点我先帮您记上。"}}],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "available_time_required_for_availability_claim"
        for item in plan["tool_policy_violations"]
    )


def test_direct_reply_current_availability_question_cannot_be_answered_without_tool_or_scope_question() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "5点有空吧"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "payment_state": "unknown",
            "payment_action": "none",
            "reply_messages": [
                {"type": "text", "content": {"text": "您是说5点方便吗"}},
                {"type": "text", "content": {"text": "我这边先按厦门百星湖里店记着"}},
            ],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "available_time_required_for_availability_claim"
        for item in plan["tool_policy_violations"]
    )


def test_no_reply_not_allowed_for_current_availability_question() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "5点有空吧"},
        {
            "decision": "no_reply",
            "stage": "S4",
            "sub_rule_id": "S4_APPOINTMENT_FOLLOWUP",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "no_action",
            "payment_state": "unknown",
            "payment_action": "none",
            "reply_messages": [],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "no_reply_not_allowed_for_appointment_availability_question"
        for item in plan["tool_policy_violations"]
    )


def test_reply_validation_requires_card_for_payment_promise_without_order() -> None:
    with pytest.raises(ValueError, match="payment_collection_required_when_reply_promises_payment_entry"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "好的，我重新发您10元预约金入口"}}],
            {"conversion_stage": "deposit_push", "next_step": "send_deposit"},
        )


def test_reply_validation_still_requires_card_after_work_order_rejection() -> None:
    with pytest.raises(ValueError, match="payment_collection_required_when_reply_promises_payment_entry"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "这家门店的预约入口还在核对中。"}}],
            {
                "conversion_stage": "deposit_push",
                "next_step": "send_deposit",
                "payment_action": "send_now",
                "payment_decision": {"action": "send_now", "amount": 10},
                "order_decision": {"action": "create_work", "store_id": "386"},
                "fact_envelope": {
                    "structured_facts": {
                        "order_facts": [{"type": "work_order", "status": "rejected"}],
                    }
                }
            },
        )


def test_reply_validation_allows_card_after_work_order_rejection() -> None:
    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": {"text": "10元小程序收款卡发您了，到店会抵扣。"}},
            {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
        ],
        {
            "conversion_stage": "deposit_push",
            "next_step": "send_deposit",
            "payment_action": "send_now",
            "payment_decision": {"action": "send_now", "amount": 10},
            "order_decision": {"action": "create_work", "store_id": "386"},
            "fact_envelope": {
                "structured_facts": {
                    "order_facts": [{"type": "work_order", "status": "rejected"}],
                }
            },
        },
    )


def test_reply_validation_still_requires_card_after_work_order_tool_error() -> None:
    with pytest.raises(ValueError, match="payment_collection_required_when_reply_promises_payment_entry"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "这家门店的预约入口还在核对中。"}}],
            {
                "conversion_stage": "deposit_push",
                "next_step": "send_deposit",
                "payment_action": "send_now",
                "payment_decision": {"action": "send_now", "amount": 10},
                "order_decision": {"action": "create_work", "store_id": "386"},
                "fact_envelope": {
                    "structured_facts": {
                        "order_facts": [{"type": "work_order", "status": "tool_error"}],
                    }
                }
            },
        )


def test_reply_validation_allows_confirmed_store_reference_without_appointment_commitment() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": {"text": "门店已经按厦门百星湖里店记着了。"}}],
        {
            "appointment_decision": {"action": "none", "commitment_level": "none"},
            "fact_envelope": {"structured_facts": {"appointment_facts": []}},
        },
    )


def test_reply_validation_requires_card_for_signup_promise_without_order() -> None:
    with pytest.raises(ValueError, match="payment_collection_required_when_reply_promises_payment_entry"):
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


def test_reply_validation_blocks_payment_card_when_payment_action_only_offers_resend() -> None:
    with pytest.raises(ValueError, match="payment_collection_blocked_by_payment_action"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": {"text": "在的，我在。您需要的话我可以再给您发一次入口。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            {"payment_state": "link_sent", "payment_action": "offer_resend"},
        )


def test_reply_validation_blocks_payment_entry_text_when_payment_action_is_not_send_now() -> None:
    with pytest.raises(ValueError, match="payment_collection_blocked_by_payment_action"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "之前那个预约金入口还在，如果要我重发就回我重发。"}}],
            {"payment_state": "link_sent", "payment_action": "confirm_next_step"},
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


def test_final_reply_appends_missing_required_handoff_notice() -> None:
    messages = [
        {
            "type": "text",
            "order": 1,
            "content": {"text": _u(r"\u5230\u5e97\u5148\u505a\u68c0\u6d4b\u8bc4\u4f30\uff0c\u786e\u8ba4\u9002\u5408\u518d\u5b89\u6392\u3002")},
        }
    ]
    state = {
        "normalized_content": _u(r"\u6211\u662f\u4e25\u91cd\u8fc7\u654f\u4f53\u8d28\uff0c\u8138\u4e4b\u524d\u80bf\u8fc7\uff0c\u8fd9\u4e2a\u80fd\u4e0d\u80fd\u505a"),
        "handoff": {"needed": True, "reason": _u(r"\u5065\u5eb7\u9ad8\u98ce\u9669\uff1a\u5fc3\u810f\u75c5/\u9ad8\u8840\u538b")},
        "required_tools": [{"name": "professional_assist", "reason": _u(r"\u5065\u5eb7\u9ad8\u98ce\u9669")}],
    }

    normalized, changed = _ensure_required_handoff_notice(messages, state)

    assert changed is True
    assert [item["type"] for item in normalized] == ["text", "human_handoff_notice"]
    assert normalized[1]["content"]["handoff_reason"]
    assert _u(r"\u5fc3\u810f\u75c5") not in normalized[1]["content"]["handoff_reason"]
    assert _u(r"\u9ad8\u8840\u538b") not in normalized[1]["content"]["handoff_reason"]
    validate_reply_consistency(normalized, state)


def test_final_reply_suppresses_stale_history_health_handoff_notice() -> None:
    messages = [
        {"type": "text", "order": 1, "content": {"text": "周年庆活动价268元，线上10元预约金锁名额，到店抵扣，未做或不满意可退。"}},
        {"type": "text", "order": 2, "content": {"text": "目前您的健康评估正在由专业人员加急处理，结果出来后我会第一时间同步您。"}},
        {"type": "human_handoff_notice", "order": 3, "content": {"handoff_reason": "健康高风险评估未闭环"}},
    ]
    state = {
        "normalized_content": "价格多少，会不会隐形消费",
        "conversation_history": [
            "用户: 我有心脏病，这个能做吗",
            "小贝: 到店先做检测确认适合再安排。",
            '小贝: human_handoff_notice {"handoff_reason":"健康高风险"}',
        ],
        "handoff": {"needed": True, "reason": "健康高风险评估未闭环"},
    }

    normalized, changed = _suppress_stale_handoff_notice(messages, state)

    assert changed is True
    assert [item["type"] for item in normalized] == ["text"]
    assert len(normalized) == 1
    assert "活动价268元" in normalized[0]["content"]["text"]
    validate_reply_consistency(normalized, state)


def test_final_reply_keeps_current_refund_handoff_notice() -> None:
    messages = [
        {"type": "text", "order": 1, "content": {"text": "我先把情况核对清楚。您是在我们哪家门店做的？"}},
        {"type": "human_handoff_notice", "order": 2, "content": {"handoff_reason": "投诉退款或付款纠纷：需核对门店、付款时间、金额和项目"}},
    ]
    state = {"normalized_content": "我刚刚多收钱了，要退款"}

    normalized, changed = _suppress_stale_handoff_notice(messages, state)

    assert changed is False
    assert [item["type"] for item in normalized] == ["text", "human_handoff_notice"]
    validate_reply_consistency(normalized, state)


def test_required_payment_collection_fallback_adds_missing_card() -> None:
    state = {
        "normalized_content": _u(r"\u53ef\u4ee5"),
        "conversion_stage": "deposit_push",
        "next_step": "send_deposit",
        "payment_decision": {"action": "send_now", "party_size": 2, "amount": 20},
        "order_decision": {"action": "use_existing", "order_id": "order-20", "store_id": "227", "amount": 20},
        "fact_envelope": {
            "structured_facts": {
                "order_facts": [
                    {
                        "status": "reused",
                        "order_id": "order-20",
                        "store_id": "227",
                        "prepay_required": 20,
                        "deposit_state": "required_unpaid",
                    }
                ]
            }
        },
        "current_turn_context": {
            "confirmed_store": {"store_name": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97")},
            "open_task": "deposit_push",
            "deposit_state": "payment_link_sent",
        },
        "conversation_history": [
            _u(r"\u7528\u6237: \u670b\u53cb\u4e00\u8d77\u8fc7\u53bb"),
            _u(r"\u5c0f\u8d1d: \u6211\u628a\u53cc\u4eba20\u5143\u9884\u7ea6\u91d1\u5165\u53e3\u53d1\u60a8"),
        ],
    }

    messages = _maybe_build_required_payment_collection_fallback(
        state,
        ValueError("payment_collection_required_when_reply_promises_payment_entry"),
        messages=[
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": _u(
                        r"\u6211\u628a\u53cc\u4eba20\u5143\u9884\u7ea6\u91d1\u5165\u53e3\u53d1\u60a8\uff0c\u6bcf\u4f4d10\u5143\uff0c\u5230\u5e97\u62b5\u6263\u3002"
                    )
                },
            }
        ],
    )

    assert messages is not None
    assert [item["type"] for item in messages] == ["text", "payment_collection"]
    assert messages[1]["content"]["amount"] == 20
    assert _u(r"\u53cc\u4eba20\u5143\u9884\u7ea6\u91d1\u5165\u53e3") in messages[0]["content"]["text"]
    validate_reply_consistency(messages, state)


def test_required_payment_collection_fallback_respects_hard_health_risk() -> None:
    state = {
        "normalized_content": _u(r"\u6211\u6709\u9ad8\u8840\u538b\u548c\u5fc3\u810f\u75c5\uff0c\u660e\u5929\u53ef\u4ee5\u5417"),
        "conversion_stage": "deposit_push",
        "next_step": "send_deposit",
    }

    messages = _maybe_build_required_payment_collection_fallback(
        state,
        ValueError("payment_collection_required_when_reply_promises_payment_entry"),
        messages=[
            {
                "type": "text",
                "order": 1,
                "content": {"text": _u(r"\u6211\u628a10\u5143\u9884\u7ea6\u91d1\u5165\u53e3\u53d1\u60a8")},
            }
        ],
    )

    assert messages is None


def test_merged_health_risk_overrides_store_lookup_task() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u8fd9\u5bb6\u5730\u5740\u53d1\u6211\u4e00\u4e0b"),
            "request_context": {
                "merged_customer_messages": [
                    _u(r"\u8fd9\u5bb6\u5730\u5740\u53d1\u6211\u4e00\u4e0b"),
                    _u(r"\u6211\u6709\u8fc7\u654f\u4f53\u8d28\uff0c\u4e4b\u524d\u505a\u533b\u7f8e\u8138\u80bf\u8fc7\uff0c\u8fd9\u4e2a\u80fd\u505a\u5417"),
                ]
            },
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_STORE_ADDRESS",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "logistics",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": _u(r"\u5e7f\u5dde\u767d\u4e91\u4e09\u5e97"),
                }
            ],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == [
        {"name": "professional_assist", "reason": _u(r"\u5065\u5eb7\u9ad8\u98ce\u9669\uff1a\u9700\u5230\u5e97\u68c0\u6d4b\u540e\u786e\u8ba4\u9002\u914d\u6027")}
    ]
    assert plan["handoff"]["needed"] is True
    assert plan["reply_strategy"]["risk_hold"]["risk_hold"] == "health_check_required"


def test_store_detail_tool_clears_deposit_stage_residue() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": _u(r"\u8fd9\u5bb6\u5730\u5740\u53d1\u6211\u4e00\u4e0b"),
            "confirmed_store_name": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
        },
        {
            "decision": "need_tools",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "risk",
            "main_blocker": "risk",
            "next_step": "send_deposit",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "detail",
                    "query": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
                }
            ],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_stage"] == "S2"
    assert plan["planner_sub_rule_id"] == "S2_STORE_ADDRESS"
    assert plan["conversion_stage"] == "store_match"
    assert plan["next_step"] == "lookup_store"
    assert plan["planner_tool_calls"] == [
        {
            "name": "customer_store_lookup",
            "purpose": "detail",
            "query": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
        }
    ]
    assert not plan["tool_policy_violations"]


def test_history_health_context_does_not_hijack_current_time_change() -> None:
    state = {
        "normalized_content": _u(
            r"\u6f58\u6c5f\u9f99\uff1a\u660e\u5929\u53ef\u4ee5\uff0c\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97\u6700\u65e9\u80fd\u7ea609:00\uff0c\u60a8\u770b\u8fd9\u4e2a\u65f6\u95f4\u65b9\u4fbf\u5417\uff1f\n"
            r"\u6211\u8981\u4e0b\u5348\u624d\u80fd\u8fc7\u53bb\u4e86"
        ),
        "conversation_history": [
            _u(r"\u7528\u6237: \u6211\u6709\u5fc3\u810f\u75c5\uff0c\u8fd9\u4e2a\u80fd\u505a\u5417"),
            _u(r"\u5c0f\u8d1d: \u8fd9\u4e2a\u8981\u5230\u5e97\u5148\u505a\u68c0\u6d4b\uff0c\u786e\u8ba4\u9002\u5408\u518d\u5b89\u6392\u64cd\u4f5c\u3002"),
            '小贝: human_handoff_notice {"handoff_reason":"健康高风险"}',
        ],
        "customer_profile": {
            "customer_type_tags": [_u(r"\u5065\u5eb7\u98ce\u9669\u578b"), _u(r"\u65f6\u95f4\u578b")],
            "main_objection": _u(r"\u5fc3\u810f\u75c5\u662f\u5426\u9002\u5408\u64cd\u4f5c"),
        },
        "customer_basic_info": {
            "preferred_store_name": _u(r"\u53a6\u95e8\u767e\u661f\u6e56\u91cc\u5e97"),
            "intent_date": "2026-07-07",
            "intent_time": "09:00",
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "stage": "S4",
            "sub_rule_id": "S4_COMPLAINT_REFUND",
            "conversion_stage": "objection_resolution",
            "customer_type": "risk",
            "main_blocker": "risk",
            "next_step": "solve_blocker",
            "reply_messages": [{"type": "text", "content": {"text": _u(r"\u7a0d\u7b49\u4e00\u4e0b\u54c8")}}],
            "tool_calls": [
                {
                    "name": "professional_assist",
                    "reason": _u(r"\u5065\u5eb7\u98ce\u9669\u8bc4\u4f30\u672a\u5173\u95ed\uff0c\u9700\u4e13\u4e1a\u534f\u52a9"),
                }
            ],
            "handoff": {"needed": True, "reason": _u(r"\u5065\u5eb7\u98ce\u9669\u8bc4\u4f30\u672a\u5173\u95ed")},
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert plan["planner_tool_calls"] == []
    assert plan["required_tools"][0]["name"] == "no_tool"
    assert plan["handoff"]["needed"] is False
    assert plan["reply_strategy"]["current_turn_context_guard"] == "advisory_health_history_removed_professional_assist_tool"
    assert any(
        item.get("missing") == "professional_assist_from_advisory_health_context"
        for item in plan["tool_policy_violations"]
    )


def test_history_health_context_removes_direct_reply_handoff_notice() -> None:
    state = {
        "normalized_content": _u(r"\u6211\u8981\u4e0b\u5348\u624d\u80fd\u8fc7\u53bb\u4e86"),
        "conversation_history": [
            _u(r"\u7528\u6237: \u6211\u6709\u5fc3\u810f\u75c5\uff0c\u8fd9\u4e2a\u80fd\u505a\u5417"),
            _u(r"\u5c0f\u8d1d: \u5230\u5e97\u5148\u505a\u68c0\u6d4b\uff0c\u786e\u8ba4\u9002\u5408\u518d\u5b89\u6392\u3002"),
        ],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_APPOINTMENT_TIME",
            "conversion_stage": "time_confirm",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "confirm_time",
            "reply_messages": [
                {"type": "text", "content": {"text": _u(r"\u53ef\u4ee5\uff0c\u90a3\u5c31\u6309\u4e0b\u5348\u7ee7\u7eed\u786e\u8ba4\u3002")}},
                {
                    "type": "human_handoff_notice",
                    "content": {"handoff_reason": _u(r"\u5065\u5eb7\u98ce\u9669\u8bc4\u4f30\u672a\u5173\u95ed")},
                },
            ],
            "tool_calls": [],
            "handoff": {"needed": True, "reason": _u(r"\u5065\u5eb7\u98ce\u9669\u8bc4\u4f30\u672a\u5173\u95ed")},
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["handoff"]["needed"] is False
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]
    assert plan["reply_strategy"]["current_turn_context_guard"] == "advisory_health_history_removed_handoff_notice"


def test_history_health_context_does_not_block_payment_collection_after_notice() -> None:
    state = {
        "normalized_content": _u(r"\u90a3\u6211\u5148\u5230\u5e97\u68c0\u6d4b\uff0c\u660e\u5929\u4e0b\u5348\u53ef\u4ee5\uff0c\u53d1\u5165\u53e3"),
        "conversation_history": [
            _u(r"\u5c0f\u8d1d: \u60a8\u6709\u8fc7\u654f\u4f53\u8d28\uff0c\u8fd9\u4e2a\u8981\u5230\u5e97\u5148\u505a\u68c0\u6d4b\uff0c\u8ba9\u95e8\u5e97\u4e13\u4e1a\u4eba\u5458\u770b\u4e0b\u9002\u4e0d\u9002\u5408\u518d\u5b89\u6392\u3002"),
            '小贝: human_handoff_notice {"handoff_reason":"health"}',
        ],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "time",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [
                {"type": "text", "content": {"text": _u(r"\u53ef\u4ee5\uff0c\u660e\u5929\u4e0b\u5348\u5148\u5230\u5e97\u68c0\u6d4b")}},
                {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["conversion_stage"] == "deposit_push"
    assert plan["next_step"] == "send_deposit"
    assert any(item["type"] == "payment_collection" for item in plan["planner_reply_messages"])

    validate_reply_consistency(
        plan["planner_reply_messages"],
        {**state, "conversion_stage": "deposit_push", "next_step": "send_deposit"},
    )


def test_current_payment_entry_overrides_unfinished_store_lookup_for_friend() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "我和朋友一起过去，发入口",
            "conversation_history": [
                "用户: 我在厦门机场附近，哪家近一点",
                "小贝: 厦门机场附近暂未匹配到门店，您常去思明区还是湖里区？",
            ],
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [
                {"name": "customer_store_lookup", "purpose": "nearby_candidates", "query": "厦门市机场附近"},
                {"name": "distance_calculate", "origin": "厦门市机场附近", "candidate_source": "customer_store_lookup"},
            ],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]
    assert not any(item["type"] == "payment_collection" for item in plan["planner_reply_messages"])


def test_current_payment_entry_uses_three_person_amount() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "带两个朋友一起去，发入口"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "accompany",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "payment_state": "needs_payment",
            "payment_action": "send_now",
            "reply_messages": [{"type": "text", "content": {"text": "可以，我给您发预约金入口。"}}],
            "tool_calls": [],
        },
    )

    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text", "payment_collection"]
    assert plan["planner_reply_messages"][1]["content"]["amount"] == 30


def test_current_payment_entry_uses_twenty_yuan_for_two_person_total() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我和朋友两个人想预约，发入口", "content": "我和朋友两个人想预约，发入口"},
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_PAYMENT_COLLECTION",
            "conversion_stage": "deposit_push",
            "customer_type": "high_intent",
            "main_blocker": "none",
            "next_step": "send_deposit",
            "reply_messages": [
                {"type": "text", "content": {"text": "可以，10元预约金入口发您。"}},
                {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
            ],
            "tool_calls": [],
        },
    )

    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text", "payment_collection"]
    assert plan["planner_reply_messages"][1]["content"]["amount"] == 20


def test_current_time_confirmation_missing_location_does_not_reuse_failed_distance_lookup() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "明天下午可以过去",
            "conversation_history": [
                "用户: 我在厦门机场附近，哪家近一点",
                "小贝: 厦门机场附近暂未匹配到门店，您常去思明区还是湖里区？",
            ],
        },
        {
            "decision": "need_tools",
            "stage": "S2",
            "sub_rule_id": "S2_LOCATION_DETAIL",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [
                {"name": "customer_store_lookup", "purpose": "nearby_candidates", "query": "厦门市机场附近"},
                {"name": "distance_calculate", "origin": "厦门市机场附近", "candidate_source": "customer_store_lookup"},
            ],
        },
    )

    assert plan["planner_decision"] == "need_tools"
    assert [item["type"] for item in plan["planner_reply_messages"]] == ["text"]


def test_current_health_risk_hold_blocks_payment_collection() -> None:
    state = {
        "normalized_content": _u(r"\u6211\u6709\u8fc7\u654f\u4f53\u8d28\uff0c\u660e\u5929\u4e0b\u5348\u53ef\u4ee5\u5417"),
        "conversation_history": [],
    }
    with pytest.raises(ValueError, match="payment_collection_blocked_by_health_risk_hold"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": {"text": _u(r"\u53ef\u4ee5\uff0c\u7ebf\u4e0a10\u5143\u9884\u7ea6\u91d1\u9501\u540d\u989d\u3002")}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10, "remark": ""}},
            ],
            state,
        )


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


def test_reply_validation_rejects_payment_collection_after_paid_deposit_context() -> None:
    with pytest.raises(ValueError, match="payment_collection_blocked_by_paid_deposit_context"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": {"text": "收到，我继续给您安排后续到店。"}},
                {"type": "payment_collection", "order": 2, "content": {"amount": 20, "remark": ""}},
            ],
            {
                "conversion_stage": "time_confirm",
                "next_step": "confirm_time",
                "current_turn_context": {
                    "deposit_state": "deposit_paid",
                    "open_task": "post_deposit_next_step_clarification",
                },
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


def test_reply_validation_merges_same_store_id_facts_before_card_consistency_check() -> None:
    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": {"text": "厦门百星湖里店地址和停车信息在这里。"}},
            {"type": "store_address", "order": 2, "content": {"store_id": "386"}},
        ],
        {
            "fact_envelope": {
                "structured_facts": {
                    "appointment_facts": [{"type": "appointment_created", "store_id": "386"}],
                    "store_facts": [
                        {
                            "store_id": "386",
                            "store_name": "厦门百星湖里店",
                            "city": "厦门市",
                            "district": "湖里区",
                            "parking": "商场停车场",
                        }
                    ],
                }
            }
        },
    )


def test_reply_validation_rejects_store_address_card_conflicting_with_visible_store_text() -> None:
    with pytest.raises(ValueError, match="store_address_text_card_mismatch"):
        validate_reply_consistency(
            [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "渝中区这家是重庆百星渝中店，地址是重庆市渝中区瑞天路10号嘉陵中心A馆。"},
                },
                {"type": "store_address", "order": 2, "content": {"store_id": "369"}},
            ],
            {
                "customer_store_knowledge": {
                    "stores": [
                        {"store_id": "369", "store_name": "银川兴庆店", "city": "银川市", "district": "兴庆区"},
                        {"store_id": "467", "store_name": "重庆百星渝中店", "city": "重庆市", "district": "渝中区"},
                    ]
                },
                "fact_envelope": {
                    "structured_facts": {
                        "store_facts": [
                            {"store_id": "369", "store_name": "银川兴庆店", "city": "银川市", "district": "兴庆区"}
                        ]
                    }
                },
            },
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


def test_reply_validation_allows_asking_location_before_nearby_matching() -> None:
    validate_reply_consistency(
        [
            {
                "type": "text",
                "order": 1,
                "content": {"text": "可以做，这类斑点大多数客户改善反馈都不错，到店检测后看斑型会更准。方便告诉我您所在城市吗？"},
            }
        ],
        {"normalized_content": "我想淡斑，效果怎么样", "planner_decision": "direct_reply"},
    )


def test_reply_validation_allows_look_at_time_phrase_without_schedule_lookup() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": {"text": "您看时间方便的话，到店检测会更准，确认适合再安排。"}}],
        {"normalized_content": "有做完效果图吗"},
    )


def test_reply_validation_requires_distance_fact_for_specific_nearer_store() -> None:
    with pytest.raises(ValueError, match="distance_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "厦门思明店离您更近一些。"}}],
            {"normalized_content": "哪家近一点", "planner_decision": "direct_reply"},
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


def test_reply_validation_allows_flexible_future_visit_without_confirming_a_slot() -> None:
    validate_reply_consistency(
        [
            {"type": "text", "order": 1, "content": {"text": "可以的，下个月去也行，到店时间按您方便安排。"}},
            {"type": "text", "order": 2, "content": {"text": "活动资格可以先留着，等您方便时再定门店时间。"}},
        ],
        {
            "appointment_decision": {"action": "tentative_arrange", "commitment_level": "tentative"},
            "fact_envelope": {"structured_facts": {"appointment_facts": []}},
        },
    )


def test_reply_validation_rejects_new_time_confirmation_based_only_on_old_appointment() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "可以，给您改到3点了，时间已经安排好。"}}],
            {
                "appointment_decision": {"action": "check_availability", "commitment_level": "tentative"},
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [
                            {"type": "appointment_created", "appointment_id": "old-1", "appointment_time": "2026-07-13 14:30:00"}
                        ]
                    }
                },
            },
        )


def test_reply_validation_rejects_implicit_reschedule_confirmation_without_new_plan_fact() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "好的，改到15:00，您按这个时间到店就行。"}}],
            {
                "appointment_decision": {"action": "check_availability", "commitment_level": "tentative"},
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [
                            {
                                "type": "appointment_created",
                                "appointment_id": "old-1",
                                "appointment_time": "2026-07-13 14:30:00",
                            },
                            {
                                "type": "available_time",
                                "date": "2026-07-13",
                                "target_time": "15:00",
                                "target_time_available": True,
                            },
                        ]
                    }
                },
            },
        )


def test_reply_validation_rejects_change_execution_wording_with_only_available_time() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "14:30可以，我给您按这个时间改过去。"}}],
            {
                "appointment_decision": {"action": "check_availability", "commitment_level": "tentative"},
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [
                            {
                                "type": "available_time",
                                "target_time": "14:30",
                                "target_time_available": True,
                            }
                        ]
                    }
                },
            },
        )


def test_reply_validation_allows_reschedule_confirmation_question_with_available_time() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": {"text": "可以，这个时间目前可以，您确认要改到14:30吗？"}}],
        {
            "appointment_decision": {"action": "check_availability", "commitment_level": "tentative"},
            "fact_envelope": {
                "structured_facts": {
                    "appointment_facts": [
                        {
                            "type": "available_time",
                            "target_time": "14:30",
                            "target_time_available": True,
                        }
                    ]
                }
            },
        },
    )


def test_reply_payload_lifts_current_mobile_sync_fact() -> None:
    payload = reply_user_payload_for_model(
        {
            "normalized_content": "13800138000",
            "customer_basic_info": {"customer_name": "陈雨桐", "phone": "13800138000"},
            "fact_envelope": {
                "structured_facts": {
                    "registration_facts": [
                        {
                            "type": "customer_mobile_sync",
                            "status": "synced",
                            "source": "platform_agent.customer.add_mobile",
                        }
                    ]
                }
            },
        }
    )

    assert payload["current_turn_context"]["registration_evidence"]["phone_collected"] is True
    assert payload["transaction_facts"]["registration"][0]["status"] == "synced"


def test_reply_validation_rejects_claimed_registration_without_order_fact() -> None:
    with pytest.raises(ValueError, match="registration_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "可以，先给您报上。"}}],
            {"fact_envelope": {"structured_facts": {"appointment_facts": []}}},
        )


def test_reply_validation_allows_claimed_registration_with_created_order_fact() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": {"text": "可以，先给您报上。"}}],
        {
            "fact_envelope": {
                "structured_facts": {
                    "order_facts": [{"status": "created", "order_id": "order-1"}],
                    "appointment_facts": [],
                }
            }
        },
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


def test_reply_validation_rejects_time_can_book_without_available_time_fact() -> None:
    with pytest.raises(ValueError, match="available_time_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "5点可以，我先帮你按今天晚点安排。"}}],
            {"fact_envelope": {"structured_facts": {"appointment_facts": []}}},
        )


def test_reply_validation_rejects_hold_wording_without_appointment_fact() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "可以，带朋友一起来没问题，我把你们按今天晚点先留着。"}}],
            {"fact_envelope": {"structured_facts": {"appointment_facts": []}}},
        )


def test_reply_validation_rejects_lock_time_wording_without_appointment_fact() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "可以，一起过来没问题。我先给你们锁今天这个时段。"}}],
            {"fact_envelope": {"structured_facts": {"appointment_facts": []}}},
        )


def test_reply_validation_rejects_hold_wording_with_only_available_time_fact() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "5点暂未看到可约，最近能帮您留10点。"}}],
            {
                "fact_envelope": {
                    "structured_facts": {
                        "appointment_facts": [
                            {
                                "type": "available_time",
                                "recommended_slot": "10:00",
                                "backup_slots": ["10:45"],
                                "target_time_available": False,
                            }
                        ]
                    }
                }
            },
        )


def test_reply_validation_rejects_arrange_group_wording_without_appointment_fact() -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "可以，朋友一起也行。那我先帮你按两位一起安排。"}}],
            {"fact_envelope": {"structured_facts": {"appointment_facts": []}}},
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


def test_reply_validation_requires_case_image_for_effect_turn_when_available() -> None:
    state = {
        "planner_sub_rule_id": "S1_CASE_REQUEST",
        "customer_type": "effect",
        "main_blocker": "effect",
        "fact_envelope": {
            "structured_facts": {
                "case_facts": [{"document_id": "doc-1", "image_url": "https://example.com/case.jpg"}]
            }
        },
    }
    messages = [{"type": "text", "order": 1, "content": {"text": "可以做，这类斑点大多数客户改善反馈都不错。"}}]

    validate_reply_consistency(messages, state)
    warnings = collect_reply_soft_warnings(messages, state)

    assert any("case_image_required_for_effect_turn" in item["detail"] for item in warnings)


def test_reply_validation_rejects_effect_reply_starting_with_risk_disclaimer() -> None:
    messages = [{"type": "text", "order": 1, "content": {"text": "淡斑效果因人而异，主要看斑点类型和皮肤状态。"}}]
    state = {
        "planner_sub_rule_id": "S1_CASE_REQUEST",
        "customer_type": "effect",
        "main_blocker": "effect",
    }

    validate_reply_consistency(messages, state)
    warnings = collect_reply_soft_warnings(messages, state)

    assert any("effect_reply_confidence_order_required" in item["detail"] for item in warnings)


def test_reply_validation_rejects_effect_absolute_safety_claim() -> None:
    with pytest.raises(ValueError, match="effect_absolute_safety_claim"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": "淡斑方向可以看，不会导致反黑，到店检测后再安排。"}}],
            {"normalized_content": "做完会不会反黑"},
        )


def test_reply_validation_allows_non_absolute_safety_confidence() -> None:
    validate_reply_consistency(
        [
            {
                "type": "text",
                "order": 1,
                "content": {
                    "text": "一般不会反黑，我们绝大多数客户反馈都比较正常，到店会先检测评估。"
                },
            }
        ],
        {"normalized_content": "会不会反黑"},
    )


@pytest.mark.parametrize("绝对表达", ["绝对不会反黑", "保证不会反黑", "100%不会反黑"])
def test_reply_validation_rejects_absolute_safety_guarantees(绝对表达: str) -> None:
    with pytest.raises(ValueError, match="effect_absolute_safety_claim"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": {"text": f"{绝对表达}，可以放心做。"}}],
            {"normalized_content": "会不会反黑"},
        )


def test_reply_validation_allows_positive_effect_text_and_case_image() -> None:
    validate_reply_consistency(
        [
            {
                "type": "text",
                "order": 1,
                "content": {"text": "可以做，这类斑点大多数客户改善反馈都不错，到店检测后看斑型会更准。"},
            },
            {"type": "image", "order": 2, "content": {"url": "https://example.com/case.jpg"}},
        ],
        {
            "planner_sub_rule_id": "S1_CASE_REQUEST",
            "customer_type": "effect",
            "main_blocker": "effect",
            "fact_envelope": {
                "structured_facts": {
                    "case_facts": [{"document_id": "doc-1", "image_url": "https://example.com/case.jpg"}]
                }
            },
        },
    )


def test_reply_validation_allows_view_case_reference_after_case_tool() -> None:
    validate_reply_consistency(
        [
            {
                "type": "text",
                "order": 1,
                "content": {"text": "老年斑可以改善，很多客户反馈斑点淡化明显，先给您看这张同类参考。"},
            },
            {"type": "image", "order": 2, "content": {"url": "https://example.com/case.jpg"}},
        ],
        {
            "planner_decision": "need_tools",
            "planner_sub_rule_id": "S1_CASE_REQUEST",
            "customer_type": "effect",
            "main_blocker": "effect",
            "fact_envelope": {
                "structured_facts": {
                    "case_facts": [{"document_id": "doc-1", "image_url": "https://example.com/case.jpg"}]
                }
            },
        },
    )


def test_effect_case_soft_warning_keeps_reply_non_blocking() -> None:
    state = {
        "normalized_content": "老年斑可以改善吗",
        "planner_decision": "need_tools",
        "planner_sub_rule_id": "S1_CASE_REQUEST",
        "customer_type": "effect",
        "main_blocker": "effect",
        "fact_envelope": {
            "structured_facts": {
                "case_facts": [{"document_id": "doc-1", "image_url": "https://example.com/case.jpg"}]
            }
        },
    }
    messages = [{"type": "text", "order": 1, "content": {"text": "老年斑可以改善，到店检测看斑型会更准。"}}]

    validate_reply_consistency(messages, state)
    warnings = collect_reply_soft_warnings(messages, state)

    assert any("case_image_required_for_effect_turn" in item["detail"] for item in warnings)


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


def test_reply_payload_uses_model_owned_progression_for_store_mainline() -> None:
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
            "sop_progress_evidence": {
                "completed_pack_ids": ["s10_new_customer_opening"],
                "completed_categories": ["opening"],
                "unfinished_sops": [
                    {
                        "id": "s10_need_and_case",
                        "sop_category": "effect_case",
                        "purpose": "承接需求和案例效果",
                        "order": 20,
                    }
                ],
            },
            "sales_progression": {
                "status": "continue",
                "target_stage": "need_and_case",
                "action": "ask_need_context",
                "goal": "承接客户斑点情况",
                "basis": ["门店事实已解决"],
            },
        }
    )
    assert payload["reply_mode"] == "normal_answer"
    assert "next_candidates" not in payload["sop_progress"]
    assert payload["sop_progress"]["selected_progression"]["action"] == "ask_need_context"
    assert payload["sop_progress"]["unfinished_sops"][0]["id"] == "s10_need_and_case"


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
            "sop_progress_evidence": {
                "completed_pack_ids": ["s10_activity_intro"],
                "completed_categories": ["activity_intro"],
                "unfinished_sops": [],
            },
            "sales_progression": {
                "status": "continue",
                "target_stage": "deposit",
                "action": "send_payment_card",
                "goal": "响应当前报名意图",
                "basis": ["客户当前明确报名"],
            },
        }
    )

    assert payload["sent_message_summary"]["payment_collection_sent"] is True
    assert payload["sop_progress"]["selected_progression"]["action"] == "send_payment_card"
    assert "next_candidates" not in payload["sop_progress"]


def test_planner_requires_model_owned_sales_progression_with_live_sop_evidence() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "我在朝阳区",
            "sop_progress_evidence": {
                "completed_pack_ids": ["s10_new_customer_opening"],
                "completed_categories": ["opening"],
                "unfinished_sops": [{"id": "s10_need_and_case", "purpose": "承接需求"}],
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S2",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "none",
            "next_step": "solve_blocker",
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "朝阳区这边的门店我给您发过去。"}}
            ],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "sales_progression_required"
        for item in plan["tool_policy_violations"]
    )


def test_planner_preserves_model_owned_sales_progression_without_business_template() -> None:
    message = "朝阳区这边的门店我给您发过去。方便问下，您的斑点大概有多久了？"
    plan = build_planner_plan_v2(
        {
            "normalized_content": "我在朝阳区",
            "sop_progress_evidence": {
                "completed_pack_ids": ["s10_new_customer_opening"],
                "completed_categories": ["opening"],
                "unfinished_sops": [{"id": "s10_need_and_case", "purpose": "承接需求"}],
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S2",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "none",
            "next_step": "solve_blocker",
            "sales_progression": {
                "status": "continue",
                "target_stage": "need_and_case",
                "action": "ask_need_context",
                "goal": "承接斑点情况",
                "basis": ["门店问题已解决"],
            },
            "reply_messages": [{"type": "text", "order": 1, "content": {"text": message}}],
            "tool_calls": [],
        },
    )

    assert plan["sales_progression"]["action"] == "ask_need_context"
    assert plan["planner_reply_messages"][0]["content"]["text"] == message
    assert not any(
        item.get("missing") == "sales_progression_required"
        for item in plan["tool_policy_violations"]
    )
def test_planner_keeps_store_card_backed_by_current_fact_envelope() -> None:
    plan = build_planner_plan_v2(
        {
            "normalized_content": "广告不是说集美有吗，都有点远",
            "fact_envelope": {
                "structured_facts": {
                    "store_facts": [
                        {"store_id": "227", "store_name": "厦门百星湖里店", "city": "厦门市"}
                    ]
                }
            },
        },
        {
            "decision": "direct_reply",
            "stage": "S2",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "confirm_store",
            "appointment_decision": {
                "action": "none",
                "commitment_level": "none",
                "basis": ["本轮已有真实门店事实"],
            },
            "sales_progression": {
                "status": "continue",
                "target_stage": "need_and_case",
                "action": "ask_need_context",
                "goal": "承接斑点情况",
                "basis": ["门店问题已解决"],
            },
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "这个是平台同城展示定位，我先把湖里这家发您。"}},
                {"type": "store_address", "order": 2, "content": {"store_id": "227"}},
                {"type": "text", "order": 3, "content": {"text": "您脸上的斑大概有多久了？"}},
            ],
            "tool_calls": [],
        },
    )

    assert plan["planner_decision"] == "direct_reply"
    assert plan["planner_tool_calls"] == []
    assert any(item["type"] == "store_address" for item in plan["planner_reply_messages"])
    assert not any(
        item.get("missing") == "store_detail_tool_required"
        for item in plan["tool_policy_violations"]
    )


def test_planner_can_ask_store_scope_without_triggering_pending_lookup_violation() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "你们门店在哪里"},
        {
            "decision": "direct_reply",
            "stage": "S2",
            "conversion_stage": "store_match",
            "customer_type": "distance",
            "main_blocker": "distance",
            "next_step": "lookup_store",
            "appointment_decision": {
                "action": "ask_store",
                "commitment_level": "none",
                "basis": ["缺少城市或区域"],
            },
            "sales_progression": {
                "status": "continue",
                "target_stage": "store",
                "action": "confirm_store",
                "goal": "确认城市或区域",
                "basis": ["当前没有门店范围"],
            },
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "您在哪个城市或区域？我给您匹配门店。"}}
            ],
            "tool_calls": [],
        },
    )

    assert not any(
        item.get("missing") == "store_lookup_action_requires_tool_or_store_card"
        for item in plan["tool_policy_violations"]
    )


def test_planner_allows_flexible_visit_date_without_claiming_a_slot() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "我改天再去看看吧"},
        {
            "decision": "direct_reply",
            "stage": "S4",
            "conversion_stage": "objection_resolution",
            "customer_type": "time",
            "main_blocker": "time",
            "next_step": "solve_blocker",
            "appointment_decision": {
                "action": "none",
                "commitment_level": "none",
                "basis": ["客户尚未选择日期"],
            },
            "sales_progression": {
                "status": "continue",
                "target_stage": "activity",
                "action": "explain_deposit",
                "goal": "说明活动资格可先保留",
                "basis": ["客户只是暂时推迟"],
            },
            "reply_messages": [
                {
                    "type": "text",
                    "order": 1,
                    "content": {"text": "可以的，活动资格可以先留着，到店日期后面按您方便再定。"},
                }
            ],
            "tool_calls": [],
        },
    )

    assert not any(
        item.get("missing") == "available_time_required_for_availability_claim"
        for item in plan["tool_policy_violations"]
    )


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
    assert payload["current_turn_context"]["binding_source"] in {"last_assistant", "none"}
    assert "payment_context_available" in payload["current_turn_context"]["context_hints"]
    assert payload["current_turn_context"]["confirmed_store"]["store_name"] == "广州白云三店"
    assert "open_task" not in payload["current_turn_context"]
    assert "evidence_summary" not in payload["current_turn_context"]


def test_planner_prompt_treats_payment_sent_as_context_not_hard_dedupe() -> None:
    assert "已发送过 payment_collection 只是频率证据，不是硬去重" in PLANNER_SYSTEM_PROMPT
    assert "历史累计次数都不能单独决定发或不发" in PLANNER_SYSTEM_PROMPT
    assert "优先看客户当前态度和新的成交推进" in PLANNER_SYSTEM_PROMPT
    assert "其次看今天发送次数" in PLANNER_SYSTEM_PROMPT
    assert "你还没付/支付失败/刚才没付款" in PLANNER_SYSTEM_PROMPT
    assert "要交钱吗/预约金怎么抵扣/能不能退/是不是额外收费/尾款多少" in PLANNER_SYSTEM_PROMPT
    assert "不要求客户逐字说“发入口”" in PLANNER_SYSTEM_PROMPT
    assert "普通顾虑被解决后的明确接受也可以进入 send_now" in PLANNER_SYSTEM_PROMPT
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


def test_background_context_replaces_request_history_with_platform_20_messages() -> None:
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

    assert calls[0]["limit"] == 20
    assert calls[0]["customer_id"] == "external"
    assert len(output["conversation_history"]) == 20
    assert output["conversation_history"][0] == "用户: message-15"
    assert output["conversation_history"][1] == "小贝: message-16"
    assert output["conversation_fetch"]["status"] == "ok"
    assert output["conversation_fetch"]["used_message_count"] == 20


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
