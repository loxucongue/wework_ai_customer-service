from __future__ import annotations

from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.services.payment_collection import unanswered_payment_collection


def _payment_state() -> dict:
    return {
        "normalized_content": "好的",
        "sop_progress_evidence": {"completed_pack_ids": ["s10_activity_intro"]},
        "history_events": [
            {
                "event_id": "payment-1",
                "event_type": "payment_collection_sent",
                "event_time": "2026-08-09T10:00:00+08:00",
                "facts": {"amount": 10},
            }
        ],
    }


def _payment_payload(action: str) -> dict:
    return {
        "decision": "direct_reply",
        "stage": "S3",
        "sub_rule_id": "S3_PAYMENT_COLLECTION",
        "conversion_stage": "deposit_push",
        "customer_type": "price",
        "main_blocker": "none",
        "next_step": "send_deposit",
        "payment_state": "needs_payment",
        "payment_action": "send_now" if action == "send_now" else "offer_resend",
        "payment_decision": {
            "action": action,
            "method": "mini_program",
            "party_size": 1,
            "amount": 10,
            "source": "model",
            "confidence": "high",
            "basis": ["客户当前未付"],
        },
        "reply_messages": [
            {"type": "text", "content": {"text": "您可以点卡片把名额锁住。"}},
            {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
        ],
        "tool_calls": [],
    }


def test_unanswered_payment_card_is_detected_across_assistant_batches() -> None:
    result = unanswered_payment_collection(
        [
            {"role": "user", "content": "我考虑一下"},
            {
                "role": "assistant",
                "reply_messages": [{"type": "payment_collection", "content": {"amount": 10}}],
                "created_at": "2026-08-09T10:00:00+08:00",
            },
            {"role": "assistant", "reply_messages": [{"type": "text", "content": "名额可以先保留。"}]},
        ]
    )

    assert result["active"] is True
    assert result["reason"] == "payment_card_sent_without_customer_reply"


def test_customer_reply_clears_unanswered_payment_card_guard() -> None:
    result = unanswered_payment_collection(
        [
            {
                "role": "assistant",
                "reply_messages": [{"type": "payment_collection", "content": {"amount": 10}}],
            },
            {"role": "user", "content": "卡片没看到，再发一下"},
        ]
    )

    assert result["active"] is False
    assert result["reason"] == "customer_replied_after_payment_card"


def test_existing_card_turns_send_now_into_explain_existing() -> None:
    plan = build_planner_plan_v2(_payment_state(), _payment_payload("send_now"))

    assert plan["payment_decision"]["action"] == "explain"
    assert plan["payment_action"] == "explain_existing"
    assert all(message["type"] != "payment_collection" for message in plan["planner_reply_messages"])


def test_explicit_resend_decision_can_keep_payment_card() -> None:
    plan = build_planner_plan_v2(_payment_state(), _payment_payload("resend"))

    assert plan["payment_decision"]["action"] == "resend"
    assert any(message["type"] == "payment_collection" for message in plan["planner_reply_messages"])
