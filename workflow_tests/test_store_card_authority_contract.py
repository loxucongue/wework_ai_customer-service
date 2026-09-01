from __future__ import annotations

import pytest

from app.graph.nodes.reply_nodes import _prepare_structural_messages
from app.graph.nodes.reply_validation import validate_reply_consistency
from app.services.store_resolution import customer_location_hint_texts


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
