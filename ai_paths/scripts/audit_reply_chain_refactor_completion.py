from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_paths.scripts.audit_model_semantics_ownership import audit_model_semantics_ownership
from app.services.parallel_reply_chain_runner import run_parallel_gate_planner_shadow
from app.services.parallel_reply_chain_shadow import parallel_reply_chain_shadow
from app.services.read_only_tool_executor_shadow import read_only_tool_executor_shadow_from_plan
from app.services.reply_chain_behavior_switch_guard import reply_chain_behavior_switch_guard
from app.services.reply_chain_commit_shadow import reply_chain_commit_shadow
from app.services.reply_chain_refactor_flags import reply_chain_refactor_flag_snapshot
from app.services.reply_chain_shadow_bundle_audit import reply_chain_shadow_bundle_audit
from app.services.reply_chain_join_shadow import reply_chain_join_shadow
from app.services.reply_final_brain_handoff import reply_final_brain_handoff_shadow_from_planner_output
from app.services.tool_plan_preview import tool_plan_preview_from_planner_output


def audit_reply_chain_refactor_completion(*, repo_root: Path, head_ref: str = "HEAD") -> dict[str, Any]:
    """Audit whether the refactor branch has the target shadow architecture in place.

    This is intentionally read-only. It does not call models, execute platform
    tools, send messages, write state, or approve behavior switching.
    """

    commit = _git_output(repo_root, ["git", "rev-parse", head_ref])
    branch = _git_output(repo_root, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    flag_snapshot = reply_chain_refactor_flag_snapshot(settings=None)
    context = _reply_chain_shadow_context(commit=commit)
    gate = _gate_router_shadow(commit=commit)
    tool_plan = tool_plan_preview_from_planner_output(
        {
            "planner_tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "arguments": {"query": "sim_location"},
                    "purpose": "fetch visible store facts",
                },
                {
                    "name": "kb_search",
                    "arguments": {"query": "sim_case"},
                    "purpose": "fetch real case facts",
                },
            ],
        }
    )
    tool_plan["git_commit"] = commit
    read_executor = read_only_tool_executor_shadow_from_plan(tool_plan)
    read_executor["git_commit"] = commit
    join = reply_chain_join_shadow(gate_router_shadow=gate, tool_plan_preview=tool_plan)
    join["git_commit"] = commit
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        {
            "tool_plan_preview": tool_plan,
            "read_only_tool_executor_shadow": read_executor,
            "reply_chain_join_shadow": join,
        },
        reply_chain_shadow_context=context,
        gate_router_shadow=gate,
    )
    handoff["git_commit"] = commit
    parallel = parallel_reply_chain_shadow(
        reply_chain_shadow_context=context,
        gate_router_shadow=gate,
        tool_plan_preview=tool_plan,
        read_only_tool_executor_shadow=read_executor,
        reply_chain_join_shadow=join,
        reply_final_brain_handoff_shadow=handoff,
        refactor_flags=flag_snapshot,
    )
    parallel["git_commit"] = commit
    runner = _run_parallel_runner_probe(
        context=context,
        gate=gate,
        tool_plan=tool_plan,
        flag_snapshot=flag_snapshot,
    )
    runner["git_commit"] = commit
    comparison = _comparison_shadow(commit=commit, join=join, runner=runner)
    diagnostics = _diagnostics_shadow(commit=commit)
    commit_shadow = reply_chain_commit_shadow(
        final_state={
            "test_isolated": True,
            "reply_source": "completion_audit_shadow",
            "sales_contact_key": "sim_contact",
            "request_context": {"memory_persist_allowed": True},
            "reply_control": {"sync_return": {"type": "reply_messages"}},
            "tool_plan_preview": tool_plan,
        },
        reply_messages=[{"type": "text", "content": "shadow static reply"}],
        allow_empty_reply=False,
    )
    commit_shadow["git_commit"] = commit
    state = {
        "git_commit": commit,
        "reply_chain_shadow_context": context,
        "sop_gate_router_shadow": gate,
        "tool_plan_preview": tool_plan,
        "read_only_tool_executor_shadow": read_executor,
        "reply_chain_join_shadow": join,
        "reply_final_brain_handoff_shadow": handoff,
        "parallel_reply_chain_shadow": parallel,
        "parallel_gate_planner_runner_shadow": runner,
        "parallel_reply_chain_comparison": comparison,
        "parallel_reply_chain_diagnostics": diagnostics,
        "reply_chain_commit_shadow": commit_shadow,
    }
    bundle = reply_chain_shadow_bundle_audit(state=state, require_commit_shadow=True)
    ownership = audit_model_semantics_ownership(repo_root=repo_root, head_ref=head_ref)
    behavior_guard = reply_chain_behavior_switch_guard(
        flag_snapshot=flag_snapshot,
        shadow_bundle_audit=bundle,
        diagnostics=diagnostics,
        model_semantics_ownership_report=ownership,
    )
    blockers = _completion_blockers(
        branch=branch,
        flag_snapshot=flag_snapshot,
        bundle=bundle,
        ownership=ownership,
        behavior_guard=behavior_guard,
    )
    return {
        "schema_version": "reply_chain_refactor_completion_audit_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": commit,
        "git_commit_set": [commit] if commit else [],
        "branch": branch,
        "expected_branch": "codex/reply-chain-refactor",
        "architecture_status": {
            "shared_context": _component_status(context, "reply_chain_shadow_v1"),
            "sop_chat_gate": _component_status(gate, "chat_gate_router_shadow_v1"),
            "tool_planner": _component_status(tool_plan, "tool_plan_preview_v2"),
            "read_only_tool_executor": _component_status(read_executor, "read_only_tool_executor_shadow_v1"),
            "join": _component_status(join, "reply_chain_join_shadow_v1"),
            "reply_final_brain_handoff": _component_status(handoff, "reply_final_brain_handoff_shadow_v1"),
            "commit_coordinator": _component_status(commit_shadow, "reply_chain_commit_shadow_v1"),
            "parallel_runner": _component_status(runner, "parallel_gate_planner_runner_shadow_v1"),
        },
        "ownership_summary": {
            "semantic_ownership_passed": ownership.get("semantic_ownership_passed") is True,
            "normalizer_boundary_passed": (
                (ownership.get("normalizer_boundary_audit") or {}).get("normalizer_boundary_passed") is True
            ),
            "tool_planner_only_ready": ownership.get("tool_planner_only_ready") is True,
            "join_final_customer_message_owner": ownership.get("join_final_customer_message_owner"),
            "reply_handoff_ready": ownership.get("reply_handoff_ready") is True,
            "blockers": ownership.get("blockers") or [],
        },
        "shadow_bundle_summary": {
            "ready_for_refactor_review": bundle.get("ready_for_refactor_review") is True,
            "structural_blockers": _structural_bundle_blockers(bundle),
            "release_gate_blockers": _release_gate_bundle_blockers(bundle),
            "phase": bundle.get("phase"),
            "blockers": bundle.get("blockers") or [],
        },
        "behavior_switch_summary": {
            "requested": behavior_guard.get("behavior_switch_requested") is True,
            "can_enable_behavior_switch": behavior_guard.get("can_enable_behavior_switch") is True,
            "blockers": behavior_guard.get("blockers") or [],
        },
        "model_choice": {
            "selected_candidate": "gpt-5.4",
            "reason": "current small matrix found gpt-5.4 as the only stable candidate; Claude/Gemini remain non-release candidates until full matrix evidence clears",
            "release_gate_requires_full_matrix": True,
        },
        "completion_passed": not blockers,
        "blockers": blockers,
        "remaining_release_gates": _remaining_release_gates(behavior_guard),
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
            "does_not_call_external_tools": True,
            "does_not_deploy": True,
            "does_not_merge_main": True,
        },
        "source": "audit_reply_chain_refactor_completion",
    }


async def _gate_branch(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "completion_audit_static_gate",
        "gate_router_shadow": state["sop_gate_router_shadow"],
    }


async def _planner_branch(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "completion_audit_static_tool_planner",
        "tool_plan_preview": state["tool_plan_preview"],
    }


def _run_parallel_runner_probe(
    *,
    context: dict[str, Any],
    gate: dict[str, Any],
    tool_plan: dict[str, Any],
    flag_snapshot: dict[str, Any],
) -> dict[str, Any]:
    import asyncio

    return asyncio.run(
        run_parallel_gate_planner_shadow(
            initial_state={
                "reply_chain_shadow_context": context,
                "sop_gate_router_shadow": gate,
                "tool_plan_preview": tool_plan,
            },
            gate_branch=_gate_branch,
            planner_branch=_planner_branch,
            refactor_flags=flag_snapshot,
        )
    )


def _completion_blockers(
    *,
    branch: str,
    flag_snapshot: dict[str, Any],
    bundle: dict[str, Any],
    ownership: dict[str, Any],
    behavior_guard: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if branch != "codex/reply-chain-refactor":
        blockers.append(f"wrong_branch:{branch or 'missing'}")
    if flag_snapshot.get("mode") != "shadow_only":
        blockers.append(f"flags_not_shadow_only:{flag_snapshot.get('mode') or 'missing'}")
    if flag_snapshot.get("safe_for_current_runtime") is not True:
        blockers.append("flags_not_safe_for_current_runtime")
    structural_bundle_blockers = _structural_bundle_blockers(bundle)
    blockers.extend(f"shadow_bundle:{item}" for item in structural_bundle_blockers)
    if ownership.get("semantic_ownership_passed") is not True:
        blockers.append("semantic_ownership_not_passed")
    if behavior_guard.get("can_enable_behavior_switch") is True:
        blockers.append("behavior_switch_unexpectedly_enabled")
    if "behavior_switch_not_requested" not in set(behavior_guard.get("blockers") or []):
        blockers.append("behavior_switch_guard_missing_not_requested_blocker")
    return blockers


def _remaining_release_gates(behavior_guard: dict[str, Any]) -> list[str]:
    blockers = [str(item) for item in behavior_guard.get("blockers") or []]
    gates: list[str] = []
    marker_map = {
        "missing_offline_simulation_report": "full_offline_simulation_report",
        "missing_model_matrix_report": "three_model_matrix_report",
        "missing_payload_isolation_audit": "payload_isolation_report",
        "missing_business_wording_freeze_audit": "business_wording_freeze_report",
        "missing_refactor_rollback_evidence": "rollback_evidence_report",
        "missing_human_review_approval": "human_review_approval",
    }
    for marker, gate in marker_map.items():
        if marker in blockers:
            gates.append(gate)
    if not gates:
        return ["manual_behavior_switch_review_still_required"]
    return gates


def _structural_bundle_blockers(bundle: dict[str, Any]) -> list[str]:
    return [
        item
        for item in [str(value) for value in bundle.get("blockers") or []]
        if not item.startswith("release_review_gate_unproven:")
    ]


def _release_gate_bundle_blockers(bundle: dict[str, Any]) -> list[str]:
    return [
        item
        for item in [str(value) for value in bundle.get("blockers") or []]
        if item.startswith("release_review_gate_unproven:")
    ]


def _component_status(value: dict[str, Any], expected_schema: str) -> dict[str, Any]:
    return {
        "expected_schema": expected_schema,
        "observed_schema": value.get("schema_version") if isinstance(value, dict) else None,
        "valid": isinstance(value, dict) and value.get("schema_version") == expected_schema,
    }


def _reply_chain_shadow_context(*, commit: str) -> dict[str, Any]:
    return {
        "schema_version": "reply_chain_shadow_v1",
        "git_commit": commit,
        "complete_timed_chat": [
            {
                "message_ref": "msg_1",
                "sender": "customer",
                "message_type": "text",
                "content": "sim message",
                "sent_at": "2026-08-07T10:00:00+08:00",
            }
        ],
        "current_message": {
            "message_ref": "msg_1",
            "sender": "customer",
            "message_type": "text",
            "content": "sim message",
            "sent_at": "2026-08-07T10:00:00+08:00",
        },
        "authoritative_facts": {
            "payment": {},
            "orders": {},
            "visible_store_scope": {},
            "sop_deliveries": {},
            "structured_messages": {},
            "risk_holds": {},
        },
        "authority_audit": {
            "schema_version": "reply_chain_authority_audit_v1",
            "complete_chat_is_primary_authority": True,
            "soft_profile_excluded_from_authority": True,
            "non_authority_profile_fields": ["next_sales_strategy", "intent_level", "customer_type"],
            "soft_profile_fields_seen": [],
            "timeline_message_count": 1,
            "all_messages_have_sent_at": True,
            "timeline_window_audit": {
                "schema_version": "reply_chain_timeline_window_audit_v1",
                "ready_for_authoritative_model_input": True,
                "source_window_complete": True,
                "truncated": False,
                "dropped_message_count": 0,
                "retained_window": {
                    "schema_version": "reply_chain_retained_timeline_window_v1",
                    "oldest_message_ref": "msg_1",
                    "newest_message_ref": "msg_1",
                    "current_request_message_refs": ["msg_1"],
                },
                "blockers": [],
            },
            "current_message_audit": {
                "schema_version": "reply_chain_current_message_audit_v1",
                "current_message_in_timeline": True,
                "current_message_is_last": True,
                "ready_for_authoritative_model_input": True,
                "blockers": [],
            },
            "fact_snapshot": {
                "schema_version": "reply_chain_fact_snapshot_audit_v1",
                "sections_with_error": [],
            },
        },
    }


def _gate_router_shadow(*, commit: str) -> dict[str, Any]:
    return {
        "schema_version": "chat_gate_router_shadow_v1",
        "git_commit": commit,
        "route_suggestion": "content_and_tools",
        "selected_content": {
            "message_count": 1,
            "source": "static_candidate_reference_only",
        },
        "dynamic_fact_expectation": {
            "requirement": "required",
            "capability_classes": ["store_lookup"],
        },
        "commit_boundary": {
            "schema_version": "chat_gate_commit_boundary_v1",
            "shadow_output_only": True,
            "this_shadow_creates_sop_task": False,
            "this_shadow_updates_send_once": False,
            "this_shadow_sends_customer_messages": False,
            "this_shadow_writes_database": False,
            "target_commit_owner": "reply_chain_commit_phase_after_reply_validation",
        },
    }


def _comparison_shadow(*, commit: str, join: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "parallel_reply_chain_comparison_v1",
        "git_commit": commit,
        "status": "matched_shadow_replay",
        "serial_join_route": join.get("final_route"),
        "parallel_runner_mode": runner.get("mode"),
        "diffs": [],
    }


def _diagnostics_shadow(*, commit: str) -> dict[str, Any]:
    return {
        "schema_version": "parallel_reply_chain_diagnostics_v1",
        "git_commit": commit,
        "phase": "ready_for_human_review",
        "release_review": {
            "schema_version": "reply_chain_release_review_checklist_v1",
            "can_enable_behavior_switch": False,
            "missing_or_unproven_gates": [
                "simulation_regression_review",
                "model_matrix_review",
                "payload_isolation_review",
                "business_wording_freeze_review",
                "rollback_evidence_review",
            ],
            "blocker_groups": {},
            "gates": [
                {
                    "gate_id": "authority_snapshot_review",
                    "passed": True,
                    "evidence_observed": {
                        "shared_context_timeline_retained_window_schema": "reply_chain_retained_timeline_window_v1",
                        "shared_context_soft_profile_excluded": True,
                        "shared_context_non_authority_profile_fields": [
                            "next_sales_strategy",
                            "intent_level",
                            "customer_type",
                        ],
                    },
                }
            ],
        },
    }


def _git_output(repo_root: Path, command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit reply-chain refactor completion on the refactor branch.")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--report", type=Path, default=Path(".tmp_runtime/reply_chain_refactor_completion_audit.json"))
    return parser.parse_args()


def main() -> int:
    args = _args()
    repo_root = Path(__file__).resolve().parents[2]
    report = audit_reply_chain_refactor_completion(repo_root=repo_root, head_ref=args.head_ref)
    output = args.report if args.report.is_absolute() else repo_root / args.report
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["completion_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
