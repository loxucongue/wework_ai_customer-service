from __future__ import annotations

import json

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.parallel_reply_chain_diagnostics import parallel_reply_chain_diagnostics


def _completed_runner_shadow(*, input_shadow_fields: list[str] | None = None) -> dict:
    return {
        "schema_version": "parallel_gate_planner_runner_shadow_v1",
        "mode": "completed_shadow",
        "input_isolation_audit": {
            "schema_version": "parallel_branch_input_isolation_audit_v1",
            "initial_state_unchanged_after_branches": True,
            "shadow_only_fields_present_in_initial_state": input_shadow_fields or [],
        },
        "branch_output_contract_audit": {
            "schema_version": "parallel_branch_output_contract_audit_v1",
            "ready": True,
            "blockers": [],
            "required_outputs": {
                "sop_chat_gate": {
                    "required_field": "gate_router_shadow",
                    "required_schema_version": "chat_gate_router_shadow_v1",
                    "observed_schema_version": "chat_gate_router_shadow_v1",
                    "valid": True,
                },
                "tool_planner": {
                    "required_field": "tool_plan_preview",
                    "required_schema_version": "tool_plan_preview_v2",
                    "observed_schema_version": "tool_plan_preview_v2",
                    "valid": True,
                },
            },
        },
        "branches": {
            "sop_chat_gate": {"status": "completed"},
            "tool_planner": {"status": "completed"},
        },
    }


def _commit_shadow(**overrides: object) -> dict:
    base = {
        "schema_version": "reply_chain_commit_shadow_v1",
        "commit_phase_owner": "runtime_after_reply_validation",
        "requires_reply_validation_before_commit": True,
        "precommit_validation_audit": {
            "schema_version": "reply_chain_precommit_validation_audit_v1",
            "ready_for_commit_shadow": True,
            "blockers": [],
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
        "must_not_be_owned_by": ["sop_chat_gate", "tool_planner", "reply_chain_join"],
    }
    base.update(overrides)
    return base


def test_diagnostics_reports_single_git_commit_when_all_evidence_matches() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "git_commit": "abc123",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow={**_completed_runner_shadow(), "git_commit": "abc123"},
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "git_commit": "abc123",
            "status": "matched_shadow_replay",
        },
        commit_shadow=_commit_shadow(git_commit="abc123"),
    )

    assert diagnostics["git_commit"] == "abc123"
    assert diagnostics["git_commit_set"] == ["abc123"]


def test_diagnostics_reports_git_commit_set_when_evidence_differs() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "git_commit": "abc123",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow={**_completed_runner_shadow(), "git_commit": "def456"},
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "git_commit": "abc123",
            "status": "matched_shadow_replay",
        },
        commit_shadow=_commit_shadow(git_commit="def456"),
    )

    assert "git_commit" not in diagnostics
    assert diagnostics["git_commit_set"] == ["abc123", "def456"]


def test_diagnostics_reports_runner_integration_as_next_step_when_contract_ready() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
            "target_topology": {"final_expression_owner": "reply"},
        },
    )

    assert diagnostics["schema_version"] == "parallel_reply_chain_diagnostics_v1"
    assert diagnostics["phase"] == "ready_for_runner_integration"
    assert diagnostics["next_safe_step"] == "wire_shadow_runner_without_runtime_behavior_change"
    assert diagnostics["contract"]["final_expression_owner"] == "reply"
    assert diagnostics["safety"]["diagnostic_only"] is True


def test_diagnostics_reports_contract_blockers_before_runner_work() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {
                "ready_for_shadow_parallel_runner": False,
                "blockers": ["missing_gate_router_shadow"],
            },
        },
    )

    assert diagnostics["phase"] == "contract_blocked"
    assert diagnostics["next_safe_step"] == "fix_shadow_contract_or_flag_blockers"
    assert diagnostics["contract"]["blockers"] == ["missing_gate_router_shadow"]


def test_diagnostics_reports_runner_branch_errors() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow={
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "mode": "completed_shadow",
            "input_isolation_audit": {
                "schema_version": "parallel_branch_input_isolation_audit_v1",
                "initial_state_unchanged_after_branches": True,
                "shadow_only_fields_present_in_initial_state": [],
            },
            "branches": {
                "sop_chat_gate": {"status": "completed"},
                "tool_planner": {"status": "error"},
            },
        },
    )

    assert diagnostics["phase"] == "runner_blocked"
    assert "branch_error:tool_planner" in diagnostics["runner"]["blockers"]
    assert diagnostics["runner"]["branch_status"]["tool_planner"] == "error"


def test_diagnostics_reports_ready_for_shadow_comparison_after_runner_success() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
    )

    assert diagnostics["phase"] == "ready_for_shadow_comparison"
    assert diagnostics["next_safe_step"] == "collect_old_vs_new_shadow_diffs_before_behavior_switch"


def test_diagnostics_blocks_behavior_switch_when_comparison_has_diffs() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "diffs_found",
            "diffs": [{"field": "gate_route", "serial": "tools_only", "parallel": "direct_text"}],
        },
    )

    assert diagnostics["phase"] == "comparison_blocked"
    assert diagnostics["next_safe_step"] == "fix_shadow_comparison_diffs_before_behavior_switch"
    assert diagnostics["comparison"]["blockers"] == ["comparison_diffs_found"]
    assert diagnostics["comparison"]["diff_count"] == 1


def test_diagnostics_requires_human_review_after_matched_shadow_comparison() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
            "review_gate": {"can_enable_behavior_switch": False},
        },
        commit_shadow=_commit_shadow(),
    )

    assert diagnostics["phase"] == "ready_for_human_review"
    assert diagnostics["next_safe_step"] == "run_review_gates_and_offline_simulation_before_behavior_switch"
    assert diagnostics["comparison"]["status"] == "matched_shadow_replay"
    assert diagnostics["commit"]["present"] is True
    assert diagnostics["commit"]["commit_phase_owner"] == "runtime_after_reply_validation"
    assert diagnostics["commit"]["requires_reply_validation_before_commit"] is True
    assert diagnostics["release_review"]["schema_version"] == "reply_chain_release_review_checklist_v1"
    assert diagnostics["release_review"]["can_enable_behavior_switch"] is False
    assert diagnostics["release_review"]["required_gate_count"] == 16
    assert "simulation_regression_review" in diagnostics["release_review"]["missing_or_unproven_gates"]
    assert "model_matrix_review" in diagnostics["release_review"]["missing_or_unproven_gates"]
    groups = diagnostics["release_review"]["blocker_groups"]
    assert groups["contract"]["ready"] is True
    assert groups["runner"]["ready"] is True
    assert groups["comparison"]["ready"] is True
    assert groups["commit"]["ready"] is True
    assert groups["migration"]["ready"] is True
    assert groups["manual_review"]["ready"] is False
    assert "gate_not_proven:simulation_regression_review" in groups["manual_review"]["blockers"]
    assert "gate_not_proven:model_matrix_review" in groups["manual_review"]["blockers"]


def test_diagnostics_review_checklist_records_automated_gate_evidence() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
            "current_serial_observation": {
                "shared_context_authority_audit_schema": "reply_chain_authority_audit_v1",
                "shared_context_timeline_window_audit_schema": "reply_chain_timeline_window_audit_v1",
                "shared_context_timeline_window_ready": True,
                "shared_context_current_message_audit_schema": "reply_chain_current_message_audit_v1",
                "shared_context_current_message_ready": True,
                "shared_context_fact_snapshot_schema": "reply_chain_fact_snapshot_audit_v1",
                "gate_commit_boundary_schema": "chat_gate_commit_boundary_v1",
                "gate_shadow_output_only": True,
                "gate_shadow_creates_sop_task": False,
                "gate_shadow_updates_send_once": False,
                "gate_shadow_sends_customer_messages": False,
                "gate_shadow_writes_database": False,
                "join_final_expression_boundary_schema": "reply_final_expression_boundary_v1",
                "join_final_customer_message_owner": "reply",
                "join_generates_customer_visible_text": False,
                "join_decides_sales_psychology": False,
                "direct_reply_allowed": False,
                "direct_reply_guard_schema": "reply_chain_direct_reply_guard_audit_v1",
                "direct_reply_guard_ready": False,
                "reply_handoff_readiness_schema": "reply_final_brain_handoff_readiness_audit_v1",
                "reply_handoff_ready_for_payload_switch_shadow": True,
                "reply_target_input_schema_audit_schema": "reply_final_brain_target_input_schema_audit_v1",
                "reply_target_input_schema_version": "reply_final_brain_target_input_schema_v1",
                "reply_target_input_schema_ready": True,
                "reply_handoff_legacy_business_field_count": 0,
            },
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
            "review_gate": {"can_enable_behavior_switch": False},
        },
        commit_shadow=_commit_shadow(),
    )

    gates = {gate["gate_id"]: gate for gate in diagnostics["release_review"]["gates"]}
    assert gates["authority_snapshot_review"]["passed"] is True
    assert gates["gate_commit_boundary_review"]["passed"] is True
    assert gates["branch_input_isolation_review"]["passed"] is True
    assert gates["final_expression_owner_review"]["passed"] is True
    assert gates["direct_reply_guard_review"]["passed"] is True
    assert gates["reply_handoff_readiness_review"]["passed"] is True
    assert gates["reply_target_input_schema_review"]["passed"] is True
    assert gates["reply_handoff_semantic_residue_review"]["passed"] is True
    assert gates["commit_phase_shadow_review"]["passed"] is True
    assert gates["payload_isolation_review"]["evidence_type"] == "external_report_required"
    assert (
        gates["payload_isolation_review"]["required_evidence"]
        == "attach_reply_chain_payload_isolation_audit_before_behavior_switch"
    )
    assert gates["business_wording_freeze_review"]["evidence_type"] == "external_report_required"
    assert (
        gates["business_wording_freeze_review"]["required_evidence"]
        == "attach_reply_chain_business_wording_freeze_audit_before_behavior_switch"
    )
    assert gates["rollback_evidence_review"]["evidence_type"] == "external_report_required"
    assert (
        gates["rollback_evidence_review"]["required_evidence"]
        == "attach_reply_chain_refactor_rollback_evidence_before_behavior_switch"
    )
    assert gates["model_semantics_ownership_review"]["evidence_type"] == "external_report_required"
    assert (
        gates["model_semantics_ownership_review"]["required_evidence"]
        == "attach_reply_chain_model_semantics_ownership_audit_before_behavior_switch"
    )
    assert diagnostics["release_review"]["can_enable_behavior_switch"] is False
    assert "payload_isolation_review" in diagnostics["release_review"]["missing_or_unproven_gates"]
    assert "business_wording_freeze_review" in diagnostics["release_review"]["missing_or_unproven_gates"]
    assert "rollback_evidence_review" in diagnostics["release_review"]["missing_or_unproven_gates"]
    assert "model_semantics_ownership_review" in diagnostics["release_review"]["missing_or_unproven_gates"]
    assert diagnostics["release_review"]["blocker_groups"]["reply_payload_schema"]["ready"] is True
    assert diagnostics["release_review"]["blocker_groups"]["manual_review"]["ready"] is False


def test_diagnostics_blocks_when_commit_shadow_has_wrong_owner() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        commit_shadow=_commit_shadow(commit_phase_owner="sop_chat_gate"),
    )

    assert diagnostics["phase"] == "commit_phase_blocked"
    assert diagnostics["next_safe_step"] == "fix_or_record_reply_chain_commit_shadow_before_behavior_switch"
    assert diagnostics["commit"]["blockers"] == ["commit_owner_not_runtime_after_reply_validation"]


def test_diagnostics_blocks_when_deferred_write_handoff_is_missing() -> None:
    commit = _commit_shadow()
    commit.pop("deferred_write_handoff_audit")

    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        commit_shadow=commit,
    )

    assert diagnostics["phase"] == "commit_phase_blocked"
    assert diagnostics["commit"]["blockers"] == ["missing_reply_chain_deferred_write_handoff_audit"]


def test_diagnostics_blocks_when_commit_precommit_audit_is_missing() -> None:
    commit_shadow = _commit_shadow()
    commit_shadow.pop("precommit_validation_audit")

    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        commit_shadow=commit_shadow,
    )

    assert diagnostics["phase"] == "commit_phase_blocked"
    assert "missing_reply_chain_precommit_validation_audit" in diagnostics["commit"]["blockers"]


def test_diagnostics_blocks_when_commit_write_inventory_is_missing() -> None:
    commit_shadow = _commit_shadow()
    commit_shadow.pop("write_action_inventory")

    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        commit_shadow=commit_shadow,
    )

    assert diagnostics["phase"] == "commit_phase_blocked"
    assert "missing_reply_chain_write_action_inventory" in diagnostics["commit"]["blockers"]


def test_diagnostics_blocks_when_commit_write_inventory_is_not_ready() -> None:
    commit_shadow = _commit_shadow()
    commit_shadow["write_action_inventory"] = {
        "schema_version": "reply_chain_write_action_inventory_v1",
        "commit_phase_owner": "runtime_after_reply_validation",
        "requires_reply_validation_before_write": True,
        "all_runtime_writes_after_reply_validation": False,
        "ready_for_commit_refactor_review": False,
        "actions": [],
        "blockers": ["write_allowed_without_ready_precommit:conversation_assistant_message"],
    }

    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        commit_shadow=commit_shadow,
    )

    assert diagnostics["phase"] == "commit_phase_blocked"
    assert (
        "write_inventory:write_allowed_without_ready_precommit:conversation_assistant_message"
        in diagnostics["commit"]["blockers"]
    )


def test_diagnostics_blocks_when_commit_precommit_audit_is_not_ready() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        commit_shadow=_commit_shadow(
            precommit_validation_audit={
                "schema_version": "reply_chain_precommit_validation_audit_v1",
                "ready_for_commit_shadow": False,
                "blockers": ["empty_reply_not_allowed_before_commit"],
            }
        ),
    )

    assert diagnostics["phase"] == "commit_phase_blocked"
    assert "precommit:empty_reply_not_allowed_before_commit" in diagnostics["commit"]["blockers"]


def test_diagnostics_blocks_when_tool_planner_still_has_legacy_semantics() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
            "current_serial_observation": {
                "tool_planner_legacy_residue_count": 3,
                "tool_planner_only_ready": False,
            },
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
    )

    assert diagnostics["phase"] == "legacy_semantics_migration_blocked"
    assert diagnostics["next_safe_step"] == "move_legacy_planner_semantics_to_reply_before_behavior_switch"
    assert diagnostics["migration"]["blockers"] == ["tool_planner_legacy_semantic_residue:3"]
    assert diagnostics["migration"]["tool_planner_legacy_residue_count"] == 3
    assert diagnostics["migration"]["tool_planner_only_ready"] is False


def test_diagnostics_blocks_when_reply_handoff_still_has_legacy_planner_semantics() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
            "current_serial_observation": {
                "tool_planner_legacy_residue_count": 0,
                "tool_planner_only_ready": True,
                "reply_handoff_legacy_business_field_count": 5,
            },
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
    )

    assert diagnostics["phase"] == "legacy_semantics_migration_blocked"
    assert diagnostics["next_safe_step"] == "move_legacy_planner_semantics_to_reply_before_behavior_switch"
    assert diagnostics["migration"]["blockers"] == ["reply_handoff_legacy_business_field_residue:5"]
    assert diagnostics["migration"]["tool_planner_legacy_residue_count"] == 0
    assert diagnostics["migration"]["reply_handoff_legacy_business_field_count"] == 5
    assert diagnostics["release_review"]["blocker_groups"]["migration"]["blockers"] == [
        "reply_handoff_legacy_business_field_residue:5"
    ]


def test_diagnostics_groups_reply_schema_gate_blockers_for_review() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
            "current_serial_observation": {
                "shared_context_authority_audit_schema": "reply_chain_authority_audit_v1",
                "shared_context_timeline_window_audit_schema": "reply_chain_timeline_window_audit_v1",
                "shared_context_timeline_window_ready": True,
                "shared_context_current_message_audit_schema": "reply_chain_current_message_audit_v1",
                "shared_context_current_message_ready": True,
                "shared_context_fact_snapshot_schema": "reply_chain_fact_snapshot_audit_v1",
                "gate_commit_boundary_schema": "chat_gate_commit_boundary_v1",
                "gate_shadow_output_only": True,
                "gate_shadow_creates_sop_task": False,
                "gate_shadow_updates_send_once": False,
                "gate_shadow_sends_customer_messages": False,
                "gate_shadow_writes_database": False,
                "join_final_expression_boundary_schema": "reply_final_expression_boundary_v1",
                "join_final_customer_message_owner": "reply",
                "join_generates_customer_visible_text": False,
                "join_decides_sales_psychology": False,
                "direct_reply_allowed": False,
                "direct_reply_guard_schema": "reply_chain_direct_reply_guard_audit_v1",
                "direct_reply_guard_ready": False,
                "reply_handoff_readiness_schema": "reply_final_brain_handoff_readiness_audit_v1",
                "reply_handoff_ready_for_payload_switch_shadow": True,
                "reply_handoff_legacy_business_field_count": 0,
            },
        },
        runner_shadow=_completed_runner_shadow(),
        comparison_shadow={
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "matched_shadow_replay",
        },
        commit_shadow=_commit_shadow(),
    )

    reply_schema_group = diagnostics["release_review"]["blocker_groups"]["reply_payload_schema"]
    assert reply_schema_group["ready"] is False
    assert reply_schema_group["blockers"] == ["gate_not_proven:reply_target_input_schema_review"]


def test_diagnostics_blocks_when_runner_input_contains_shadow_fields() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=_completed_runner_shadow(input_shadow_fields=["sop_gate_router_shadow"]),
    )

    assert diagnostics["phase"] == "runner_blocked"
    assert "runner_input_contains_shadow_fields:1" in diagnostics["runner"]["blockers"]


def test_diagnostics_blocks_when_runner_branch_output_contract_is_invalid() -> None:
    runner_shadow = _completed_runner_shadow()
    runner_shadow["branch_output_contract_audit"] = {
        "schema_version": "parallel_branch_output_contract_audit_v1",
        "ready": False,
        "blockers": ["branch_missing_required_output:sop_chat_gate.gate_router_shadow"],
    }

    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=runner_shadow,
    )

    assert diagnostics["phase"] == "runner_blocked"
    assert (
        "runner_output_contract:branch_missing_required_output:sop_chat_gate.gate_router_shadow"
        in diagnostics["runner"]["blockers"]
    )


def test_diagnostics_blocks_when_runner_branch_output_audit_is_missing() -> None:
    runner_shadow = _completed_runner_shadow()
    runner_shadow.pop("branch_output_contract_audit")

    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow=runner_shadow,
    )

    assert diagnostics["phase"] == "runner_blocked"
    assert "missing_runner_branch_output_contract_audit" in diagnostics["runner"]["blockers"]


def test_diagnostics_are_not_consumed_by_current_model_payloads() -> None:
    state = {
        "normalized_content": "怎么预约",
        "conversation_history": ["用户: 怎么预约"],
        "parallel_reply_chain_diagnostics": {
            "schema_version": "parallel_reply_chain_diagnostics_v1",
            "next_safe_step": "shadow-only-diagnostics-marker",
        },
        "parallel_gate_planner_runner_shadow": {
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "input_mode": "runner-shadow-only-marker",
        },
        "parallel_reply_chain_comparison": {
            "schema_version": "parallel_reply_chain_comparison_v1",
            "status": "shadow-only-comparison-marker",
        },
        "request_context": {},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    assert "parallel_reply_chain_diagnostics" not in planner_payload
    assert "parallel_reply_chain_diagnostics" not in reply_payload
    assert "parallel_gate_planner_runner_shadow" not in planner_payload
    assert "parallel_gate_planner_runner_shadow" not in reply_payload
    assert "parallel_reply_chain_comparison" not in planner_payload
    assert "parallel_reply_chain_comparison" not in reply_payload
    assert "shadow-only-diagnostics-marker" not in combined
    assert "runner-shadow-only-marker" not in combined
    assert "shadow-only-comparison-marker" not in combined
