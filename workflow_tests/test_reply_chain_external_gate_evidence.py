from __future__ import annotations

from pathlib import Path

from app.services.reply_chain_external_gate_evidence import (
    business_wording_freeze_report_blockers,
    model_matrix_report_blockers,
    rollback_evidence_report_blockers,
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
        "git_commit_set": ["abc123"],
        "relay_base_url": "https://linkai.shop/v1",
        "profiles_requested": ["claude", "gemini", "openai"],
        "executed_profile_count": 3,
        "profiles": [
            {
                "status": "completed",
                "model_profile": {
                    "name": "claude",
                    "model": "claude-opus-4-7",
                    "protocol": "openai-compatible relay",
                    "api_key_value_logged": False,
                },
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
                "model_profile": {
                    "name": "gemini",
                    "model": "gemini-3.5-flash",
                    "protocol": "openai-compatible relay",
                    "api_key_value_logged": False,
                },
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
                "model_profile": {
                    "name": "openai",
                    "model": "gpt-5.4",
                    "protocol": "openai-compatible relay",
                    "api_key_value_logged": False,
                },
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


def _business_wording_freeze_ready() -> dict:
    return {
        "schema_version": "reply_chain_business_wording_freeze_audit_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "base_ref": "main",
        "head_ref": "HEAD",
        "protected_paths": [
            "ai_paths/app/policies/business_rules.json",
            "config/sop_reply_packs.json",
        ],
        "changed_paths": ["ai_paths/app/services/chat_gate_router_shadow.py"],
        "changed_protected_paths": [],
        "customer_visible_business_assets_unchanged": True,
        "review_required": False,
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
        },
    }


def _rollback_evidence_ready() -> dict:
    return {
        "schema_version": "reply_chain_refactor_rollback_evidence_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "base_ref": "main",
        "head_ref": "HEAD",
        "branch": "codex/reply-chain-refactor",
        "expected_branch": "codex/reply-chain-refactor",
        "changed_paths": ["ai_paths/app/services/chat_gate_router_shadow.py"],
        "changed_deployment_sensitive_paths": [],
        "branch_is_refactor": True,
        "main_branch_untouched": True,
        "deployment_sensitive_paths_unchanged": True,
        "rollback_plan": {
            "schema_version": "reply_chain_behavior_switch_rollback_plan_v1",
            "restore_flags_to_shadow_or_disabled": True,
            "revert_stage_commit": True,
            "rerun_diagnostics_before_reenable": True,
            "no_deployment_from_refactor_branch": True,
            "rollback_steps": ["disable flags", "revert commit", "rerun diagnostics"],
        },
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
            "does_not_deploy": True,
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


def test_external_gate_evidence_blocks_missing_or_mismatched_simulation_commit_set() -> None:
    simulation = _simulation_ready()
    del simulation["git_commit_set"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_git_commit_set" in blockers

    simulation = _simulation_ready()
    simulation["git_commit_set"] = ["def456"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_git_commit_set_mismatch:def456!=abc123" in blockers


def test_external_gate_evidence_blocks_missing_or_mixed_model_matrix_commit() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["git_commit"] = ""
    model_matrix["git_commit_set"] = ["abc123", "def456"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_git_commit" in blockers
    assert "model_matrix_multiple_git_commits:abc123,def456" in blockers


def test_external_gate_evidence_blocks_missing_or_mismatched_model_matrix_commit_set() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["git_commit_set"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_git_commit_set" in blockers

    model_matrix = _model_matrix_ready()
    model_matrix["git_commit_set"] = ["def456"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_git_commit_set_mismatch:def456!=abc123" in blockers


def test_external_gate_evidence_accepts_business_wording_freeze_report() -> None:
    assert business_wording_freeze_report_blockers(_business_wording_freeze_ready()) == []


def test_external_gate_evidence_accepts_rollback_evidence_report() -> None:
    assert rollback_evidence_report_blockers(_rollback_evidence_ready()) == []


def test_external_gate_evidence_blocks_rollback_evidence_wrong_branch_or_deploy_paths() -> None:
    report = _rollback_evidence_ready()
    report["branch"] = "main"
    report["branch_is_refactor"] = False
    report["main_branch_untouched"] = False
    report["changed_deployment_sensitive_paths"] = [".github/workflows/deploy.yml"]
    report["deployment_sensitive_paths_unchanged"] = False

    blockers = rollback_evidence_report_blockers(report)

    assert "rollback_evidence_wrong_branch:main" in blockers
    assert "rollback_evidence_branch_not_refactor" in blockers
    assert "rollback_evidence_main_branch_not_untouched" in blockers
    assert "rollback_evidence_deployment_sensitive_path_changed:.github/workflows/deploy.yml" in blockers
    assert "rollback_evidence_deployment_sensitive_paths_not_unchanged" in blockers


def test_external_gate_evidence_blocks_rollback_evidence_missing_plan_or_safety() -> None:
    report = _rollback_evidence_ready()
    report["rollback_plan"]["rollback_steps"] = []
    report["rollback_plan"]["revert_stage_commit"] = False
    report["safety"]["does_not_deploy"] = False
    del report["git_commit_set"]

    blockers = rollback_evidence_report_blockers(report)

    assert "rollback_evidence_missing_git_commit_set" in blockers
    assert "rollback_evidence_missing_revert_stage_commit" in blockers
    assert "rollback_evidence_missing_rollback_steps" in blockers
    assert "rollback_evidence_missing_no_deploy_safety" in blockers


def test_external_gate_evidence_blocks_business_wording_freeze_protected_changes() -> None:
    report = _business_wording_freeze_ready()
    report["changed_protected_paths"] = [
        "ai_paths/app/policies/business_rules.json",
        "config/sop_reply_packs.json",
    ]
    report["customer_visible_business_assets_unchanged"] = False

    blockers = business_wording_freeze_report_blockers(report)

    assert (
        "business_wording_freeze_protected_path_changed:ai_paths/app/policies/business_rules.json"
        in blockers
    )
    assert "business_wording_freeze_protected_path_changed:config/sop_reply_packs.json" in blockers
    assert "business_wording_freeze_assets_not_unchanged" in blockers


def test_external_gate_evidence_blocks_business_wording_freeze_missing_safety() -> None:
    report = _business_wording_freeze_ready()
    report["safety"]["does_not_call_models"] = False
    del report["git_commit_set"]

    blockers = business_wording_freeze_report_blockers(report)

    assert "business_wording_freeze_missing_git_commit_set" in blockers
    assert "business_wording_freeze_missing_no_model_call_safety" in blockers


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


def test_external_gate_evidence_blocks_unapproved_model_matrix_relay_or_key_logging() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["relay_base_url"] = "https://other-relay.example/v1"
    model_matrix["profiles"][0]["model_profile"]["protocol"] = "anthropic"
    del model_matrix["profiles"][1]["model_profile"]["api_key_value_logged"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_unapproved_relay_base_url:https://other-relay.example/v1" in blockers
    assert "model_matrix_profile_protocol_mismatch:claude" in blockers
    assert "model_matrix_profile_key_redaction_missing:gemini" in blockers


def test_bundle_audit_does_not_import_final_behavior_switch_guard_private_helpers() -> None:
    source = (ROOT / "ai_paths/app/services/reply_chain_shadow_bundle_audit.py").read_text(encoding="utf-8")

    assert "from app.services.reply_chain_behavior_switch_guard import" not in source
    assert "_simulation_blockers" not in source
    assert "_model_matrix_blockers" not in source
