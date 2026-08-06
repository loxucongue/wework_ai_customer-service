from __future__ import annotations

from pathlib import Path

from app.services.reply_chain_external_gate_evidence import (
    model_matrix_report_blockers,
    simulation_report_blockers,
)


ROOT = Path(__file__).resolve().parents[1]


def _simulation_ready() -> dict:
    return {
        "schema_version": "offline_reply_chain_simulation_report_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "scenario_count": 100,
        "attempt_count": 300,
        "hard_error_count": 0,
        "semantic_pass_rate": 0.93,
        "failed_critical_scenarios": [],
        "scenario_summary": {
            f"sim_case_{index}": {
                "category": "sim",
                "critical": False,
                "attempts": 3,
                "hard_passes": 3,
                "semantic_passes": 3,
                "infrastructure_failures": 0,
            }
            for index in range(100)
        },
        "summary": {
            "infrastructure_failures": 0,
            "acceptance": {
                "hard_errors_zero": True,
                "semantic_at_least_90": True,
                "critical_all_pass": True,
                "infrastructure_failures_zero": True,
                "scenario_coverage_complete": True,
                "isolation_audit_passed": True,
            },
        },
        "coverage": {
            "schema_version": "offline_simulation_coverage_audit_v1",
            "missing_required_categories": [],
            "missing_critical_required_categories": [],
        },
        "effect_review": {
            "schema_version": "offline_simulation_effect_review_v1",
            "result_count": 300,
            "issue_count": 0,
            "low_score_count": 0,
            "hard_or_infra_count": 0,
            "items": [],
        },
        "review_artifacts": {
            "schema_version": "offline_simulation_review_artifacts_v1",
            "result_count": 300,
            "request_count": 10,
            "event_count": 3,
            "tool_call_count": 5,
            "outbox_batch_count": 4,
            "simulated_write_count": 2,
            "results": [{"scenario_id": "sim_case", "request_ids": ["sim_request_1"]}],
        },
        "safety": {
            "production_customer_messages_sent": False,
            "production_writes_allowed": False,
            "virtual_outbox_only": True,
            "production_write_count": 0,
        },
        "isolation_audit": {
            "schema_version": "offline_simulation_isolation_summary_v1",
            "result_count": 300,
            "missing_result_count": 0,
            "failed_result_count": 0,
            "passed": True,
            "run_dirs_under_tmp_simulation": True,
            "paths_within_run_dir": True,
            "connector_urls_simulation_only": True,
            "adapters_simulation_only": True,
            "identity_simulation_scoped": True,
            "real_connector_credentials_present": False,
        },
    }


def _model_matrix_ready() -> dict:
    return {
        "schema_version": "reply_chain_refactor_model_matrix_v1",
        "git_commit": "abc123",
        "profiles_requested": ["claude", "gemini", "openai"],
        "executed_profile_count": 3,
        "profiles": [
            {
                "status": "completed",
                "model_profile": {"name": "claude", "model": "claude-opus-4-7"},
                "profile_summary": {
                    "semantic_pass_rate": 0.91,
                    "p50_ms": 6200,
                    "p90_ms": 11000,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 0,
                    "effect_low_score_count": 0,
                    "effect_hard_or_infra_count": 0,
                    "accepted_by_release_thresholds": True,
                },
            },
            {
                "status": "completed",
                "model_profile": {"name": "gemini", "model": "gemini-3.5-flash"},
                "profile_summary": {
                    "semantic_pass_rate": 0.9,
                    "p50_ms": 3900,
                    "p90_ms": 7600,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 1,
                    "effect_low_score_count": 1,
                    "effect_hard_or_infra_count": 0,
                    "accepted_by_release_thresholds": True,
                },
            },
            {
                "status": "completed",
                "model_profile": {"name": "openai", "model": "gpt-5.4"},
                "profile_summary": {
                    "semantic_pass_rate": 0.94,
                    "p50_ms": 4800,
                    "p90_ms": 8200,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 0,
                    "effect_low_score_count": 0,
                    "effect_hard_or_infra_count": 0,
                    "accepted_by_release_thresholds": True,
                },
            },
        ],
        "ranking": [
            {
                "name": "openai",
                "model": "gpt-5.4",
                "semantic_pass_rate": 0.94,
                "hard_error_count": 0,
                "infrastructure_failures": 0,
                "p50_ms": 4800,
                "p90_ms": 8200,
                "effect_issue_count": 0,
                "effect_low_score_count": 0,
                "effect_hard_or_infra_count": 0,
            },
            {
                "name": "claude",
                "model": "claude-opus-4-7",
                "semantic_pass_rate": 0.91,
                "hard_error_count": 0,
                "infrastructure_failures": 0,
                "p50_ms": 6200,
                "p90_ms": 11000,
                "effect_issue_count": 0,
                "effect_low_score_count": 0,
                "effect_hard_or_infra_count": 0,
            },
            {
                "name": "gemini",
                "model": "gemini-3.5-flash",
                "semantic_pass_rate": 0.9,
                "hard_error_count": 0,
                "infrastructure_failures": 0,
                "p50_ms": 3900,
                "p90_ms": 7600,
                "effect_issue_count": 1,
                "effect_low_score_count": 1,
                "effect_hard_or_infra_count": 0,
            },
        ],
        "safety": {
            "api_keys_written_to_report": False,
            "production_customer_messages_sent": False,
            "production_writes_allowed": False,
        },
    }


def test_external_gate_evidence_accepts_complete_reports() -> None:
    assert simulation_report_blockers(_simulation_ready()) == []
    assert model_matrix_report_blockers(_model_matrix_ready()) == []


def test_external_gate_evidence_blocks_unsafe_or_incomplete_reports() -> None:
    simulation = _simulation_ready()
    simulation["semantic_pass_rate"] = 0.89
    simulation["safety"]["production_customer_messages_sent"] = True

    model_matrix = _model_matrix_ready()
    model_matrix["profiles"] = [
        item
        for item in model_matrix["profiles"]
        if item["model_profile"]["name"] != "gemini"
    ]
    model_matrix["executed_profile_count"] = 2

    assert "simulation_semantic_pass_rate_below_90:0.890" in simulation_report_blockers(simulation)
    assert "simulation_missing_no_customer_send_safety" in simulation_report_blockers(simulation)
    assert "model_matrix_profile_not_completed:gemini" in model_matrix_report_blockers(model_matrix)
    assert "model_matrix_executed_profile_count_mismatch:2" in model_matrix_report_blockers(model_matrix)


def test_external_gate_evidence_blocks_too_small_simulation_report() -> None:
    simulation = _simulation_ready()
    simulation["scenario_count"] = 17
    simulation["attempt_count"] = 17
    simulation["review_artifacts"]["result_count"] = 16

    blockers = simulation_report_blockers(simulation)

    assert "simulation_scenario_count_below_100:17" in blockers
    assert "simulation_review_artifacts_result_count_below_scenario_count:16<17" in blockers


def test_external_gate_evidence_blocks_attempt_count_below_scenario_count() -> None:
    simulation = _simulation_ready()
    simulation["attempt_count"] = 99

    blockers = simulation_report_blockers(simulation)

    assert "simulation_attempt_count_below_scenario_count:99<100" in blockers


def test_external_gate_evidence_blocks_insufficient_repeated_attempts() -> None:
    simulation = _simulation_ready()
    simulation["scenario_summary"]["sim_case_0"]["attempts"] = 1
    simulation["scenario_summary"]["sim_case_1"]["critical"] = True
    simulation["scenario_summary"]["sim_case_1"]["attempts"] = 3
    simulation["review_artifacts"]["result_count"] = 299

    blockers = simulation_report_blockers(simulation)

    assert "simulation_scenario_attempts_below_required:sim_case_0:1<3" in blockers
    assert "simulation_scenario_attempts_below_required:sim_case_1:3<5" in blockers
    assert "simulation_review_artifacts_result_count_below_attempt_count:299<300" in blockers


def test_external_gate_evidence_blocks_missing_scenario_summary() -> None:
    simulation = _simulation_ready()
    del simulation["scenario_summary"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_scenario_summary" in blockers


def test_external_gate_evidence_blocks_missing_or_mixed_simulation_commit() -> None:
    simulation = _simulation_ready()
    simulation["git_commit"] = ""
    simulation["git_commit_set"] = ["abc123", "def456"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_git_commit" in blockers
    assert "simulation_multiple_git_commits:abc123,def456" in blockers


def test_external_gate_evidence_blocks_missing_model_matrix_commit() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["git_commit"] = ""

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_git_commit" in blockers


def test_external_gate_evidence_blocks_missing_simulation_coverage() -> None:
    simulation = _simulation_ready()
    simulation["coverage"] = {
        "schema_version": "offline_simulation_coverage_audit_v1",
        "missing_required_categories": ["健康风险"],
    }
    simulation["summary"]["acceptance"]["scenario_coverage_complete"] = False

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_required_category:健康风险" in blockers
    assert "simulation_scenario_coverage_incomplete" in blockers


def test_external_gate_evidence_blocks_missing_or_failed_simulation_infrastructure_acceptance() -> None:
    simulation = _simulation_ready()
    simulation["summary"] = {
        "infrastructure_failures": 1,
        "acceptance": {
            "scenario_coverage_complete": True,
        },
    }

    blockers = simulation_report_blockers(simulation)

    assert "simulation_infrastructure_failures:1" in blockers
    assert "simulation_infrastructure_acceptance_missing_or_false" in blockers


def test_external_gate_evidence_names_timed_out_model_profile() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][1] = {
        "status": "timed_out",
        "model_profile": {"name": "gemini", "model": "gemini-3.5-flash"},
        "profile_summary": {
            "infrastructure_failures": 1,
            "timeout_seconds": 120,
            "accepted_by_release_thresholds": False,
        },
    }

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_profile_not_completed:gemini" in blockers
    assert "model_matrix_profile_timed_out:gemini:120" in blockers


def test_external_gate_evidence_blocks_wrong_model_for_profile() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][2]["model_profile"]["model"] = "gpt-5.4-mini"

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_profile_model_mismatch:openai:gpt-5.4-mini" in blockers


def test_external_gate_evidence_blocks_missing_core_simulation_acceptance_fields() -> None:
    simulation = _simulation_ready()
    simulation["summary"]["acceptance"] = {
        "infrastructure_failures_zero": True,
        "scenario_coverage_complete": True,
    }

    blockers = simulation_report_blockers(simulation)

    assert "simulation_hard_error_acceptance_missing_or_false" in blockers
    assert "simulation_semantic_acceptance_missing_or_false" in blockers
    assert "simulation_critical_acceptance_missing_or_false" in blockers


def test_external_gate_evidence_blocks_missing_simulation_review_artifacts() -> None:
    simulation = _simulation_ready()
    del simulation["review_artifacts"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_review_artifacts" in blockers


def test_external_gate_evidence_blocks_missing_critical_simulation_category() -> None:
    simulation = _simulation_ready()
    simulation["coverage"]["missing_critical_required_categories"] = ["精准问答"]
    simulation["summary"]["acceptance"]["scenario_coverage_complete"] = False

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_critical_required_category:精准问答" in blockers
    assert "simulation_scenario_coverage_incomplete" in blockers


def test_external_gate_evidence_blocks_missing_simulation_effect_review() -> None:
    simulation = _simulation_ready()
    del simulation["effect_review"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_effect_review" in blockers


def test_external_gate_evidence_blocks_incomplete_simulation_effect_review() -> None:
    simulation = _simulation_ready()
    simulation["effect_review"]["result_count"] = 299

    blockers = simulation_report_blockers(simulation)

    assert "simulation_effect_review_result_count_below_attempt_count:299<300" in blockers


def test_external_gate_evidence_requires_effect_review_samples_for_failures() -> None:
    simulation = _simulation_ready()
    simulation["hard_error_count"] = 1
    simulation["semantic_pass_rate"] = 0.89
    simulation["effect_review"]["hard_or_infra_count"] = 0
    simulation["effect_review"]["low_score_count"] = 0

    blockers = simulation_report_blockers(simulation)

    assert "simulation_effect_review_missing_hard_error_samples:1" in blockers
    assert "simulation_effect_review_missing_low_score_samples" in blockers


def test_external_gate_evidence_blocks_missing_simulation_isolation_audit() -> None:
    simulation = _simulation_ready()
    del simulation["isolation_audit"]
    simulation["summary"]["acceptance"]["isolation_audit_passed"] = False

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_isolation_audit" in blockers
    assert "simulation_isolation_acceptance_missing_or_false" in blockers


def test_external_gate_evidence_blocks_failed_simulation_isolation_audit() -> None:
    simulation = _simulation_ready()
    simulation["isolation_audit"]["passed"] = False
    simulation["isolation_audit"]["connector_urls_simulation_only"] = False
    simulation["isolation_audit"]["real_connector_credentials_present"] = True
    simulation["isolation_audit"]["failed_result_count"] = 1
    simulation["isolation_audit"]["result_count"] = 99

    blockers = simulation_report_blockers(simulation)

    assert "simulation_isolation_not_passed" in blockers
    assert "simulation_isolation_connector_url_not_simulation" in blockers
    assert "simulation_isolation_real_credentials_present" in blockers
    assert "simulation_isolation_failed_results:1" in blockers
    assert "simulation_isolation_result_count_below_scenario_count:99<100" in blockers


def test_external_gate_evidence_blocks_incomplete_simulation_review_artifacts() -> None:
    simulation = _simulation_ready()
    del simulation["review_artifacts"]["tool_call_count"]
    simulation["review_artifacts"]["results"] = "not-a-list"

    blockers = simulation_report_blockers(simulation)

    assert "simulation_review_artifacts_missing_field:tool_call_count" in blockers
    assert "simulation_review_artifacts_missing_results" in blockers


def test_external_gate_evidence_blocks_accepted_model_with_infrastructure_failures() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][0]["profile_summary"]["infrastructure_failures"] = 2

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_accepted_profile_has_infrastructure_failures:claude:2" in blockers


def test_external_gate_evidence_blocks_accepted_model_missing_infrastructure_failures() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["profiles"][0]["profile_summary"]["infrastructure_failures"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_accepted_profile_missing_infrastructure_failures:claude" in blockers


def test_external_gate_evidence_blocks_model_matrix_missing_effect_review_counts() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["profiles"][1]["profile_summary"]["effect_issue_count"]
    del model_matrix["profiles"][1]["profile_summary"]["effect_low_score_count"]
    del model_matrix["profiles"][1]["profile_summary"]["effect_hard_or_infra_count"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_effect_issue_count:gemini" in blockers
    assert "model_matrix_missing_effect_low_score_count:gemini" in blockers
    assert "model_matrix_missing_effect_hard_or_infra_count:gemini" in blockers


def test_external_gate_evidence_blocks_model_matrix_incomplete_ranking() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["ranking"] = [item for item in model_matrix["ranking"] if item["name"] != "gemini"]
    del model_matrix["ranking"][0]["effect_issue_count"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_ranking_missing_completed_profile:gemini" in blockers
    assert "model_matrix_ranking_missing_effect_issue_count:openai" in blockers


def test_bundle_audit_does_not_import_final_behavior_switch_guard_private_helpers() -> None:
    source = (ROOT / "ai_paths/app/services/reply_chain_shadow_bundle_audit.py").read_text(encoding="utf-8")

    assert "from app.services.reply_chain_behavior_switch_guard import" not in source
    assert "_simulation_blockers" not in source
    assert "_model_matrix_blockers" not in source
