from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.services.reply_chain_external_gate_evidence import (
    REQUIRED_SIMULATION_COVERAGE_CATEGORIES,
    business_wording_freeze_report_blockers,
    model_matrix_report_blockers,
    model_semantics_ownership_report_blockers,
    payload_isolation_report_blockers,
    rollback_evidence_report_blockers,
    simulation_report_blockers,
)


ROOT = Path(__file__).resolve().parents[1]


def _secret_like_value() -> str:
    return "s" + "k-test-secret-value-should-never-enter-release-reports"


def _review_artifact_results(count: int = 300) -> list[dict]:
    return [
        {
            "scenario_id": f"sim_case_{index % 100}",
            "attempt": (index % 3) + 1,
            "request_ids": [f"sim_request_{index}"],
            "event_ids": [],
            "node_trace_names": ["sop_chat_gate", "planner", "reply"],
            "tool_call_names": ["customer_store_lookup"],
            "sync_reply_message_count": 1,
            "outbox_batch_count": 1,
            "simulated_write_count": 0,
        }
        for index in range(count)
    ]


def _profile_artifacts(profile: str, *, attempt_count: int = 300) -> dict:
    return {
        "schema_version": "reply_chain_refactor_model_profile_artifacts_v1",
        "result_json_path": f".tmp_runtime/simulation/model-matrix/{profile}/result.json",
        "report_md_path": f".tmp_runtime/simulation/model-matrix/{profile}/report.md",
        "result_json_written": True,
        "report_md_written": True,
        "scenario_count": 100,
        "attempt_count": attempt_count,
        "effect_review_result_count": attempt_count,
        "review_artifacts_result_count": attempt_count,
    }


def _simulation_ready() -> dict:
    return {
        "schema_version": "offline_reply_chain_simulation_report_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "evaluation_scope": {
            "schema_version": "offline_simulation_scope_v1",
            "scenario_id": "",
            "category": "",
            "max_cases": 0,
            "targeted_smoke": False,
            "full_release_gate_candidate": True,
        },
        "run_options": {
            "schema_version": "offline_simulation_run_options_v1",
            "attempts": 3,
            "critical_attempts": 5,
            "concurrency": 2,
            "skip_review": False,
            "reviewer_model": "",
        },
        "scenario_count": 100,
        "attempt_count": 300,
        "hard_error_count": 0,
        "semantic_pass_rate": 0.93,
        "failed_critical_scenarios": [],
        "baseline_comparison": {
            "schema_version": "offline_simulation_baseline_comparison_v1",
            "available": True,
            "improved": [],
            "regressed": [],
            "unchanged": [f"sim_case_{index}" for index in range(100)],
        },
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
            "evaluable_attempts": 300,
            "infrastructure_failures": 0,
            "acceptance": {
                "hard_errors_zero": True,
                "semantic_review_complete": True,
                "semantic_at_least_90": True,
                "critical_all_pass": True,
                "infrastructure_failures_zero": True,
                "scenario_coverage_complete": True,
                "isolation_audit_passed": True,
                "baseline_comparison_passed": True,
                "semantic_ownership_passed": True,
            },
        },
        "semantic_ownership_audit": {
            "schema_version": "offline_simulation_semantic_ownership_audit_v1",
            "result_count": 300,
            "evidence_result_count": 300,
            "missing_evidence_count": 0,
            "violation_count": 0,
            "passed": True,
            "required_evidence": [
                "chat_gate_commit_boundary_v1",
                "tool_plan_preview_v2",
                "reply_chain_join_shadow_v1",
                "parallel_reply_chain_shadow_v1",
            ],
            "checks": [
                "gate_shadow_cannot_commit_or_send",
                "tool_planner_has_zero_business_semantic_residue",
                "join_does_not_generate_customer_visible_text",
                "join_does_not_decide_sales_psychology",
                "reply_remains_final_expression_owner_for_complex_turns",
            ],
            "violations": [],
        },
        "coverage": {
            "schema_version": "offline_simulation_coverage_audit_v1",
            "required_categories": list(REQUIRED_SIMULATION_COVERAGE_CATEGORIES),
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
            "results": _review_artifact_results(),
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
        "evaluation_scope": {
            "schema_version": "reply_chain_refactor_model_matrix_scope_v1",
            "scenario_id": "",
            "category": "",
            "max_cases": 0,
            "targeted_smoke": False,
            "full_release_gate_candidate": True,
        },
        "run_options": {
            "schema_version": "reply_chain_refactor_model_matrix_run_options_v1",
            "attempts": 3,
            "critical_attempts": 5,
            "concurrency": 2,
            "skip_review": False,
            "profile_timeout_seconds": 120,
            "baseline_path_present": True,
        },
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
                    "hard_error_count": 0,
                    "failed_critical_scenarios": [],
                    "p50_ms": 6200,
                    "p90_ms": 11000,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 0,
                    "effect_low_score_count": 0,
                    "effect_hard_or_infra_count": 0,
                    "baseline_comparison_available": True,
                    "baseline_regression_count": 0,
                    "baseline_regressed_scenarios": [],
                    "accepted_by_release_thresholds": True,
                },
                "profile_artifacts": _profile_artifacts("claude"),
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
                    "hard_error_count": 0,
                    "failed_critical_scenarios": [],
                    "p50_ms": 3900,
                    "p90_ms": 7600,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 1,
                    "effect_low_score_count": 1,
                    "effect_hard_or_infra_count": 0,
                    "baseline_comparison_available": True,
                    "baseline_regression_count": 0,
                    "baseline_regressed_scenarios": [],
                    "accepted_by_release_thresholds": True,
                },
                "profile_artifacts": _profile_artifacts("gemini"),
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
                    "hard_error_count": 0,
                    "failed_critical_scenarios": [],
                    "p50_ms": 4800,
                    "p90_ms": 8200,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 0,
                    "effect_low_score_count": 0,
                    "effect_hard_or_infra_count": 0,
                    "baseline_comparison_available": True,
                    "baseline_regression_count": 0,
                    "baseline_regressed_scenarios": [],
                    "accepted_by_release_thresholds": True,
                },
                "profile_artifacts": _profile_artifacts("openai"),
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


def _payload_isolation_ready() -> dict:
    return {
        "schema_version": "reply_chain_payload_isolation_audit_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "head_ref": "HEAD",
        "shadow_only_fields": ["reply_chain_shadow_context", "parallel_reply_chain_diagnostics"],
        "payloads_checked": [
            "planner",
            "reply",
            "sop_chat_gate_selector",
            "sop_chat_gate_messages",
        ],
        "leaked_fields_by_payload": {
            "planner": [],
            "reply": [],
            "sop_chat_gate_selector": [],
            "sop_chat_gate_messages": [],
        },
        "payload_isolation_passed": True,
        "active_model_payloads_checked": True,
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


def _model_semantics_ownership_ready() -> dict:
    return {
        "schema_version": "reply_chain_model_semantics_ownership_audit_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "head_ref": "HEAD",
        "ownership_contract_checked": True,
        "tool_planner_must_not_own": ["customer_visible_text", "sales_psychology", "closing_move"],
        "reply_owns": ["final_customer_visible_messages", "complex_turn_outcome", "single_mainline_action"],
        "code_must_not_own": ["normal_sales_intent", "objection_psychology", "sales_rhythm"],
        "tool_planner_legacy_residue_count": 0,
        "tool_planner_only_ready": True,
        "join_final_expression_boundary_schema": "reply_final_expression_boundary_v1",
        "join_final_customer_message_owner": "reply",
        "join_generates_customer_visible_text": False,
        "join_decides_sales_psychology": False,
        "direct_reply_scope": "static_candidate_only_no_dynamic_facts",
        "direct_reply_final_customer_message_owner": "validated_static_gate_candidate",
        "direct_reply_requires_commit_validation": True,
        "reply_handoff_schema": "reply_final_brain_handoff_shadow_v1",
        "reply_handoff_ready": True,
        "legacy_business_field_mapping_schema": "reply_legacy_field_mapping_audit_v1",
        "unmapped_legacy_business_fields": [],
        "parallel_shadow_schema": "parallel_reply_chain_shadow_v1",
        "normalizer_boundary_audit": {
            "schema_version": "planner_normalizer_boundary_audit_v1",
            "normalizer_boundary_passed": True,
            "summary": {
                "semantic_overreach_count": 0,
                "missing_required_count": 0,
            },
            "blockers": [],
        },
        "semantic_ownership_passed": True,
        "blockers": [],
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
            "does_not_call_external_tools": True,
        },
    }


def test_external_gate_evidence_accepts_complete_reports() -> None:
    assert simulation_report_blockers(_simulation_ready()) == []
    assert model_matrix_report_blockers(_model_matrix_ready()) == []
    assert model_semantics_ownership_report_blockers(_model_semantics_ownership_ready()) == []


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


def test_external_gate_evidence_blocks_scenario_level_hard_or_infra_failures() -> None:
    simulation = _simulation_ready()
    simulation["scenario_summary"]["sim_case_0"]["hard_passes"] = 2
    simulation["scenario_summary"]["sim_case_1"]["infrastructure_failures"] = 1

    blockers = simulation_report_blockers(simulation)

    assert "simulation_scenario_hard_passes_below_attempts:sim_case_0:2<3" in blockers
    assert "simulation_scenario_infrastructure_failures:sim_case_1:1" in blockers


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


def test_external_gate_evidence_blocks_targeted_simulation_smoke_as_release_gate() -> None:
    simulation = _simulation_ready()
    simulation["evaluation_scope"] = {
        "schema_version": "offline_simulation_scope_v1",
        "scenario_id": "store_case",
        "category": "",
        "max_cases": 0,
        "targeted_smoke": True,
        "full_release_gate_candidate": False,
    }

    blockers = simulation_report_blockers(simulation)

    assert "simulation_not_full_release_gate_candidate" in blockers


def test_external_gate_evidence_blocks_missing_simulation_scope_or_run_options() -> None:
    simulation = _simulation_ready()
    del simulation["evaluation_scope"]
    del simulation["run_options"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_evaluation_scope" in blockers
    assert "simulation_missing_run_options" in blockers


def test_external_gate_evidence_blocks_simulation_skip_review_and_low_attempts() -> None:
    simulation = _simulation_ready()
    simulation["run_options"]["skip_review"] = True
    simulation["run_options"]["attempts"] = 1
    simulation["run_options"]["critical_attempts"] = 2

    blockers = simulation_report_blockers(simulation)

    assert "simulation_skip_review_not_allowed" in blockers
    assert "simulation_attempts_below_required:1<3" in blockers
    assert "simulation_critical_attempts_below_required:2<5" in blockers


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


def test_external_gate_evidence_accepts_payload_isolation_report() -> None:
    assert payload_isolation_report_blockers(_payload_isolation_ready()) == []


def test_external_gate_evidence_accepts_model_semantics_ownership_report() -> None:
    assert model_semantics_ownership_report_blockers(_model_semantics_ownership_ready()) == []


def test_external_gate_evidence_blocks_model_semantics_ownership_residue() -> None:
    report = _model_semantics_ownership_ready()
    report["tool_planner_legacy_residue_count"] = 1
    report["join_decides_sales_psychology"] = True
    report["semantic_ownership_passed"] = False
    report["blockers"] = ["tool_planner_legacy_residue:1"]
    report["safety"]["does_not_call_models"] = False

    blockers = model_semantics_ownership_report_blockers(report)

    assert "model_semantics_ownership_tool_planner_residue:1" in blockers
    assert "model_semantics_ownership_join_decides_sales_psychology" in blockers
    assert "model_semantics_ownership_report_blocker:tool_planner_legacy_residue:1" in blockers
    assert "model_semantics_ownership_not_passed" in blockers
    assert "model_semantics_ownership_missing_no_model_call_safety" in blockers


def test_external_gate_evidence_blocks_model_semantics_missing_normalizer_boundary() -> None:
    report = _model_semantics_ownership_ready()
    report.pop("normalizer_boundary_audit")

    blockers = model_semantics_ownership_report_blockers(report)

    assert "model_semantics_ownership_missing_normalizer_boundary_audit" in blockers


def test_external_gate_evidence_blocks_model_semantics_normalizer_overreach() -> None:
    report = _model_semantics_ownership_ready()
    report["normalizer_boundary_audit"]["normalizer_boundary_passed"] = False
    report["normalizer_boundary_audit"]["summary"]["semantic_overreach_count"] = 1
    report["normalizer_boundary_audit"]["blockers"] = [
        "semantic_overreach_marker:order_required_before_payment_card:入口还没对上成功"
    ]

    blockers = model_semantics_ownership_report_blockers(report)

    assert "model_semantics_ownership_normalizer_boundary_not_passed" in blockers
    assert "model_semantics_ownership_normalizer_semantic_overreach:1" in blockers
    assert (
        "model_semantics_ownership_normalizer_boundary:"
        "semantic_overreach_marker:order_required_before_payment_card:入口还没对上成功"
    ) in blockers


def test_external_gate_evidence_blocks_payload_isolation_leaks() -> None:
    report = _payload_isolation_ready()
    report["leaked_fields_by_payload"]["planner"] = ["parallel_reply_chain_diagnostics"]
    report["payload_isolation_passed"] = False

    blockers = payload_isolation_report_blockers(report)

    assert "payload_isolation_leaked_field:planner:parallel_reply_chain_diagnostics" in blockers
    assert "payload_isolation_not_passed" in blockers


def test_external_gate_evidence_blocks_simulation_without_semantic_ownership_evidence() -> None:
    report = _simulation_ready()
    report["summary"]["acceptance"]["semantic_ownership_passed"] = False
    report["semantic_ownership_audit"]["passed"] = False
    report["semantic_ownership_audit"]["evidence_result_count"] = 299
    report["semantic_ownership_audit"]["missing_evidence_count"] = 1
    report["semantic_ownership_audit"]["violation_count"] = 1
    report["semantic_ownership_audit"]["violations"] = [
        {"scenario_id": "sim_case_1", "attempt": 1, "violation": "join_decides_sales_psychology"}
    ]

    blockers = simulation_report_blockers(report)

    assert "simulation_semantic_ownership_acceptance_missing_or_false" in blockers
    assert "simulation_semantic_ownership_evidence_below_attempt_count:299<300" in blockers
    assert "simulation_semantic_ownership_missing_evidence:1" in blockers
    assert "simulation_semantic_ownership_violations:1" in blockers
    assert "simulation_semantic_ownership_not_passed" in blockers


def test_external_gate_evidence_blocks_payload_isolation_missing_payloads_or_safety() -> None:
    report = _payload_isolation_ready()
    report["payloads_checked"] = ["planner"]
    report["safety"]["does_not_call_models"] = False
    del report["git_commit_set"]

    blockers = payload_isolation_report_blockers(report)

    assert "payload_isolation_missing_git_commit_set" in blockers
    assert "payload_isolation_missing_payload_check:reply" in blockers
    assert "payload_isolation_missing_payload_check:sop_chat_gate_selector" in blockers
    assert "payload_isolation_missing_payload_check:sop_chat_gate_messages" in blockers
    assert "payload_isolation_missing_no_model_call_safety" in blockers


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
        "required_categories": list(REQUIRED_SIMULATION_COVERAGE_CATEGORIES),
        "missing_required_categories": ["健康风险"],
    }
    simulation["summary"]["acceptance"]["scenario_coverage_complete"] = False

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_required_category:健康风险" in blockers
    assert "simulation_scenario_coverage_incomplete" in blockers


def test_external_gate_evidence_blocks_missing_simulation_baseline_comparison() -> None:
    simulation = _simulation_ready()
    del simulation["baseline_comparison"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_baseline_comparison" in blockers


def test_external_gate_evidence_blocks_unavailable_simulation_baseline_comparison() -> None:
    simulation = _simulation_ready()
    simulation["baseline_comparison"] = {
        "schema_version": "offline_simulation_baseline_comparison_v1",
        "available": False,
    }

    blockers = simulation_report_blockers(simulation)

    assert "simulation_baseline_comparison_unavailable" in blockers


def test_external_gate_evidence_blocks_missing_simulation_baseline_acceptance() -> None:
    simulation = _simulation_ready()
    simulation["summary"]["acceptance"]["baseline_comparison_passed"] = False

    blockers = simulation_report_blockers(simulation)

    assert "simulation_baseline_acceptance_missing_or_false" in blockers


def test_external_gate_evidence_blocks_simulation_baseline_regressions() -> None:
    simulation = _simulation_ready()
    simulation["baseline_comparison"]["regressed"] = ["store_v2_county_confirm"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_baseline_regressed:store_v2_county_confirm" in blockers


def test_external_gate_evidence_blocks_simulation_coverage_manifest_drift() -> None:
    simulation = _simulation_ready()
    simulation["coverage"]["required_categories"] = [
        item for item in REQUIRED_SIMULATION_COVERAGE_CATEGORIES if item != "明确拒绝"
    ] + ["自定义未审批分类"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_required_category_manifest_missing:明确拒绝" in blockers
    assert "simulation_required_category_manifest_unapproved:自定义未审批分类" in blockers


def test_external_gate_evidence_blocks_missing_simulation_coverage_manifest() -> None:
    simulation = _simulation_ready()
    del simulation["coverage"]["required_categories"]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_missing_required_category_manifest" in blockers


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


def test_external_gate_evidence_blocks_targeted_model_matrix_smoke_as_release_gate() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["evaluation_scope"] = {
        "schema_version": "reply_chain_refactor_model_matrix_scope_v1",
        "scenario_id": "store_v2_county_confirm",
        "category": "",
        "max_cases": 0,
        "targeted_smoke": True,
        "full_release_gate_candidate": False,
    }

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_not_full_release_gate_candidate" in blockers


def test_external_gate_evidence_blocks_missing_model_matrix_scope() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["evaluation_scope"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_evaluation_scope" in blockers


def test_external_gate_evidence_blocks_missing_model_matrix_run_options() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["run_options"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_run_options" in blockers


def test_external_gate_evidence_blocks_model_matrix_missing_baseline_path() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["run_options"]["baseline_path_present"] = False

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_baseline_path" in blockers


def test_external_gate_evidence_blocks_model_matrix_skip_review() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["run_options"]["skip_review"] = True

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_skip_review_not_allowed" in blockers


def test_external_gate_evidence_blocks_model_matrix_attempts_below_required() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["run_options"]["attempts"] = 1
    model_matrix["run_options"]["critical_attempts"] = 1

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_attempts_below_required:1<3" in blockers
    assert "model_matrix_critical_attempts_below_required:1<5" in blockers


def test_external_gate_evidence_blocks_tiny_model_matrix_profile_artifacts() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][2]["profile_artifacts"] = _profile_artifacts("openai", attempt_count=3)
    model_matrix["profiles"][2]["profile_artifacts"]["scenario_count"] = 3

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_profile_artifact_scenario_count_below_100:openai:3" in blockers


def test_external_gate_evidence_blocks_model_matrix_attempts_below_profile_scenarios() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][2]["profile_artifacts"] = _profile_artifacts("openai", attempt_count=99)

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_profile_artifact_attempt_count_below_scenario_count:openai:99<100" in blockers
    assert "model_matrix_profile_effect_review_below_attempt_count:openai:99<99" not in blockers


def test_external_gate_evidence_blocks_missing_core_simulation_acceptance_fields() -> None:
    simulation = _simulation_ready()
    simulation["summary"]["acceptance"] = {
        "infrastructure_failures_zero": True,
        "scenario_coverage_complete": True,
    }

    blockers = simulation_report_blockers(simulation)

    assert "simulation_hard_error_acceptance_missing_or_false" in blockers
    assert "simulation_semantic_review_incomplete" in blockers
    assert "simulation_semantic_acceptance_missing_or_false" in blockers
    assert "simulation_critical_acceptance_missing_or_false" in blockers
    assert "simulation_baseline_acceptance_missing_or_false" in blockers


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


def test_external_gate_evidence_blocks_incomplete_simulation_review_artifact_rows() -> None:
    simulation = _simulation_ready()
    simulation["review_artifacts"]["results"] = [
        {
            "scenario_id": "sim_case",
            "attempt": 1,
            "request_ids": "sim_request_1",
            "node_trace_names": [],
            "sync_reply_message_count": "",
        },
        "not-a-dict",
    ]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_review_artifacts_result_field_not_list:sim_case:request_ids" in blockers
    assert "simulation_review_artifacts_result_missing_field:sim_case:event_ids" in blockers
    assert "simulation_review_artifacts_result_missing_field:sim_case:tool_call_names" in blockers
    assert "simulation_review_artifacts_result_field_not_number:sim_case:sync_reply_message_count" in blockers
    assert "simulation_review_artifacts_invalid_result:1" in blockers


def test_external_gate_evidence_blocks_review_artifact_results_below_attempt_count() -> None:
    simulation = _simulation_ready()
    simulation["review_artifacts"]["results"] = _review_artifact_results(299)

    blockers = simulation_report_blockers(simulation)

    assert "simulation_review_artifacts_results_length_below_attempt_count:299<300" in blockers


def test_external_gate_evidence_blocks_incomplete_effect_review_items() -> None:
    simulation = _simulation_ready()
    simulation["semantic_pass_rate"] = 0.89
    simulation["effect_review"]["low_score_count"] = 1
    simulation["effect_review"]["items"] = [
        {
            "scenario_id": "low_score_case",
            "attempt": 1,
            "issue_types": ["semantic_low_score"],
            "customer_input_excerpt": "做一次能好吗",
        },
        "not-a-dict",
    ]

    blockers = simulation_report_blockers(simulation)

    assert "simulation_effect_review_item_missing_field:low_score_case:assistant_reply_excerpt" in blockers
    assert "simulation_effect_review_item_missing_field:low_score_case:review_reasons" in blockers
    assert "simulation_effect_review_item_missing_scores:low_score_case" in blockers
    assert "simulation_effect_review_invalid_item:1" in blockers


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


def test_external_gate_evidence_blocks_missing_model_matrix_hard_error_counts() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["profiles"][1]["profile_summary"]["hard_error_count"]
    del model_matrix["profiles"][1]["profile_summary"]["failed_critical_scenarios"]
    del model_matrix["ranking"][1]["hard_error_count"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_hard_error_count:gemini" in blockers
    assert "model_matrix_missing_failed_critical_scenarios:gemini" in blockers
    assert "model_matrix_ranking_missing_hard_error_count:claude" in blockers


def test_external_gate_evidence_blocks_accepted_model_with_hard_or_critical_failures() -> None:
    model_matrix = _model_matrix_ready()
    summary = model_matrix["profiles"][0]["profile_summary"]
    summary["hard_error_count"] = 1
    summary["failed_critical_scenarios"] = ["store_scope_visible_only"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_accepted_profile_has_hard_errors:claude:1" in blockers
    assert "model_matrix_accepted_profile_failed_critical:claude:store_scope_visible_only" in blockers


def test_external_gate_evidence_blocks_accepted_model_below_semantic_threshold() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][1]["profile_summary"]["semantic_pass_rate"] = 0.89

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_accepted_profile_semantic_below_90:gemini:0.890" in blockers


def test_external_gate_evidence_blocks_model_matrix_missing_effect_review_counts() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["profiles"][1]["profile_summary"]["effect_issue_count"]
    del model_matrix["profiles"][1]["profile_summary"]["effect_low_score_count"]
    del model_matrix["profiles"][1]["profile_summary"]["effect_hard_or_infra_count"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_missing_effect_issue_count:gemini" in blockers
    assert "model_matrix_missing_effect_low_score_count:gemini" in blockers
    assert "model_matrix_missing_effect_hard_or_infra_count:gemini" in blockers


def test_external_gate_evidence_blocks_model_matrix_missing_profile_baseline() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["profiles"][1]["profile_summary"]["baseline_comparison_available"]
    del model_matrix["profiles"][1]["profile_summary"]["baseline_regression_count"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_profile_missing_baseline_comparison:gemini" in blockers
    assert "model_matrix_missing_baseline_regression_count:gemini" in blockers


def test_external_gate_evidence_blocks_accepted_model_matrix_baseline_regressions() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][0]["profile_summary"]["baseline_regression_count"] = 2
    model_matrix["profiles"][0]["profile_summary"]["baseline_regressed_scenarios"] = [
        "soft_refusal",
        "health_risk",
    ]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_accepted_profile_has_baseline_regressions:claude:2" in blockers


def test_external_gate_evidence_blocks_model_matrix_missing_profile_artifacts() -> None:
    model_matrix = _model_matrix_ready()
    del model_matrix["profiles"][0]["profile_artifacts"]

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_profile_missing_artifacts:claude" in blockers


def test_external_gate_evidence_blocks_model_matrix_incomplete_profile_artifacts() -> None:
    model_matrix = _model_matrix_ready()
    artifacts = model_matrix["profiles"][2]["profile_artifacts"]
    artifacts["result_json_written"] = False
    artifacts["effect_review_result_count"] = 299
    artifacts["review_artifacts_result_count"] = 298

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_profile_result_json_not_written:openai" in blockers
    assert "model_matrix_profile_effect_review_below_attempt_count:openai:299<300" in blockers
    assert "model_matrix_profile_review_artifacts_below_attempt_count:openai:298<300" in blockers


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


def test_external_gate_evidence_blocks_secret_like_model_matrix_values() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][0]["debug_payload"] = {
        "api_key": _secret_like_value(),
        "safe_task_id": "platform-task-after-cycle",
    }

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_contains_secret_like_value" in blockers


def test_external_gate_evidence_blocks_secret_like_values_in_all_release_reports() -> None:
    reports = [
        ("simulation", deepcopy(_simulation_ready()), simulation_report_blockers),
        ("payload_isolation", deepcopy(_payload_isolation_ready()), payload_isolation_report_blockers),
        ("business_wording_freeze", deepcopy(_business_wording_freeze_ready()), business_wording_freeze_report_blockers),
        ("rollback_evidence", deepcopy(_rollback_evidence_ready()), rollback_evidence_report_blockers),
        ("model_semantics_ownership", deepcopy(_model_semantics_ownership_ready()), model_semantics_ownership_report_blockers),
    ]

    for label, report, blocker_fn in reports:
        report["debug_payload"] = {
            "api_key": _secret_like_value(),
            "safe_task_id": "platform-task-after-cycle",
        }

        assert f"{label}_contains_secret_like_value" in blocker_fn(report)


def test_external_gate_evidence_does_not_treat_task_ids_as_secret_values() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"][0]["debug_payload"] = {
        "safe_task_id": "platform-task-after-cycle",
    }

    blockers = model_matrix_report_blockers(model_matrix)

    assert "model_matrix_contains_secret_like_value" not in blockers


def test_bundle_audit_does_not_import_final_behavior_switch_guard_private_helpers() -> None:
    source = (ROOT / "ai_paths/app/services/reply_chain_shadow_bundle_audit.py").read_text(encoding="utf-8")

    assert "from app.services.reply_chain_behavior_switch_guard import" not in source
    assert "_simulation_blockers" not in source
    assert "_model_matrix_blockers" not in source
