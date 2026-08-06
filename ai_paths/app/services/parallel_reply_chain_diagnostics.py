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
            },
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
            return "tool_planner_migration_blocked"
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
        "tool_planner_migration_blocked": "move_legacy_planner_semantics_to_reply_before_behavior_switch",
        "ready_for_human_review": "run_review_gates_and_offline_simulation_before_behavior_switch",
    }.get(phase, "inspect_parallel_refactor_diagnostics")


def _runner_blockers(runner: dict[str, Any]) -> list[str]:
    blockers = _list_strings(runner.get("activation_blockers"))
    blockers.extend(_runner_input_isolation_blockers(runner))
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
    forbidden_owners = commit.get("must_not_be_owned_by")
    if isinstance(forbidden_owners, list):
        required_forbidden = {"sop_chat_gate", "tool_planner", "reply_chain_join"}
        missing = sorted(required_forbidden.difference({str(item) for item in forbidden_owners}))
        blockers.extend([f"commit_forbidden_owner_missing:{item}" for item in missing])
    else:
        blockers.append("commit_missing_forbidden_owners")
    return blockers


def _migration_blockers(parallel_reply_chain_shadow: dict[str, Any]) -> list[str]:
    residue_count = _tool_planner_legacy_residue_count(parallel_reply_chain_shadow)
    if residue_count <= 0:
        return []
    return [f"tool_planner_legacy_semantic_residue:{residue_count}"]


def _tool_planner_legacy_residue_count(parallel_reply_chain_shadow: dict[str, Any]) -> int:
    observation = parallel_reply_chain_shadow.get("current_serial_observation")
    if not isinstance(observation, dict):
        return 0
    try:
        return int(observation.get("tool_planner_legacy_residue_count") or 0)
    except (TypeError, ValueError):
        return 0


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
