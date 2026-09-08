from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes.reply_admission import validate_model_led_reply_admission  # noqa: E402
from app.graph.nodes.reply_nodes import _validate_parallel_raw_reply_schema  # noqa: E402


def _payment_state(
    *,
    party_status: str = "unknown",
    party_size: int | None = None,
    payment_status: str = "payment_request",
    deposit_state: str = "required_unpaid",
    offer_refs: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "request_context": {
            "interface_version": "v3",
            "reply_chain_mode": "model_led_sales_brain_v3",
        },
        "evidence_join": {
            "content_candidates": [],
            "shared_context": {
                "conversation": [
                    {
                        "role": "assistant",
                        "message_ref": "assistant:activity-price",
                        "content": "活动价和预约金规则已完整介绍。",
                    }
                ],
                "authoritative_facts": {
                    "orders_and_payment": {
                        "resolved_payment": {"deposit_state": deposit_state}
                    },
                    "sop_progress": {"completed_pack_ids": []},
                },
                "content_indexes": {"available_sop": {"sop_packs": []}},
            },
            "normalized_tool_facts": {"structured_facts": {}},
        },
        "reply_selected_content_ids": [],
        "reply_deposit_evidence": {
            "offer_prior_turn_refs": (
                ["assistant:activity-price"] if offer_refs is None else offer_refs
            ),
            "supporting_key": "",
            "supporting_refs": [],
            "current_intent_refs": [],
        },
        "reply_party_size_assessment": {
            "status": party_status,
            "party_size": party_size,
            "evidence_refs": [],
        },
        "reply_payment_assessment": {
            "status": payment_status,
            "evidence_refs": ["current_message"],
        },
    }


def _payment_messages(amount: int) -> list[dict[str, Any]]:
    return [
        {"type": "text", "order": 1, "content": "预约金收款卡发您。"},
        {
            "type": "payment_collection",
            "order": 2,
            "content": {"amount": amount, "remark": ""},
        },
    ]


@pytest.mark.parametrize("party_size", [1, 2, 3, 4])
def test_payment_gate_accepts_exact_ten_yuan_per_person(party_size: int) -> None:
    validate_model_led_reply_admission(
        _payment_messages(party_size * 10),
        _payment_state(party_status="known", party_size=party_size),
    )


@pytest.mark.parametrize("amount", [0, 5, 50, 99])
def test_payment_gate_rejects_amounts_outside_whitelist(amount: int) -> None:
    with pytest.raises(ValueError, match="invalid_parallel_payment_collection_amount"):
        validate_model_led_reply_admission(
            _payment_messages(amount),
            _payment_state(),
        )


def test_payment_gate_rejects_amount_that_conflicts_with_party_size() -> None:
    with pytest.raises(
        ValueError,
        match="payment_collection_amount_conflicts_with_party_size_assessment",
    ):
        validate_model_led_reply_admission(
            _payment_messages(10),
            _payment_state(party_status="known", party_size=2),
        )


def test_payment_gate_requires_known_party_size_above_ten_yuan() -> None:
    with pytest.raises(
        ValueError,
        match="multi_person_payment_requires_known_party_size_assessment",
    ):
        validate_model_led_reply_admission(_payment_messages(20), _payment_state())


def test_payment_gate_rejects_more_than_four_people() -> None:
    with pytest.raises(ValueError, match="payment_participant_count_confirm_required"):
        validate_model_led_reply_admission(
            _payment_messages(40),
            _payment_state(party_status="over_limit", party_size=5),
        )


def test_payment_gate_rejects_authoritative_paid_customer() -> None:
    with pytest.raises(ValueError, match="payment_collection_blocked_by_paid_deposit_context"):
        validate_model_led_reply_admission(
            _payment_messages(10),
            _payment_state(deposit_state="paid_by_order"),
        )


def test_payment_gate_rejects_customer_paid_claim() -> None:
    with pytest.raises(ValueError, match="payment_collection_blocked_by_customer_paid_claim"):
        validate_model_led_reply_admission(
            _payment_messages(10),
            _payment_state(payment_status="unverified_paid_claim"),
        )


def test_payment_gate_requires_prior_activity_and_price_reference() -> None:
    with pytest.raises(ValueError, match="payment_collection_requires_prior_activity_evidence"):
        validate_model_led_reply_admission(
            _payment_messages(10),
            _payment_state(offer_refs=[]),
        )


def test_payment_gate_does_not_require_old_supporting_or_current_action_fields() -> None:
    validate_model_led_reply_admission(_payment_messages(10), _payment_state())


def test_structure_gate_rejects_duplicate_payment_cards_before_normalization() -> None:
    with pytest.raises(ValueError, match="duplicate_payment_collection_in_single_turn"):
        _validate_parallel_raw_reply_schema(
            {
                "reply_messages": [
                    {"type": "text", "content": "收款卡发您。"},
                    {"type": "payment_collection", "content": {"amount": 10}},
                    {"type": "payment_collection", "content": {"amount": 10}},
                ]
            }
        )


@pytest.mark.parametrize(
    "reply",
    [
        "已留位。",
        "已经帮您约好了。",
        "预约成功。",
        "已排客。",
        "排客成功。",
    ],
)
def test_appointment_completion_gate_rejects_only_without_authoritative_fact(reply: str) -> None:
    state = _payment_state()
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": reply}],
            state,
        )

    state["evidence_join"]["normalized_tool_facts"]["structured_facts"] = {
        "appointment_facts": [
            {
                "type": "appointment_confirmed",
                "appointment_id": "appointment-1",
            }
        ]
    }
    validate_model_led_reply_admission([{"type": "text", "content": reply}], state)


@pytest.mark.parametrize(
    "reply",
    [
        "我们早上9点开门。",
        "明天下午有档期。",
        "已经帮您登记好了。",
        "今天直接过去就行。",
        "您和姐姐明天早上9点过来就好。",
    ],
)
def test_removed_fact_and_sales_rules_do_not_reject_reply(reply: str) -> None:
    validate_model_led_reply_admission(
        [{"type": "text", "content": reply}],
        _payment_state(),
    )
