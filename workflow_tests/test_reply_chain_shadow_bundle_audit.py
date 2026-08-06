from __future__ import annotations

from app.services.reply_chain_shadow_bundle_audit import reply_chain_shadow_bundle_audit


def _ready_state() -> dict:
    return {
        "git_commit": "abc123",
        "reply_chain_shadow_context": {"schema_version": "reply_chain_shadow_v1"},
        "sop_gate_router_shadow": {"schema_version": "chat_gate_router_shadow_v1"},
        "tool_plan_preview": {
            "schema_version": "tool_plan_preview_v2",
            "migration_audit": {
                "schema_version": "tool_planner_migration_audit_v1",
                "legacy_residue_count": 0,
                "tool_planner_only_ready": True,
                "review_required_before_migration": False,
            },
        },
        "read_only_tool_executor_shadow": {"schema_version": "read_only_tool_executor_shadow_v1"},
        "reply_chain_join_shadow": {"schema_version": "reply_chain_join_shadow_v1"},
        "reply_final_brain_handoff_shadow": {
            "schema_version": "reply_final_brain_handoff_shadow_v1",
            "migration_audit": {
                "legacy_business_field_count": 0,
                "field_mapping_audit": {
                    "schema_version": "reply_legacy_field_mapping_audit_v1",
                    "legacy_business_field_count": 0,
                    "mapped_legacy_business_field_count": 0,
                    "unmapped_legacy_business_fields": [],
                    "all_legacy_business_fields_mapped": True,
                },
            },
        },
        "parallel_reply_chain_shadow": {
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
            "current_serial_observation": {
                "shared_context_authority_audit_schema": "reply_chain_authority_audit_v1",
                "shared_context_timeline_window_ready": True,
                "shared_context_current_message_ready": True,
                "shared_context_fact_snapshot_schema": "reply_chain_fact_snapshot_audit_v1",
                "gate_commit_boundary_schema": "chat_gate_commit_boundary_v1",
                "gate_shadow_creates_sop_task": False,
                "gate_shadow_updates_send_once": False,
                "gate_shadow_sends_customer_messages": False,
                "gate_shadow_writes_database": False,
                "tool_planner_only_ready": True,
                "direct_reply_allowed": False,
                "direct_reply_guard_schema": "reply_chain_direct_reply_guard_audit_v1",
                "direct_reply_guard_requested": False,
                "direct_reply_guard_ready": False,
                "join_final_expression_boundary_schema": "reply_final_expression_boundary_v1",
                "join_final_customer_message_owner": "reply",
                "join_generates_customer_visible_text": False,
                "join_decides_sales_psychology": False,
                "reply_handoff_ready_for_payload_switch_shadow": True,
            },
        },
        "parallel_gate_planner_runner_shadow": {
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "mode": "completed_shadow",
            "branch_output_contract_audit": {
                "schema_version": "parallel_branch_output_contract_audit_v1",
                "ready": True,
                "blockers": [],
            },
        },
        "parallel_reply_chain_comparison": {
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        "parallel_reply_chain_diagnostics": {
            "schema_version": "parallel_reply_chain_diagnostics_v1",
            "git_commit": "abc123",
            "git_commit_set": ["abc123"],
            "phase": "ready_for_human_review",
        },
        "reply_chain_commit_shadow": {
            "schema_version": "reply_chain_commit_shadow_v1",
            "commit_phase_owner": "runtime_after_reply_validation",
            "requires_reply_validation_before_commit": True,
            "precommit_validation_audit": {
                "schema_version": "reply_chain_precommit_validation_audit_v1",
                "ready_for_commit_shadow": True,
            },
            "deferred_write_handoff_audit": {
                "schema_version": "reply_chain_deferred_write_handoff_audit_v1",
                "commit_phase_owner": "runtime_after_reply_validation",
                "early_execution_forbidden": True,
                "current_runtime_executes_deferred_writes": False,
                "requires_reply_validation_before_write": True,
                "ready_for_deferred_write_refactor_review": True,
                "blockers": [],
            },
            "write_action_inventory": {
                "schema_version": "reply_chain_write_action_inventory_v1",
                "commit_phase_owner": "runtime_after_reply_validation",
                "requires_reply_validation_before_write": True,
                "all_runtime_writes_after_reply_validation": True,
                "ready_for_commit_refactor_review": True,
                "actions": [
                    {
                        "id": "conversation_assistant_message",
                        "owner": "runtime_after_reply_validation",
                        "execution_phase": "after_reply_validation",
                    }
                ],
                "blockers": [],
            },
        },
    }


def test_bundle_audit_reports_git_commit_when_evidence_matches() -> None:
    audit = reply_chain_shadow_bundle_audit(
        state=_ready_state(),
        require_commit_shadow=True,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        business_wording_freeze_report=_business_wording_freeze_ready(),
    )

    assert audit["git_commit"] == "abc123"
    assert audit["git_commit_set"] == ["abc123"]


def test_bundle_audit_reports_git_commit_set_when_evidence_differs() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["git_commit"] = "def456"
    state["parallel_reply_chain_diagnostics"]["git_commit_set"] = ["def456"]

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        business_wording_freeze_report=_business_wording_freeze_ready(),
    )

    assert "git_commit" not in audit
    assert audit["git_commit_set"] == ["abc123", "def456"]


def test_bundle_audit_accepts_business_wording_freeze_external_gate() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["business_wording_freeze_review"],
        "blocker_groups": {
            "manual_review": {
                "ready": False,
                "blocker_count": 1,
                "blockers": ["gate_not_proven:business_wording_freeze_review"],
            }
        },
    }

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        business_wording_freeze_report=_business_wording_freeze_ready(),
    )

    assert audit["ready_for_refactor_review"] is True
    assert "business_wording_freeze_review" in audit["external_gate_evidence"]["proven_gates"]


def test_bundle_audit_accepts_payload_isolation_external_gate() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["payload_isolation_review"],
        "blocker_groups": {
            "manual_review": {
                "ready": False,
                "blocker_count": 1,
                "blockers": ["gate_not_proven:payload_isolation_review"],
            }
        },
    }

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        payload_isolation_report=_payload_isolation_ready(),
    )

    assert audit["ready_for_refactor_review"] is True
    assert "payload_isolation_review" in audit["external_gate_evidence"]["proven_gates"]


def test_bundle_audit_accepts_rollback_evidence_external_gate() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["rollback_evidence_review"],
        "blocker_groups": {
            "manual_review": {
                "ready": False,
                "blocker_count": 1,
                "blockers": ["gate_not_proven:rollback_evidence_review"],
            }
        },
    }

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        rollback_evidence_report=_rollback_evidence_ready(),
    )

    assert audit["ready_for_refactor_review"] is True
    assert "rollback_evidence_review" in audit["external_gate_evidence"]["proven_gates"]


def test_bundle_audit_accepts_model_semantics_ownership_external_gate() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["model_semantics_ownership_review"],
        "blocker_groups": {
            "manual_review": {
                "ready": False,
                "blocker_count": 1,
                "blockers": ["gate_not_proven:model_semantics_ownership_review"],
            }
        },
    }

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        model_semantics_ownership_report=_model_semantics_ownership_ready(),
    )

    assert audit["ready_for_refactor_review"] is True
    assert "model_semantics_ownership_review" in audit["external_gate_evidence"]["proven_gates"]


def test_bundle_audit_blocks_invalid_model_semantics_ownership_report() -> None:
    report = _model_semantics_ownership_ready()
    report["semantic_ownership_passed"] = False
    report["join_generates_customer_visible_text"] = True

    audit = reply_chain_shadow_bundle_audit(
        state=_ready_state(),
        require_commit_shadow=True,
        model_semantics_ownership_report=report,
    )

    assert audit["ready_for_refactor_review"] is False
    assert (
        "model_semantics_ownership_report:model_semantics_ownership_join_generates_text"
        in audit["blockers"]
    )


def test_bundle_audit_blocks_simulation_report_without_complete_semantic_review() -> None:
    report = _simulation_ready()
    report["summary"]["evaluable_attempts"] = 0
    report["summary"]["acceptance"]["semantic_review_complete"] = False

    audit = reply_chain_shadow_bundle_audit(
        state=_ready_state(),
        require_commit_shadow=True,
        simulation_report=report,
    )

    assert audit["ready_for_refactor_review"] is False
    assert "simulation_report:simulation_evaluable_attempts_below_attempt_count:0<300" in audit["blockers"]
    assert "simulation_report:simulation_semantic_review_incomplete" in audit["blockers"]


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
            "results": [
                {
                    "scenario_id": "sim_case",
                    "attempt": 1,
                    "request_ids": ["sim_request_1"],
                    "event_ids": [],
                    "node_trace_names": ["sop_chat_gate", "planner", "reply"],
                    "tool_call_names": ["customer_store_lookup"],
                    "sync_reply_message_count": 1,
                    "outbox_batch_count": 1,
                    "simulated_write_count": 0,
                }
            ],
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


def test_bundle_audit_reports_precommit_shadow_bundle_ready() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"] = {
        "schema_version": "parallel_reply_chain_diagnostics_v1",
        "phase": "ready_for_shadow_comparison",
    }
    state.pop("reply_chain_commit_shadow")

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=False)

    assert audit["schema_version"] == "reply_chain_shadow_bundle_audit_v1"
    assert audit["phase"] == "precommit"
    assert audit["ready_for_refactor_review"] is True
    assert audit["safety"]["does_not_approve_behavior_switch"] is True


def test_bundle_audit_blocks_postcommit_without_commit_shadow() -> None:
    state = _ready_state()
    state.pop("reply_chain_commit_shadow")

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "missing_shadow_component:reply_chain_commit_shadow" in audit["blockers"]
    assert "commit_precommit_audit_not_ready" in audit["blockers"]


def test_bundle_audit_reports_postcommit_shadow_bundle_ready() -> None:
    audit = reply_chain_shadow_bundle_audit(state=_ready_state(), require_commit_shadow=True)

    assert audit["phase"] == "postcommit"
    assert audit["ready_for_refactor_review"] is True
    assert audit["components"]["reply_chain_commit_shadow"]["valid"] is True
    assert audit["review_gates"]["commit_phase_ready"]["passed"] is True


def test_bundle_audit_blocks_reply_handoff_without_migration_audit() -> None:
    state = _ready_state()
    state["reply_final_brain_handoff_shadow"].pop("migration_audit")

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "reply_handoff_missing_migration_audit" in audit["blockers"]
    assert "review_gate_not_ready:reply_handoff_has_no_legacy_business_residue" in audit["blockers"]
    assert audit["review_gates"]["reply_handoff_has_no_legacy_business_residue"]["passed"] is False


def test_bundle_audit_blocks_reply_handoff_legacy_business_residue() -> None:
    state = _ready_state()
    migration_audit = state["reply_final_brain_handoff_shadow"]["migration_audit"]
    migration_audit["legacy_business_field_count"] = 2
    mapping_audit = migration_audit["field_mapping_audit"]
    mapping_audit["legacy_business_field_count"] = 2
    mapping_audit["mapped_legacy_business_field_count"] = 2

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "reply_handoff_legacy_business_field_residue:2" in audit["blockers"]
    assert "review_gate_not_ready:reply_handoff_has_no_legacy_business_residue" in audit["blockers"]
    assert audit["review_gates"]["reply_handoff_has_no_legacy_business_residue"]["passed"] is False


def test_bundle_audit_blocks_postcommit_without_write_action_inventory() -> None:
    state = _ready_state()
    state["reply_chain_commit_shadow"].pop("write_action_inventory")

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "missing_reply_chain_write_action_inventory" in audit["blockers"]
    assert "review_gate_not_ready:commit_phase_ready" in audit["blockers"]


def test_bundle_audit_blocks_unresolved_release_review_groups() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": [],
        "blocker_groups": {
            "reply_payload_schema": {
                "ready": False,
                "blocker_count": 1,
                "blockers": ["gate_not_proven:reply_target_input_schema_review"],
            }
        },
    }

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "release_review_blocker_group_unresolved:reply_payload_schema" in audit["blockers"]
    assert (
        "release_review_blocker_group:reply_payload_schema:gate_not_proven:reply_target_input_schema_review"
        in audit["blockers"]
    )


def test_bundle_audit_blocks_flat_unproven_release_review_gates() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["business_wording_freeze_review"],
        "blocker_groups": {},
    }

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "release_review_gate_unproven:business_wording_freeze_review" in audit["blockers"]


def test_bundle_audit_accepts_valid_external_reports_for_external_review_gates() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["simulation_regression_review", "model_matrix_review"],
        "blocker_groups": {
            "manual_review": {
                "ready": False,
                "blocker_count": 2,
                "blockers": [
                    "gate_not_proven:simulation_regression_review",
                    "gate_not_proven:model_matrix_review",
                ],
            }
        },
    }

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
    )

    assert audit["ready_for_refactor_review"] is True
    assert audit["external_gate_evidence"]["proven_gates"] == [
        "simulation_regression_review",
        "model_matrix_review",
    ]


def test_bundle_audit_blocks_external_report_from_different_commit() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["model_matrix_review"],
        "blocker_groups": {
            "manual_review": {
                "ready": False,
                "blocker_count": 1,
                "blockers": ["gate_not_proven:model_matrix_review"],
            }
        },
    }
    model_matrix = _model_matrix_ready()
    model_matrix["git_commit"] = "def456"
    model_matrix["git_commit_set"] = ["def456"]

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        model_matrix_report=model_matrix,
    )

    assert audit["ready_for_refactor_review"] is False
    assert "model_matrix_review" not in audit["external_gate_evidence"].get("proven_gates", [])
    assert "model_matrix_report:model_matrix_git_commit_mismatch:def456!=abc123" in audit["blockers"]
    assert "release_review_gate_unproven:model_matrix_review" in audit["blockers"]


def test_bundle_audit_blocks_external_gate_proof_when_bundle_has_mixed_commits() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["git_commit"] = "def456"
    state["parallel_reply_chain_diagnostics"]["git_commit_set"] = ["def456"]
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["simulation_regression_review"],
        "blocker_groups": {
            "manual_review": {
                "ready": False,
                "blocker_count": 1,
                "blockers": ["gate_not_proven:simulation_regression_review"],
            }
        },
    }

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        simulation_report=_simulation_ready(),
    )

    assert audit["ready_for_refactor_review"] is False
    assert "simulation_regression_review" not in audit["external_gate_evidence"].get("proven_gates", [])
    assert "simulation_report:simulation_bundle_git_commit_set_not_single:abc123,def456" in audit["blockers"]


def test_bundle_audit_blocks_invalid_external_reports() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": False,
        "missing_or_unproven_gates": ["simulation_regression_review"],
        "blocker_groups": {},
    }
    simulation = _simulation_ready()
    simulation["safety"]["production_writes_allowed"] = True

    audit = reply_chain_shadow_bundle_audit(
        state=state,
        require_commit_shadow=True,
        simulation_report=simulation,
    )

    assert audit["ready_for_refactor_review"] is False
    assert "simulation_report:simulation_missing_no_production_write_safety" in audit["blockers"]


def test_bundle_audit_blocks_release_review_that_claims_switch_approval() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "can_enable_behavior_switch": True,
        "missing_or_unproven_gates": [],
        "blocker_groups": {},
    }

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "release_review_missing_non_approval_marker" in audit["blockers"]


def test_bundle_audit_blocks_when_join_would_own_customer_text() -> None:
    state = _ready_state()
    state["parallel_reply_chain_shadow"]["current_serial_observation"][
        "join_generates_customer_visible_text"
    ] = True

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "review_gate_not_ready:join_keeps_reply_as_final_owner" in audit["blockers"]


def test_bundle_audit_blocks_tool_plan_preview_with_legacy_residue_even_if_observation_ready() -> None:
    state = _ready_state()
    state["tool_plan_preview"]["migration_audit"] = {
        "schema_version": "tool_planner_migration_audit_v1",
        "legacy_residue_count": 2,
        "legacy_residue_fields": ["reply_strategy", "payment_decision"],
        "tool_planner_only_ready": False,
        "review_required_before_migration": True,
    }
    state["parallel_reply_chain_shadow"]["current_serial_observation"][
        "tool_planner_only_ready"
    ] = True

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "tool_plan_preview_not_tool_planner_only" in audit["blockers"]
    assert "tool_plan_preview_legacy_residue:2" in audit["blockers"]
    assert "tool_plan_preview_requires_migration_review" in audit["blockers"]
    assert "review_gate_not_ready:tool_planner_is_tool_only" in audit["blockers"]


def test_bundle_audit_blocks_tool_plan_preview_without_migration_audit() -> None:
    state = _ready_state()
    state["tool_plan_preview"] = {"schema_version": "tool_plan_preview_v2"}

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "tool_plan_preview_missing_migration_audit" in audit["blockers"]
    assert "review_gate_not_ready:tool_planner_is_tool_only" in audit["blockers"]


def test_bundle_audit_blocks_when_direct_reply_is_allowed_without_guard() -> None:
    state = _ready_state()
    observation = state["parallel_reply_chain_shadow"]["current_serial_observation"]
    observation["direct_reply_allowed"] = True
    observation["direct_reply_guard_ready"] = False
    observation["direct_reply_guard_blockers"] = ["read_tools_present"]

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "review_gate_not_ready:direct_reply_guard_review" in audit["blockers"]
