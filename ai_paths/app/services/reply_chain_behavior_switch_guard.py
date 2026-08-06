from __future__ import annotations

from typing import Any


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


def reply_chain_behavior_switch_guard(
    *,
    flag_snapshot: dict[str, Any],
    shadow_bundle_audit: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    simulation_report: dict[str, Any] | None = None,
    model_matrix_report: dict[str, Any] | None = None,
    human_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate behavior-switch approval without changing runtime behavior."""

    flags = _dict(flag_snapshot.get("flags"))
    shadow = _dict(shadow_bundle_audit)
    diag = _dict(diagnostics)
    simulation = _dict(simulation_report)
    model_matrix = _dict(model_matrix_report)
    review = _dict(human_review)
    switch_requested = _behavior_switch_requested(flags)
    simulation_blockers = _simulation_blockers(simulation)
    model_matrix_blockers = _model_matrix_blockers(model_matrix)
    proven_external_gates: set[str] = set()
    if not simulation_blockers:
        proven_external_gates.add("simulation_regression_review")
    if not model_matrix_blockers:
        proven_external_gates.add("model_matrix_review")

    blockers: list[str] = []
    if not switch_requested:
        blockers.append("behavior_switch_not_requested")
    blockers.extend(_flag_blockers(flag_snapshot, flags))
    blockers.extend(_shadow_bundle_blockers(shadow))
    blockers.extend(_diagnostic_blockers(diag, proven_external_gates=proven_external_gates))
    blockers.extend(simulation_blockers)
    blockers.extend(model_matrix_blockers)
    blockers.extend(_human_review_blockers(review))

    return _drop_empty(
        {
            "schema_version": "reply_chain_behavior_switch_guard_v1",
            "behavior_switch_requested": switch_requested,
            "can_enable_behavior_switch": not blockers,
            "blockers": blockers,
            "diagnostic_blocker_groups": _diagnostic_blocker_groups(diag),
            "required_evidence": {
                "flags": list(CORE_ACTIVE_FLAGS),
                "shadow_bundle_audit": "reply_chain_shadow_bundle_audit_v1 ready_for_refactor_review=true",
                "diagnostics": "parallel_reply_chain_diagnostics_v1 phase=ready_for_human_review",
                "simulation_report": "offline full-chain simulation with zero hard errors and required pass rate",
                "model_matrix_report": "three-model relay matrix with accuracy and latency summary",
                "human_review": "explicit reviewer approval for this branch and commit",
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
    blockers.extend(f"shadow_bundle:{item}" for item in _list_strings(shadow.get("blockers")))
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
        blockers.extend(_release_review_group_blockers(release_review, proven_external_gates=proven_external_gates))
    return blockers


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


def _diagnostic_blocker_groups(diagnostics: dict[str, Any]) -> dict[str, Any]:
    if diagnostics.get("schema_version") != "parallel_reply_chain_diagnostics_v1":
        return {}
    release_review = _dict(diagnostics.get("release_review"))
    if release_review.get("schema_version") != "reply_chain_release_review_checklist_v1":
        return {}
    blocker_groups = release_review.get("blocker_groups")
    return blocker_groups if isinstance(blocker_groups, dict) else {}


def _simulation_blockers(simulation: dict[str, Any]) -> list[str]:
    if simulation.get("schema_version") != "offline_reply_chain_simulation_report_v1":
        return ["missing_offline_simulation_report"]
    blockers: list[str] = []
    if simulation.get("hard_error_count") not in (0, "0"):
        blockers.append(f"simulation_hard_errors:{simulation.get('hard_error_count')}")
    try:
        pass_rate = float(simulation.get("semantic_pass_rate") or 0.0)
    except (TypeError, ValueError):
        pass_rate = 0.0
    if pass_rate < 0.9:
        blockers.append(f"simulation_semantic_pass_rate_below_90:{pass_rate:.3f}")
    failed_critical = _list_strings(simulation.get("failed_critical_scenarios"))
    if failed_critical:
        blockers.extend(f"simulation_critical_failed:{item}" for item in failed_critical)
    return blockers


def _model_matrix_blockers(model_matrix: dict[str, Any]) -> list[str]:
    if model_matrix.get("schema_version") != "reply_chain_refactor_model_matrix_v1":
        return ["missing_model_matrix_report"]
    blockers: list[str] = []
    requested = set(_list_strings(model_matrix.get("profiles_requested")))
    required = {"claude", "gemini", "openai"}
    missing = sorted(required - requested)
    if missing:
        blockers.extend(f"model_matrix_missing_requested_profile:{item}" for item in missing)
    profiles = model_matrix.get("profiles") if isinstance(model_matrix.get("profiles"), list) else []
    completed_names = {
        str((_dict(item.get("model_profile")).get("name") or "")).strip()
        for item in profiles
        if isinstance(item, dict) and item.get("status") == "completed"
    }
    missing_completed = sorted(required - completed_names)
    if missing_completed:
        blockers.extend(f"model_matrix_profile_not_completed:{item}" for item in missing_completed)
    accepted = False
    for item in profiles:
        if not isinstance(item, dict) or item.get("status") != "completed":
            continue
        summary = _dict(item.get("profile_summary"))
        if not _has_number(summary.get("semantic_pass_rate")):
            blockers.append(f"model_matrix_missing_semantic_pass_rate:{_profile_name(item)}")
        if not _has_number(summary.get("p50_ms")):
            blockers.append(f"model_matrix_missing_p50:{_profile_name(item)}")
        if not _has_number(summary.get("p90_ms")):
            blockers.append(f"model_matrix_missing_p90:{_profile_name(item)}")
        accepted = accepted or summary.get("accepted_by_release_thresholds") is True
    if not accepted:
        blockers.append("model_matrix_no_candidate_meets_release_thresholds")
    safety = _dict(model_matrix.get("safety"))
    if safety.get("api_keys_written_to_report") is not False:
        blockers.append("model_matrix_missing_key_redaction_safety")
    if safety.get("production_customer_messages_sent") is not False:
        blockers.append("model_matrix_missing_no_send_safety")
    if safety.get("production_writes_allowed") is not False:
        blockers.append("model_matrix_missing_no_write_safety")
    return blockers


def _profile_name(item: dict[str, Any]) -> str:
    return str(_dict(item.get("model_profile")).get("name") or "unknown")


def _has_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _only_proven_external_gate_blockers(group: dict[str, Any], proven_external_gates: set[str]) -> bool:
    original = _list_strings(group.get("blockers"))
    if not original:
        return False
    return all(item in {f"gate_not_proven:{gate_id}" for gate_id in proven_external_gates} for item in original)


def _human_review_blockers(review: dict[str, Any]) -> list[str]:
    if review.get("schema_version") != "reply_chain_human_review_approval_v1":
        return ["missing_human_review_approval"]
    blockers: list[str] = []
    if review.get("approved") is not True:
        blockers.append("human_review_not_approved")
    if review.get("branch") != "codex/reply-chain-refactor":
        blockers.append(f"human_review_wrong_branch:{review.get('branch') or 'missing'}")
    if not isinstance(review.get("commit_sha"), str) or not review.get("commit_sha"):
        blockers.append("human_review_missing_commit_sha")
    if review.get("scope") != "parallel_gate_planner_behavior_switch":
        blockers.append(f"human_review_wrong_scope:{review.get('scope') or 'missing'}")
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
