from __future__ import annotations

from pathlib import Path
import asyncio
from datetime import datetime, timezone
import tempfile

import pytest

from app.config import Settings
from app.graph.nodes.action_nodes import create_execute_actions_node
from app.graph.nodes.layer_nodes import _platform_unknown_transfer_image_info
from app.graph.nodes.image_validation import validated_image_info
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.graph.nodes.sent_message_summary import sent_message_summary_for_model
from app.services.customer_order_context import appointment_from_orders, compact_order
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
        "sop_progress_evidence": {
            "completed_pack_ids": ["s10_activity_intro"],
            "completed_categories": ["activity_intro"],
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


def test_old_unpaid_order_is_historical_and_not_a_current_appointment() -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    normalized = normalize_prepay_facts(
        {
            "id": "old-unpaid",
            "status": 1,
            "store_id": 383,
            "prepay_required": 10,
            "prepay_paid": 0,
            "created_at": "2024-08-05T00:00:00+00:00",
        },
        now=now,
    )

    assert normalized["deposit_state"] == "historical_unpaid_expired"
    assert normalized["order_recency_status"] == "historical_unpaid_expired"
    assert resolved_payment_fact(
        orders=[
            {
                "id": "old-unpaid",
                "status": 1,
                "store_id": 383,
                "prepay_required": 10,
                "prepay_paid": 0,
                "created_at": "2024-08-05T00:00:00+00:00",
            }
        ]
    ) == {}
    appointment = appointment_from_orders(
        [
            {
                "id": "old-unpaid",
                "status": 1,
                "store_id": 383,
                "store_name": "杭州富阳店",
                "prepay_required": 10,
                "prepay_paid": 0,
                "created_at": "2024-08-05T00:00:00+00:00",
            }
        ]
    )
    assert appointment["has_active"] is False


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
