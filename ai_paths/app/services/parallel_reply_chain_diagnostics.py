from __future__ import annotations

from typing import Any


def parallel_reply_chain_diagnostics(
    *,
    parallel_reply_chain_shadow: dict[str, Any],
    runner_shadow: dict[str, Any] | None = None,
    comparison_shadow: dict[str, Any] | None = None,
    commit_shadow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize migration readiness without owning business semantics."""

    runner = runner_shadow if isinstance(runner_shadow, dict) else {}
    comparison = comparison_shadow if isinstance(comparison_shadow, dict) else {}
    commit = commit_shadow if isinstance(commit_shadow, dict) else {}
    contract_blockers = _list_strings((parallel_reply_chain_shadow.get("activation") or {}).get("blockers"))
    runner_mode = str(runner.get("mode") or "not_integrated")
    runner_blockers = _runner_blockers(runner)
    comparison_status = str(comparison.get("status") or "not_collected")
    comparison_blockers = _comparison_blockers(comparison)
    commit_blockers = _commit_blockers(commit)
    migration_blockers = _migration_blockers(parallel_reply_chain_shadow)
    git_commits = _git_commit_set(parallel_reply_chain_shadow, runner, comparison, commit)
    phase = _phase(
        parallel_shadow_present=parallel_reply_chain_shadow.get("schema_version") == "parallel_reply_chain_shadow_v1",
        contract_blockers=contract_blockers,
        runner_mode=runner_mode,
        runner_blockers=runner_blockers,
        comparison_status=comparison_status,
        comparison_blockers=comparison_blockers,
        commit_blockers=commit_blockers,
        migration_blockers=migration_blockers,
    )
    return _drop_empty(
        {
            "schema_version": "parallel_reply_chain_diagnostics_v1",
            "git_commit": git_commits[0] if len(git_commits) == 1 else "",
            "git_commit_set": git_commits,
            "phase": phase,
            "contract": {
                "present": parallel_reply_chain_shadow.get("schema_version") == "parallel_reply_chain_shadow_v1",
                "ready_for_shadow_runner": bool((parallel_reply_chain_shadow.get("activation") or {}).get("ready_for_shadow_parallel_runner")),
                "blockers": contract_blockers,
                "final_expression_owner": (parallel_reply_chain_shadow.get("target_topology") or {}).get("final_expression_owner"),
            },
            "runner": {
                "mode": runner_mode,
                "blockers": runner_blockers,
                "branch_status": _branch_status(runner),
            },
            "comparison": {
                "status": comparison_status,
                "blockers": comparison_blockers,
                "diff_count": len(comparison.get("diffs") or []) if isinstance(comparison.get("diffs"), list) else 0,
            },
            "commit": {
                "present": commit.get("schema_version") == "reply_chain_commit_shadow_v1",
                "blockers": commit_blockers,
                "commit_phase_owner": commit.get("commit_phase_owner"),
                "requires_reply_validation_before_commit": commit.get("requires_reply_validation_before_commit"),
            },
            "migration": {
                "blockers": migration_blockers,
                "tool_planner_legacy_residue_count": _tool_planner_legacy_residue_count(parallel_reply_chain_shadow),
                "tool_planner_only_ready": (parallel_reply_chain_shadow.get("current_serial_observation") or {}).get("tool_planner_only_ready"),
                "reply_handoff_legacy_business_field_count": _reply_handoff_legacy_business_field_count(parallel_reply_chain_shadow),
            },
            "release_review": _release_review_gate_checklist(
                parallel_reply_chain_shadow=parallel_reply_chain_shadow,
                runner_shadow=runner,
                comparison_shadow=comparison,
                commit_shadow=commit,
                phase=phase,
                contract_blockers=contract_blockers,
                runner_blockers=runner_blockers,
                comparison_blockers=comparison_blockers,
                commit_blockers=commit_blockers,
                migration_blockers=migration_blockers,
            ),
            "next_safe_step": _next_safe_step(phase),
            "safety": {
                "diagnostic_only": True,
                "no_runtime_behavior_change": True,
                "no_model_payload_consumption": True,
                "no_customer_messages_sent": True,
                "no_database_writes": True,
            },
        }
    )


def _phase(
    *,
    parallel_shadow_present: bool,
    contract_blockers: list[str],
    runner_mode: str,
    runner_blockers: list[str],
    comparison_status: str,
    comparison_blockers: list[str],
    commit_blockers: list[str],
    migration_blockers: list[str],
) -> str:
    if not parallel_shadow_present:
        return "missing_parallel_contract"
    if contract_blockers:
        return "contract_blocked"
    if runner_mode == "not_integrated":
        return "ready_for_runner_integration"
    if runner_mode == "skipped" or runner_blockers:
        return "runner_blocked"
    if runner_mode == "completed_shadow":
        if comparison_status == "not_collected":
            return "ready_for_shadow_comparison"
        if comparison_blockers:
            return "comparison_blocked"
        if commit_blockers:
            return "commit_phase_blocked"
        if migration_blockers:
            return "legacy_semantics_migration_blocked"
        return "ready_for_human_review"
    return "unknown"


def _next_safe_step(phase: str) -> str:
    return {
        "missing_parallel_contract": "build_parallel_reply_chain_shadow_contract",
        "contract_blocked": "fix_shadow_contract_or_flag_blockers",
        "ready_for_runner_integration": "wire_shadow_runner_without_runtime_behavior_change",
        "runner_blocked": "fix_runner_inputs_or_refactor_flags",
        "ready_for_shadow_comparison": "collect_old_vs_new_shadow_diffs_before_behavior_switch",
        "comparison_blocked": "fix_shadow_comparison_diffs_before_behavior_switch",
        "commit_phase_blocked": "fix_or_record_reply_chain_commit_shadow_before_behavior_switch",
        "legacy_semantics_migration_blocked": "move_legacy_planner_semantics_to_reply_before_behavior_switch",
        "ready_for_human_review": "run_review_gates_and_offline_simulation_before_behavior_switch",
    }.get(phase, "inspect_parallel_refactor_diagnostics")


def _release_review_gate_checklist(
    *,
    parallel_reply_chain_shadow: dict[str, Any],
    runner_shadow: dict[str, Any],
    comparison_shadow: dict[str, Any],
    commit_shadow: dict[str, Any],
    phase: str,
    contract_blockers: list[str],
    runner_blockers: list[str],
    comparison_blockers: list[str],
    commit_blockers: list[str],
    migration_blockers: list[str],
) -> dict[str, Any]:
    """Expose release-review evidence without approving a behavior switch."""

    observation = parallel_reply_chain_shadow.get("current_serial_observation")
    if not isinstance(observation, dict):
        observation = {}
    activation = parallel_reply_chain_shadow.get("activation")
    if not isinstance(activation, dict):
        activation = {}
    comparison_review = comparison_shadow.get("review_gate")
    if not isinstance(comparison_review, dict):
        comparison_review = {}

    gates = [
        _gate("rule_matrix_delta_review", "manual_required", "review_rule_ownership_matrix_delta_for_this_commit"),
        _gate(
            "payload_isolation_review",
            "external_report_required",
            "attach_reply_chain_payload_isolation_audit_before_behavior_switch",
        ),
        _gate(
            "authority_snapshot_review",
            "automated_shadow_evidence",
            "authority_timeline_current_message_and_fact_audits_present",
            passed=(
                observation.get("shared_context_authority_audit_schema") == "reply_chain_authority_audit_v1"
                and observation.get("shared_context_timeline_window_audit_schema") == "reply_chain_timeline_window_audit_v1"
                and observation.get("shared_context_timeline_window_ready") is True
                and observation.get("shared_context_current_message_audit_schema") == "reply_chain_current_message_audit_v1"
                and observation.get("shared_context_fact_snapshot_schema") == "reply_chain_fact_snapshot_audit_v1"
                and observation.get("shared_context_current_message_ready") is True
            ),
        ),
        _gate(
            "gate_commit_boundary_review",
            "automated_shadow_evidence",
            "gate_shadow_has_no_commit_side_effects",
            passed=(
                observation.get("gate_commit_boundary_schema") == "chat_gate_commit_boundary_v1"
                and observation.get("gate_shadow_output_only") is True
                and observation.get("gate_shadow_creates_sop_task") is False
                and observation.get("gate_shadow_updates_send_once") is False
                and observation.get("gate_shadow_sends_customer_messages") is False
                and observation.get("gate_shadow_writes_database") is False
            ),
        ),
        _gate(
            "branch_input_isolation_review",
            "automated_runner_evidence",
            "runner_input_isolation_audit_present_and_clean",
            passed=not _runner_input_isolation_blockers(runner_shadow) if runner_shadow else False,
        ),
        _gate(
            "final_expression_owner_review",
            "automated_shadow_evidence",
            "join_keeps_reply_as_final_complex_owner",
            passed=(
                observation.get("join_final_expression_boundary_schema") == "reply_final_expression_boundary_v1"
                and observation.get("join_final_customer_message_owner") == "reply"
                and observation.get("join_generates_customer_visible_text") is False
                and observation.get("join_decides_sales_psychology") is False
            ),
        ),
        _gate(
            "direct_reply_guard_review",
            "automated_shadow_evidence",
            "direct_reply_exception_has_explicit_guard",
            passed=(
                observation.get("direct_reply_allowed") is not True
                or (
                    observation.get("direct_reply_guard_schema") == "reply_chain_direct_reply_guard_audit_v1"
                    and observation.get("direct_reply_guard_ready") is True
                )
            ),
        ),
        _gate(
            "reply_handoff_readiness_review",
            "automated_shadow_evidence",
            "reply_handoff_readiness_audit_ready",
            passed=(
                observation.get("reply_handoff_readiness_schema") == "reply_final_brain_handoff_readiness_audit_v1"
                and observation.get("reply_handoff_ready_for_payload_switch_shadow") is True
            ),
        ),
        _gate(
            "reply_target_input_schema_review",
            "automated_shadow_evidence",
            "target_reply_input_schema_audit_ready",
            passed=(
                observation.get("reply_target_input_schema_audit_schema")
                == "reply_final_brain_target_input_schema_audit_v1"
                and observation.get("reply_target_input_schema_version")
                == "reply_final_brain_target_input_schema_v1"
                and observation.get("reply_target_input_schema_ready") is True
            ),
        ),
        _gate(
            "reply_handoff_semantic_residue_review",
            "automated_shadow_evidence",
            "reply_handoff_has_no_legacy_planner_business_fields",
            passed=_reply_handoff_legacy_business_field_count(parallel_reply_chain_shadow) == 0
            and _reply_handoff_legacy_business_field_observed(parallel_reply_chain_shadow),
        ),
        _gate(
            "commit_phase_shadow_review",
            "automated_runtime_evidence",
            "commit_shadow_owner_is_runtime_after_reply_validation",
            passed=not _commit_blockers(commit_shadow) if commit_shadow else False,
        ),
        _gate(
            "business_wording_freeze_review",
            "external_report_required",
            "attach_reply_chain_business_wording_freeze_audit_before_behavior_switch",
        ),
        _gate("model_semantics_ownership_review", "manual_required", "confirm_gate_tool_join_do_not_own_sales_psychology"),
        _gate(
            "simulation_regression_review",
            "external_report_required",
            "attach_offline_simulation_report_before_behavior_switch",
        ),
        _gate(
            "model_matrix_review",
            "external_report_required",
            "attach_refactor_model_matrix_report_before_behavior_switch",
        ),
        _gate(
            "rollback_evidence_review",
            "external_report_required",
            "attach_reply_chain_refactor_rollback_evidence_before_behavior_switch",
        ),
    ]
    missing = [gate["gate_id"] for gate in gates if gate.get("passed") is not True]
    return {
        "schema_version": "reply_chain_release_review_checklist_v1",
        "phase": phase,
        "can_enable_behavior_switch": False,
        "reason": "diagnostics_only_review_evidence_not_release_approval",
        "comparison_review_gate_can_enable": comparison_review.get("can_enable_behavior_switch"),
        "required_gate_count": len(gates),
        "missing_or_unproven_gates": missing,
        "blocker_groups": _release_review_blocker_groups(
            gates=gates,
            contract_blockers=contract_blockers,
            runner_blockers=runner_blockers,
            comparison_blockers=comparison_blockers,
            commit_blockers=commit_blockers,
            migration_blockers=migration_blockers,
        ),
        "gates": gates,
    }


def _gate(gate_id: str, evidence_type: str, required_evidence: str, *, passed: bool | None = None) -> dict[str, Any]:
    return _drop_empty(
        {
            "gate_id": gate_id,
            "evidence_type": evidence_type,
            "required_evidence": required_evidence,
            "passed": passed,
        }
    )


def _release_review_blocker_groups(
    *,
    gates: list[dict[str, Any]],
    contract_blockers: list[str],
    runner_blockers: list[str],
    comparison_blockers: list[str],
    commit_blockers: list[str],
    migration_blockers: list[str],
) -> dict[str, Any]:
    """Group release blockers by review owner for human audits."""

    gate_blockers = _gate_blockers(gates)
    return {
        "contract": _blocker_group(contract_blockers),
        "runner": _blocker_group(runner_blockers),
        "comparison": _blocker_group(comparison_blockers),
        "commit": _blocker_group(commit_blockers),
        "migration": _blocker_group(migration_blockers),
        "reply_payload_schema": _blocker_group(
            _selected_gate_blockers(
                gate_blockers,
                {
                    "reply_handoff_readiness_review",
                    "reply_target_input_schema_review",
                    "reply_handoff_semantic_residue_review",
                    "final_expression_owner_review",
                    "direct_reply_guard_review",
                },
            )
        ),
        "manual_review": _blocker_group(
            _selected_gate_blockers(
                gate_blockers,
                {
                    "rule_matrix_delta_review",
                    "payload_isolation_review",
                    "business_wording_freeze_review",
                    "model_semantics_ownership_review",
                    "simulation_regression_review",
                    "model_matrix_review",
                    "rollback_evidence_review",
                },
            )
        ),
    }


def _gate_blockers(gates: list[dict[str, Any]]) -> list[str]:
    return [
        f"gate_not_proven:{gate['gate_id']}"
        for gate in gates
        if isinstance(gate, dict) and gate.get("passed") is not True and isinstance(gate.get("gate_id"), str)
    ]


def _selected_gate_blockers(gate_blockers: list[str], gate_ids: set[str]) -> list[str]:
    selected = {f"gate_not_proven:{gate_id}" for gate_id in gate_ids}
    return [blocker for blocker in gate_blockers if blocker in selected]


def _blocker_group(blockers: list[str]) -> dict[str, Any]:
    return _drop_empty(
        {
            "ready": not blockers,
            "blocker_count": len(blockers),
            "blockers": blockers,
        }
    )


def _runner_blockers(runner: dict[str, Any]) -> list[str]:
    blockers = _list_strings(runner.get("activation_blockers"))
    blockers.extend(_runner_input_isolation_blockers(runner))
    blockers.extend(_runner_output_contract_blockers(runner))
    branches = runner.get("branches") if isinstance(runner.get("branches"), dict) else {}
    for branch_name, branch in branches.items():
        if isinstance(branch, dict) and branch.get("status") == "error":
            blockers.append(f"branch_error:{branch_name}")
    return blockers


def _runner_input_isolation_blockers(runner: dict[str, Any]) -> list[str]:
    if str(runner.get("mode") or "") != "completed_shadow":
        return []
    audit = runner.get("input_isolation_audit")
    if not isinstance(audit, dict) or audit.get("schema_version") != "parallel_branch_input_isolation_audit_v1":
        return ["missing_runner_input_isolation_audit"]
    blockers: list[str] = []
    if audit.get("initial_state_unchanged_after_branches") is not True:
        blockers.append("runner_initial_state_mutated")
    shadow_fields = audit.get("shadow_only_fields_present_in_initial_state")
    if isinstance(shadow_fields, list) and shadow_fields:
        blockers.append(f"runner_input_contains_shadow_fields:{len(shadow_fields)}")
    return blockers


def _runner_output_contract_blockers(runner: dict[str, Any]) -> list[str]:
    if str(runner.get("mode") or "") != "completed_shadow":
        return []
    audit = runner.get("branch_output_contract_audit")
    if not isinstance(audit, dict) or audit.get("schema_version") != "parallel_branch_output_contract_audit_v1":
        return ["missing_runner_branch_output_contract_audit"]
    if audit.get("ready") is True:
        return []
    blockers = _list_strings(audit.get("blockers"))
    return [f"runner_output_contract:{item}" for item in blockers] or ["runner_output_contract:not_ready"]


def _branch_status(runner: dict[str, Any]) -> dict[str, str]:
    branches = runner.get("branches") if isinstance(runner.get("branches"), dict) else {}
    return {
        str(name): str(branch.get("status") or "unknown")
        for name, branch in branches.items()
        if isinstance(branch, dict)
    }


def _comparison_blockers(comparison: dict[str, Any]) -> list[str]:
    if comparison.get("schema_version") != "parallel_reply_chain_comparison_v1":
        return []
    status = str(comparison.get("status") or "")
    if status == "matched_shadow_replay":
        return []
    if status == "diffs_found":
        return ["comparison_diffs_found"]
    if status == "not_comparable":
        return ["comparison_not_comparable"]
    return [f"comparison_status:{status or 'unknown'}"]


def _commit_blockers(commit: dict[str, Any]) -> list[str]:
    if not commit:
        return []
    if commit.get("schema_version") != "reply_chain_commit_shadow_v1":
        return ["invalid_reply_chain_commit_shadow"]
    blockers: list[str] = []
    if commit.get("commit_phase_owner") != "runtime_after_reply_validation":
        blockers.append("commit_owner_not_runtime_after_reply_validation")
    if commit.get("requires_reply_validation_before_commit") is not True:
        blockers.append("commit_does_not_require_reply_validation")
    blockers.extend(_commit_precommit_audit_blockers(commit))
    blockers.extend(_commit_deferred_write_handoff_blockers(commit))
    blockers.extend(_commit_write_action_inventory_blockers(commit))
    forbidden_owners = commit.get("must_not_be_owned_by")
    if isinstance(forbidden_owners, list):
        required_forbidden = {"sop_chat_gate", "tool_planner", "reply_chain_join"}
        missing = sorted(required_forbidden.difference({str(item) for item in forbidden_owners}))
        blockers.extend([f"commit_forbidden_owner_missing:{item}" for item in missing])
    else:
        blockers.append("commit_missing_forbidden_owners")
    return blockers


def _commit_write_action_inventory_blockers(commit: dict[str, Any]) -> list[str]:
    inventory = commit.get("write_action_inventory")
    if not isinstance(inventory, dict) or inventory.get("schema_version") != "reply_chain_write_action_inventory_v1":
        return ["missing_reply_chain_write_action_inventory"]
    if inventory.get("commit_phase_owner") != "runtime_after_reply_validation":
        return ["write_inventory_owner_not_runtime_after_reply_validation"]
    if inventory.get("requires_reply_validation_before_write") is not True:
        return ["write_inventory_missing_reply_validation_requirement"]
    if inventory.get("all_runtime_writes_after_reply_validation") is not True:
        blockers = _list_strings(inventory.get("blockers"))
        return [f"write_inventory:{item}" for item in blockers] or ["write_inventory:not_ready"]
    if inventory.get("ready_for_commit_refactor_review") is not True:
        blockers = _list_strings(inventory.get("blockers"))
        return [f"write_inventory:{item}" for item in blockers] or ["write_inventory:not_ready"]
    actions = inventory.get("actions")
    if not isinstance(actions, list):
        return ["write_inventory_actions_not_list"]
    for action in actions:
        if not isinstance(action, dict):
            return ["write_inventory_action_not_dict"]
        action_id = str(action.get("id") or "missing")
        if action.get("owner") != "runtime_after_reply_validation":
            return [f"write_inventory_action_owner_not_runtime_after_reply_validation:{action_id}"]
        phase = str(action.get("execution_phase") or "")
        if phase not in {"after_reply_validation", "deferred_after_reply_validation"}:
            return [f"write_inventory_action_phase_not_after_reply_validation:{action_id}"]
    return []


def _commit_deferred_write_handoff_blockers(commit: dict[str, Any]) -> list[str]:
    audit = commit.get("deferred_write_handoff_audit")
    if not isinstance(audit, dict) or audit.get("schema_version") != "reply_chain_deferred_write_handoff_audit_v1":
        return ["missing_reply_chain_deferred_write_handoff_audit"]
    if audit.get("commit_phase_owner") != "runtime_after_reply_validation":
        return ["deferred_write_owner_not_runtime_after_reply_validation"]
    if audit.get("early_execution_forbidden") is not True:
        return ["deferred_write_early_execution_not_forbidden"]
    if audit.get("current_runtime_executes_deferred_writes") is not False:
        return ["deferred_write_current_runtime_executes_writes"]
    if audit.get("requires_reply_validation_before_write") is not True:
        return ["deferred_write_missing_reply_validation_requirement"]
    if audit.get("ready_for_deferred_write_refactor_review") is True:
        return []
    blockers = _list_strings(audit.get("blockers"))
    return [f"deferred_write_handoff:{item}" for item in blockers] or ["deferred_write_handoff:not_ready"]


def _commit_precommit_audit_blockers(commit: dict[str, Any]) -> list[str]:
    audit = commit.get("precommit_validation_audit")
    if not isinstance(audit, dict) or audit.get("schema_version") != "reply_chain_precommit_validation_audit_v1":
        return ["missing_reply_chain_precommit_validation_audit"]
    if audit.get("ready_for_commit_shadow") is True:
        return []
    blockers = _list_strings(audit.get("blockers"))
    return [f"precommit:{item}" for item in blockers] or ["precommit:not_ready"]


def _migration_blockers(parallel_reply_chain_shadow: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    tool_residue_count = _tool_planner_legacy_residue_count(parallel_reply_chain_shadow)
    if tool_residue_count > 0:
        blockers.append(f"tool_planner_legacy_semantic_residue:{tool_residue_count}")
    reply_residue_count = _reply_handoff_legacy_business_field_count(parallel_reply_chain_shadow)
    if reply_residue_count > 0:
        blockers.append(f"reply_handoff_legacy_business_field_residue:{reply_residue_count}")
    return blockers


def _tool_planner_legacy_residue_count(parallel_reply_chain_shadow: dict[str, Any]) -> int:
    observation = parallel_reply_chain_shadow.get("current_serial_observation")
    if not isinstance(observation, dict):
        return 0
    try:
        return int(observation.get("tool_planner_legacy_residue_count") or 0)
    except (TypeError, ValueError):
        return 0


def _reply_handoff_legacy_business_field_count(parallel_reply_chain_shadow: dict[str, Any]) -> int:
    observation = parallel_reply_chain_shadow.get("current_serial_observation")
    if not isinstance(observation, dict):
        return 0
    value = observation.get("reply_handoff_legacy_business_field_count")
    if value is None:
        value = observation.get("reply_legacy_business_field_count")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _reply_handoff_legacy_business_field_observed(parallel_reply_chain_shadow: dict[str, Any]) -> bool:
    observation = parallel_reply_chain_shadow.get("current_serial_observation")
    if not isinstance(observation, dict):
        return False
    return (
        "reply_handoff_legacy_business_field_count" in observation
        or "reply_legacy_business_field_count" in observation
    )


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _git_commit_set(*items: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(item.get("git_commit") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("git_commit") or "").strip()
        }
    )


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
