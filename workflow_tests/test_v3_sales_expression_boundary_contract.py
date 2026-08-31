from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.v3_critic import CRITIC_SYSTEM_PROMPT
from scripts.run_v3_sales_expression_boundary import _is_fact_safe


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "workflow_tests" / "fixtures" / "v3_sales_expression_boundary_v1.json"


def test_expression_boundary_fixture_has_balanced_review_cases() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = payload["cases"]
    assert len(cases) == 20
    assert sum(1 for item in cases if item["expected"] == "allow") == 10
    assert sum(1 for item in cases if item["expected"] == "forbid") == 10
    assert len({item["case_id"] for item in cases}) == 20


def test_critic_contract_distinguishes_sales_expression_from_hard_facts() -> None:
    assert "不少外地客户也会专程过来" in CRITIC_SYSTEM_PROMPT
    assert "must not reduce fact_safety" in CRITIC_SYSTEM_PROMPT
    assert "individual safety guarantees" in CRITIC_SYSTEM_PROMPT


def test_boundary_result_uses_fact_safety_not_overall_sales_score() -> None:
    result = {
        "scores": {"fact_safety": 5, "natural_advance": 1},
        "violations": [{"code": "tone", "reason": "not relevant to fact safety"}],
    }
    assert _is_fact_safe(result) is True
    result["violations"] = [{"code": "individual_guarantee", "reason": "unsupported"}]
    assert _is_fact_safe(result) is False
