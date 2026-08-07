from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.parallel_reply_chain_shadow import parallel_reply_chain_shadow
from app.services.reply_chain_join_shadow import reply_chain_join_shadow
from app.services.reply_final_brain_handoff import reply_final_brain_handoff_shadow_from_planner_output
from app.services.tool_plan_preview import tool_plan_preview_from_planner_output
from ai_paths.scripts.audit_planner_normalizer_boundaries import audit_planner_normalizer_boundaries


def audit_model_semantics_ownership(*, repo_root: Path, head_ref: str = "HEAD") -> dict[str, Any]:
    """Audit reply-chain semantic ownership without changing runtime behavior."""

    normalizer_boundary = audit_planner_normalizer_boundaries(repo_root=repo_root, head_ref=head_ref)
    tool_plan = tool_plan_preview_from_planner_output(
        {
            "planner_tool_calls": [
                {
                    "name": "customer_store_lookup",
                    "purpose": "fetch visible store facts",
                    "query": "simulated location",
                }
            ],
        }
    )
    gate_router = _gate_router_shadow()
    join_shadow = reply_chain_join_shadow(gate_router_shadow=gate_router, tool_plan_preview=tool_plan)
    planner_output_for_handoff = {
        "tool_plan_preview": tool_plan,
        "read_only_tool_executor_shadow": _read_only_executor_shadow(),
        "reply_chain_join_shadow": join_shadow,
    }
    handoff = reply_final_brain_handoff_shadow_from_planner_output(
        planner_output_for_handoff,
        reply_chain_shadow_context=_reply_chain_shadow_context(),
        gate_router_shadow=gate_router,
    )
    parallel = parallel_reply_chain_shadow(
        reply_chain_shadow_context=_reply_chain_shadow_context(),
        gate_router_shadow=gate_router,
        tool_plan_preview=tool_plan,
        read_only_tool_executor_shadow=_read_only_executor_shadow(),
        reply_chain_join_shadow=join_shadow,
        reply_final_brain_handoff_shadow=handoff,
        refactor_flags={"mode": "audit_shadow_only"},
    )
    direct_join = reply_chain_join_shadow(
        gate_router_shadow=_direct_static_gate_router_shadow(),
        tool_plan_preview=tool_plan_preview_from_planner_output({"planner_tool_calls": []}),
    )
    contract = parallel.get("ownership_contract") if isinstance(parallel.get("ownership_contract"), dict) else {}
    tool_contract = contract.get("tool_planner") if isinstance(contract.get("tool_planner"), dict) else {}
    reply_contract = contract.get("reply") if isinstance(contract.get("reply"), dict) else {}
    code_contract = contract.get("code") if isinstance(contract.get("code"), dict) else {}
    observation = parallel.get("current_serial_observation") if isinstance(parallel.get("current_serial_observation"), dict) else {}
    final_boundary = join_shadow.get("final_expression_boundary") if isinstance(join_shadow.get("final_expression_boundary"), dict) else {}
    direct_boundary = direct_join.get("final_expression_boundary") if isinstance(direct_join.get("final_expression_boundary"), dict) else {}
    handoff_migration = handoff.get("migration_audit") if isinstance(handoff.get("migration_audit"), dict) else {}
    field_mapping = handoff_migration.get("field_mapping_audit") if isinstance(handoff_migration.get("field_mapping_audit"), dict) else {}

    blockers = _ownership_blockers(
        tool_plan=tool_plan,
        tool_contract=tool_contract,
        reply_contract=reply_contract,
        code_contract=code_contract,
        final_boundary=final_boundary,
        direct_boundary=direct_boundary,
        handoff=handoff,
        field_mapping=field_mapping,
    )
    blockers.extend(
        f"normalizer_boundary:{item}"
        for item in _list_strings(normalizer_boundary.get("blockers"))
    )
    commit = _git_output(repo_root, ["git", "rev-parse", head_ref])
    return {
        "schema_version": "reply_chain_model_semantics_ownership_audit_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": commit,
        "git_commit_set": [commit] if commit else [],
        "head_ref": head_ref,
        "ownership_contract_checked": True,
        "tool_planner_must_not_own": _list_strings(tool_contract.get("must_not_own")),
        "reply_owns": _list_strings(reply_contract.get("owns")),
        "code_must_not_own": _list_strings(code_contract.get("must_not_own")),
        "tool_planner_legacy_residue_count": _int_value((tool_plan.get("migration_audit") or {}).get("legacy_residue_count")),
        "tool_planner_only_ready": (tool_plan.get("migration_audit") or {}).get("tool_planner_only_ready") is True,
        "join_final_expression_boundary_schema": final_boundary.get("schema_version"),
        "join_final_customer_message_owner": final_boundary.get("final_customer_message_owner"),
        "join_generates_customer_visible_text": final_boundary.get("join_generates_customer_visible_text"),
        "join_decides_sales_psychology": final_boundary.get("join_decides_sales_psychology"),
        "direct_reply_scope": direct_boundary.get("direct_reply_scope"),
        "direct_reply_final_customer_message_owner": direct_boundary.get("final_customer_message_owner"),
        "direct_reply_requires_commit_validation": direct_boundary.get("direct_reply_requires_commit_validation"),
        "reply_handoff_schema": handoff.get("schema_version"),
        "reply_handoff_ready": (handoff.get("handoff_readiness_audit") or {}).get("ready_for_reply_payload_switch_shadow") is True,
        "legacy_business_field_mapping_schema": field_mapping.get("schema_version"),
        "unmapped_legacy_business_fields": _list_strings(field_mapping.get("unmapped_legacy_business_fields")),
        "parallel_shadow_schema": parallel.get("schema_version"),
        "parallel_observation": {
            "tool_planner_legacy_residue_count": observation.get("tool_planner_legacy_residue_count"),
            "tool_planner_only_ready": observation.get("tool_planner_only_ready"),
            "join_final_customer_message_owner": observation.get("join_final_customer_message_owner"),
            "join_generates_customer_visible_text": observation.get("join_generates_customer_visible_text"),
            "join_decides_sales_psychology": observation.get("join_decides_sales_psychology"),
            "reply_handoff_ready_for_payload_switch_shadow": observation.get("reply_handoff_ready_for_payload_switch_shadow"),
        },
        "normalizer_boundary_audit": {
            "schema_version": normalizer_boundary.get("schema_version"),
            "normalizer_boundary_passed": normalizer_boundary.get("normalizer_boundary_passed"),
            "summary": normalizer_boundary.get("summary"),
            "blockers": normalizer_boundary.get("blockers"),
        },
        "semantic_ownership_passed": not blockers,
        "blockers": blockers,
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
            "does_not_call_external_tools": True,
        },
        "source": "audit_model_semantics_ownership",
    }


def _ownership_blockers(
    *,
    tool_plan: dict[str, Any],
    tool_contract: dict[str, Any],
    reply_contract: dict[str, Any],
    code_contract: dict[str, Any],
    final_boundary: dict[str, Any],
    direct_boundary: dict[str, Any],
    handoff: dict[str, Any],
    field_mapping: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    tool_must_not_own = set(_list_strings(tool_contract.get("must_not_own")))
    for required in ("customer_visible_text", "sales_psychology", "closing_move"):
        if required not in tool_must_not_own:
            blockers.append(f"tool_planner_missing_must_not_own:{required}")
    reply_owns = set(_list_strings(reply_contract.get("owns")))
    for required in ("final_customer_visible_messages", "complex_turn_outcome", "single_mainline_action"):
        if required not in reply_owns:
            blockers.append(f"reply_missing_owns:{required}")
    code_must_not_own = set(_list_strings(code_contract.get("must_not_own")))
    for required in ("normal_sales_intent", "objection_psychology", "sales_rhythm"):
        if required not in code_must_not_own:
            blockers.append(f"code_missing_must_not_own:{required}")
    migration = tool_plan.get("migration_audit") if isinstance(tool_plan.get("migration_audit"), dict) else {}
    if migration.get("schema_version") != "tool_planner_migration_audit_v1":
        blockers.append("missing_tool_planner_migration_audit")
    if _int_value(migration.get("legacy_residue_count")) != 0:
        blockers.append(f"tool_planner_legacy_residue:{migration.get('legacy_residue_count')}")
    if migration.get("tool_planner_only_ready") is not True:
        blockers.append("tool_planner_not_tool_only_ready")
    if final_boundary.get("schema_version") != "reply_final_expression_boundary_v1":
        blockers.append("missing_join_final_expression_boundary")
    if final_boundary.get("final_customer_message_owner") != "reply":
        blockers.append(f"join_complex_turn_owner_not_reply:{final_boundary.get('final_customer_message_owner') or 'missing'}")
    if final_boundary.get("join_generates_customer_visible_text") is not False:
        blockers.append("join_generates_customer_visible_text")
    if final_boundary.get("join_decides_sales_psychology") is not False:
        blockers.append("join_decides_sales_psychology")
    if direct_boundary.get("final_customer_message_owner") != "validated_static_gate_candidate":
        blockers.append("direct_reply_not_limited_to_validated_static_candidate")
    if direct_boundary.get("direct_reply_scope") != "static_candidate_only_no_dynamic_facts":
        blockers.append(f"direct_reply_scope_not_static_only:{direct_boundary.get('direct_reply_scope') or 'missing'}")
    if direct_boundary.get("direct_reply_requires_commit_validation") is not True:
        blockers.append("direct_reply_missing_commit_validation")
    if handoff.get("schema_version") != "reply_final_brain_handoff_shadow_v1":
        blockers.append("missing_reply_handoff_shadow")
    readiness = handoff.get("handoff_readiness_audit") if isinstance(handoff.get("handoff_readiness_audit"), dict) else {}
    if readiness.get("ready_for_reply_payload_switch_shadow") is not True:
        blockers.append("reply_handoff_not_ready")
    if field_mapping.get("schema_version") != "reply_legacy_field_mapping_audit_v1":
        blockers.append("missing_reply_legacy_field_mapping")
    unmapped = _list_strings(field_mapping.get("unmapped_legacy_business_fields"))
    if unmapped:
        blockers.extend(f"unmapped_legacy_business_field:{field}" for field in unmapped)
    return blockers


def _reply_chain_shadow_context() -> dict[str, Any]:
    return {
        "schema_version": "reply_chain_shadow_v1",
        "authority_audit": {
            "schema_version": "reply_chain_authority_audit_v1",
            "complete_chat_is_primary_authority": True,
            "soft_profile_excluded_from_authority": True,
            "timeline_message_count": 3,
            "all_messages_have_sent_at": True,
            "timeline_window_audit": {
                "schema_version": "reply_chain_timeline_window_audit_v1",
                "ready_for_authoritative_model_input": True,
                "source_window_complete": True,
                "truncated": False,
                "dropped_message_count": 0,
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


def _gate_router_shadow() -> dict[str, Any]:
    return {
        "schema_version": "chat_gate_router_shadow_v1",
        "route_suggestion": "content_and_tools",
        "selected_content": {"message_count": 1, "source": "static_candidate_reference_only"},
        "commit_boundary": _gate_commit_boundary(),
    }


def _direct_static_gate_router_shadow() -> dict[str, Any]:
    return {
        "schema_version": "chat_gate_router_shadow_v1",
        "route_suggestion": "direct_text",
        "direct_reply_candidate": {"type": "text", "content": "static candidate"},
        "direct_reply_candidate_audit": {
            "schema_version": "chat_gate_direct_reply_candidate_audit_v1",
            "safe_for_direct_reply_static_candidate": True,
        },
        "commit_boundary": _gate_commit_boundary(),
    }


def _gate_commit_boundary() -> dict[str, Any]:
    return {
        "schema_version": "chat_gate_commit_boundary_v1",
        "shadow_output_only": True,
        "this_shadow_creates_sop_task": False,
        "this_shadow_updates_send_once": False,
        "this_shadow_sends_customer_messages": False,
        "this_shadow_writes_database": False,
        "target_commit_owner": "reply_chain_commit_phase_after_reply_validation",
    }


def _read_only_executor_shadow() -> dict[str, Any]:
    return {
        "schema_version": "read_only_tool_executor_shadow_v1",
        "mode": "shadow_no_external_tool_calls",
        "summary": {"blocked_count": 0},
        "dependency_audit": {
            "schema_version": "read_only_tool_dependency_audit_v1",
            "ready_for_early_execution_ordering": True,
            "blockers": [],
        },
        "blocked": [],
    }


def _git_output(repo_root: Path, command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit that Tool Planner, Join, and code do not own customer psychology, "
            "objection handling, sales rhythm, or final customer-visible expression. "
            "This is read-only and does not call models."
        )
    )
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--report", type=Path, default=Path(".tmp_runtime/model_semantics_ownership_audit.json"))
    return parser.parse_args()


def main() -> int:
    args = _args()
    repo_root = Path(__file__).resolve().parents[2]
    report = audit_model_semantics_ownership(repo_root=repo_root, head_ref=args.head_ref)
    output = args.report if args.report.is_absolute() else repo_root / args.report
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["semantic_ownership_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
