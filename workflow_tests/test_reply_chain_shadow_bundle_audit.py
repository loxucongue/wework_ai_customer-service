from __future__ import annotations

from app.services.reply_chain_shadow_bundle_audit import reply_chain_shadow_bundle_audit


def _ready_state() -> dict:
    return {
        "reply_chain_shadow_context": {"schema_version": "reply_chain_shadow_v1"},
        "sop_gate_router_shadow": {"schema_version": "chat_gate_router_shadow_v1"},
        "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
        "read_only_tool_executor_shadow": {"schema_version": "read_only_tool_executor_shadow_v1"},
        "reply_chain_join_shadow": {"schema_version": "reply_chain_join_shadow_v1"},
        "reply_final_brain_handoff_shadow": {"schema_version": "reply_final_brain_handoff_shadow_v1"},
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


def test_bundle_audit_blocks_unresolved_release_review_groups() -> None:
    state = _ready_state()
    state["parallel_reply_chain_diagnostics"]["release_review"] = {
        "schema_version": "reply_chain_release_review_checklist_v1",
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


def test_bundle_audit_blocks_when_join_would_own_customer_text() -> None:
    state = _ready_state()
    state["parallel_reply_chain_shadow"]["current_serial_observation"][
        "join_generates_customer_visible_text"
    ] = True

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "review_gate_not_ready:join_keeps_reply_as_final_owner" in audit["blockers"]


def test_bundle_audit_blocks_when_direct_reply_is_allowed_without_guard() -> None:
    state = _ready_state()
    observation = state["parallel_reply_chain_shadow"]["current_serial_observation"]
    observation["direct_reply_allowed"] = True
    observation["direct_reply_guard_ready"] = False
    observation["direct_reply_guard_blockers"] = ["read_tools_present"]

    audit = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)

    assert audit["ready_for_refactor_review"] is False
    assert "review_gate_not_ready:direct_reply_guard_review" in audit["blockers"]
