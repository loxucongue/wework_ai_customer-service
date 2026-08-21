from __future__ import annotations

import pytest

from app.graph.nodes.reply_nodes import (
    _prepare_structural_messages,
    _store_fact_recovery_messages,
)
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.services.store_resolution_v2 import customer_location_hint_texts


def _store_state() -> dict:
    return {
        "content": "什么时候发货呀？",
        "customer_store_knowledge": {
            "stores": [
                {"store_id": "241", "store_name": "荆州万达店", "city": "荆州市"},
                {"store_id": "242", "store_name": "荆州沙市店", "city": "荆州市"},
            ]
        },
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": {
                    "status": "send_multiple",
                    "delivery_store_ids": ["241", "242"],
                    "visible_candidate_ids": ["241", "242"],
                },
                "store_facts": [
                    {"store_id": "241", "store_name": "荆州万达店", "city": "荆州市"},
                    {"store_id": "242", "store_name": "荆州沙市店", "city": "荆州市"},
                ],
            }
        },
    }


def test_reply_postprocess_does_not_append_store_cards_from_tool_facts() -> None:
    state = _store_state()
    messages = [{"type": "text", "order": 1, "content": "发货时间需要按订单进度核对。"}]

    prepared = _prepare_structural_messages(messages, state, [])

    assert prepared == messages
    assert not any(item.get("type") == "store_address" for item in prepared)


def test_store_resolution_fact_does_not_force_card_delivery() -> None:
    validate_reply_consistency(
        [{"type": "text", "order": 1, "content": "发货时间需要按订单进度核对。"}],
        _store_state(),
    )


def test_v3_current_turn_store_delivery_materializes_verified_cards() -> None:
    state = {
        **_store_state(),
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "tool_facts": {"store_resolution_fact": {"status": "send_multiple"}},
        },
    }
    warnings: list[dict] = []

    prepared = _prepare_structural_messages(
        [{"type": "text", "content": "门店位置发您，您看哪家方便。"}],
        state,
        warnings,
    )

    assert [item["type"] for item in prepared] == [
        "text",
        "store_address",
        "store_address",
    ]
    assert [
        item["content"]["store_id"]
        for item in prepared
        if item["type"] == "store_address"
    ] == ["241", "242"]
    assert warnings == [
        {
            "node": "synthesize_reply",
            "message": "store_delivery_materialized_from_tool_fact",
        }
    ]


def test_v3_broad_city_delivery_materializes_every_authorized_store() -> None:
    store_ids = [str(index) for index in range(1, 7)]
    stores = [
        {"store_id": store_id, "store_name": f"同城门店{store_id}", "city": "武汉市"}
        for store_id in store_ids
    ]
    state = {
        "customer_store_knowledge": {"stores": stores},
        "fact_envelope": {
            "structured_facts": {
                "store_resolution_fact": {
                    "status": "send_multiple",
                    "allow_broad_scope_delivery": True,
                    "delivery_store_ids": store_ids,
                    "visible_candidate_ids": store_ids,
                },
                "store_facts": stores,
            }
        },
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "tool_facts": {"store_resolution_fact": {"status": "send_multiple"}},
        },
    }

    prepared = _prepare_structural_messages(
        [{"type": "text", "content": "这个城市的门店位置都发您。"}],
        state,
        [],
    )

    assert [
        item["content"]["store_id"]
        for item in prepared
        if item["type"] == "store_address"
    ] == store_ids


def test_v3_store_fact_recovery_uses_cards_instead_of_neutral_fallback() -> None:
    state = {
        **_store_state(),
        "evidence_join": {
            "schema_version": "reply_chain_evidence_join_v1",
            "tool_facts": {"store_resolution_fact": {"status": "send_multiple"}},
        },
    }

    recovered = _store_fact_recovery_messages(state)

    assert recovered[0]["content"] == "门店位置发您。"
    assert [item["type"] for item in recovered] == [
        "text",
        "store_address",
        "store_address",
    ]
    assert all(item.get("content") != "您稍等一下" for item in recovered)


def test_emitted_store_card_must_match_current_authoritative_facts() -> None:
    with pytest.raises(ValueError):
        validate_reply_consistency(
            [
                {"type": "text", "order": 1, "content": "门店位置发您。"},
                {"type": "store_address", "order": 2, "content": {"store_id": "999"}},
            ],
            _store_state(),
        )


def test_customer_location_hints_exclude_assistant_history() -> None:
    hints = customer_location_hint_texts(
        {
            "content": "什么时候发货呀？",
            "conversation_history": [
                "用户: 我在魏县",
                "小贝: 厦门湖里区这家门店发您",
                "用户: 先看看",
            ],
        }
    )

    assert "什么时候发货呀？" in hints
    assert "我在魏县" in hints
    assert "先看看" in hints
    assert all("厦门" not in item for item in hints)
