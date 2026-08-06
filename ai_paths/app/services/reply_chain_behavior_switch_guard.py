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


CORE_ACTIVE_FLAGS = (
    "parallel_gate_planner_enabled",
    "sop_chat_gate_v2_enabled",
    "tool_planner_v2_enabled",
    "reply_final_brain_v2_enabled",
)

OPTIONAL_ACTIVE_FLAGS = (
    "gate_direct_reply_enabled",
    "read_tool_early_execution_enabled",
    "deferred_write_execution_enabled",
)

REQUIRED_HUMAN_REVIEW_EVIDENCE = (
    "shadow_bundle_audit",
    "diagnostics",
    "simulation_report",
    "model_matrix_report",
    "payload_isolation_report",
    "business_wording_freeze_report",
    "rollback_evidence_report",
    "model_semantics_ownership_report",
)


def reply_chain_behavior_switch_guard(
    *,
    flag_snapshot: dict[str, Any],
    shadow_bundle_audit: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    simulation_report: dict[str, Any] | None = None,
    model_matrix_report: dict[str, Any] | None = None,
    payload_isolation_report: dict[str, Any] | None = None,
    business_wording_freeze_report: dict[str, Any] | None = None,
    rollback_evidence_report: dict[str, Any] | None = None,
    model_semantics_ownership_report: dict[str, Any] | None = None,
    human_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate behavior-switch approval without changing runtime behavior."""

    flags = _dict(flag_snapshot.get("flags"))
    shadow = _dict(shadow_bundle_audit)
    diag = _dict(diagnostics)
    simulation = _dict(simulation_report)
    model_matrix = _dict(model_matrix_report)
    payload_isolation = _dict(payload_isolation_report)
    business_wording_freeze = _dict(business_wording_freeze_report)
    rollback_evidence = _dict(rollback_evidence_report)
    model_semantics_ownership = _dict(model_semantics_ownership_report)
    review = _dict(human_review)
    switch_requested = _behavior_switch_requested(flags)
    simulation_blockers = simulation_report_blockers(simulation)
    model_matrix_blockers = model_matrix_report_blockers(model_matrix)
    payload_isolation_blockers = payload_isolation_report_blockers(payload_isolation)
    business_wording_freeze_blockers = business_wording_freeze_report_blockers(business_wording_freeze)
    rollback_evidence_blockers = rollback_evidence_report_blockers(rollback_evidence)
    model_semantics_ownership_blockers = model_semantics_ownership_report_blockers(model_semantics_ownership)
    proven_external_gates: set[str] = set()
    if not simulation_blockers:
        proven_external_gates.add("simulation_regression_review")
    if not model_matrix_blockers:
        proven_external_gates.add("model_matrix_review")
    if not payload_isolation_blockers:
        proven_external_gates.add("payload_isolation_review")
    if not business_wording_freeze_blockers:
        proven_external_gates.add("business_wording_freeze_review")
    if not rollback_evidence_blockers:
        proven_external_gates.add("rollback_evidence_review")
    if not model_semantics_ownership_blockers:
        proven_external_gates.add("model_semantics_ownership_review")

    blockers: list[str] = []
    if not switch_requested:
        blockers.append("behavior_switch_not_requested")
    blockers.extend(_flag_blockers(flag_snapshot, flags))
    blockers.extend(_shadow_bundle_blockers(shadow))
    blockers.extend(_diagnostic_blockers(diag, proven_external_gates=proven_external_gates))
    blockers.extend(simulation_blockers)
    blockers.extend(model_matrix_blockers)
    blockers.extend(payload_isolation_blockers)
    blockers.extend(business_wording_freeze_blockers)
    blockers.extend(rollback_evidence_blockers)
    blockers.extend(model_semantics_ownership_blockers)
    blockers.extend(
        _human_review_blockers(
            review,
            shadow_bundle_audit=shadow,
            diagnostics=diag,
            simulation_report=simulation,
            model_matrix_report=model_matrix,
            payload_isolation_report=payload_isolation,
            business_wording_freeze_report=business_wording_freeze,
            rollback_evidence_report=rollback_evidence,
            model_semantics_ownership_report=model_semantics_ownership,
        )
    )

    return _drop_empty(
        {
            "schema_version": "reply_chain_behavior_switch_guard_v1",
            "behavior_switch_requested": switch_requested,
            "can_enable_behavior_switch": not blockers,
            "blockers": blockers,
            "diagnostic_blocker_groups": _diagnostic_blocker_groups(diag),
            "effective_diagnostic_blocker_groups": _diagnostic_blocker_groups(
                diag,
                proven_external_gates=proven_external_gates,
            ),
            "required_evidence": {
                "flags": list(CORE_ACTIVE_FLAGS),
                "shadow_bundle_audit": "reply_chain_shadow_bundle_audit_v1 ready_for_refactor_review=true",
                "diagnostics": "parallel_reply_chain_diagnostics_v1 phase=ready_for_human_review",
                "simulation_report": (
                    "offline full-chain simulation with zero hard errors, complete required scenario coverage, "
                    "complete semantic review for every attempt, and required pass rate"
                ),
                "model_matrix_report": "three-model relay matrix with accuracy and latency summary",
                "payload_isolation_report": (
                    "reply_chain_payload_isolation_audit_v1 proving shadow-only diagnostics do not "
                    "enter active Planner, Reply, or SOP Chat Gate model payloads"
                ),
                "business_wording_freeze_report": (
                    "reply_chain_business_wording_freeze_audit_v1 proving protected customer-visible "
                    "business assets were not changed by the structural refactor"
                ),
                "rollback_evidence_report": (
                    "reply_chain_refactor_rollback_evidence_v1 proving this stage is on the refactor "
                    "branch, has no deployment-sensitive path changes, and has rollback steps"
                ),
                "model_semantics_ownership_report": (
                    "reply_chain_model_semantics_ownership_audit_v1 proving Tool Planner, Join, "
                    "and code do not own customer psychology, objections, sales rhythm, or final wording"
                ),
                "human_review": (
                    "explicit reviewer approval for this branch, commit, scope, rollback plan, "
                    "and reviewed evidence list"
                ),
            },
            "safety": {
                "guard_only": True,
                "does_not_enable_flags": True,
                "does_not_change_runtime_behavior": True,
                "does_not_send_customer_messages": True,
                "does_not_write_database": True,
            },
            "source": "reply_chain_behavior_switch_guard",
        }
    )


def _behavior_switch_requested(flags: dict[str, Any]) -> bool:
    return any(bool(flags.get(name)) for name in (*CORE_ACTIVE_FLAGS, *OPTIONAL_ACTIVE_FLAGS))


def _flag_blockers(flag_snapshot: dict[str, Any], flags: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if flag_snapshot.get("schema_version") != "reply_chain_refactor_flags_v1":
        blockers.append("invalid_or_missing_flag_snapshot")
        return blockers
    blockers.extend(f"flag_snapshot:{item}" for item in _list_strings(flag_snapshot.get("activation_blockers")))
    for name in CORE_ACTIVE_FLAGS:
        if flags.get(name) is not True:
            blockers.append(f"required_active_flag_missing:{name}")
    if flags.get("parallel_gate_planner_shadow") is True and flags.get("deferred_write_execution_enabled") is True:
        blockers.append("deferred_writes_forbidden_while_shadow_enabled")
    return blockers


def _shadow_bundle_blockers(shadow: dict[str, Any]) -> list[str]:
    if shadow.get("schema_version") != "reply_chain_shadow_bundle_audit_v1":
        return ["missing_reply_chain_shadow_bundle_audit"]
    blockers: list[str] = []
    if shadow.get("ready_for_refactor_review") is not True:
        blockers.append("shadow_bundle_not_ready_for_refactor_review")
    if shadow.get("phase") != "postcommit":
        blockers.append(f"shadow_bundle_phase_not_postcommit:{shadow.get('phase') or 'missing'}")
    safety = _dict(shadow.get("safety"))
    if safety.get("does_not_approve_behavior_switch") is not True:
        blockers.append("shadow_bundle_missing_non_approval_safety_marker")
    blockers.extend(_shadow_bundle_commit_phase_evidence_blockers(shadow))
    blockers.extend(f"shadow_bundle:{item}" for item in _list_strings(shadow.get("blockers")))
    return blockers


def _shadow_bundle_commit_phase_evidence_blockers(shadow: dict[str, Any]) -> list[str]:
    components = _dict(shadow.get("components"))
    commit_component = _dict(components.get("reply_chain_commit_shadow"))
    review_gates = _dict(shadow.get("review_gates"))
    commit_gate = _dict(review_gates.get("commit_phase_ready"))
    blockers: list[str] = []
    if commit_component.get("valid") is not True:
        blockers.append("shadow_bundle_commit_component_not_valid")
    if commit_gate.get("passed") is not True:
        blockers.append("shadow_bundle_commit_phase_gate_not_passed")
    return blockers


def _diagnostic_blockers(diagnostics: dict[str, Any], *, proven_external_gates: set[str] | None = None) -> list[str]:
    proven_external_gates = proven_external_gates or set()
    if diagnostics.get("schema_version") != "parallel_reply_chain_diagnostics_v1":
        return ["missing_parallel_reply_chain_diagnostics"]
    blockers: list[str] = []
    if diagnostics.get("phase") != "ready_for_human_review":
        blockers.append(f"diagnostics_not_ready_for_human_review:{diagnostics.get('phase') or 'missing'}")
    release_review = _dict(diagnostics.get("release_review"))
    if release_review.get("schema_version") != "reply_chain_release_review_checklist_v1":
        blockers.append("missing_release_review_checklist")
    else:
        if release_review.get("can_enable_behavior_switch") is not False:
            blockers.append("release_review_missing_non_approval_marker")
        unproven = [
            item
            for item in _list_strings(release_review.get("missing_or_unproven_gates"))
            if item not in proven_external_gates
        ]
        if unproven:
            blockers.extend(f"release_review_gate_unproven:{item}" for item in unproven)
        group_blockers = _release_review_group_blockers(
            release_review,
            proven_external_gates=proven_external_gates,
        )
        blockers.extend(group_blockers)
        if not unproven and not group_blockers:
            blockers.extend(_release_review_final_authority_evidence_blockers(release_review))
    return blockers


def _release_review_final_authority_evidence_blockers(release_review: dict[str, Any]) -> list[str]:
    gates = release_review.get("gates")
    if not isinstance(gates, list):
        return ["release_review_missing_authority_gate_evidence"]
    authority_gate = next(
        (
            gate
            for gate in gates
            if isinstance(gate, dict) and gate.get("gate_id") == "authority_snapshot_review"
        ),
        {},
    )
    if not authority_gate:
        return ["release_review_missing_authority_gate_evidence"]
    blockers: list[str] = []
    if authority_gate.get("passed") is not True:
        blockers.append("release_review_authority_gate_not_passed")
    observed = _dict(authority_gate.get("evidence_observed"))
    if observed.get("shared_context_timeline_retained_window_schema") != "reply_chain_retained_timeline_window_v1":
        blockers.append("release_review_missing_retained_timeline_evidence")
    if observed.get("shared_context_soft_profile_excluded") is not True:
        blockers.append("release_review_missing_soft_profile_exclusion_evidence")
    if not _has_non_authority_profile_inventory(observed):
        blockers.append("release_review_missing_non_authority_profile_inventory")
    return blockers


def _has_non_authority_profile_inventory(observed: dict[str, Any]) -> bool:
    fields = observed.get("shared_context_non_authority_profile_fields")
    if not isinstance(fields, list):
        return False
    required = {"next_sales_strategy", "intent_level", "customer_type"}
    return required.issubset({str(item) for item in fields})


def _release_review_group_blockers(
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


def _diagnostic_blocker_groups(
    diagnostics: dict[str, Any],
    *,
    proven_external_gates: set[str] | None = None,
) -> dict[str, Any]:
    proven_external_gates = proven_external_gates or set()
    if diagnostics.get("schema_version") != "parallel_reply_chain_diagnostics_v1":
        return {}
    release_review = _dict(diagnostics.get("release_review"))
    if release_review.get("schema_version") != "reply_chain_release_review_checklist_v1":
        return {}
    blocker_groups = release_review.get("blocker_groups")
    if not isinstance(blocker_groups, dict):
        return {}
    if not proven_external_gates:
        return blocker_groups
    return {
        str(group_name): _effective_blocker_group(_dict(group), proven_external_gates)
        for group_name, group in blocker_groups.items()
        if isinstance(group, dict)
    }


def _effective_blocker_group(group: dict[str, Any], proven_external_gates: set[str]) -> dict[str, Any]:
    proven_gate_blockers = {f"gate_not_proven:{gate_id}" for gate_id in proven_external_gates}
    blockers = [item for item in _list_strings(group.get("blockers")) if item not in proven_gate_blockers]
    result: dict[str, Any] = {
        "ready": not blockers,
        "blocker_count": len(blockers),
    }
    if blockers:
        result["blockers"] = blockers
    return result


def _only_proven_external_gate_blockers(group: dict[str, Any], proven_external_gates: set[str]) -> bool:
    original = _list_strings(group.get("blockers"))
    if not original:
        return False
    return all(item in {f"gate_not_proven:{gate_id}" for gate_id in proven_external_gates} for item in original)


def _human_review_blockers(
    review: dict[str, Any],
    *,
    shadow_bundle_audit: dict[str, Any],
    diagnostics: dict[str, Any],
    simulation_report: dict[str, Any],
    model_matrix_report: dict[str, Any],
    payload_isolation_report: dict[str, Any],
    business_wording_freeze_report: dict[str, Any],
    rollback_evidence_report: dict[str, Any],
    model_semantics_ownership_report: dict[str, Any],
) -> list[str]:
    if review.get("schema_version") != "reply_chain_human_review_approval_v1":
        return ["missing_human_review_approval"]
    blockers: list[str] = []
    if review.get("approved") is not True:
        blockers.append("human_review_not_approved")
    if review.get("branch") != "codex/reply-chain-refactor":
        blockers.append(f"human_review_wrong_branch:{review.get('branch') or 'missing'}")
    if not isinstance(review.get("commit_sha"), str) or not review.get("commit_sha"):
        blockers.append("human_review_missing_commit_sha")
    else:
        blockers.extend(
            _review_commit_match_blockers(
                str(review.get("commit_sha") or "").strip(),
                shadow_bundle_audit=shadow_bundle_audit,
                diagnostics=diagnostics,
                simulation_report=simulation_report,
                model_matrix_report=model_matrix_report,
                payload_isolation_report=payload_isolation_report,
                business_wording_freeze_report=business_wording_freeze_report,
                rollback_evidence_report=rollback_evidence_report,
                model_semantics_ownership_report=model_semantics_ownership_report,
            )
        )
    if review.get("scope") != "parallel_gate_planner_behavior_switch":
        blockers.append(f"human_review_wrong_scope:{review.get('scope') or 'missing'}")
    blockers.extend(_reviewed_evidence_blockers(review))
    blockers.extend(_rollback_plan_blockers(_dict(review.get("rollback_plan"))))
    return blockers


def _reviewed_evidence_blockers(review: dict[str, Any]) -> list[str]:
    reviewed = set(_list_strings(review.get("reviewed_evidence")))
    if not reviewed:
        return ["human_review_missing_reviewed_evidence"]
    blockers: list[str] = []
    for item in REQUIRED_HUMAN_REVIEW_EVIDENCE:
        if item not in reviewed:
            blockers.append(f"human_review_missing_reviewed_evidence:{item}")
    extra = sorted(reviewed.difference(REQUIRED_HUMAN_REVIEW_EVIDENCE))
    blockers.extend(f"human_review_unrecognized_reviewed_evidence:{item}" for item in extra)
    return blockers


def _review_commit_match_blockers(
    commit_sha: str,
    *,
    shadow_bundle_audit: dict[str, Any],
    diagnostics: dict[str, Any],
    simulation_report: dict[str, Any],
    model_matrix_report: dict[str, Any],
    payload_isolation_report: dict[str, Any],
    business_wording_freeze_report: dict[str, Any],
    rollback_evidence_report: dict[str, Any],
    model_semantics_ownership_report: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    simulation_commit = str(simulation_report.get("git_commit") or "").strip()
    model_matrix_commit = str(model_matrix_report.get("git_commit") or "").strip()
    payload_isolation_commit = str(payload_isolation_report.get("git_commit") or "").strip()
    business_wording_freeze_commit = str(business_wording_freeze_report.get("git_commit") or "").strip()
    rollback_evidence_commit = str(rollback_evidence_report.get("git_commit") or "").strip()
    model_semantics_ownership_commit = str(model_semantics_ownership_report.get("git_commit") or "").strip()
    blockers.extend(_commit_evidence_blockers("shadow_bundle", shadow_bundle_audit, commit_sha))
    blockers.extend(_commit_evidence_blockers("diagnostics", diagnostics, commit_sha))
    if simulation_commit and simulation_commit != commit_sha:
        blockers.append(f"human_review_commit_mismatch:simulation:{simulation_commit}")
    if model_matrix_commit and model_matrix_commit != commit_sha:
        blockers.append(f"human_review_commit_mismatch:model_matrix:{model_matrix_commit}")
    if payload_isolation_commit and payload_isolation_commit != commit_sha:
        blockers.append(f"human_review_commit_mismatch:payload_isolation:{payload_isolation_commit}")
    if business_wording_freeze_commit and business_wording_freeze_commit != commit_sha:
        blockers.append(f"human_review_commit_mismatch:business_wording_freeze:{business_wording_freeze_commit}")
    if rollback_evidence_commit and rollback_evidence_commit != commit_sha:
        blockers.append(f"human_review_commit_mismatch:rollback_evidence:{rollback_evidence_commit}")
    if model_semantics_ownership_commit and model_semantics_ownership_commit != commit_sha:
        blockers.append(f"human_review_commit_mismatch:model_semantics_ownership:{model_semantics_ownership_commit}")
    return blockers


def _commit_evidence_blockers(label: str, evidence: dict[str, Any], commit_sha: str) -> list[str]:
    blockers: list[str] = []
    commit = str(evidence.get("git_commit") or "").strip()
    commit_set = _list_strings(evidence.get("git_commit_set"))
    if not commit:
        blockers.append(f"human_review_missing_commit_evidence:{label}")
    elif commit != commit_sha:
        blockers.append(f"human_review_commit_mismatch:{label}:{commit}")
    if not commit_set:
        blockers.append(f"human_review_missing_commit_set_evidence:{label}")
    elif commit_set != [commit_sha]:
        blockers.append(f"human_review_commit_set_mismatch:{label}:{','.join(commit_set)}")
    return blockers


def _rollback_plan_blockers(plan: dict[str, Any]) -> list[str]:
    if plan.get("schema_version") != "reply_chain_behavior_switch_rollback_plan_v1":
        return ["missing_behavior_switch_rollback_plan"]
    blockers: list[str] = []
    if plan.get("reviewed") is not True:
        blockers.append("rollback_plan_not_reviewed")
    if plan.get("restore_flags_to_shadow_or_disabled") is not True:
        blockers.append("rollback_plan_missing_flag_restore")
    if plan.get("no_deployment_from_refactor_branch") is not True:
        blockers.append("rollback_plan_missing_no_refactor_deploy")
    if not _list_strings(plan.get("rollback_steps")):
        blockers.append("rollback_plan_missing_steps")
    if not isinstance(plan.get("owner"), str) or not plan.get("owner").strip():
        blockers.append("rollback_plan_missing_owner")
    return blockers


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
