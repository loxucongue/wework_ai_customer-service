from __future__ import annotations

import json

from app.config import Settings
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.reply_chain_behavior_switch_guard import reply_chain_behavior_switch_guard
from app.services.reply_chain_refactor_flags import reply_chain_refactor_flag_snapshot


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


def _active_flag_snapshot() -> dict:
    return reply_chain_refactor_flag_snapshot(
        Settings(
            _env_file=None,
            PARALLEL_GATE_PLANNER_ENABLED=True,
            PARALLEL_GATE_PLANNER_SHADOW=False,
            SOP_CHAT_GATE_V2_ENABLED=True,
            TOOL_PLANNER_V2_ENABLED=True,
            REPLY_FINAL_BRAIN_V2_ENABLED=True,
        )
    )


def _shadow_bundle_ready() -> dict:
    return {
        "schema_version": "reply_chain_shadow_bundle_audit_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "phase": "postcommit",
        "ready_for_refactor_review": True,
        "blockers": [],
        "components": {
            "reply_chain_commit_shadow": {
                "required_schema_version": "reply_chain_commit_shadow_v1",
                "observed_schema_version": "reply_chain_commit_shadow_v1",
                "present": True,
                "valid": True,
            }
        },
        "review_gates": {
            "commit_phase_ready": {
                "passed": True,
                "purpose": "writes_remain_after_reply_validation",
            }
        },
        "safety": {
            "does_not_approve_behavior_switch": True,
        },
    }


def _diagnostics_ready() -> dict:
    return {
        "schema_version": "parallel_reply_chain_diagnostics_v1",
        "git_commit": "abc123",
        "git_commit_set": ["abc123"],
        "phase": "ready_for_human_review",
        "release_review": {
            "schema_version": "reply_chain_release_review_checklist_v1",
            "can_enable_behavior_switch": False,
            "missing_or_unproven_gates": [],
            "blocker_groups": {
                "manual_review": {
                    "ready": True,
                    "blocker_count": 0,
                }
            },
        },
    }


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
                    "p50_ms": 6200,
                    "p90_ms": 11000,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 0,
                    "effect_low_score_count": 0,
                    "effect_hard_or_infra_count": 0,
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
                    "p50_ms": 3900,
                    "p90_ms": 7600,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 1,
                    "effect_low_score_count": 1,
                    "effect_hard_or_infra_count": 0,
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
                    "p50_ms": 4800,
                    "p90_ms": 8200,
                    "infrastructure_failures": 0,
                    "effect_issue_count": 0,
                    "effect_low_score_count": 0,
                    "effect_hard_or_infra_count": 0,
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


def _human_review_approved() -> dict:
    return {
        "schema_version": "reply_chain_human_review_approval_v1",
        "approved": True,
        "branch": "codex/reply-chain-refactor",
        "commit_sha": "abc123",
        "scope": "parallel_gate_planner_behavior_switch",
        "rollback_plan": {
            "schema_version": "reply_chain_behavior_switch_rollback_plan_v1",
            "reviewed": True,
            "restore_flags_to_shadow_or_disabled": True,
            "no_deployment_from_refactor_branch": True,
            "rollback_steps": [
                "disable PARALLEL_GATE_PLANNER_ENABLED",
                "disable SOP_CHAT_GATE_V2_ENABLED",
                "disable TOOL_PLANNER_V2_ENABLED",
                "disable REPLY_FINAL_BRAIN_V2_ENABLED",
            ],
            "owner": "reviewer",
        },
    }


def test_behavior_switch_guard_blocks_default_shadow_mode() -> None:
    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=reply_chain_refactor_flag_snapshot(Settings(_env_file=None)),
    )

    assert guard["schema_version"] == "reply_chain_behavior_switch_guard_v1"
    assert guard["behavior_switch_requested"] is False
    assert guard["can_enable_behavior_switch"] is False
    assert "behavior_switch_not_requested" in guard["blockers"]
    assert "flag_snapshot:parallel_runner_disabled" in guard["blockers"]
    assert "required_active_flag_missing:parallel_gate_planner_enabled" in guard["blockers"]
    assert "missing_reply_chain_shadow_bundle_audit" in guard["blockers"]
    assert guard["safety"]["does_not_enable_flags"] is True


def test_behavior_switch_guard_blocks_without_simulation_and_human_review() -> None:
    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "missing_offline_simulation_report" in guard["blockers"]
    assert "missing_model_matrix_report" in guard["blockers"]
    assert "missing_human_review_approval" in guard["blockers"]


def test_behavior_switch_guard_blocks_human_review_without_rollback_plan() -> None:
    review = _human_review_approved()
    del review["rollback_plan"]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=review,
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "missing_behavior_switch_rollback_plan" in guard["blockers"]


def test_behavior_switch_guard_blocks_unreviewed_rollback_plan() -> None:
    review = _human_review_approved()
    review["rollback_plan"]["reviewed"] = False
    review["rollback_plan"]["rollback_steps"] = []
    review["rollback_plan"]["no_deployment_from_refactor_branch"] = False

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=review,
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "rollback_plan_not_reviewed" in guard["blockers"]
    assert "rollback_plan_missing_steps" in guard["blockers"]
    assert "rollback_plan_missing_no_refactor_deploy" in guard["blockers"]


def test_behavior_switch_guard_blocks_human_review_for_different_commit() -> None:
    shadow = _shadow_bundle_ready()
    diagnostics = _diagnostics_ready()
    simulation = _simulation_ready()
    model_matrix = _model_matrix_ready()
    shadow["git_commit"] = "shadow-old"
    diagnostics["git_commit"] = "diag-old"
    simulation["git_commit"] = "sim-old"
    model_matrix["git_commit"] = "matrix-old"

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=shadow,
        diagnostics=diagnostics,
        simulation_report=simulation,
        model_matrix_report=model_matrix,
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "human_review_commit_mismatch:shadow_bundle:shadow-old" in guard["blockers"]
    assert "human_review_commit_mismatch:diagnostics:diag-old" in guard["blockers"]
    assert "human_review_commit_mismatch:simulation:sim-old" in guard["blockers"]
    assert "human_review_commit_mismatch:model_matrix:matrix-old" in guard["blockers"]


def test_behavior_switch_guard_blocks_human_review_without_shadow_commit_evidence() -> None:
    shadow = _shadow_bundle_ready()
    diagnostics = _diagnostics_ready()
    shadow.pop("git_commit")
    shadow.pop("git_commit_set")
    diagnostics.pop("git_commit")
    diagnostics.pop("git_commit_set")

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=shadow,
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "human_review_missing_commit_evidence:shadow_bundle" in guard["blockers"]
    assert "human_review_missing_commit_set_evidence:shadow_bundle" in guard["blockers"]
    assert "human_review_missing_commit_evidence:diagnostics" in guard["blockers"]
    assert "human_review_missing_commit_set_evidence:diagnostics" in guard["blockers"]


def test_behavior_switch_guard_blocks_human_review_with_multi_commit_shadow_evidence() -> None:
    shadow = _shadow_bundle_ready()
    diagnostics = _diagnostics_ready()
    shadow["git_commit_set"] = ["abc123", "def456"]
    diagnostics["git_commit_set"] = ["abc123", "ghi789"]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=shadow,
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "human_review_commit_set_mismatch:shadow_bundle:abc123,def456" in guard["blockers"]
    assert "human_review_commit_set_mismatch:diagnostics:abc123,ghi789" in guard["blockers"]


def test_behavior_switch_guard_blocks_shadow_bundle_without_commit_phase_evidence() -> None:
    shadow = _shadow_bundle_ready()
    shadow["components"] = {}
    shadow["review_gates"] = {}

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=shadow,
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "shadow_bundle_commit_component_not_valid" in guard["blockers"]
    assert "shadow_bundle_commit_phase_gate_not_passed" in guard["blockers"]


def test_behavior_switch_guard_blocks_unproven_release_review_gates() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = [
        "simulation_regression_review",
        "business_wording_freeze_review",
    ]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_gate_unproven:simulation_regression_review" not in guard["blockers"]
    assert "release_review_gate_unproven:business_wording_freeze_review" in guard["blockers"]
    assert guard["diagnostic_blocker_groups"]["manual_review"]["ready"] is True


def test_behavior_switch_guard_filters_business_wording_freeze_report_gate() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["business_wording_freeze_review"]
    diagnostics["release_review"]["blocker_groups"] = {
        "manual_review": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:business_wording_freeze_review"],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        business_wording_freeze_report=_business_wording_freeze_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard


def test_behavior_switch_guard_blocks_invalid_business_wording_freeze_report() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["business_wording_freeze_review"]
    report = _business_wording_freeze_ready()
    report["changed_protected_paths"] = ["config/sop_reply_packs.json"]
    report["customer_visible_business_assets_unchanged"] = False

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        business_wording_freeze_report=report,
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "business_wording_freeze_protected_path_changed:config/sop_reply_packs.json" in guard["blockers"]
    assert "business_wording_freeze_assets_not_unchanged" in guard["blockers"]


def test_behavior_switch_guard_filters_payload_isolation_report_gate() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["payload_isolation_review"]
    diagnostics["release_review"]["blocker_groups"] = {
        "manual_review": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:payload_isolation_review"],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        payload_isolation_report=_payload_isolation_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard


def test_behavior_switch_guard_blocks_invalid_payload_isolation_report() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["payload_isolation_review"]
    report = _payload_isolation_ready()
    report["leaked_fields_by_payload"]["reply"] = ["reply_chain_shadow_context"]
    report["payload_isolation_passed"] = False

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        payload_isolation_report=report,
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "payload_isolation_leaked_field:reply:reply_chain_shadow_context" in guard["blockers"]
    assert "payload_isolation_not_passed" in guard["blockers"]


def test_behavior_switch_guard_filters_model_semantics_ownership_report_gate() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["model_semantics_ownership_review"]
    diagnostics["release_review"]["blocker_groups"] = {
        "manual_review": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:model_semantics_ownership_review"],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        model_semantics_ownership_report=_model_semantics_ownership_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard


def test_behavior_switch_guard_blocks_invalid_model_semantics_ownership_report() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["model_semantics_ownership_review"]
    report = _model_semantics_ownership_ready()
    report["join_decides_sales_psychology"] = True
    report["semantic_ownership_passed"] = False

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        model_semantics_ownership_report=report,
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "model_semantics_ownership_join_decides_sales_psychology" in guard["blockers"]
    assert "model_semantics_ownership_not_passed" in guard["blockers"]


def test_behavior_switch_guard_filters_rollback_evidence_report_gate() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["rollback_evidence_review"]
    diagnostics["release_review"]["blocker_groups"] = {
        "manual_review": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:rollback_evidence_review"],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        rollback_evidence_report=_rollback_evidence_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard


def test_behavior_switch_guard_blocks_invalid_rollback_evidence_report() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["rollback_evidence_review"]
    report = _rollback_evidence_ready()
    report["branch"] = "main"
    report["branch_is_refactor"] = False
    report["changed_deployment_sensitive_paths"] = [".github/workflows/deploy.yml"]
    report["deployment_sensitive_paths_unchanged"] = False

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        rollback_evidence_report=report,
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "rollback_evidence_wrong_branch:main" in guard["blockers"]
    assert "rollback_evidence_deployment_sensitive_path_changed:.github/workflows/deploy.yml" in guard["blockers"]


def test_behavior_switch_guard_filters_externally_proven_simulation_and_model_matrix_gates() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = [
        "simulation_regression_review",
        "model_matrix_review",
    ]
    diagnostics["release_review"]["blocker_groups"] = {
        "manual_review": {
            "ready": False,
            "blocker_count": 2,
            "blockers": [
                "gate_not_proven:simulation_regression_review",
                "gate_not_proven:model_matrix_review",
            ],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard


def test_behavior_switch_guard_exposes_release_review_blocker_groups_for_review() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["reply_target_input_schema_review"]
    diagnostics["release_review"]["blocker_groups"] = {
        "reply_payload_schema": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:reply_target_input_schema_review"],
        },
        "manual_review": {
            "ready": True,
            "blocker_count": 0,
        },
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_gate_unproven:reply_target_input_schema_review" in guard["blockers"]
    assert guard["diagnostic_blocker_groups"]["reply_payload_schema"]["ready"] is False
    assert guard["diagnostic_blocker_groups"]["reply_payload_schema"]["blockers"] == [
        "gate_not_proven:reply_target_input_schema_review"
    ]


def test_behavior_switch_guard_blocks_unresolved_release_review_groups_even_without_flat_gates() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = []
    diagnostics["release_review"]["blocker_groups"] = {
        "reply_payload_schema": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:reply_target_input_schema_review"],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_blocker_group_unresolved:reply_payload_schema" in guard["blockers"]
    assert (
        "release_review_blocker_group:reply_payload_schema:gate_not_proven:reply_target_input_schema_review"
        in guard["blockers"]
    )


def test_behavior_switch_guard_blocks_release_review_that_claims_switch_approval() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["can_enable_behavior_switch"] = True

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_missing_non_approval_marker" in guard["blockers"]


def test_behavior_switch_guard_allows_only_with_complete_evidence() -> None:
    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["behavior_switch_requested"] is True
    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard
    assert guard["diagnostic_blocker_groups"]["manual_review"]["ready"] is True
    assert guard["required_evidence"]["simulation_report"].startswith("offline full-chain")
    assert guard["required_evidence"]["model_matrix_report"].startswith("three-model")


def test_behavior_switch_guard_blocks_invalid_model_matrix_report() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"] = [
        item
        for item in model_matrix["profiles"]
        if item["model_profile"]["name"] != "gemini"
    ]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=model_matrix,
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "model_matrix_profile_not_completed:gemini" in guard["blockers"]


def test_behavior_switch_guard_blocks_simulation_without_isolation_safety() -> None:
    simulation = _simulation_ready()
    simulation["safety"] = {
        "production_customer_messages_sent": True,
        "production_writes_allowed": True,
        "virtual_outbox_only": False,
        "production_write_count": 2,
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=simulation,
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "simulation_missing_no_customer_send_safety" in guard["blockers"]
    assert "simulation_missing_no_production_write_safety" in guard["blockers"]
    assert "simulation_missing_virtual_outbox_safety" in guard["blockers"]
    assert "simulation_production_writes:2" in guard["blockers"]


def test_behavior_switch_guard_blocks_simulation_without_required_category_coverage() -> None:
    simulation = _simulation_ready()
    simulation["summary"]["acceptance"]["scenario_coverage_complete"] = False
    simulation["coverage"]["missing_required_categories"] = ["精准问答", "预约金"]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=simulation,
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "simulation_missing_required_category:精准问答" in guard["blockers"]
    assert "simulation_missing_required_category:预约金" in guard["blockers"]
    assert "simulation_scenario_coverage_incomplete" in guard["blockers"]


def test_behavior_switch_guard_blocks_simulation_without_complete_semantic_review() -> None:
    simulation = _simulation_ready()
    simulation["summary"]["evaluable_attempts"] = 0
    simulation["summary"]["acceptance"]["semantic_review_complete"] = False

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=simulation,
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "simulation_evaluable_attempts_below_attempt_count:0<300" in guard["blockers"]
    assert "simulation_semantic_review_incomplete" in guard["blockers"]


def test_behavior_switch_guard_is_not_consumed_by_current_model_payloads() -> None:
    state = {
        "normalized_content": "how to book",
        "conversation_history": ["user: how to book"],
        "reply_chain_behavior_switch_guard": {
            "schema_version": "reply_chain_behavior_switch_guard_v1",
            "source": "behavior-switch-guard-marker",
        },
        "request_context": {},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    assert "reply_chain_behavior_switch_guard" not in planner_payload
    assert "reply_chain_behavior_switch_guard" not in reply_payload
    assert "behavior-switch-guard-marker" not in combined
