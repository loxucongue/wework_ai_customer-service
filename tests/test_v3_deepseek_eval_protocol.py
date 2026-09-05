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
