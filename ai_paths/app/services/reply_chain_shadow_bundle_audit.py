from __future__ import annotations

from typing import Any

from app.services.reply_chain_external_gate_evidence import (
    business_wording_freeze_report_blockers,
    model_matrix_report_blockers,
    model_semantics_ownership_report_blockers,
    payload_isolation_report_blockers,
    rollback_evidence_report_blockers,
    simulation_report_blockers,
)


CORE_COMPONENT_SCHEMAS = {
    "reply_chain_shadow_context": "reply_chain_shadow_v1",
    "sop_gate_router_shadow": "chat_gate_router_shadow_v1",
    "tool_plan_preview": "tool_plan_preview_v2",
    "read_only_tool_executor_shadow": "read_only_tool_executor_shadow_v1",
    "reply_chain_join_shadow": "reply_chain_join_shadow_v1",
    "reply_final_brain_handoff_shadow": "reply_final_brain_handoff_shadow_v1",
    "parallel_reply_chain_shadow": "parallel_reply_chain_shadow_v1",
    "parallel_gate_planner_runner_shadow": "parallel_gate_planner_runner_shadow_v1",
    "parallel_reply_chain_comparison": "parallel_reply_chain_comparison_v1",
    "parallel_reply_chain_diagnostics": "parallel_reply_chain_diagnostics_v1",
}

POSTCOMMIT_COMPONENT_SCHEMAS = {
    "reply_chain_commit_shadow": "reply_chain_commit_shadow_v1",
}


def reply_chain_shadow_bundle_audit(
    *,
    state: dict[str, Any],
    require_commit_shadow: bool,
    simulation_report: dict[str, Any] | None = None,
    model_matrix_report: dict[str, Any] | None = None,
    payload_isolation_report: dict[str, Any] | None = None,
    business_wording_freeze_report: dict[str, Any] | None = None,
    rollback_evidence_report: dict[str, Any] | None = None,
    model_semantics_ownership_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize shadow migration evidence without changing reply behavior."""

    component_schemas = dict(CORE_COMPONENT_SCHEMAS)
    if require_commit_shadow:
        component_schemas.update(POSTCOMMIT_COMPONENT_SCHEMAS)
    components = {
        field: _component_status(state.get(field), required_schema)
        for field, required_schema in component_schemas.items()
    }
    git_commits = _git_commit_set(state, component_schemas)
    external_gate_evidence = _external_gate_evidence(
        simulation_report=simulation_report,
        model_matrix_report=model_matrix_report,
        payload_isolation_report=payload_isolation_report,
        business_wording_freeze_report=business_wording_freeze_report,
        rollback_evidence_report=rollback_evidence_report,
        model_semantics_ownership_report=model_semantics_ownership_report,
        expected_git_commits=git_commits,
    )
    blockers = _component_blockers(components)
    blockers.extend(external_gate_evidence["blockers"])
    blockers.extend(
        _cross_component_blockers(
            state,
            require_commit_shadow=require_commit_shadow,
            proven_external_gates=set(external_gate_evidence["proven_gates"]),
        )
    )
    gates = _review_gates(state, require_commit_shadow=require_commit_shadow)
    blockers.extend(
        f"review_gate_not_ready:{gate_id}"
        for gate_id, gate in gates.items()
        if gate.get("passed") is False
    )
    return _drop_empty(
        {
            "schema_version": "reply_chain_shadow_bundle_audit_v1",
            "git_commit": git_commits[0] if len(git_commits) == 1 else "",
            "git_commit_set": git_commits,
            "phase": "postcommit" if require_commit_shadow else "precommit",
            "ready_for_refactor_review": not blockers,
            "blockers": blockers,
            "components": components,
            "review_gates": gates,
            "external_gate_evidence": external_gate_evidence,
            "safety": {
                "audit_only": True,
                "no_runtime_behavior_change": True,
                "no_model_payload_consumption": True,
                "no_customer_messages_sent": True,
                "no_database_writes": True,
                "does_not_approve_behavior_switch": True,
            },
            "source": "reply_chain_shadow_bundle_audit",
        }
    )


def _component_status(value: Any, required_schema: str) -> dict[str, Any]:
    observed_schema = value.get("schema_version") if isinstance(value, dict) else None
    return {
        "required_schema_version": required_schema,
        "observed_schema_version": observed_schema,
        "present": isinstance(value, dict),
        "valid": observed_schema == required_schema,
    }


def _component_blockers(components: dict[str, dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for field, status in components.items():
        if status.get("valid") is True:
            continue
        if status.get("present") is not True:
            blockers.append(f"missing_shadow_component:{field}")
        else:
            blockers.append(
                f"invalid_shadow_component_schema:{field}:"
                f"{status.get('observed_schema_version') or 'missing'}"
            )
    return blockers


def _cross_component_blockers(
    state: dict[str, Any],
    *,
    require_commit_shadow: bool,
    proven_external_gates: set[str] | None = None,
) -> list[str]:
    proven_external_gates = proven_external_gates or set()
    blockers: list[str] = []
    parallel_shadow = _dict(state.get("parallel_reply_chain_shadow"))
    if _list_strings((parallel_shadow.get("activation") or {}).get("blockers")):
        blockers.append("parallel_contract_has_blockers")
    blockers.extend(_tool_plan_migration_blockers(state))
    blockers.extend(_reply_handoff_migration_blockers(state))
    runner_shadow = _dict(state.get("parallel_gate_planner_runner_shadow"))
    if str(runner_shadow.get("mode") or "") != "completed_shadow":
        blockers.append(f"parallel_runner_not_completed:{runner_shadow.get('mode') or 'missing'}")
    comparison = _dict(state.get("parallel_reply_chain_comparison"))
    if str(comparison.get("status") or "") != "matched_shadow_replay":
        blockers.append(f"parallel_comparison_not_matched:{comparison.get('status') or 'missing'}")
    diagnostics = _dict(state.get("parallel_reply_chain_diagnostics"))
    phase = str(diagnostics.get("phase") or "")
    if require_commit_shadow:
        if phase != "ready_for_human_review":
            blockers.append(f"diagnostics_not_ready_for_human_review:{phase or 'missing'}")
    elif phase not in {"ready_for_shadow_comparison", "comparison_blocked", "ready_for_human_review"}:
        blockers.append(f"diagnostics_not_past_runner:{phase or 'missing'}")
    commit = _dict(state.get("reply_chain_commit_shadow"))
    if require_commit_shadow:
        audit = _dict(commit.get("precommit_validation_audit"))
        if audit.get("ready_for_commit_shadow") is not True:
            blockers.append("commit_precommit_audit_not_ready")
        write_inventory = _dict(commit.get("write_action_inventory"))
        if write_inventory.get("schema_version") != "reply_chain_write_action_inventory_v1":
            blockers.append("missing_reply_chain_write_action_inventory")
        else:
            if write_inventory.get("commit_phase_owner") != "runtime_after_reply_validation":
                blockers.append("write_inventory_owner_not_runtime_after_reply_validation")
            if write_inventory.get("requires_reply_validation_before_write") is not True:
                blockers.append("write_inventory_missing_reply_validation_requirement")
            if write_inventory.get("all_runtime_writes_after_reply_validation") is not True:
                blockers.append("write_inventory_not_after_reply_validation")
            if write_inventory.get("ready_for_commit_refactor_review") is not True:
                blockers.append("write_inventory_not_ready_for_commit_refactor_review")
    blockers.extend(_diagnostic_release_review_blockers(diagnostics, proven_external_gates=proven_external_gates))
    return blockers


def _diagnostic_release_review_blockers(
    diagnostics: dict[str, Any],
    *,
    proven_external_gates: set[str] | None = None,
) -> list[str]:
    proven_external_gates = proven_external_gates or set()
    release_review = _dict(diagnostics.get("release_review"))
    if release_review.get("schema_version") != "reply_chain_release_review_checklist_v1":
        return []
    blockers: list[str] = []
    if release_review.get("can_enable_behavior_switch") is not False:
        blockers.append("release_review_missing_non_approval_marker")
    unproven = [
        item
        for item in _list_strings(release_review.get("missing_or_unproven_gates"))
        if item not in proven_external_gates
    ]
    blockers.extend(f"release_review_gate_unproven:{item}" for item in unproven)
    blockers.extend(
        _diagnostic_release_review_group_blockers(
            release_review,
            proven_external_gates=proven_external_gates,
        )
    )
    return blockers


def _diagnostic_release_review_group_blockers(
    release_review: dict[str, Any],
    *,
    proven_external_gates: set[str] | None = None,
) -> list[str]:
    proven_external_gates = proven_external_gates or set()
    blocker_groups = release_review.get("blocker_groups")
    if not isinstance(blocker_groups, dict):
        return []
    blockers: list[str] = []
    for group_name, group in blocker_groups.items():
        if not isinstance(group, dict):
            blockers.append(f"release_review_blocker_group_invalid:{group_name}")
            continue
        group_blockers = [
            item
            for item in _list_strings(group.get("blockers"))
            if item not in {f"gate_not_proven:{gate_id}" for gate_id in proven_external_gates}
        ]
        blocker_count = len(group_blockers)
        if group.get("ready") is False or group_blockers or blocker_count > 0:
            if group.get("ready") is False and not group_blockers and _only_proven_external_gate_blockers(group, proven_external_gates):
                continue
            blockers.append(f"release_review_blocker_group_unresolved:{group_name}")
            blockers.extend(f"release_review_blocker_group:{group_name}:{item}" for item in group_blockers)
    return blockers


def _external_gate_evidence(
    *,
    simulation_report: dict[str, Any] | None,
    model_matrix_report: dict[str, Any] | None,
    payload_isolation_report: dict[str, Any] | None,
    business_wording_freeze_report: dict[str, Any] | None,
    rollback_evidence_report: dict[str, Any] | None,
    model_semantics_ownership_report: dict[str, Any] | None,
    expected_git_commits: list[str],
) -> dict[str, Any]:
    proven_gates: list[str] = []
    blockers: list[str] = []
    if simulation_report is not None:
        simulation = _dict(simulation_report)
        simulation_blockers = simulation_report_blockers(simulation)
        simulation_blockers.extend(_external_report_commit_blockers("simulation", simulation, expected_git_commits))
        if simulation_blockers:
            blockers.extend(f"simulation_report:{item}" for item in simulation_blockers)
        else:
            proven_gates.append("simulation_regression_review")
    if model_matrix_report is not None:
        model_matrix = _dict(model_matrix_report)
        model_matrix_blockers = model_matrix_report_blockers(model_matrix)
        model_matrix_blockers.extend(
            _external_report_commit_blockers("model_matrix", model_matrix, expected_git_commits)
        )
        if model_matrix_blockers:
            blockers.extend(f"model_matrix_report:{item}" for item in model_matrix_blockers)
        else:
            proven_gates.append("model_matrix_review")
    if payload_isolation_report is not None:
        payload = _dict(payload_isolation_report)
        payload_blockers = payload_isolation_report_blockers(payload)
        payload_blockers.extend(_external_report_commit_blockers("payload_isolation", payload, expected_git_commits))
        if payload_blockers:
            blockers.extend(f"payload_isolation_report:{item}" for item in payload_blockers)
        else:
            proven_gates.append("payload_isolation_review")
    if business_wording_freeze_report is not None:
        freeze = _dict(business_wording_freeze_report)
        freeze_blockers = business_wording_freeze_report_blockers(freeze)
        freeze_blockers.extend(_external_report_commit_blockers("business_wording_freeze", freeze, expected_git_commits))
        if freeze_blockers:
            blockers.extend(f"business_wording_freeze_report:{item}" for item in freeze_blockers)
        else:
            proven_gates.append("business_wording_freeze_review")
    if rollback_evidence_report is not None:
        rollback = _dict(rollback_evidence_report)
        rollback_blockers = rollback_evidence_report_blockers(rollback)
        rollback_blockers.extend(_external_report_commit_blockers("rollback_evidence", rollback, expected_git_commits))
        if rollback_blockers:
            blockers.extend(f"rollback_evidence_report:{item}" for item in rollback_blockers)
        else:
            proven_gates.append("rollback_evidence_review")
    if model_semantics_ownership_report is not None:
        ownership = _dict(model_semantics_ownership_report)
        ownership_blockers = model_semantics_ownership_report_blockers(ownership)
        ownership_blockers.extend(
            _external_report_commit_blockers("model_semantics_ownership", ownership, expected_git_commits)
        )
        if ownership_blockers:
            blockers.extend(f"model_semantics_ownership_report:{item}" for item in ownership_blockers)
        else:
            proven_gates.append("model_semantics_ownership_review")
    return {
        "proven_gates": proven_gates,
        "blockers": blockers,
    }


def _external_report_commit_blockers(
    label: str,
    report: dict[str, Any],
    expected_git_commits: list[str],
) -> list[str]:
    if len(expected_git_commits) != 1:
        suffix = ",".join(expected_git_commits) if expected_git_commits else "missing"
        return [f"{label}_bundle_git_commit_set_not_single:{suffix}"]
    expected = expected_git_commits[0]
    report_commit = str(report.get("git_commit") or "").strip()
    if report_commit and report_commit != expected:
        return [f"{label}_git_commit_mismatch:{report_commit}!={expected}"]
    return []


def _only_proven_external_gate_blockers(group: dict[str, Any], proven_external_gates: set[str]) -> bool:
    original = _list_strings(group.get("blockers"))
    if not original:
        return False
    return all(item in {f"gate_not_proven:{gate_id}" for gate_id in proven_external_gates} for item in original)


def _tool_plan_migration_blockers(state: dict[str, Any]) -> list[str]:
    tool_plan = _dict(state.get("tool_plan_preview"))
    audit = _dict(tool_plan.get("migration_audit"))
    if audit.get("schema_version") != "tool_planner_migration_audit_v1":
        return ["tool_plan_preview_missing_migration_audit"]
    blockers: list[str] = []
    if audit.get("tool_planner_only_ready") is not True:
        blockers.append("tool_plan_preview_not_tool_planner_only")
    residue_count = _int_value(audit.get("legacy_residue_count"))
    if residue_count > 0:
        blockers.append(f"tool_plan_preview_legacy_residue:{residue_count}")
    if audit.get("review_required_before_migration") is True:
        blockers.append("tool_plan_preview_requires_migration_review")
    return blockers


def _reply_handoff_migration_blockers(state: dict[str, Any]) -> list[str]:
    handoff = _dict(state.get("reply_final_brain_handoff_shadow"))
    if handoff.get("schema_version") != "reply_final_brain_handoff_shadow_v1":
        return []
    audit = _dict(handoff.get("migration_audit"))
    if not audit:
        return ["reply_handoff_missing_migration_audit"]
    blockers: list[str] = []
    residue_count = _int_value(audit.get("legacy_business_field_count"))
    if residue_count > 0:
        blockers.append(f"reply_handoff_legacy_business_field_residue:{residue_count}")
    mapping_audit = _dict(audit.get("field_mapping_audit"))
    if mapping_audit.get("schema_version") != "reply_legacy_field_mapping_audit_v1":
        blockers.append("reply_handoff_missing_legacy_field_mapping_audit")
    elif mapping_audit.get("all_legacy_business_fields_mapped") is not True:
        unmapped = _list_strings(mapping_audit.get("unmapped_legacy_business_fields"))
        if unmapped:
            blockers.extend(f"reply_handoff_unmapped_legacy_business_field:{item}" for item in unmapped)
        else:
            blockers.append("reply_handoff_legacy_business_fields_not_mapped")
    return blockers


def _review_gates(state: dict[str, Any], *, require_commit_shadow: bool) -> dict[str, dict[str, Any]]:
    parallel_shadow = _dict(state.get("parallel_reply_chain_shadow"))
    observation = _dict(parallel_shadow.get("current_serial_observation"))
    tool_plan_migration = _dict(_dict(state.get("tool_plan_preview")).get("migration_audit"))
    reply_handoff_migration = _dict(_dict(state.get("reply_final_brain_handoff_shadow")).get("migration_audit"))
    reply_handoff_mapping = _dict(reply_handoff_migration.get("field_mapping_audit"))
    runner = _dict(state.get("parallel_gate_planner_runner_shadow"))
    comparison = _dict(state.get("parallel_reply_chain_comparison"))
    diagnostics = _dict(state.get("parallel_reply_chain_diagnostics"))
    commit = _dict(state.get("reply_chain_commit_shadow"))
    return {
        "shared_context_authority_ready": {
            "passed": (
                observation.get("shared_context_authority_audit_schema") == "reply_chain_authority_audit_v1"
                and observation.get("shared_context_timeline_window_ready") is True
                and observation.get("shared_context_current_message_ready") is True
                and observation.get("shared_context_fact_snapshot_schema") == "reply_chain_fact_snapshot_audit_v1"
            ),
            "purpose": "complete_timed_chat_and_authoritative_facts_are_available",
        },
        "gate_is_shadow_only": {
            "passed": (
                observation.get("gate_commit_boundary_schema") == "chat_gate_commit_boundary_v1"
                and observation.get("gate_shadow_creates_sop_task") is False
                and observation.get("gate_shadow_updates_send_once") is False
                and observation.get("gate_shadow_sends_customer_messages") is False
                and observation.get("gate_shadow_writes_database") is False
            ),
            "purpose": "sop_chat_gate_cannot_commit_or_send_in_shadow",
        },
        "tool_planner_is_tool_only": {
            "passed": (
                observation.get("tool_planner_only_ready") is True
                and tool_plan_migration.get("schema_version") == "tool_planner_migration_audit_v1"
                and tool_plan_migration.get("tool_planner_only_ready") is True
                and _int_value(tool_plan_migration.get("legacy_residue_count")) == 0
                and tool_plan_migration.get("review_required_before_migration") is not True
            ),
            "purpose": "tool_planner_does_not_own_customer_visible_sales_semantics",
        },
        "join_keeps_reply_as_final_owner": {
            "passed": (
                observation.get("join_final_expression_boundary_schema") == "reply_final_expression_boundary_v1"
                and observation.get("join_final_customer_message_owner") == "reply"
                and observation.get("join_generates_customer_visible_text") is False
                and observation.get("join_decides_sales_psychology") is False
            ),
            "purpose": "join_does_not_become_business_brain",
        },
        "direct_reply_guard_review": {
            "passed": (
                observation.get("direct_reply_allowed") is not True
                or (
                    observation.get("direct_reply_guard_schema") == "reply_chain_direct_reply_guard_audit_v1"
                    and observation.get("direct_reply_guard_ready") is True
                )
            ),
            "purpose": "gate_direct_reply_exception_is_explicitly_guarded",
        },
        "reply_handoff_ready": {
            "passed": (observation.get("reply_handoff_ready_for_payload_switch_shadow") is True),
            "purpose": "reply_can_receive_gate_and_tool_facts_without_losing_context",
        },
        "reply_handoff_has_no_legacy_business_residue": {
            "passed": (
                bool(reply_handoff_migration)
                and _int_value(reply_handoff_migration.get("legacy_business_field_count")) == 0
                and reply_handoff_mapping.get("schema_version") == "reply_legacy_field_mapping_audit_v1"
                and reply_handoff_mapping.get("all_legacy_business_fields_mapped") is True
            ),
            "purpose": "legacy_planner_business_semantics_are_not_carried_into_reply_switch_inputs",
        },
        "runner_contract_ready": {
            "passed": _dict(runner.get("branch_output_contract_audit")).get("ready") is True,
            "purpose": "parallel_runner_outputs_match_required_branch_contracts",
        },
        "comparison_matched": {
            "passed": comparison.get("status") == "matched_shadow_replay",
            "purpose": "serial_and_parallel_shadow_outputs_have_no_known_diffs",
        },
        "diagnostics_ready": {
            "passed": diagnostics.get("phase") == ("ready_for_human_review" if require_commit_shadow else "ready_for_shadow_comparison")
            or (not require_commit_shadow and diagnostics.get("phase") == "ready_for_human_review"),
            "purpose": "diagnostics_have_reached_the_expected_review_stage",
        },
        "commit_phase_ready": {
            "passed": (
                not require_commit_shadow
                or (
                    commit.get("commit_phase_owner") == "runtime_after_reply_validation"
                    and _dict(commit.get("precommit_validation_audit")).get("ready_for_commit_shadow") is True
                    and _dict(commit.get("write_action_inventory")).get("schema_version")
                    == "reply_chain_write_action_inventory_v1"
                    and _dict(commit.get("write_action_inventory")).get("ready_for_commit_refactor_review") is True
                    and _dict(commit.get("write_action_inventory")).get("all_runtime_writes_after_reply_validation")
                    is True
                )
            ),
            "purpose": "writes_remain_after_reply_validation",
        },
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _git_commit_set(state: dict[str, Any], component_schemas: dict[str, str]) -> list[str]:
    commits = {str(state.get("git_commit") or "").strip()}
    for field in component_schemas:
        component = state.get(field)
        if isinstance(component, dict):
            commits.add(str(component.get("git_commit") or "").strip())
    return sorted(commit for commit in commits if commit)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
