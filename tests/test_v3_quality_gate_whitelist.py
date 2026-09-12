from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.graph.nodes.reply_admission import validate_model_led_reply_admission  # noqa: E402
from app.graph.nodes.reply_nodes import (  # noqa: E402
    _parallel_generic_reply_repair_messages,
    _parallel_reply_repair_context,
    _reply_action_from_payload,
    _reply_full_task_retry_messages,
    _reply_payment_repair_guard,
    _reply_repair_hint,
    _validate_parallel_raw_reply_schema,
)
from app.graph.nodes.reply_validation import (  # noqa: E402
    _validate_parallel_activity_delivery_completeness,
    activity_delivery_contract_for_state,
)


def test_timeout_full_task_retry_reasserts_structured_delivery_contracts() -> None:
    messages = _reply_full_task_retry_messages(
        [{"role": "system", "content": "原始合同"}],
        TimeoutError("reply deadline exceeded"),
    )

    retry = messages[-1]["content"]
    assert "活动清单全部非空事实" in retry
    assert "实际输出候选要求的图片、视频或卡片" in retry
    assert "实际输出 payment_collection" in retry
    assert "人数未知按单人10元" in retry
    assert "不能用占位回复" in retry


def test_raw_schema_lifts_payment_audit_siblings_from_policy_decision() -> None:
    payload = {
        "reply_messages": [
            {"type": "text", "content": "付款入口发您。"},
            {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
        ],
        "sales_judgment": {
            "next_sales_action": {"type": "send_payment", "target_stage": "appointment"}
        },
        "policy_decision": {
            "deposit_evidence": {
                "offer_prior_turn_refs": ["sent_messages:activity_intro"],
                "supporting_key": "",
                "supporting_refs": [],
                "current_intent_refs": ["now"],
            },
            "party_size_assessment": {"status": "unknown", "evidence_refs": []},
        },
    }

    _validate_parallel_raw_reply_schema(payload)

    assert payload["action"] == "payment"
    assert payload["deposit_evidence"]["offer_prior_turn_refs"] == [
        "sent_messages:activity_intro"
    ]
    assert payload["party_size_assessment"] == {
        "status": "unknown",
        "evidence_refs": [],
    }
    assert "deposit_evidence" not in payload["policy_decision"]
    assert "party_size_assessment" not in payload["policy_decision"]


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


def test_payment_gate_accepts_exact_structured_activity_delivery_reference() -> None:
    state = _payment_state(offer_refs=["sent_messages:activity_intro"])
    shared = state["evidence_join"]["shared_context"]
    shared["conversation"] = []
    shared["authoritative_facts"]["sent_messages"] = {
        "activity_intro_image_sent": True,
    }

    validate_model_led_reply_admission(_payment_messages(10), state)


def test_payment_gate_rejects_non_activity_structured_delivery_reference() -> None:
    state = _payment_state(offer_refs=["sent_messages:case_image"])
    shared = state["evidence_join"]["shared_context"]
    shared["conversation"] = []
    shared["authoritative_facts"]["sent_messages"] = {"case_image_sent": True}

    with pytest.raises(ValueError, match="payment_collection_requires_prior_activity_evidence"):
        validate_model_led_reply_admission(_payment_messages(10), state)


def test_payment_card_normalizes_legacy_none_action_to_payment() -> None:
    assert _reply_action_from_payload(
        {
            "action": "none",
            "reply_messages": _payment_messages(10),
        }
    ) == "payment"


def test_multi_person_payment_repair_hint_is_targeted() -> None:
    hint = _reply_repair_hint("multi_person_payment_requires_known_party_size_assessment")

    assert "validation_context.current_message" in hint
    assert "status=known" in hint
    assert "每人10元" in hint


def test_missing_payment_card_repair_is_pinned_to_exact_structure() -> None:
    previous = {
        "reply_messages": [{"type": "text", "content": "我把付款入口发您。"}],
        "sales_judgment": {
            "next_sales_action": {"type": "send_payment", "target_stage": "appointment"}
        },
    }
    validation_context = {
        "schema_version": "parallel_reply_repair_context_v2",
        "structured_delivery_options": {
            "payment_collection": {
                "message_payloads": [
                    {"type": "payment_collection", "content": {"amount": 10, "remark": ""}},
                    {"type": "payment_collection", "content": {"amount": 20, "remark": ""}},
                ]
            }
        },
        "structured_prior_activity_refs": ["sent_messages:activity_intro"],
        "prior_assistant_message_refs": ["history_001"],
        "valid_customer_message_refs": ["current_message"],
        "current_message": {"content": "怎么付款报名？"},
        "mainline_delivery_state": {
            "next_missing_stage": "appointment",
            "allowed_next_sales_action_types": ["send_payment", "invite_booking"],
        },
    }

    repair_messages = _parallel_generic_reply_repair_messages(
        [{"role": "user", "content": "原始完整上下文"}],
        ValueError("reply_admission_violations::payment_action_requires_payment_collection"),
        previous_payload=previous,
        validation_context=validation_context,
    )
    rendered = "\n".join(str(item.get("content") or "") for item in repair_messages)

    assert "逐字复制 exact_payment_delivery_contract.message_payloads" in rendered
    assert "不得省略整个 deposit_evidence 对象" in rendered
    assert '"offer_prior_turn_refs":["sent_messages:activity_intro"]' in rendered
    assert "人数未知时不得继续询问，按单人10元交付" in rendered


def test_payment_repair_guard_reads_declared_next_action() -> None:
    guard = _reply_payment_repair_guard(
        {
            "sales_judgment": {
                "next_sales_action": {"type": "send_payment"}
            }
        }
    )

    assert "实际输出 exact_payment_delivery_contract" in guard
    assert "不得反问人数" in guard


def _activity_state() -> dict[str, Any]:
    offer = {
        "public_names": ["测试焕肤活动", "焕肤活动"],
        "new_customer_price": 321,
        "includes": ["肤况分析", "基础清洁"],
        "body_scope": "单部位体验",
        "offer_structure": "321元对应当前测试活动，不是多个套餐。",
        "registration_skin_test": "完成线上登记后，可免费做肤况检测。",
        "registration_gift": {"name": "保湿管理", "stated_value": 88},
        "quota": "限17名；名额满恢复原价",
        "original_price_visibility": "名额满恢复原价，不主动报原价金额。",
    }
    return {
        "evidence_join": {
            "shared_context": {
                "conversation": [],
                "rules": {"AUTHORITATIVE FACTS": {"offer": offer}},
            },
            "content_candidates": [],
        },
        "reply_sales_judgment": {
            "next_sales_action": {"type": "explain_activity"}
        },
    }


def test_activity_delivery_contract_is_dynamic_and_conditional() -> None:
    state = _activity_state()
    contract = activity_delivery_contract_for_state(state)

    assert contract["offer_facts"]["new_customer_price"] == 321
    assert any(group.get("all_of") == ["17", "名额", "恢复原价"] for group in contract["required_groups"])

    with pytest.raises(ValueError, match="activity_delivery_incomplete"):
        _validate_parallel_activity_delivery_completeness(
            [{"type": "text", "content": "活动挺划算，您要不要了解？"}],
            state,
        )

    complete = (
        "测试焕肤活动新客321元，含肤况分析和基础清洁，按单部位做。"
        "线上登记后可免费做肤况检测，还送价值88元的保湿管理。"
        "限17个名额，名额满恢复原价。"
    )
    _validate_parallel_activity_delivery_completeness(
        [{"type": "text", "content": complete}],
        state,
    )

    state["reply_sales_judgment"]["next_sales_action"]["type"] = "keep_open"
    _validate_parallel_activity_delivery_completeness(
        [{"type": "text", "content": "收到。"}],
        state,
    )


def test_activity_repair_receives_exact_dynamic_offer_facts() -> None:
    state = _activity_state()
    context = _parallel_reply_repair_context(state)
    hint = _reply_repair_hint("activity_delivery_incomplete:活动价格")

    assert context["activity_delivery_contract"]["offer_facts"]["new_customer_price"] == 321
    assert context["activity_delivery_contract"]["offer_facts"]["includes"] == [
        "肤况分析",
        "基础清洁",
    ]
    assert "保留 explain_activity" in hint
    assert "不得改成 keep_open" in hint


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


def test_missing_business_hours_are_a_current_authoritative_fact_boundary() -> None:
    with pytest.raises(ValueError, match="business_hours_fact_required"):
        validate_model_led_reply_admission(
            [{"type": "text", "content": "我们早上9点开门。"}],
            _payment_state(),
        )
