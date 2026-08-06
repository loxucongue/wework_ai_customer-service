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
        "must_not_be_owned_by": ["sop_chat_gate", "tool_planner", "reply_chain_join"],
    }
    base.update(overrides)
    return base


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

    assert diagnostics["phase"] == "tool_planner_migration_blocked"
    assert diagnostics["next_safe_step"] == "move_legacy_planner_semantics_to_reply_before_behavior_switch"
    assert diagnostics["migration"]["blockers"] == ["tool_planner_legacy_semantic_residue:3"]
    assert diagnostics["migration"]["tool_planner_legacy_residue_count"] == 3
    assert diagnostics["migration"]["tool_planner_only_ready"] is False


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
