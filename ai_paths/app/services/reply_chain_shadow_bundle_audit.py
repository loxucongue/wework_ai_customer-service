from __future__ import annotations

from typing import Any


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
) -> dict[str, Any]:
    """Summarize shadow migration evidence without changing reply behavior."""

    component_schemas = dict(CORE_COMPONENT_SCHEMAS)
    if require_commit_shadow:
        component_schemas.update(POSTCOMMIT_COMPONENT_SCHEMAS)
    components = {
        field: _component_status(state.get(field), required_schema)
        for field, required_schema in component_schemas.items()
    }
    blockers = _component_blockers(components)
    blockers.extend(_cross_component_blockers(state, require_commit_shadow=require_commit_shadow))
    gates = _review_gates(state, require_commit_shadow=require_commit_shadow)
    blockers.extend(
        f"review_gate_not_ready:{gate_id}"
        for gate_id, gate in gates.items()
        if gate.get("passed") is False
    )
    return _drop_empty(
        {
            "schema_version": "reply_chain_shadow_bundle_audit_v1",
            "phase": "postcommit" if require_commit_shadow else "precommit",
            "ready_for_refactor_review": not blockers,
            "blockers": blockers,
            "components": components,
            "review_gates": gates,
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


def _cross_component_blockers(state: dict[str, Any], *, require_commit_shadow: bool) -> list[str]:
    blockers: list[str] = []
    parallel_shadow = _dict(state.get("parallel_reply_chain_shadow"))
    if _list_strings((parallel_shadow.get("activation") or {}).get("blockers")):
        blockers.append("parallel_contract_has_blockers")
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
    blockers.extend(_diagnostic_release_review_blockers(diagnostics))
    return blockers


def _diagnostic_release_review_blockers(diagnostics: dict[str, Any]) -> list[str]:
    release_review = _dict(diagnostics.get("release_review"))
    if release_review.get("schema_version") != "reply_chain_release_review_checklist_v1":
        return []
    blockers: list[str] = []
    if release_review.get("can_enable_behavior_switch") is not False:
        blockers.append("release_review_missing_non_approval_marker")
    blockers.extend(_diagnostic_release_review_group_blockers(release_review))
    return blockers


def _diagnostic_release_review_group_blockers(release_review: dict[str, Any]) -> list[str]:
    blocker_groups = release_review.get("blocker_groups")
    if not isinstance(blocker_groups, dict):
        return []
    blockers: list[str] = []
    for group_name, group in blocker_groups.items():
        if not isinstance(group, dict):
            blockers.append(f"release_review_blocker_group_invalid:{group_name}")
            continue
        group_blockers = _list_strings(group.get("blockers"))
        blocker_count = _int_value(group.get("blocker_count"))
        if group.get("ready") is False or group_blockers or blocker_count > 0:
            blockers.append(f"release_review_blocker_group_unresolved:{group_name}")
            blockers.extend(f"release_review_blocker_group:{group_name}:{item}" for item in group_blockers)
    return blockers


def _review_gates(state: dict[str, Any], *, require_commit_shadow: bool) -> dict[str, dict[str, Any]]:
    parallel_shadow = _dict(state.get("parallel_reply_chain_shadow"))
    observation = _dict(parallel_shadow.get("current_serial_observation"))
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
            "passed": (observation.get("tool_planner_only_ready") is True),
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
