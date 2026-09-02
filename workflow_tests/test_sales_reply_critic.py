from __future__ import annotations

import pytest

from app.evaluation.sales_reply_critic import validate_sales_reply_evaluation


def _result(score: int = 4) -> dict:
    return {
        "cardpoint": {"applicable": True, "pass": score >= 4, "score": score},
        "closing": {"applicable": True, "pass": score >= 4, "score": score},
        "priority": {
            "applicable": True,
            "multi_issue": True,
            "pass": score >= 4,
            "score": score,
            "expected_order": ["回答问题", "处理卡点"],
            "actual_order": ["回答问题", "处理卡点"],
        },
        "fact_safe": True,
        "human_review_required": False,
    }


def test_sales_reply_evaluation_contract_accepts_consistent_scores() -> None:
    result = validate_sales_reply_evaluation(_result())
    assert result["priority"]["multi_issue"] is True
    assert result["cardpoint"]["pass"] is True


def test_sales_reply_evaluation_contract_rejects_inconsistent_pass() -> None:
    value = _result(3)
    value["closing"]["pass"] = True
    with pytest.raises(ValueError, match="closing pass"):
        validate_sales_reply_evaluation(value)
