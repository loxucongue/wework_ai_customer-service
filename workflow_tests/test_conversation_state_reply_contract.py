from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.graph.nodes.conversation_state import build_conversation_state
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2


FIXTURE = Path(__file__).parent / "fixtures" / "ordinary_reply_multiturn_regressions.json"


def _case(case_id: str) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return next(item for item in payload["cases"] if item["id"] == case_id)


def test_conversation_state_extracts_customer_registration_with_evidence() -> None:
    case = _case("paid_registration_and_tentative_visit")
    snapshot = build_conversation_state(
        {
            "conversation_turns": case["turns"],
            "conversation_history": [],
            "customer_basic_info": {},
            "history_events": [],
        }
    )

    assert snapshot["customer_fields"]["name"]["status"] == "known"
    assert snapshot["customer_fields"]["mobile"]["value"] == "15800000000"
    assert snapshot["customer_fields"]["mobile"]["evidence_ids"] == ["m11"]


def test_reply_contract_blocks_requesting_known_registration_again() -> None:
    state = {
        "reply_contract": {"known_fields_not_to_request": ["name", "mobile"]},
        "conversation_state": {},
    }
    with pytest.raises(ValueError, match="known_customer_field_requested_again"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": "您把姓名和手机号再发我一下。"}],
            state,
        )


def test_reply_contract_requires_locked_image_delivery() -> None:
    state = {
        "reply_contract": {
            "required_deliveries": [
                {"message_type": "text"},
                {"message_type": "image", "asset_id": "case-1"},
            ]
        },
        "conversation_state": {},
    }
    with pytest.raises(ValueError, match="reply_contract_required_deliveries_missing:image"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": "您要的话我再发效果图。"}],
            state,
        )


def test_adjacent_payment_card_is_blocked_but_transfer_text_is_allowed() -> None:
    case = _case("adjacent_payment_card_cooldown")
    snapshot = build_conversation_state(
        {
            "conversation_turns": case["turns"],
            "conversation_history": [],
            "customer_basic_info": {},
            "history_events": [],
        }
    )
    assert snapshot["payment_card_cooldown"]["active"] is True

    state = {"conversation_state": snapshot, "reply_contract": {}}
    with pytest.raises(ValueError, match="adjacent_payment_collection_not_allowed"):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "我再发一次预约金卡。"},
                {"type": "payment_collection", "order": 2, "content": {"amount": 10}},
            ],
            state,
        )

    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": "上一张卡仍可以使用，您也可以选择转账，按您方便来。"}],
        state,
    )


def test_planner_normalizer_removes_adjacent_payment_card_and_locks_known_fields() -> None:
    snapshot = {
        "customer_fields": {
            "name": {"status": "known", "value": "何某燕"},
            "mobile": {"status": "known", "value": "15800000000"},
        },
        "payment_card_cooldown": {"active": True},
    }
    plan = build_planner_plan_v2(
        {
            "normalized_content": "还有别的付款方式吗",
            "conversation_state": snapshot,
            "conversation_history": [],
            "customer_basic_info": {},
        },
        {
            "decision": "direct_reply",
            "stage": "S3",
            "sub_rule_id": "S3_DEPOSIT",
            "conversion_stage": "deposit_push",
            "customer_type": "price",
            "main_blocker": "price",
            "next_step": "send_deposit",
            "payment_state": "needs_payment",
            "payment_action": "send_now",
            "payment_decision": {"action": "send_now", "method": "mini_program", "amount": 10},
            "current_turn_resolution": {
                "required": True,
                "customer_question": "还有别的付款方式吗",
                "resolution_goal": "回答付款方式",
            },
            "sales_progression": {
                "status": "continue",
                "target_stage": "deposit",
                "action": "send_payment_card",
                "required_message_types": ["text", "payment_collection"],
            },
            "reply_contract": {
                "known_fields_not_to_request": [],
                "required_deliveries": ["text", "payment_collection"],
            },
            "reply_messages": [
                {"type": "text", "content": "我再发一次卡。"},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
            "tool_calls": [],
            "handoff": {"needed": False, "reason": ""},
        },
    )

    assert not any(item["type"] == "payment_collection" for item in plan["planner_reply_messages"])
    assert plan["payment_action"] == "explain_existing"
    assert plan["sales_progression"]["required_message_types"] == ["text"]
    assert set(plan["reply_contract"]["known_fields_not_to_request"]) >= {"name", "mobile"}


def test_reply_context_keeps_gate_scene_evidence_and_candidate_messages() -> None:
    candidate_messages = [
        {"type": "text", "order": 1, "content": {"text": "效果参考"}},
        {"type": "image", "order": 2, "content": {"url": "https://example.com/case.png"}},
    ]
    payload = reply_user_payload_for_model(
        {
            "normalized_content": "什么方向？",
            "conversation_history": [],
            "sop_gate_decision": {
                "route": "ai_then_sop",
                "reason": "当前应交付效果案例",
                "task": {"task": "解释方向并发送案例"},
                "sop_pack_id": "s10_need_and_case",
                "sop_message_types": ["text", "image"],
            },
            "sop_gate_candidate_messages": candidate_messages,
            "reply_contract": {"required_deliveries": [{"message_type": "text"}, {"message_type": "image"}]},
        }
    )

    assert payload["sop_gate_decision"]["reason"] == "当前应交付效果案例"
    assert payload["sop_gate_candidate_messages"] == candidate_messages


def test_rendered_platform_payment_card_activates_adjacent_cooldown() -> None:
    snapshot = build_conversation_state(
        {
            "conversation_turns": [
                {"message_ref": "a1", "role": "assistant", "content": "付款给：某公司"},
                {"message_ref": "c1", "role": "customer", "content": "还有别的付款方式吗"},
            ],
            "conversation_history": [],
            "history_events": [],
        }
    )

    assert snapshot["payment_card_cooldown"]["active"] is True


@pytest.mark.parametrize(
    "text",
    [
        "可以，后天先给您暂定上。",
        "后天我先给您记上，您到时候方便了直接过来就行。",
        "这个时间目前可以，您确认要改到后天吗？",
    ],
)
def test_tentative_visit_cannot_be_rendered_as_booked_or_held(text: str) -> None:
    with pytest.raises(ValueError, match="appointment_confirmation_fact_required"):
        validate_reply_consistency(
            [{"type": "text", "order": 1, "content": text}],
            {
                "conversation_state": {"visit_context": {"state": "tentative", "date_text": "后天"}},
                "appointment_decision": {"action": "tentative_arrange", "commitment_level": "tentative"},
                "reply_contract": {"required_deliveries": [{"message_type": "text"}]},
            },
        )
