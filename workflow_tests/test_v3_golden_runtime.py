from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.evaluation.v3_critic import (
    CRITIC_SYSTEM_PROMPT,
    CriticContractError,
    build_delivery_audit,
    evaluate_with_critic,
    validate_critic_result,
)
from app.evaluation.v3_golden import golden_case_to_simulation, simulation_result_to_golden_result
from app.services.v3_evaluation_service import V3EvaluationService
from scripts.evaluate_v3_trusted_golden import evaluate
from scripts.run_v3_trusted_golden import _candidate_knowledge, _git_commit


def test_golden_case_maps_to_isolated_simulation_without_reference_answers() -> None:
    case = _case()
    scenario = golden_case_to_simulation(case)

    assert scenario["id"] == "golden_v3_test"
    assert scenario["initial"]["stores"][0]["store_id"] == "12"
    assert scenario["initial"]["history_events"] == []
    assert scenario["timeline"][0]["content"] == "多少钱"
    assert "reference_reply_examples" not in json.dumps(scenario, ensure_ascii=False)


def test_golden_case_preserves_non_text_current_message_facts() -> None:
    case = _case()
    case["input"]["current_message"] = {
        "msgtype": "location",
        "content": "定位卡",
        "location": {"latitude": 23.0, "longitude": 113.0, "address": "测试位置"},
    }

    scenario = golden_case_to_simulation(case)

    assert scenario["timeline"][0]["location"] == {
        "latitude": 23.0,
        "longitude": 113.0,
        "address": "测试位置",
    }


def test_golden_case_preserves_structured_confirmed_store_fact() -> None:
    case = _case()
    case["input"]["confirmed_store_name"] = "荆州万达二店"

    scenario = golden_case_to_simulation(case)

    assert scenario["timeline"][0]["confirmed_store_name"] == "荆州万达二店"


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
                        "reply_knowledge_use": {
                            "sequence_id": "seq-1",
                            "step_id": "step-1",
                            "selected_script_ids": ["script-1"],
                        },
                    },
                }
            ],
        },
    )
    assert result["reply_messages"][0]["content"] == "活动价268元"
    assert result["content_selection_metrics"]["nominated_ids"] == ["activity"]
    assert result["content_decisions"][0]["decision"] == "adopt"
    assert result["knowledge_use"]["sequence_id"] == "seq-1"
    assert result["knowledge_use"]["selected_script_ids"] == ["script-1"]
    assert result["human_review"]["status"] == "pending"


def test_simulation_result_exposes_only_sales_policy_diagnostics() -> None:
    result = simulation_result_to_golden_result(
        _case(),
        {"hard_pass": True, "steps": [{"request_id": "req", "sync_reply_messages": [{"type": "text", "content": "先说清楚价格"}], "response_meta": {}, "run": {"customer_id": "must-not-leak", "output_snapshot": {"primary_task": {"type": "answer_current_question"}, "cardpoint_decision": {"category_key": "price_objection"}, "cardpoint_candidates": [{"content_id": "content-1", "scenario_name": "价格高", "reference_text": "解释价值", "source": {"row": 1}}]}}}]},
    )
    assert result["sales_policy"]["cardpoint_decision"]["category_key"] == "price_objection"
    assert result["sales_policy"]["cardpoint_candidates"][0]["content_id"] == "content-1"
    assert "source" not in result["sales_policy"]["cardpoint_candidates"][0]
    assert "customer_id" not in result["sales_policy"]


def test_offline_candidate_uses_runtime_provenance_field_names() -> None:
    knowledge = _candidate_knowledge(
        {
            "candidate_key": "soft_hesitation_unknown",
            "objective": "识别一个真实顾虑",
            "forbidden": ["重复报价", "直接付款"],
            "sequence": {
                "id": "candidate_seq",
                "sequence_name": "未知顾虑最小确认",
                "steps": [
                    {
                        "id": "candidate_step",
                        "action_code": "empathy",
                    }
                ],
            },
            "scripts": [
                {
                    "id": "candidate_script",
                    "script_code": "candidate_script",
                    "action_code": "empathy",
                    "paragraphs": [],
                }
            ],
        }
    )

    sequence = knowledge["sequence_candidates"][0]
    script = knowledge["candidates"][0]
    assert sequence["sequence_id"] == "candidate_seq"
    assert sequence["steps"][0]["step_id"] == "candidate_step"
    assert script["sequence_links"] == [
        {
            "sequence_id": "candidate_seq",
            "step_id": "candidate_step",
            "action_code": "empathy",
        }
    ]
    assert knowledge["candidate_objective"] == "识别一个真实顾虑"
    assert knowledge["candidate_boundaries"] == ["重复报价", "直接付款"]


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
                "sales_humanness": 5,
                "confidence_building": 4,
                "value_reframing": 4,
            },
            "failure_owner": "none",
            "violations": [],
            "reason": "符合标准",
        }
    )
    assert parsed["status"] == "pass"
    assert parsed["scores"]["sales_humanness"] == 5
    assert "qualitative social proof" in lowered
    assert "must not reduce fact_safety" in lowered
    assert "when required_ids is empty, never invent a" in lowered
    assert "check every must_answer_points item explicitly" in lowered
    assert "introduces_new_concern=false" in lowered
    assert "registration or payment without first" in lowered
    assert "judge must-answer coverage by meaning" in lowered
    assert "asking whether the customer wants to continue" in lowered
    assert "require authoritative completion evidence" in lowered


def test_critic_contract_rejects_inconsistent_pass_scores() -> None:
    invalid = {
        "status": "pass",
        "scores": {
            "current_question": 1,
            "history_continuity": 1,
            "natural_advance": 1,
            "evidence_relevance": 1,
            "human_tone": 1,
            "fact_safety": 1,
            "sales_humanness": 1,
            "confidence_building": 1,
            "value_reframing": 1,
        },
        "failure_owner": "none",
        "violations": [],
        "reason": "",
    }
    with pytest.raises(ValueError, match="score below 4"):
        validate_critic_result(invalid)


def _critic_scores() -> dict[str, int]:
    return {
        "current_question": 5,
        "history_continuity": 5,
        "natural_advance": 5,
        "evidence_relevance": 5,
        "human_tone": 5,
        "fact_safety": 5,
        "sales_humanness": 5,
        "confidence_building": 5,
        "value_reframing": 5,
    }


def test_delivery_audit_does_not_invent_required_assets() -> None:
    audit = build_delivery_audit(
        case={"annotation": {"required_deliveries": []}},
        result={"reply_messages": [{"type": "text", "content": "先说明规则"}]},
    )

    assert audit["required_ids"] == []
    assert audit["missing_required_ids"] == []
    with pytest.raises(CriticContractError, match="delivery_owner_without"):
        validate_critic_result(
            {
                "status": "fail",
                "scores": _critic_scores(),
                "failure_owner": "delivery",
                "violations": [],
                "reason": "误认为需要发卡",
            },
            delivery_audit=audit,
        )


def test_delivery_audit_identifies_required_and_adopted_delivery_gaps() -> None:
    audit = build_delivery_audit(
        case={"annotation": {"required_deliveries": ["activity_image"]}},
        result={
            "content_selection_metrics": {
                "adopted_ids": ["s10_activity_intro"],
                "delivered_ids": [],
            },
            "reply_messages": [{"type": "text", "content": "活动价268元"}],
        },
    )

    assert audit["missing_required_ids"] == ["activity_image"]
    assert audit["adopted_not_delivered_ids"] == ["s10_activity_intro"]


def test_delivery_audit_records_exact_payment_collection_amount() -> None:
    audit = build_delivery_audit(
        case={"annotation": {"required_deliveries": ["payment_collection:20"]}},
        result={
            "reply_messages": [
                {"type": "payment_collection", "content": {"amount": 20, "remark": ""}}
            ]
        },
    )

    assert "payment_collection" in audit["delivered_ids"]
    assert "payment_collection:20" in audit["delivered_ids"]
    assert audit["missing_required_ids"] == []


def test_critic_retries_invalid_delivery_owner_without_relabeling() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_json(self, messages, **kwargs):
            self.calls += 1
            owner = "delivery" if self.calls == 1 else "reply"
            return {
                "status": "fail",
                "scores": _critic_scores(),
                "failure_owner": owner,
                "violations": [{"code": "missing_answer", "quote": "", "reason": "必答点遗漏"}],
                "reason": "必答点遗漏",
            }

    client = StubClient()
    result = asyncio.run(
        evaluate_with_critic(
            client,  # type: ignore[arg-type]
            case={"case_id": "case", "annotation": {"required_deliveries": []}, "input": {}},
            result={"reply_messages": [{"type": "text", "content": "收到"}]},
        )
    )

    assert client.calls == 2
    assert result["failure_owner"] == "reply"
    assert result["critic_contract_invalid"] is True
    assert result["contract_retry_count"] == 1


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


def test_v3_golden_runner_mounts_the_real_semantic_router() -> None:
    source = Path("ai_paths/scripts/run_v3_trusted_golden.py").read_text(encoding="utf-8")

    assert "DeepSeekSemanticClient(settings, fallback_client=None)" in source
    assert '"gpt-5.4"' not in source
    assert '"gpt-5.4-mini"' not in source
    assert "knowledge_client=follow_knowledge" in source
    assert "runtime_router = _OfflineKnowledgeConditionRouter(" in source
    assert "semantic_router," in source
    assert "semantic_router_service=runtime_router" in source
    assert 'if self.condition == "online":' in source


def test_local_candidate_knowledge_is_simulation_only_and_never_imported_by_runtime() -> None:
    fixture = Path("workflow_tests/fixtures/v3_local_sales_knowledge_candidates_v1.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("ai_paths/app").rglob("*.py")
    )

    assert payload["runtime_allowed"] is False
    assert payload["callback_allowed"] is False
    assert payload["review_status"] == "pending_business_review"
    assert len(payload["cases"]) == 6
    assert "v3_local_sales_knowledge_candidates_v1" not in runtime_source
    assert "candidate_seq_" not in runtime_source
    for item in payload["cases"]:
        assert str(item["sequence"]["id"]).startswith("candidate_seq_")
        assert all(str(script["script_code"]).startswith("candidate_script_") for script in item["scripts"])


def test_focused_golden_cases_allow_router_label_sets_without_weakening_reply_contract() -> None:
    payload = json.loads(
        Path("workflow_tests/fixtures/v3_trusted_golden_set_v1.json").read_text(encoding="utf-8")
    )
    by_id = {item["case_id"]: item for item in payload["cases"]}

    assert by_id["golden_v3_019"]["annotation"]["acceptable_checkpoint_codes"] == [
        "time_conflict",
        "hesitation",
        "distance",
    ]
    assert by_id["golden_v3_023"]["annotation"]["acceptable_checkpoint_codes"] == [
        "time_conflict",
        "hesitation",
    ]
    assert by_id["golden_v3_047"]["annotation"]["acceptable_checkpoint_codes"] == ["hesitation"]
    assert by_id["golden_v3_049"]["annotation"]["acceptable_checkpoint_codes"] == []
    for case_id in ("golden_v3_019", "golden_v3_023", "golden_v3_047", "golden_v3_049"):
        annotation = by_id[case_id]["annotation"]
        assert annotation["must_answer_points"]
        assert "forbidden_actions" in annotation


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
