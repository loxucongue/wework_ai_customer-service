from __future__ import annotations

from pathlib import Path
import asyncio
import tempfile

import pytest

from app.config import Settings
from app.graph.nodes.action_nodes import create_execute_actions_node
from app.graph.nodes.image_validation import validated_image_info
from app.graph.nodes.profile_nodes import _deterministic_customer_state_updates
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.services.customer_order_context import compact_order
from app.services.customer_payment_state import resolved_payment_fact
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


def test_payment_card_requires_order_fact_in_full_planner_state() -> None:
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
    assert any(item.get("missing") == "work_order_required_before_payment_collection" for item in plan["tool_policy_violations"])

    with pytest.raises(ValueError, match="payment_collection_requires_active_work_order"):
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


def test_active_order_from_another_store_does_not_authorize_payment_card() -> None:
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

    assert any(item.get("missing") == "work_order_required_before_payment_collection" for item in plan["tool_policy_violations"])


def test_create_plan_decision_requires_create_order_plan_tool() -> None:
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
            "tool_calls": [],
        },
    )
    assert any(item.get("missing") == "create_order_plan_tool_required" for item in plan["tool_policy_violations"])


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
    assert platform.created_work == []


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


def test_paid_registered_customer_can_sync_mobile_and_create_plan() -> None:
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
            }
        ))

    assert output["tool_results"]["add_customer_mobile"]["status"] == "synced"
    assert output["tool_results"]["create_order_plan"]["status"] == "created"
    assert output["tool_results"]["create_order_plan"]["store_name"] == "厦门百星店"
    appointment_facts = output["fact_envelope"]["structured_facts"]["appointment_facts"]
    assert appointment_facts[0]["type"] == "appointment_created"


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
