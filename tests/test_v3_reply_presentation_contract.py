from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.graph.nodes.reply_nodes import (  # noqa: E402
    _chat_json_with_deadline,
    _normalized_policy_decision,
)
from app.graph.nodes.reply_presentation import (  # noqa: E402
    compact_reply_message_format,
    reply_presentation_metrics,
    reply_presentation_violations,
)


def _policy_state(*, paid: bool = False) -> dict[str, Any]:
    policy = json.loads(
        (PROJECT_ROOT / "ai_paths" / "app" / "policies" / "ai_sales_policy_v2.json").read_text(
            encoding="utf-8"
        )
    )
    policy["runtime_mode"] = "active"
    shared_context: dict[str, Any] = {
        "conversation": [{"role": "customer", "message_ref": "current_message"}]
    }
    if paid:
        shared_context["authoritative_facts"] = {
            "orders_and_payment": {
                "resolved_payment": {"deposit_state": "paid_by_order"}
            }
        }
    return {"ai_sales_policy": policy, "shared_context": shared_context}


def _decision(customer_state: str) -> dict[str, Any]:
    return {
        "primary_task": {
            "type": "answer_current_question",
            "goal": "回答当前问题",
        },
        "secondary_tasks": [],
        "realtime_intent": {
            "type": "fact_inquiry",
            "confidence": "high",
            "evidence_refs": ["current_message"],
        },
        "emotion_decision": {
            "label": "neutral",
            "confidence": "high",
            "pressure": "normal",
            "evidence_refs": ["current_message"],
        },
        "closing_decision": {
            "action": "none",
            "sequence_key": "none",
            "node_key": "",
            "trigger": "none",
            "customer_state": customer_state,
            "pressure": "normal",
            "evidence_refs": ["current_message"],
        },
    }


def test_lossless_presentation_compaction_removes_exact_duplicate_text_only() -> None:
    messages = [
        {"type": "text", "content": "  您好   呀\r\n\r\n\r\n  在的  "},
        {"type": "text", "content": "您好 呀\n\n在的"},
        {"type": "image", "content": {"url": "https://example.test/effect.jpg"}},
    ]

    compacted = compact_reply_message_format(messages)

    assert compacted == [
        {"type": "text", "content": "您好 呀\n\n在的"},
        {"type": "image", "content": {"url": "https://example.test/effect.jpg"}},
    ]


def test_presentation_hard_bounds_and_emoji_boundaries_are_explicit() -> None:
    nine_messages = [{"type": "text", "content": str(index)} for index in range(9)]
    long_text = [{"type": "text", "content": "好" * 301}]
    one_emoji = [{"type": "text", "content": "您好呀🙂"}]
    two_emojis = [{"type": "text", "content": "您好呀🙂😊"}]

    assert reply_presentation_metrics(one_emoji)["emoji_count"] == 1
    assert reply_presentation_violations(one_emoji) == []
    assert reply_presentation_violations(two_emojis) == [
        "reply_presentation_emoji_limit_exceeded:2:1"
    ]
    assert reply_presentation_violations(one_emoji, sensitive_turn=True) == [
        "reply_presentation_emoji_limit_exceeded:1:0"
    ]
    assert reply_presentation_violations(nine_messages) == [
        "reply_presentation_message_limit_exceeded:9"
    ]
    assert reply_presentation_violations(long_text) == [
        "reply_presentation_text_limit_exceeded:301"
    ]


def test_four_customer_states_and_legacy_aliases_normalize_compatibly() -> None:
    expected = {
        "continue_sales": "continue_sales",
        "pause_current_turn": "pause_current_turn",
        "hard_stop_marketing": "hard_stop_marketing",
        "engaged": "continue_sales",
        "hesitant": "pause_current_turn",
        "soft_reject": "pause_current_turn",
        "not_buying_now": "pause_current_turn",
        "new_blocker": "pause_current_turn",
        "hard_stop": "hard_stop_marketing",
    }
    for raw_state, normalized in expected.items():
        result = _normalized_policy_decision(
            _decision(raw_state),
            state=_policy_state(),
        )
        assert result["closing_decision"]["customer_state"] == normalized


def test_post_payment_state_requires_authoritative_paid_fact() -> None:
    unpaid = _normalized_policy_decision(
        _decision("post_payment_service"),
        state=_policy_state(),
    )
    paid = _normalized_policy_decision(
        _decision("post_payment_service"),
        state=_policy_state(paid=True),
    )

    assert unpaid["closing_decision"]["customer_state"] == "pause_current_turn"
    assert "post_payment_service_requires_authoritative_paid" in unpaid["decision_reasons"]
    assert paid["closing_decision"]["customer_state"] == "post_payment_service"
    assert paid["closing_decision"]["legacy_customer_state"] == "transaction_terminal_or_handoff"


def test_sales_reply_uses_small_variation_but_structural_repair_is_deterministic() -> None:
    class Model:
        settings = None

        def __init__(self) -> None:
            self.temperatures: list[float] = []

        async def chat_json(self, _messages: Any, **kwargs: Any) -> dict[str, Any]:
            self.temperatures.append(float(kwargs["temperature"]))
            return {"reply_messages": [{"type": "text", "content": "在的呀"}]}

    model = Model()
    asyncio.run(
        _chat_json_with_deadline(
            model,  # type: ignore[arg-type]
            [{"role": "system", "content": "你是 V3 唯一的最终销售大脑"}],
            tier="reply",
            deadline_monotonic=999999999.0,
        )
    )
    asyncio.run(
        _chat_json_with_deadline(
            model,  # type: ignore[arg-type]
            [{"role": "system", "content": "你是 JSON 结构修复器"}],
            tier="reply",
            deadline_monotonic=999999999.0,
        )
    )

    assert model.temperatures == [0.15, 0.0]


def test_sales_reply_temperature_uses_runtime_setting() -> None:
    class Model:
        settings = SimpleNamespace(v3_reply_temperature=0.22)

        def __init__(self) -> None:
            self.temperatures: list[float] = []

        async def chat_json(self, _messages: Any, **kwargs: Any) -> dict[str, Any]:
            self.temperatures.append(float(kwargs["temperature"]))
            return {"reply_messages": [{"type": "text", "content": "在的呀"}]}

    model = Model()
    asyncio.run(
        _chat_json_with_deadline(
            model,  # type: ignore[arg-type]
            [{"role": "system", "content": "你是 V3 唯一的最终销售大脑"}],
            tier="reply",
            deadline_monotonic=999999999.0,
        )
    )
    asyncio.run(
        _chat_json_with_deadline(
            model,  # type: ignore[arg-type]
            [{"role": "system", "content": "你是 JSON 结构修复器"}],
            tier="reply",
            deadline_monotonic=999999999.0,
        )
    )

    assert model.temperatures == [0.22, 0.0]
