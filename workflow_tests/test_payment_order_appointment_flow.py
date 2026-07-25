from __future__ import annotations

from pathlib import Path
import asyncio
import tempfile

import pytest

from app.config import Settings
from app.graph.nodes.action_nodes import create_execute_actions_node
from app.graph.nodes.layer_nodes import _platform_unknown_transfer_image_info
from app.graph.nodes.image_validation import validated_image_info
from app.graph.nodes.profile_nodes import _deterministic_customer_state_updates
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.services.customer_order_context import compact_order
from app.services.customer_payment_state import normalize_prepay_facts, resolved_payment_fact
from app.services.trace_logger import TraceLogger


class _PlatformClient:
    def __init__(self, *, orders: list[dict] | None = None, check_result: dict | None = None) -> None:
        self.orders = orders or []
        self.check_result = check_result or {"success": True, "can_create": True}
        self.created_work: list[dict] = []
        self.modified_work: list[dict] = []
        self.synced_mobile: list[dict] = []
        self.created_plan: list[dict] = []
        self.checked_customer: list[dict] = []

    def list_orders(self, **_kwargs):
        return list(self.orders)

    def check_customer(self, **kwargs):
        self.checked_customer.append(kwargs)
        return dict(self.check_result)

    def get_customer_info(self, **_kwargs):
        return {"customer_add_wechat_id": "88", "kind": 1, "category_id": "10"}

    def create_work_order(self, **kwargs):
        self.created_work.append(kwargs)
        return {"success": True, "data": {"order_id": "order-101"}}

    def modify_work_order(self, **kwargs):
        self.modified_work.append(kwargs)
        return {"success": True, "order_id": kwargs.get("order_id")}

    def add_customer_mobile(self, **kwargs):
        self.synced_mobile.append(kwargs)
        return {"success": True}

    def create_order_plan(self, **kwargs):
        self.created_plan.append(kwargs)
        return {"success": True, "id": "plan-101"}


def _base_state() -> dict:
    return {
        "request_id": "payment-flow-test",
        "customer_id": "21325693",
        "user_id": 7294,
        "request_context": {"user_id": 7294, "corp_id": "corp", "wechat": "CS001", "category_id": "10"},
        "customer_context": {
            "platform_customer_id": "21325693",
            "customer_add_wechat_id": "88",
            "customer": {"kind": 1, "category_id": "10"},
            "orders": [],
        },
        "customer_store_knowledge": {"stores": [{"store_id": "386", "store_name": "厦门百星店"}]},
        "trace": [],
        "errors": [],
    }


def test_order_context_supports_new_prepay_fields() -> None:
    order = compact_order(
        {
            "id": 1,
            "status": 2,
            "store_id": 386,
            "prepay_required": 20,
            "prepay_paid": 20,
        }
    )
    assert order["prepay_required"] == 20
    assert order["prepay_paid"] == 20
    assert order["deposit_state"] == "paid_by_order"
    assert order["order_binding_state"] == "needs_binding"


def test_successful_payment_screenshot_is_paid_and_is_not_downgraded() -> None:
    image = validated_image_info(
        {
            "info": {
                "image_type": "payment_proof",
                "image_intent": "general_image",
                "payment_result": "success",
                "payment_amount": 20,
                "confidence": 0.97,
            }
        },
        has_image=True,
    )
    payment = resolved_payment_fact(
        orders=[{"id": "order-1", "prepay_required": 20, "prepay_paid": 0, "deposit_state": "required_unpaid"}],
        image_info=image,
    )
    assert payment["deposit_state"] == "paid_by_screenshot"
    assert payment["order_id"] == "order-1"

    persisted = resolved_payment_fact(
        orders=[{"id": "order-1", "prepay_required": 20, "prepay_paid": 0, "deposit_state": "required_unpaid"}],
        existing_state="paid_by_screenshot",
        existing_source="vision.payment_proof",
        existing_fact=payment,
    )
    assert persisted["deposit_state"] == "paid_by_screenshot"


def test_platform_unknown_message_placeholder_is_treated_as_transfer_success() -> None:
    image = _platform_unknown_transfer_image_info("【未知消息类型】")
    assert image is not None
    assert image["image_type"] == "payment_proof"
    assert image["payment_result"] == "success"

    payment = resolved_payment_fact(
        orders=[{"id": "order-unknown-transfer", "prepay_required": 10, "prepay_paid": 0, "deposit_state": "required_unpaid"}],
        image_info=image,
    )

    assert payment["deposit_state"] == "paid_by_platform_transfer_event"
    assert payment["order_id"] == "order-unknown-transfer"
    assert payment["source"] == "platform.unknown_message_transfer"


def test_payment_card_does_not_require_matching_order_fact_in_full_planner_state() -> None:
    state = {
        **_base_state(),
        "normalized_content": "就这家，我报名",
        "confirmed_store_id": "386",
        "confirmed_store_name": "厦门百星店",
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "conversion_stage": "deposit_push",
            "next_step": "send_deposit",
            "payment_decision": {"action": "send_now", "party_size": 1, "amount": 10},
            "order_decision": {"action": "none"},
            "reply_messages": [
                {"type": "text", "content": {"text": "我把小程序收款卡发您。"}},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
            "tool_calls": [],
        },
    )
    assert not any(item.get("missing") == "work_order_required_before_payment_collection" for item in plan["tool_policy_violations"])
    validate_reply_consistency(
        plan["planner_reply_messages"],
        {**state, **plan, "fact_envelope": {"structured_facts": {"order_facts": []}}},
    )


def test_planner_reuses_matching_active_order_instead_of_incomplete_create_tool() -> None:
    state = {
        **_base_state(),
        "confirmed_store_id": "386",
        "customer_context": {
            **_base_state()["customer_context"],
            "orders": [
                {
                    "id": "order-existing",
                    "status": "pending",
                    "store_id": "386",
                    "category_id": "0",
                    "prepay_required": "10.00",
                    "prepay_paid": "0.00",
                }
            ],
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "payment_decision": {"action": "send_now", "amount": 10},
            "order_decision": {"action": "none"},
            "reply_messages": [{"type": "text", "content": {"text": "我先给您处理。"}}],
            "tool_calls": [{"name": "create_work_order", "store_id": "386"}],
        },
    )

    assert plan["order_decision"]["action"] == "use_existing"
    assert plan["order_decision"]["order_id"] == "order-existing"
    assert not any(item.get("name") == "create_work_order" for item in plan["planner_tool_calls"])
    assert not any(item.get("missing", "").startswith("create_work_order_missing") for item in plan["tool_policy_violations"])


def test_planner_reuses_required_unpaid_order_when_platform_omits_lifecycle_status() -> None:
    state = {
        **_base_state(),
        "confirmed_store_id": "386",
        "customer_context": {
            **_base_state()["customer_context"],
            "orders": [
                {
                    "id": "order-without-status",
                    "store_id": "386",
                    "prepay_required": 10,
                    "prepay_paid": 0,
                    "deposit_state": "required_unpaid",
                }
            ],
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "payment_decision": {"action": "send_now", "amount": 10},
            "order_decision": {"action": "use_existing", "order_id": "order-without-status", "store_id": "386", "amount": 10},
            "reply_messages": [],
            "tool_calls": [{"name": "create_work_order", "store_id": "386", "amount": 10}],
        },
    )

    assert plan["order_decision"]["action"] == "use_existing"
    assert plan["order_decision"]["order_id"] == "order-without-status"
    assert not any(item.get("name") == "create_work_order" for item in plan["planner_tool_calls"])
    assert not any(item.get("missing", "").startswith("create_work_order_missing") for item in plan["tool_policy_violations"])


def test_active_order_from_another_store_does_not_block_payment_card() -> None:
    state = {
        **_base_state(),
        "confirmed_store_id": "386",
        "customer_context": {
            **_base_state()["customer_context"],
            "orders": [
                {
                    "id": "order-other-store",
                    "status": "pending",
                    "store_id": "369",
                    "prepay_required": 10,
                }
            ],
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "payment_decision": {"action": "send_now", "amount": 10},
            "order_decision": {"action": "none", "store_id": "386"},
            "reply_messages": [
                {"type": "text", "content": {"text": "我把小程序收款卡发您。"}},
                {"type": "payment_collection", "content": {"amount": 10}},
            ],
            "tool_calls": [],
        },
    )

    assert not any(item.get("missing") == "work_order_required_before_payment_collection" for item in plan["tool_policy_violations"])


def test_payment_state_uses_marked_current_order_instead_of_other_paid_order() -> None:
    payment = resolved_payment_fact(
        orders=[
            {
                "id": "order-current",
                "status": "pending",
                "store_id": "386",
                "prepay_required": "10.00",
                "prepay_paid": "0.00",
                "is_current_order": True,
            },
            {
                "id": "order-old",
                "status": "pending",
                "store_id": "369",
                "prepay_required": "10.00",
                "prepay_paid": "10.00",
                "created_at": "2020-01-01T00:00:00+00:00",
            },
        ]
    )

    assert payment["order_id"] == "order-current"
    assert payment["deposit_state"] == "required_unpaid"


def test_payment_label_required_does_not_mean_paid() -> None:
    payment = normalize_prepay_facts({"prepay_required": 10, "prepay_paid": "需支付预约金"})

    assert payment["deposit_state"] == "required_unpaid"


def test_payment_label_paid_does_not_mean_paid_without_numeric_amount() -> None:
    payment = normalize_prepay_facts({"prepay_required": 10, "prepay_paid": "已支付"})

    assert payment["deposit_state"] == "required_unpaid"


def test_paid_customer_cannot_create_formal_appointment_plan() -> None:
    state = {
        **_base_state(),
        "customer_basic_info": {"customer_name": "测试客户", "phone": "13800138000", "deposit_state": {"status": "paid_by_screenshot"}},
        "customer_context": {**_base_state()["customer_context"], "orders": [{"id": "order-101", "status": "waiting_schedule", "store_id": "386", "deposit_state": "paid_by_order"}]},
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "payment_decision": {"action": "after_paid_next_step"},
            "order_decision": {"action": "use_existing", "order_id": "order-101", "store_id": "386"},
            "appointment_decision": {"action": "create_plan", "commitment_level": "confirmed"},
            "reply_messages": [{"type": "text", "content": {"text": "两点半已经安排好。"}}],
            "tool_calls": [
                {
                    "name": "create_order_plan",
                    "order_id": "order-101",
                    "store_id": "386",
                    "appointment_time": "2026-07-16 14:30",
                }
            ],
        },
    )
    assert any(
        item.get("missing") == "create_order_plan_disabled_after_payment"
        for item in plan["tool_policy_violations"]
    )


def test_incomplete_mobile_is_rejected_before_tool_execution() -> None:
    plan = build_planner_plan_v2(
        {**_base_state(), "normalized_content": "12345"},
        {
            "decision": "need_tools",
            "payment_decision": {"action": "after_paid_next_step"},
            "appointment_decision": {"action": "ask_time", "commitment_level": "none"},
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{"name": "add_customer_mobile", "mobile": "12345"}],
        },
    )

    assert any(
        item.get("missing") == "add_customer_mobile_invalid_mobile"
        for item in plan["tool_policy_violations"]
    )


def test_need_tools_without_executable_tool_requires_repair() -> None:
    plan = build_planner_plan_v2(
        _base_state(),
        {
            "decision": "need_tools",
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [],
        },
    )

    assert any(
        item.get("missing") == "need_tools_requires_executable_tool"
        for item in plan["tool_policy_violations"]
    )


def test_paid_payment_decision_inherits_amount_from_existing_order() -> None:
    state = {
        **_base_state(),
        "customer_context": {
            **_base_state()["customer_context"],
            "orders": [
                {
                    "id": "order-paid",
                    "status": "waiting_schedule",
                    "store_id": "386",
                    "prepay_required": 30,
                    "prepay_paid": 30,
                    "deposit_state": "paid_by_order",
                }
            ],
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "payment_state": "customer_claimed_paid",
            "payment_decision": {"action": "after_paid_next_step", "party_size": 1, "amount": 10},
            "order_decision": {"action": "use_existing", "order_id": "order-paid", "store_id": "386", "amount": 30},
            "appointment_decision": {"action": "ask_time", "commitment_level": "none"},
            "reply_messages": [{"type": "text", "content": {"text": "收到，把姓名发我就行。"}}],
            "tool_calls": [],
        },
    )

    assert plan["payment_decision"]["party_size"] == 3
    assert plan["payment_decision"]["amount"] == 30
    assert any(
        item.get("missing") == "registration_required_before_appointment_decision"
        for item in plan["tool_policy_violations"]
    )


def test_different_target_time_cannot_confirm_old_appointment() -> None:
    state = {
        **_base_state(),
        "normalized_content": "能提前到两点半吗",
        "customer_context": {
            **_base_state()["customer_context"],
            "appointment": {"status": "confirmed", "appointment_time": "2026-07-14 15:00:00"},
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "appointment_decision": {"action": "confirm_existing", "commitment_level": "confirmed"},
            "reply_messages": [{"type": "text", "content": {"text": "我先核对一下档期。"}}],
            "tool_calls": [],
        },
    )

    missing = {item.get("missing") for item in plan["tool_policy_violations"]}
    assert "direct_reply_promises_unfinished_lookup" in missing
    assert "appointment_change_requires_verification" in missing


def test_chinese_twelve_hour_time_uses_supported_afternoon_slot_for_repair() -> None:
    state = {
        **_base_state(),
        "normalized_content": "两点半没问题，就按这个改吧",
        "customer_context": {
            **_base_state()["customer_context"],
            "appointment": {"status": "confirmed", "appointment_time": "2026-07-14 15:00:00"},
        },
        "fact_envelope": {
            "structured_facts": {
                "appointment_facts": [
                    {
                        "type": "available_time",
                        "date": "2026-07-14",
                        "target_time": "14:30",
                        "target_time_available": True,
                    }
                ]
            }
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "appointment_decision": {"action": "confirm_existing", "commitment_level": "confirmed"},
            "reply_messages": [{"type": "text", "content": {"text": "好的，已改到两点半。"}}],
            "tool_calls": [],
        },
    )

    violation = next(
        item
        for item in plan["tool_policy_violations"]
        if item.get("missing") == "appointment_change_requires_verification"
    )
    assert "target time 14:30" in violation["note"]
    assert "use create_plan" in violation["note"]


def test_chinese_twelve_hour_time_matches_existing_afternoon_appointment() -> None:
    state = {
        **_base_state(),
        "normalized_content": "那就两点半",
        "customer_context": {
            **_base_state()["customer_context"],
            "appointment": {"status": "confirmed", "appointment_time": "2026-07-14 14:30:00"},
        },
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "appointment_decision": {"action": "confirm_existing", "commitment_level": "confirmed"},
            "sales_progression": {
                "status": "terminal",
                "target_stage": "close",
                "action": "close",
            },
            "reply_messages": [{"type": "text", "content": {"text": "好的，明天下午两点半见。"}}],
            "tool_calls": [],
        },
    )

    assert not any(
        item.get("missing") == "appointment_change_requires_verification"
        for item in plan["tool_policy_violations"]
    )


def test_current_appointment_created_blocks_payment_card_in_same_turn() -> None:
    state = {
        **_base_state(),
        "order_decision": {"action": "use_existing", "order_id": "order-101", "store_id": "386"},
        "fact_envelope": {
            "structured_facts": {
                "order_facts": [
                    {"status": "reused", "order_id": "order-101", "store_id": "386"},
                ],
                "appointment_facts": [
                    {
                        "type": "appointment_created",
                        "status": "created",
                        "appointment_id": "plan-101",
                        "store_id": "386",
                        "appointment_time": "2026-07-13 14:30:00",
                    }
                ],
            }
        },
    }
    messages = [
        {"type": "text", "content": {"text": "10元预约金用于锁名额，到店抵扣。"}},
        {"type": "payment_collection", "content": {"amount": 10}},
    ]

    with pytest.raises(ValueError, match="payment_collection_after_appointment_created"):
        validate_reply_consistency(messages, state)


def test_create_work_order_then_exposes_order_fact() -> None:
    platform = _PlatformClient()
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        state = {
            **_base_state(),
            "planner_tool_calls": [
                {
                    "name": "create_work_order",
                    "store_id": "386",
                    "category_id": "10",
                    "prepay": 20,
                    "store_confirmation_source": "current_message",
                }
            ],
        }
        output = asyncio.run(node(state))

    assert output["tool_results"]["create_work_order"]["status"] == "created"
    assert output["tool_results"]["create_work_order"]["order_id"] == "order-101"
    assert platform.created_work[0]["prepay"] == 20
    assert platform.checked_customer[0]["kind"] == 1
    order_facts = output["fact_envelope"]["structured_facts"]["order_facts"]
    assert order_facts[0]["status"] == "created"


def test_create_work_order_accepts_shared_current_known_store_fact() -> None:
    platform = _PlatformClient()
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        state = {
            **_base_state(),
            "customer_store_knowledge": {"stores": []},
            "current_known_store": {
                "store_id": "12",
                "store_name": "厦门思明店",
                "source": "recent_conversation",
            },
            "planner_tool_calls": [
                {
                    "name": "create_work_order",
                    "store_id": "12",
                    "category_id": "10",
                    "prepay": 10,
                    "store_confirmation_source": "recent_explicit_choice",
                }
            ],
        }
        output = asyncio.run(node(state))

    assert output["tool_results"]["create_work_order"]["status"] == "created"
    assert output["tool_results"]["create_work_order"]["store_id"] == "12"


def test_create_work_order_accepts_latest_single_store_card_anchor() -> None:
    platform = _PlatformClient()
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **_base_state(),
            "history_events": [
                {
                    "event_id": "store-card-386",
                    "event_type": "store_address_sent",
                    "created_at": "2026-07-19T08:00:00+00:00",
                    "facts": {"store_id": "386", "request_id": "store-card-request"},
                }
            ],
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "category_id": "10",
                "prepay": 10,
                "store_confirmation_source": "single_store_card_anchor",
            }],
        }))

    result = output["tool_results"]["create_work_order"]
    assert result["status"] == "created"
    assert result["store_id"] == "386"
    assert result["store_confirmation_source"] == "single_store_card_anchor"


def test_store_anchor_fact_distinguishes_unique_multi_and_unverified_batches() -> None:
    unique = sent_message_summary_for_model({
        "history_events": [{
            "event_type": "store_address_sent",
            "created_at": "2026-07-19T08:00:00+00:00",
            "facts": {"store_id": "386", "request_id": "request-1"},
        }]
    })["store_anchor_fact"]
    multi = sent_message_summary_for_model({
        "history_events": [
            {
                "event_type": "store_address_sent",
                "created_at": "2026-07-19T08:00:00+00:00",
                "facts": {"store_id": "386", "request_id": "request-2"},
            },
            {
                "event_type": "store_address_sent",
                "created_at": "2026-07-19T08:00:00+00:00",
                "facts": {"store_id": "562", "request_id": "request-2"},
            },
        ]
    })["store_anchor_fact"]
    unverified = sent_message_summary_for_model({
        "history_events": [{
            "event_type": "store_address_sent",
            "created_at": "2026-07-19T08:00:00+00:00",
            "facts": {"store_id": "386"},
        }]
    })["store_anchor_fact"]

    assert unique["status"] == "eligible"
    assert unique["store_id"] == "386"
    assert multi["status"] == "ambiguous"
    assert multi["store_ids"] == ["386", "562"]
    assert unverified["status"] == "unverified"
    assert "store_id" not in unverified


def test_planner_store_binding_exploring_blocks_work_order_tool() -> None:
    state = {**_base_state(), "normalized_content": "我再比较一下这几家"}
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "payment_decision": {"action": "explain", "amount": 10},
            "store_binding_decision": {
                "status": "exploring",
                "store_id": "386",
                "confidence": "high",
                "basis": ["客户仍在比较"],
            },
            "order_decision": {
                "action": "create_work",
                "store_id": "386",
                "amount": 10,
                "store_binding_level": "explicit_confirmed",
            },
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "prepay": 10,
                "store_confirmation_source": "current_message",
            }],
        },
    )

    assert plan["store_binding_decision"]["status"] == "exploring"
    assert any(
        item.get("missing") == "create_work_order_store_binding_not_accepted"
        for item in plan["tool_policy_violations"]
    )


def test_planner_accepted_store_binding_does_not_require_order_resolution_before_payment() -> None:
    state = {
        **_base_state(),
        "normalized_content": "这个活动具体多少钱",
        "history_events": [{
            "event_type": "store_address_sent",
            "created_at": "2026-07-19T08:00:00+00:00",
            "facts": {"store_id": "386", "request_id": "request-accepted"},
        }],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "direct_reply",
            "payment_decision": {"action": "explain", "amount": 10},
            "store_binding_decision": {
                "status": "accepted_implicit",
                "store_id": "386",
                "confidence": "high",
            },
            "order_decision": {
                "action": "none",
                "store_id": "386",
                "amount": 10,
                "store_binding_level": "single_store_card_anchor",
            },
            "appointment_decision": {"action": "none", "commitment_level": "tentative"},
            "reply_messages": [
                {"type": "text", "content": {"text": "活动是268元。"}},
                {"type": "text", "content": {"text": "到店时间后面按您方便安排。"}},
            ],
            "tool_calls": [],
        },
    )

    assert not any(
        item.get("missing") == "accepted_store_binding_requires_order_resolution"
        for item in plan["tool_policy_violations"]
    )
    assert not any(item.get("name") == "create_work_order" for item in plan["planner_tool_calls"])
    assert not any(
        item.get("missing") == "available_time_required_for_availability_claim"
        for item in plan["tool_policy_violations"]
    )
    assert plan["payment_decision"]["action"] == "explain"
    assert all(item.get("type") != "payment_collection" for item in plan["planner_reply_messages"])


def test_planner_unverified_store_event_cannot_be_accepted_implicit() -> None:
    state = {
        **_base_state(),
        "normalized_content": "活动怎么报名",
        "history_events": [{
            "event_type": "store_address_sent",
            "created_at": "2026-07-19T08:00:00+00:00",
            "facts": {"store_id": "386"},
        }],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "payment_decision": {"action": "send_now", "amount": 10},
            "store_binding_decision": {
                "status": "accepted_implicit",
                "store_id": "386",
                "confidence": "medium",
            },
            "order_decision": {
                "action": "create_work",
                "store_id": "386",
                "amount": 10,
                "store_binding_level": "single_store_card_anchor",
            },
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "prepay": 10,
                "store_confirmation_source": "single_store_card_anchor",
            }],
        },
    )

    assert any(
        item.get("missing") == "accepted_implicit_requires_eligible_store_anchor_fact"
        for item in plan["tool_policy_violations"]
    )


def test_create_work_order_allows_missing_optional_platform_fields() -> None:
    platform = _PlatformClient()
    platform.get_customer_info = lambda **_kwargs: {}
    state = _base_state()
    state["user_id"] = None
    state["request_context"] = {"corp_id": "corp", "wechat": "CS001"}
    state["customer_context"] = {
        "platform_customer_id": "21325693",
        "customer": {},
        "orders": [],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **state,
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "prepay": 10,
                "store_confirmation_source": "current_message",
            }],
        }))

    result = output["tool_results"]["create_work_order"]
    assert result["status"] == "created"
    assert result["creation_mode"] == "partial"
    assert set(result["missing_optional_fields"]) == {
        "customer_add_wechat_id",
        "user_id",
        "kind",
        "category_id",
    }
    assert platform.checked_customer == []
    assert platform.created_work[0]["user_id"] is None
    order_fact = output["fact_envelope"]["structured_facts"]["order_facts"][0]
    assert order_fact["missing_optional_fields"] == result["missing_optional_fields"]


def test_create_work_order_rejects_multi_store_card_batch_anchor() -> None:
    platform = _PlatformClient()
    state = _base_state()
    state["customer_store_knowledge"] = {
        "stores": [
            {"store_id": "386", "store_name": "厦门百星店"},
            {"store_id": "562", "store_name": "厦门湖里店"},
        ]
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **state,
            "history_events": [
                {
                    "event_id": "store-card-386",
                    "event_type": "store_address_sent",
                    "created_at": "2026-07-19T08:00:00+00:00",
                    "facts": {"store_id": "386", "request_id": "store-card-request"},
                },
                {
                    "event_id": "store-card-562",
                    "event_type": "store_address_sent",
                    "created_at": "2026-07-19T08:00:00+00:00",
                    "facts": {"store_id": "562", "request_id": "store-card-request"},
                },
            ],
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "category_id": "10",
                "prepay": 10,
                "store_confirmation_source": "single_store_card_anchor",
            }],
        }))

    result = output["tool_results"]["create_work_order"]
    assert result["status"] == "rejected"
    assert result["error"] == "single_store_card_anchor_not_authoritative"
    assert platform.created_work == []


def test_planner_policy_accepts_matching_single_store_card_anchor() -> None:
    state = {
        **_base_state(),
        "normalized_content": "活动怎么报名",
        "history_events": [
            {
                "event_id": "store-card-386",
                "event_type": "store_address_sent",
                "created_at": "2026-07-19T08:00:00+00:00",
                "facts": {"store_id": "386", "request_id": "store-card-request"},
            }
        ],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "conversion_stage": "deposit_push",
            "payment_decision": {"action": "send_now", "party_size": 1, "amount": 10},
            "order_decision": {
                "action": "create_work",
                "store_id": "386",
                "amount": 10,
                "store_binding_level": "single_store_card_anchor",
                "source": "single_store_card_anchor",
            },
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [
                {
                    "name": "create_work_order",
                    "store_id": "386",
                    "category_id": "10",
                    "prepay": 10,
                    "store_confirmation_source": "single_store_card_anchor",
                }
            ],
        },
    )

    assert plan["order_decision"]["store_binding_level"] == "single_store_card_anchor"
    assert not any(
        item.get("missing") == "create_work_order_single_store_card_anchor_mismatch"
        for item in plan["tool_policy_violations"]
    )


def test_planner_policy_rejects_mismatched_single_store_card_anchor() -> None:
    state = {
        **_base_state(),
        "normalized_content": "活动怎么报名",
        "history_events": [
            {
                "event_id": "store-card-386",
                "event_type": "store_address_sent",
                "created_at": "2026-07-19T08:00:00+00:00",
                "facts": {"store_id": "386", "request_id": "store-card-request"},
            }
        ],
    }
    plan = build_planner_plan_v2(
        state,
        {
            "decision": "need_tools",
            "conversion_stage": "deposit_push",
            "payment_decision": {"action": "send_now", "party_size": 1, "amount": 10},
            "order_decision": {
                "action": "create_work",
                "store_id": "562",
                "amount": 10,
                "store_binding_level": "single_store_card_anchor",
                "source": "single_store_card_anchor",
            },
            "reply_messages": [{"type": "text", "content": {"text": "稍等一下哈"}}],
            "tool_calls": [
                {
                    "name": "create_work_order",
                    "store_id": "562",
                    "category_id": "10",
                    "prepay": 10,
                    "store_confirmation_source": "single_store_card_anchor",
                }
            ],
        },
    )

    assert any(
        item.get("missing") == "create_work_order_single_store_card_anchor_mismatch"
        for item in plan["tool_policy_violations"]
    )


def test_no_tool_does_not_execute_platform_transaction_placeholders() -> None:
    platform = _PlatformClient()
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **_base_state(),
            "planner_tool_calls": [{"name": "no_tool"}],
        }))

    assert "create_work_order" not in output["tool_results"]
    assert "add_customer_mobile" not in output["tool_results"]
    assert "create_order_plan" not in output["tool_results"]


def test_create_work_order_accepts_nested_platform_order_id() -> None:
    platform = _PlatformClient()
    platform.create_work_order = lambda **_kwargs: {
        "success": True,
        "data": {"order": {"id": "order-nested-101"}},
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **_base_state(),
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "category_id": "10",
                "prepay": 10,
                "store_confirmation_source": "current_message",
            }],
        }))

    assert output["tool_results"]["create_work_order"]["status"] == "created"
    assert output["tool_results"]["create_work_order"]["order_id"] == "order-nested-101"


def test_existing_work_order_is_reused_without_duplicate_create() -> None:
    platform = _PlatformClient(
        orders=[{"id": "order-existing", "status": 1, "store_id": "386", "category_id": "10", "prepay_required": 10, "prepay_paid": 0}]
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node(
            {
                **_base_state(),
                "planner_tool_calls": [
                    {
                        "name": "create_work_order",
                        "store_id": "386",
                        "category_id": "10",
                        "prepay": 10,
                        "store_confirmation_source": "recent_explicit_choice",
                    }
                ],
            }
        ))

    assert output["tool_results"]["create_work_order"]["status"] == "reused"
    assert output["tool_results"]["create_work_order"]["order_id"] == "order-existing"
    assert platform.created_work == []


def test_existing_zero_category_order_is_reused_for_confirmed_category() -> None:
    platform = _PlatformClient(
        orders=[{"id": "order-existing", "status": 1, "store_id": "386", "category_id": 0, "prepay_required": 10, "prepay_paid": 0}]
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **_base_state(),
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "category_id": "10",
                "prepay": 10,
                "store_confirmation_source": "current_message",
            }],
        }))

    assert output["tool_results"]["create_work_order"]["status"] == "reused"
    assert platform.created_work == []


def test_paid_unbound_work_order_is_bound_instead_of_duplicate_create() -> None:
    platform = _PlatformClient(
        orders=[
            {
                "id": "order-paid-unbound",
                "order_no": "2607192311534541",
                "status": 2,
                "store_id": 0,
                "category_id": 0,
                "prepay_required": 0,
                "prepay_paid": 10,
            }
        ],
        check_result={"result": 0},
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **_base_state(),
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "category_id": "10",
                "prepay": 10,
                "store_confirmation_source": "current_message",
            }],
        }))

    result = output["tool_results"]["create_work_order"]
    assert result["status"] == "reused"
    assert result["source"] == "platform_agent.order.modify"
    assert result["order_binding_repaired"] is True
    assert result["order_id"] == "order-paid-unbound"
    assert result["store_id"] == "386"
    assert result["category_id"] == "10"
    assert result["prepay_required"] == 10
    assert result["deposit_state"] == "paid_by_order"
    assert platform.modified_work[0]["order_id"] == "order-paid-unbound"
    assert platform.modified_work[0]["store_id"] == "386"
    assert platform.modified_work[0]["category_id"] == "10"
    assert platform.created_work == []
    assert platform.checked_customer == []


def test_check_customer_result_zero_prevents_duplicate_work_order() -> None:
    platform = _PlatformClient(check_result={"result": 0})
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **_base_state(),
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "category_id": "10",
                "prepay": 10,
                "store_confirmation_source": "current_message",
            }],
        }))

    assert output["tool_results"]["create_work_order"]["status"] == "rejected"
    assert output["tool_results"]["create_work_order"]["check_customer"] == {"result": 0}
    assert platform.created_work == []


def test_request_category_label_uses_platform_customer_category_id() -> None:
    platform = _PlatformClient()
    state = _base_state()
    state["request_context"] = {**state["request_context"], "category_id": "S20"}
    state["customer_context"] = {
        **state["customer_context"],
        "customer": {"kind": 1, "category_id": "811"},
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node({
            **state,
            "planner_tool_calls": [{
                "name": "create_work_order",
                "store_id": "386",
                "prepay": 10,
                "store_confirmation_source": "current_message",
            }],
        }))

    assert output["tool_results"]["create_work_order"]["status"] == "created"
    assert platform.created_work[0]["category_id"] == "811"


def test_existing_work_order_amount_is_updated_for_party_size() -> None:
    platform = _PlatformClient(
        orders=[{"id": "order-existing", "status": 1, "store_id": "386", "category_id": "10", "prepay_required": 10, "prepay_paid": 0}]
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(
            node(
                {
                    **_base_state(),
                    "planner_tool_calls": [
                        {
                            "name": "create_work_order",
                            "store_id": "386",
                            "category_id": "10",
                            "prepay": 20,
                            "store_confirmation_source": "recent_explicit_choice",
                        }
                    ],
                }
            )
        )

    result = output["tool_results"]["create_work_order"]
    assert result["status"] == "reused"
    assert result["prepay_required"] == 20
    assert result["amount_updated"] is True
    assert platform.modified_work[0]["amount"] == 20
    assert platform.created_work == []


def test_paid_registered_customer_syncs_mobile_but_does_not_create_plan() -> None:
    platform = _PlatformClient()
    with tempfile.TemporaryDirectory() as tmpdir:
        node = create_execute_actions_node(
            coze_client=object(),
            trace_logger=TraceLogger(Settings(trace_log_dir=Path(tmpdir))),
            store_service=None,
            platform_agent_client=platform,
            appointment_query_from_state=lambda _content, _store_lookup, _state: {},
        )
        output = asyncio.run(node(
            {
                **_base_state(),
                "image_info": {"image_type": "payment_proof", "payment_result": "success"},
                "customer_basic_info": {"customer_name": "测试客户", "phone": "13800138000"},
                "customer_context": {
                    **_base_state()["customer_context"],
                    "orders": [
                        {
                            "id": "order-101",
                            "status": "waiting_schedule",
                            "store_id": "386",
                            "deposit_state": "required_unpaid",
                        }
                    ],
                },
                "planner_tool_calls": [
                    {"name": "add_customer_mobile", "mobile": "13800138000"},
                    {
                        "name": "create_order_plan",
                        "order_id": "order-101",
                        "store_id": "386",
                        "date": "2026-07-20 14:30:00",
                        "customer_name": "测试客户",
                        "mobile": "13800138000",
                    },
                ],
                "tool_policy_violations": [
                    {
                        "subtype": "create_order_plan",
                        "missing": "create_order_plan_disabled_after_payment",
                    }
                ],
            }
        ))

    assert output["tool_results"]["add_customer_mobile"]["status"] == "synced"
    assert output["tool_results"]["create_order_plan"]["error"].endswith(
        "create_order_plan_disabled_after_payment"
    )
    assert platform.created_plan == []


def test_deterministic_profile_state_persists_screenshot_payment() -> None:
    update, events = _deterministic_customer_state_updates(
        {
            "request_id": "screenshot-paid",
            "image_info": {
                "image_type": "payment_proof",
                "payment_result": "success",
                "payment_amount": 10,
                "confidence": 0.99,
            },
            "customer_context": {"orders": [{"id": "order-1", "status": "waiting_schedule", "store_id": "386"}]},
            "customer_basic_info": {},
            "tool_results": {},
        },
        {},
    )
    assert update["portrait"]["deposit_state"] == "deposit_paid"
    assert update["basic_info"]["deposit_state"]["status"] == "paid_by_screenshot"
    assert events[0]["event_type"] == "deposit_payment_confirmed"


def test_deterministic_profile_state_persists_confirmed_store_and_work_order() -> None:
    update, events = _deterministic_customer_state_updates(
        {
            **_base_state(),
            "planner_tool_calls": [
                {
                    "name": "create_work_order",
                    "store_id": "386",
                    "store_name": "厦门百星店",
                    "category_id": "10",
                    "prepay": 20,
                    "store_confirmation_source": "current_message",
                }
            ],
            "tool_results": {
                "create_work_order": {
                    "status": "created",
                    "order_id": "order-101",
                    "store_id": "386",
                    "category_id": "10",
                    "prepay_required": 20,
                    "source": "platform_agent.order.create_work",
                }
            },
            "customer_basic_info": {"preferred_store_id": "369", "preferred_store_name": "厦门旧候选店"},
        },
        {},
    )

    assert update["basic_info"]["confirmed_store_id"] == "386"
    assert update["basic_info"]["confirmed_store_name"] == "厦门百星店"
    assert update["basic_info"]["order_state"]["order_id"] == "order-101"
    assert update["basic_info"]["order_state"]["prepay_required"] == 20
    assert any(item["event_type"] == "store_confirmed" for item in events)


def test_preferred_store_without_executed_work_order_is_not_confirmed() -> None:
    update, events = _deterministic_customer_state_updates(
        {
            **_base_state(),
            "customer_basic_info": {"preferred_store_id": "386", "preferred_store_name": "厦门百星店"},
            "planner_tool_calls": [],
            "tool_results": {},
        },
        {},
    )

    assert "confirmed_store_id" not in update.get("basic_info", {})
    assert not any(item["event_type"] == "store_confirmed" for item in events)
