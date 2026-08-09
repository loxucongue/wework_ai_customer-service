from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.graph.nodes.reply_validation import validated_model_messages, validate_reply_consistency
from app.graph.nodes.store_scope_summary import build_store_scope_summary
from app.graph.planner.brain_v2 import _transaction_facts_for_planner
from app.graph.planner.brain_v2_normalizer import _normalize_reply_messages
from app.graph.planner.brain_v2_prompts import PLANNER_SYSTEM_PROMPT, PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT
from app.policies.s10_offer import ACTIVE_S10_OFFER_CONTEXT


ROOT = Path(__file__).resolve().parents[1]


def _district_stores() -> list[dict[str, str]]:
    return [
        {
            "store_id": str(index),
            "store_name": f"厦门思明测试店{index}",
            "province": "福建省",
            "city": "厦门市",
            "district": "思明区",
        }
        for index in range(1, 6)
    ]


def test_offer_policy_is_refundable_and_does_not_lock_arrival_time() -> None:
    context = ACTIVE_S10_OFFER_CONTEXT
    assert "未做或不满意可退" in context["signup_rule"]
    assert "按付款记录核对" in context["signup_rule"]
    assert "到店时间可按客户方便安排" in context["signup_rule"]
    assert all("不做退10元" not in item for item in context["hard_constraints"])


def test_sop_config_has_no_legacy_non_refund_copy() -> None:
    config = json.loads((ROOT / "config/sop_reply_packs.json").read_text(encoding="utf-8"))
    serialized = json.dumps(config, ensure_ascii=False)
    assert "不做退10元" not in serialized
    assert "未做或不满意可退" in serialized


def test_store_scope_exposes_every_real_store_in_requested_district() -> None:
    summary = build_store_scope_summary(
        {"source": "fixture", "stores": _district_stores()},
        location_hints=["厦门", "思明区"],
    )

    region = summary["relevant_regions"][0]
    assert region["city"] == "厦门市"
    assert region["exact_area_store_count"] == 5
    assert [item["store_id"] for item in region["requested_district_stores"]] == ["1", "2", "3", "4", "5"]


def test_planner_uses_store_resolution_v2_as_the_only_delivery_contract() -> None:
    assert "`store_resolution`" in PLANNER_SYSTEM_PROMPT
    assert "`candidate_policy.status=send_single/send_multiple`" in PLANNER_SYSTEM_PROMPT
    assert "只能发送 `delivery_store_ids`" in PLANNER_SYSTEM_PROMPT
    assert "不得自行增减门店" in PLANNER_SYSTEM_PROMPT


def test_transaction_output_gate_does_not_require_matching_order_before_payment_card() -> None:
    assert "does not require a matching active unpaid order" in PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT
    assert "backend association fact" in PLANNER_TRANSACTION_OUTPUT_GATE_PROMPT


def test_planner_transaction_facts_expose_only_structured_order_state() -> None:
    facts = _transaction_facts_for_planner(
        {
            "customer_context": {
                "orders": [
                    {"id": "unpaid-1", "store_id": "386", "prepay_required": 20, "prepay_paid": 0},
                    {"id": "paid-1", "store_id": "227", "prepay_required": 10, "prepay_paid": 10},
                ]
            }
        }
    )

    assert facts["has_unpaid_order"] is True
    assert facts["unpaid_orders"] == [
        {
            "order_id": "unpaid-1",
            "store_id": "386",
            "prepay_required": 20,
            "prepay_paid": 0,
            "deposit_state": "required_unpaid",
        }
    ]
    assert facts["paid_orders"][0]["order_id"] == "paid-1"


def test_same_district_store_cards_are_not_truncated_by_normal_visible_limit() -> None:
    stores = _district_stores()
    summary = build_store_scope_summary({"source": "fixture", "stores": stores}, location_hints=["厦门", "思明区"])
    state = {"store_scope_summary": summary, "customer_store_knowledge": {"stores": stores}}
    payload = {
        "reply_messages": [
            {"type": "text", "content": {"text": "思明区这几家都能接待，我都发您看下。"}},
            *[{"type": "store_address", "content": {"store_id": str(index)}} for index in range(1, 6)],
        ]
    }

    messages = validated_model_messages(payload, state)
    validate_reply_consistency(messages, state)

    assert [item["content"]["store_id"] for item in messages if item["type"] == "store_address"] == ["1", "2", "3", "4", "5"]


def test_planner_normalizer_preserves_same_district_store_card_sequence() -> None:
    stores = _district_stores()
    summary = build_store_scope_summary({"source": "fixture", "stores": stores}, location_hints=["厦门", "思明区"])
    messages = _normalize_reply_messages(
        [
            {"type": "text", "content": {"text": "思明区这几家都能接待，我都发您看下。"}},
            *[{"type": "store_address", "content": {"store_id": str(index)}} for index in range(1, 6)],
        ],
        state={"store_scope_summary": summary},
    )

    assert len(messages) == 6
    assert [item["content"]["store_id"] for item in messages[1:]] == ["1", "2", "3", "4", "5"]


def test_multi_store_cards_reject_mixed_districts() -> None:
    stores = [
        *_district_stores()[:2],
        {
            "store_id": "99",
            "store_name": "厦门湖里测试店",
            "province": "福建省",
            "city": "厦门市",
            "district": "湖里区",
        },
    ]
    summary = build_store_scope_summary({"source": "fixture", "stores": stores}, location_hints=["厦门", "思明区"])
    state = {"store_scope_summary": summary, "customer_store_knowledge": {"stores": stores}}
    messages = [
        {"type": "text", "order": 1, "content": {"text": "门店卡片在下面。"}},
        {"type": "store_address", "order": 2, "content": {"store_id": "1"}},
        {"type": "store_address", "order": 3, "content": {"store_id": "99"}},
    ]

    with pytest.raises(ValueError, match="multiple_store_address_cards_must_share_requested_scope"):
        validate_reply_consistency(messages, state)


def test_city_level_two_store_cards_can_span_districts_when_tool_facts_prove_scope() -> None:
    state = {
        "fact_envelope": {
            "structured_facts": {
                "store_lookup_status": {
                    "city": "荆州市",
                    "resolved_admin_level": "city",
                    "exact_scope_has_store": True,
                    "candidate_count": 2,
                },
                "store_facts": [
                    {"store_id": "241", "store_name": "荆州万达店", "city": "荆州市", "district": "荆州区"},
                    {"store_id": "242", "store_name": "荆州沙市店", "city": "荆州市", "district": "沙市区"},
                ],
            }
        }
    }
    payload = {
        "reply_messages": [
            {"type": "text", "content": "荆州市这两家都发您看下。"},
            {"type": "store_address", "content": {"store_id": "241"}},
            {"type": "store_address", "content": {"store_id": "242"}},
            {"type": "text", "content": "您按路线选一家就行。"},
        ]
    }

    messages = validated_model_messages(payload, state)
    validate_reply_consistency(messages, state)

    assert [item["content"]["store_id"] for item in messages if item["type"] == "store_address"] == ["241", "242"]


def test_district_store_cards_can_use_completed_tool_fact_scope() -> None:
    state = {
        "fact_envelope": {
            "structured_facts": {
                "store_lookup_status": {
                    "city": "成都市",
                    "district": "双流区",
                    "resolved_admin_level": "district",
                    "exact_scope_has_store": True,
                    "candidate_count": 3,
                },
                "store_facts": [
                    {"store_id": str(store_id), "city": "成都市", "district": "双流区"}
                    for store_id in (280, 379, 522)
                ],
            }
        }
    }
    messages = [
        {"type": "text", "order": 1, "content": "双流区这三家都发您。"},
        *[
            {"type": "store_address", "order": index + 2, "content": {"store_id": str(store_id)}}
            for index, store_id in enumerate((280, 379, 522))
        ],
    ]

    validate_reply_consistency(messages, state)


def test_multi_store_cards_allow_text_to_name_every_emitted_store() -> None:
    state = {
        "fact_envelope": {
            "structured_facts": {
                "store_lookup_status": {
                    "city": "荆州市",
                    "resolved_admin_level": "city",
                    "exact_scope_has_store": True,
                    "candidate_count": 2,
                },
                "store_facts": [
                    {"store_id": "241", "store_name": "荆州万达店", "city": "荆州市", "district": "荆州区"},
                    {"store_id": "242", "store_name": "荆州沙市店", "city": "荆州市", "district": "沙市区"},
                ],
            }
        }
    }
    messages = [
        {"type": "text", "order": 1, "content": "荆州有荆州万达店和荆州沙市店，我都发您。"},
        {"type": "store_address", "order": 2, "content": {"store_id": "241"}},
        {"type": "store_address", "order": 3, "content": {"store_id": "242"}},
    ]

    validate_reply_consistency(messages, state)
