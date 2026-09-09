from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.graph.nodes.reply_nodes import (  # noqa: E402
    _normalized_customer_state,
    _prepare_structural_messages,
    _reply_validation_state,
)
from app.graph.nodes.reply_validation import (  # noqa: E402
    _validate_paid_only_store_guidance,
    _validate_parallel_business_hours_facts,
)
from app.prompts.reply_synthesizer import _render_tool_facts  # noqa: E402
from app.services.store_fact_followup import build_store_fact_followup  # noqa: E402


def _store_evidence(*, paid: bool = False) -> dict:
    structured_facts = {
        "store_facts": [
            {
                "store_id": "store-1",
                "store_name": "厦门门店",
                "store_address": "厦门市思明区测试路1号",
                "floor": "3",
                "room": "301",
                "arrival_guidance": "出电梯左转",
                "reception": "到店联系王老师",
            }
        ],
        "store_resolution_fact": {
            "status": "send_single",
            "delivery_store_ids": ["store-1"],
        },
    }
    return {
        "evidence_join": {
            "shared_context": {
                "current_message": {"content": "门店在几楼？"},
                "authoritative_facts": {
                    "orders_and_payment": {
                        "resolved_payment": {
                            "deposit_state": "paid_by_order" if paid else "required_unpaid",
                        }
                    }
                },
            },
            "structured_facts": structured_facts,
            "normalized_tool_facts": {"structured_facts": structured_facts},
            "content_candidates": [],
        },
        "reply_selected_content_ids": [],
    }


def test_hard_stop_does_not_materialize_store_card_and_rejects_sales_structures() -> None:
    state = _store_evidence()
    payload = {
        "reply_messages": [{"type": "text", "content": "好的，不再打扰您。"}],
        "policy_decision": {
            "primary_task": {"type": "hard_stop", "goal": "停止自动营销"},
            "realtime_intent": {"type": "explicit_exit", "confidence": "high"},
            "closing_decision": {
                "action": "complete",
                "customer_state": "hard_stop_marketing",
                "pressure": "none",
            },
        },
    }
    state = _reply_validation_state(state, payload)

    messages = _prepare_structural_messages(
        payload["reply_messages"],
        state,
        [],
    )

    assert [item["type"] for item in messages] == ["text"]
    with pytest.raises(ValueError, match="hard_stop_structured_delivery_forbidden"):
        _prepare_structural_messages(
            [
                {"type": "text", "content": "好的，不再打扰您。"},
                {"type": "store_address", "content": {"store_id": "store-1"}},
            ],
            state,
            [],
        )


@pytest.mark.parametrize("safety_status", ["health_risk", "complaint_refund"])
def test_hard_health_or_complaint_cannot_materialize_selected_media(
    safety_status: str,
) -> None:
    state = _store_evidence()
    state["reply_safety_assessment"] = {
        "status": safety_status,
        "evidence_refs": ["current_message"],
    }
    state["reply_selected_content_ids"] = ["effect-1"]

    with pytest.raises(ValueError, match="hard_stop_structured_delivery_forbidden:selected_content"):
        _prepare_structural_messages(
            [{"type": "text", "content": "先暂停操作，建议您及时就医。"}],
            state,
            [],
        )


def test_generic_trust_risk_can_materialize_selected_real_evidence() -> None:
    state = _store_evidence()
    state["evidence_join"]["structured_facts"]["store_resolution_fact"] = {}
    state["evidence_join"]["content_candidates"] = [
        {
            "content_id": "trust-effect-1",
            "delivery_status": "available",
            "messages": [
                {
                    "type": "image",
                    "content": "https://example.com/trust-effect.png",
                }
            ],
        }
    ]
    state["reply_policy_decision"] = {
        "primary_task": {"type": "risk", "goal": "处理普通信任质疑"},
        "realtime_intent": {"type": "blocker_expression", "confidence": "high"},
        "closing_decision": {
            "action": "pause",
            "customer_state": "continue_sales",
            "pressure": "low",
        },
    }
    state["reply_safety_assessment"] = {"status": "none", "evidence_refs": []}
    state["reply_selected_content_ids"] = ["trust-effect-1"]

    messages = _prepare_structural_messages(
        [{"type": "text", "content": "您可以先看看我们实际做过的改善案例。"}],
        state,
        [],
    )

    assert messages == [
        {"type": "text", "content": "您可以先看看我们实际做过的改善案例。", "order": 1},
        {
            "type": "image",
            "content": "https://example.com/trust-effect.png",
            "order": 2,
        },
    ]


def test_unpaid_store_guidance_is_hidden_from_prompt_and_rejected_in_reply() -> None:
    state = _store_evidence(paid=False)
    evidence = {
        "normalized_tool_facts": {
            "structured_facts": state["evidence_join"]["structured_facts"],
        }
    }

    rendered = _render_tool_facts(
        evidence,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
        authoritative_paid=False,
    )

    assert "厦门市思明区测试路1号" in rendered
    assert "floor" not in rendered
    assert "room" not in rendered
    assert "出电梯左转" not in rendered
    assert "王老师" not in rendered
    with pytest.raises(ValueError, match="paid_store_arrival_guidance_required"):
        _validate_paid_only_store_guidance(
            [{"type": "text", "content": "门店在3楼301室，出电梯左转。"}],
            state,
        )


def test_authoritative_parking_floor_remains_public_before_payment() -> None:
    state = _store_evidence(paid=False)
    store = state["evidence_join"]["structured_facts"]["store_facts"][0]
    store["parking"] = "负一层停车场"

    _validate_paid_only_store_guidance(
        [{"type": "text", "content": "可以停车的，车可以停在负一层停车场。"}],
        state,
    )

    with pytest.raises(ValueError, match="paid_store_arrival_guidance_required"):
        _validate_paid_only_store_guidance(
            [{"type": "text", "content": "门店在负二层，到了联系老师。"}],
            state,
        )


def test_paid_store_guidance_remains_available() -> None:
    state = _store_evidence(paid=True)
    evidence = {
        "normalized_tool_facts": {
            "structured_facts": state["evidence_join"]["structured_facts"],
        }
    }

    rendered = _render_tool_facts(
        evidence,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
        authoritative_paid=True,
    )

    assert "floor=3" in rendered
    assert "room=301" in rendered
    assert "出电梯左转" in rendered
    _validate_paid_only_store_guidance(
        [{"type": "text", "content": "门店在3楼301室，出电梯左转。"}],
        state,
    )


def test_paid_reused_confirmed_store_guidance_remains_available() -> None:
    state = _store_evidence(paid=True)
    resolution = state["evidence_join"]["structured_facts"]["store_resolution_fact"]
    resolution.update(
        {
            "status": "reuse_confirmed_store",
            "delivery_store_ids": [],
            "already_delivered_store_ids": ["store-1"],
        }
    )
    evidence = {
        "normalized_tool_facts": {
            "structured_facts": state["evidence_join"]["structured_facts"],
        }
    }

    rendered = _render_tool_facts(
        evidence,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
        authoritative_paid=True,
    )

    assert "floor=3" in rendered
    assert "room=301" in rendered
    assert "出电梯左转" in rendered
    _validate_paid_only_store_guidance(
        [{"type": "text", "content": "门店在3楼301室，出电梯左转。"}],
        state,
    )


def test_paid_unique_store_rejects_other_or_invented_private_guidance() -> None:
    state = _store_evidence(paid=True)
    state["evidence_join"]["structured_facts"]["store_facts"].append(
        {
            "store_id": "store-2",
            "store_name": "厦门二店",
            "store_address": "厦门市湖里区测试路2号",
            "floor": "8",
            "room": "802",
            "arrival_guidance": "出电梯右转",
            "reception": "到店联系李老师",
        }
    )

    with pytest.raises(ValueError, match="paid_store_arrival_guidance_required"):
        _validate_paid_only_store_guidance(
            [{"type": "text", "content": "您到厦门二店8楼802室，出电梯右转。"}],
            state,
        )
    with pytest.raises(ValueError, match="paid_store_arrival_guidance_required"):
        _validate_paid_only_store_guidance(
            [{"type": "text", "content": "您到5楼501室。"}],
            state,
        )


def test_paid_but_unresolved_store_guidance_is_hidden_and_rejected() -> None:
    state = _store_evidence(paid=True)
    structured = state["evidence_join"]["structured_facts"]
    structured["store_facts"].append(
        {
            "store_id": "store-2",
            "store_name": "厦门二店",
            "store_address": "厦门市湖里区测试路2号",
            "floor": "8",
            "room": "802",
            "arrival_guidance": "出电梯右转",
            "reception": "到店联系李老师",
        }
    )
    structured["store_resolution_fact"] = {
        "status": "clarify_location",
        "delivery_store_ids": [],
    }
    evidence = {"normalized_tool_facts": {"structured_facts": structured}}

    rendered = _render_tool_facts(
        evidence,
        json_dumps=lambda value: json.dumps(value, ensure_ascii=False),
        authoritative_paid=True,
    )

    assert "厦门市思明区测试路1号" in rendered
    assert "厦门市湖里区测试路2号" in rendered
    assert "出电梯左转" not in rendered
    assert "出电梯右转" not in rendered
    assert "room=301" not in rendered
    assert "room=802" not in rendered
    with pytest.raises(ValueError, match="paid_store_arrival_guidance_required"):
        _validate_paid_only_store_guidance(
            [{"type": "text", "content": "先到3楼301室，出电梯左转。"}],
            state,
        )


def test_store_fact_followup_withholds_paid_only_detail_before_payment() -> None:
    resolution = {
        "status": "send_single",
        "delivery_store_ids": ["store-1"],
        "requested_detail_kind": "arrival_guidance",
    }
    stores = [
        {
            "store_id": "store-1",
            "store_name": "厦门门店",
            "address": "厦门市思明区测试路1号",
            "floor": "3",
            "room": "301",
            "arrival_guidance": "出电梯左转",
        }
    ]

    unpaid = build_store_fact_followup(
        store_resolution_fact=resolution,
        store_facts=stores,
        authoritative_paid=False,
    )
    paid = build_store_fact_followup(
        store_resolution_fact=resolution,
        store_facts=stores,
        authoritative_paid=True,
    )

    assert unpaid["requested_detail"] == {
        "kind": "arrival_guidance",
        "status": "payment_required",
        "facts": {},
        "missing_fact_keys": [],
    }
    assert unpaid["internal_followup"] == {}
    assert paid["requested_detail"]["status"] == "available"
    assert paid["requested_detail"]["facts"]["room"] == "301"


def test_appointment_window_is_not_mistaken_for_store_business_hours() -> None:
    state = {
        "evidence_join": {
            "shared_context": {"current_message": {"content": "我明天有空"}},
            "structured_facts": {},
        }
    }

    _validate_parallel_business_hours_facts(
        [{"type": "text", "content": "您明天9:00-10:00方便吗？"}],
        state,
    )

    state["evidence_join"]["shared_context"]["current_message"] = {"content": "门店几点开门？"}
    with pytest.raises(ValueError, match="business_hours_fact_required"):
        _validate_parallel_business_hours_facts(
            [{"type": "text", "content": "9:00-10:00。"}],
            state,
        )


def test_legacy_terminal_or_handoff_never_degrades_to_resumable_pause() -> None:
    assert _normalized_customer_state(
        "transaction_terminal_or_handoff",
        authoritative_paid=False,
    ) == ("hard_stop_marketing", "")
    assert _normalized_customer_state(
        "transaction_terminal_or_handoff",
        authoritative_paid=True,
    ) == ("post_payment_service", "")
