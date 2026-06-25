from __future__ import annotations

import pytest

from app.graph.nodes.reply_validation import validate_reply_consistency
from app.graph.planner.brain_v2 import _should_suppress_planner_memory
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
