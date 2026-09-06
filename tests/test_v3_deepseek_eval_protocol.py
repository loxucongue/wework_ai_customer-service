from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from scripts.evaluate_v3_full_chain_deepseek import (  # noqa: E402
    build_metrics,
    choose_samples,
    compact_facts,
    decision_summary,
    judge_messages,
    validate_evaluation_settings,
)
from scripts.v3_lifecycle_eval.protocol import (  # noqa: E402
    hard_assertions,
    merge_ai_judge,
    normalize_visible_messages,
    prior_delivery_events,
    render_visible_messages,
)


def _candidate(index: int, bucket: str) -> dict[str, object]:
    return {
        "source_path": f"run-{index}.json",
        "identity_hash": f"identity-{index}",
        "bucket": bucket,
    }


def test_stratified_samples_are_deterministic_and_interleaved() -> None:
    candidates = [
        _candidate(index, bucket)
        for index, bucket in enumerate(
            ["store"] * 80 + ["transaction"] * 70 + ["price"] * 50 + ["general"] * 120
        )
    ]

    first, distribution = choose_samples(candidates, 120)
    second, _ = choose_samples(candidates, 120)

    assert [row["source_path"] for row in first] == [row["source_path"] for row in second]
    assert len(first) == 120
    assert sum(distribution.values()) == 120
    assert len({row["bucket"] for row in first[:20]}) > 1


def test_metrics_use_conditional_adoption_denominator() -> None:
    judged = {
        "expected_intent": "blocker_expression",
        "expected_emotion": "hesitant",
        "passed": True,
        "safety_ok": True,
        "unsupported_fact": False,
    }
    rows = [
        {
            "case_id": "C1", "reply_source": "main_model", "intent": "blocker_expression",
            "emotion": "hesitant", "closing_action": "pause", "sequence_candidates": ["价格解卡"],
            "script_candidates": ["低压解释"], "adopted_sequence_id": "seq-1",
            "adopted_script_id": "script-1", "duration_ms": 100, "judge": judged,
            "closing_rule_candidates": ["客户已认可"],
            "closing_strategy_candidates": ["低压收口"],
            "closing_script_candidates": ["低压确认"],
            "closing_strategy_adopted": True, "closing_script_adopted": True,
            "model_names": ["deepseek-chat"],
        },
        {
            "case_id": "C2", "reply_source": "main_model", "intent": "explicit_exit",
            "emotion": "angry", "closing_action": "complete", "sequence_candidates": ["不应采用"],
            "script_candidates": [], "duration_ms": 120, "judge": {}, "model_names": ["deepseek-chat"],
        },
        {
            "case_id": "C3", "reply_source": "reply_failed", "sequence_candidates": ["不能计入"],
            "script_candidates": [], "duration_ms": 140, "judge": {}, "model_names": ["deepseek-chat"],
        },
    ]

    metrics = build_metrics(rows, {"distribution": {}, "audit": {"blocked_attempts": []}})

    assert metrics["policy_core_coverage"] == 0.6667
    assert metrics["adoption_eligible_count"] == 1
    assert metrics["sequence_adopted_count"] == 1
    assert metrics["script_adopted_count"] == 1
    assert metrics["closing_rule_candidate_count"] == 1
    assert metrics["closing_strategy_candidate_count"] == 1
    assert metrics["closing_script_candidate_count"] == 1
    assert metrics["closing_strategy_adopted_count"] == 1
    assert metrics["closing_script_adopted_count"] == 1


def test_judge_receives_actual_authority_and_tool_facts() -> None:
    facts = compact_facts(
        {
            "shared_context": {
                "authoritative_facts": {
                    "orders_and_payment": {"resolved_payment": {"deposit_state": "paid_by_order"}},
                    "visible_store_scope": {"count": 10},
                },
                "rules": {"AUTHORITATIVE FACTS": {"offer": {"new_customer_price": 268}}},
            },
            "evidence_join": {
                "normalized_tool_facts": {"structured_facts": {"store_lookup_status": {"status": "matched"}}}
            },
        }
    )

    assert facts["authoritative_facts"]["orders_and_payment"]["resolved_payment"]["deposit_state"] == "paid_by_order"
    assert facts["business_authority"]["offer"]["new_customer_price"] == 268
    assert facts["normalized_tool_facts"]["structured_facts"]["store_lookup_status"]["status"] == "matched"


def test_summary_reads_normalized_selected_script_ids() -> None:
    summary = decision_summary(
        {
            "semantic_route": {
                "closing_catalog_evidence": {
                    "source": "local_closing_catalog",
                    "status": "ok",
                    "match_status": "matched",
                    "selected_rules": [{"type_name": "客户主动要登记"}],
                    "candidate_sequences": [
                        {"sequence_key": "local:sequence:deposit", "name": "直接登记"}
                    ],
                }
            },
            "sales_recall": {
                "candidates": [
                    {
                        "id": "script-3",
                        "script_name": "直接发预约金",
                        "sequence_links": [
                            {
                                "sequence_id": "local:sequence:deposit",
                                "step_id": "local:node:deposit:step_1",
                                "query_source": "closing_catalog_node",
                            }
                        ],
                    }
                ]
            },
            "reply_knowledge_use": {
                "sequence_id": "local:sequence:deposit",
                "selected_script_ids": ["script-3", "script-8"],
            }
        }
    )

    assert summary["adopted_sequence_id"] == "local:sequence:deposit"
    assert summary["adopted_script_id"] == "script-3;script-8"
    assert summary["closing_catalog_source"] == "local_closing_catalog"
    assert summary["closing_rule_candidates"] == ["客户主动要登记"]
    assert summary["closing_strategy_candidates"] == ["直接登记"]
    assert summary["closing_script_candidates"] == ["直接发预约金"]
    assert summary["closing_strategy_adopted"] is True
    assert summary["closing_script_adopted"] is True


def test_full_chain_evaluation_fails_when_knowledge_token_is_missing() -> None:
    settings = SimpleNamespace(
        model_relay_api_key="relay",
        deepseek_api_key="deepseek",
        follow_knowledge_enabled=True,
        follow_knowledge_token="",
    )

    with pytest.raises(RuntimeError, match="FOLLOW_KNOWLEDGE_TOKEN"):
        validate_evaluation_settings(settings)


def test_judge_uses_current_store_resolution_over_historical_order_store() -> None:
    messages = judge_messages(
        {
            "history": [],
            "content": "地址在哪",
            "facts": {},
            "summary": {},
            "reply": "当前范围暂未查到本地门店",
            "bucket": "store",
        }
    )

    assert "历史订单里出现的门店只说明旧订单关联" in messages[0]["content"]


def _prior_store_delivery(store_id: str = "160") -> list[dict[str, object]]:
    return [
        {
            "request_id": "old-request",
            "occurred_at": "2026-09-06T10:00:00+08:00",
            "reply_messages": [
                {"type": "text", "order": 1, "content": {"text": "门店在长沙西中心"}},
                {"type": "store_address", "order": 2, "content": {"store_id": store_id}},
            ],
        }
    ]


def test_structured_messages_are_rendered_and_seeded_instead_of_discarded() -> None:
    messages = normalize_visible_messages(_prior_store_delivery()[0]["reply_messages"])

    assert [item["type"] for item in messages] == ["text", "store_address"]
    assert "[门店位置卡] store_id=160" in render_visible_messages(messages)
    events = prior_delivery_events(_prior_store_delivery())
    assert events[0]["event_type"] == "store_address_sent"
    assert events[0]["facts"]["store_id"] == "160"


def test_repeated_store_card_is_a_hard_failure_without_explicit_rerequest() -> None:
    result = hard_assertions(
        sample={"content": "可以停车吗"},
        facts={"authoritative_facts": {"orders_and_payment": {"payment_state": "required_unpaid"}}},
        prior_deliveries=_prior_store_delivery(),
        reply_messages=[
            {"type": "text", "order": 1, "content": "可以停车的"},
            {"type": "store_address", "order": 2, "content": {"store_id": "160"}},
        ],
    )

    assert result["passed"] is False
    assert "repeated_delivered_store_card" in result["failure_codes"]
    assert "appointment_goal_not_explicit" in result["failure_codes"]


def test_explicit_address_rerequest_allows_same_store_card() -> None:
    result = hard_assertions(
        sample={"content": "地址再发我一下，我导航过去"},
        facts={},
        prior_deliveries=_prior_store_delivery(),
        reply_messages=[
            {"type": "text", "order": 1, "content": "好呀，位置再发您"},
            {"type": "store_address", "order": 2, "content": {"store_id": "160"}},
        ],
    )

    assert "repeated_delivered_store_card" not in result["failure_codes"]


def test_store_detail_for_unbooked_customer_must_name_booking_goal() -> None:
    missing = hard_assertions(
        sample={"content": "停车方便吗"},
        facts={"authoritative_facts": {"orders_and_payment": {"deposit_state": "required_unpaid"}}},
        prior_deliveries=_prior_store_delivery(),
        reply_messages=[{"type": "text", "order": 1, "content": "可以停车的，您工作日还是周末过来呢？"}],
    )
    desired = hard_assertions(
        sample={"content": "停车方便吗"},
        facts={"authoritative_facts": {"orders_and_payment": {"deposit_state": "required_unpaid"}}},
        prior_deliveries=_prior_store_delivery(),
        reply_messages=[{"type": "text", "order": 1, "content": "可以停车的，楼下有停车场。您工作日还是周末过来？我帮您预约一下。"}],
    )

    assert "appointment_goal_not_explicit" in missing["failure_codes"]
    assert desired["passed"] is True


def test_confirmed_appointment_does_not_require_another_booking_bridge() -> None:
    result = hard_assertions(
        sample={"content": "停车方便吗", "appointment_id": "A-1"},
        facts={},
        prior_deliveries=_prior_store_delivery(),
        reply_messages=[{"type": "text", "order": 1, "content": "方便的，楼下就有停车场。"}],
    )

    assert "appointment_goal_not_explicit" not in result["failure_codes"]


def test_deterministic_failure_overrides_positive_ai_judge() -> None:
    merged = merge_ai_judge(
        {"passed": True, "reasons": ["整体自然"]},
        {"failures": [{"code": "appointment_goal_not_explicit", "reason": "未连接预约目标"}]},
    )

    assert merged["ai_passed"] is True
    assert merged["passed"] is False
    assert merged["hard_failure_codes"] == ["appointment_goal_not_explicit"]
