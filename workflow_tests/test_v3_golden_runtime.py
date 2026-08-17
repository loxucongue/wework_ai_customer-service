from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.v3_critic import CRITIC_SYSTEM_PROMPT, validate_critic_result
from app.evaluation.v3_golden import golden_case_to_simulation, simulation_result_to_golden_result
from app.services.v3_evaluation_service import V3EvaluationService
from scripts.evaluate_v3_trusted_golden import evaluate
from scripts.run_v3_trusted_golden import _git_commit


def test_golden_case_maps_to_isolated_simulation_without_reference_answers() -> None:
    case = _case()
    scenario = golden_case_to_simulation(case)

    assert scenario["id"] == "golden_v3_test"
    assert scenario["initial"]["stores"][0]["store_id"] == "12"
    assert scenario["timeline"][0]["content"] == "多少钱"
    assert "reference_reply_examples" not in json.dumps(scenario, ensure_ascii=False)


def test_simulation_result_extracts_customer_visible_reply_and_metrics() -> None:
    result = simulation_result_to_golden_result(
        _case(),
        {
            "hard_pass": True,
            "hard_errors": [],
            "infrastructure_errors": [],
            "duration_ms": 100,
            "run_dir": "run",
            "steps": [
                {
                    "request_id": "req",
                    "sync_reply_messages": [{"type": "text", "content": "活动价268元"}],
                    "response_meta": {
                        "content_selection_metrics": {"nominated_ids": ["activity"]},
                        "reply_content_decisions": [
                            {
                                "content_id": "activity",
                                "decision": "adopt",
                                "reason": "directly_useful",
                            }
                        ],
                    },
                }
            ],
        },
    )
    assert result["reply_messages"][0]["content"] == "活动价268元"
    assert result["content_selection_metrics"]["nominated_ids"] == ["activity"]
    assert result["content_decisions"][0]["decision"] == "adopt"
    assert result["human_review"]["status"] == "pending"


def test_critic_contract_is_evaluation_only() -> None:
    lowered = CRITIC_SYSTEM_PROMPT.lower()
    assert "do not write a replacement reply" in lowered
    assert "do not choose a new sales action" in lowered
    parsed = validate_critic_result(
        {
            "status": "pass",
            "scores": {
                "current_question": 5,
                "history_continuity": 4,
                "natural_advance": 4,
                "evidence_relevance": 5,
                "human_tone": 4,
                "fact_safety": 5,
            },
            "failure_owner": "none",
            "violations": [],
            "reason": "符合标准",
        }
    )
    assert parsed["status"] == "pass"


def test_deployed_evaluation_reads_release_commit_without_git_metadata(tmp_path, monkeypatch) -> None:
    class Completed:
        returncode = 128
        stdout = ""

    monkeypatch.setattr("scripts.run_v3_trusted_golden.subprocess.run", lambda *args, **kwargs: Completed())
    (tmp_path / "RELEASE_COMMIT").write_text("b7053d546\n", encoding="utf-8")

    assert _git_commit(tmp_path) == "b7053d546"


def test_deployed_evaluation_keeps_simulation_state_under_isolated_runtime_root() -> None:
    source = Path("ai_paths/scripts/run_v3_trusted_golden.py").read_text(encoding="utf-8")

    assert 'repo_root / ".tmp_runtime" / "simulation" / run_id' in source
    assert 'run_root=run_dir / "simulation"' not in source


def test_evaluator_does_not_claim_calibration_without_human_verdict() -> None:
    golden = {"schema_version": "test", "cases": [_case()]}
    result = {
        "results": [
            {
                "case_id": "golden_v3_test",
                "reply_messages": [{"type": "text", "content": "活动价268元"}],
                "critic": {"status": "pass"},
                "human_review": {"status": "pending", "verdict": ""},
            }
        ]
    }
    evaluation = evaluate(golden, result)
    assert evaluation["critic"]["status"] == "pending_human_review"
    assert evaluation["critic"]["calibration"]["evaluated"] == 0


def test_read_only_evaluation_service_rejects_traversal(tmp_path: Path) -> None:
    service = V3EvaluationService(tmp_path)
    with pytest.raises(ValueError):
        service.get_run("../secret")


def _case() -> dict:
    return {
        "case_id": "golden_v3_test",
        "evaluation_partition": "calibration",
        "category": "首次询价",
        "input": {
            "current_time": "2026-08-14T10:00:00+08:00",
            "conversation": [{"role": "assistant", "content": "您好"}],
            "current_message": {"msgtype": "text", "content": "多少钱"},
            "tool_facts": {"visible_stores": [{"store_id": "12", "store_name": "测试店"}]},
            "payment_and_order_facts": {"orders": []},
            "delivered_assets": [],
        },
        "annotation": {
            "must_answer_points": ["说明活动价"],
            "required_gate_asset_ids": ["activity"],
            "acceptable_gate_asset_ids": [],
            "required_deliveries": [],
            "forbidden_actions": ["payment_collection"],
            "quality_expectations": {"current_question_solved": True},
            "reference_reply_direction": "回答价格并自然推进",
            "reference_reply_examples": ["不应进入运行时"],
        },
    }
