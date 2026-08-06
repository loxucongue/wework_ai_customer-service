from __future__ import annotations

from typing import Any


def parallel_reply_chain_shadow(
    *,
    reply_chain_shadow_context: dict[str, Any],
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
    read_only_tool_executor_shadow: dict[str, Any],
    reply_chain_join_shadow: dict[str, Any],
    reply_final_brain_handoff_shadow: dict[str, Any] | None = None,
    refactor_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the target parallel reply chain without changing runtime behavior."""

    activation_blockers = _activation_blockers(
        reply_chain_shadow_context=reply_chain_shadow_context,
        gate_router_shadow=gate_router_shadow,
        tool_plan_preview=tool_plan_preview,
        read_only_tool_executor_shadow=read_only_tool_executor_shadow,
        reply_chain_join_shadow=reply_chain_join_shadow,
        reply_final_brain_handoff_shadow=reply_final_brain_handoff_shadow or {},
    )
    reply_handoff_migration = (reply_final_brain_handoff_shadow or {}).get("migration_audit") or {}
    context_authority_audit = reply_chain_shadow_context.get("authority_audit")
    if not isinstance(context_authority_audit, dict):
        context_authority_audit = {}
    fact_snapshot_audit = context_authority_audit.get("fact_snapshot")
    if not isinstance(fact_snapshot_audit, dict):
        fact_snapshot_audit = {}
    gate_commit_boundary = gate_router_shadow.get("commit_boundary")
    if not isinstance(gate_commit_boundary, dict):
        gate_commit_boundary = {}
    return _drop_empty(
        {
            "schema_version": "parallel_reply_chain_shadow_v1",
            "mode": "shadow_no_parallel_execution",
            "target_topology": {
                "start": "single_normalized_input_and_authoritative_evidence",
                "parallel_branches": ["sop_chat_gate", "tool_planner"],
                "after_tool_planner": "read_only_tool_executor",
                "join": "deterministic_join",
                "final_expression_owner": "reply",
                "write_phase": "deferred_commit_after_validation",
            },
            "ownership_contract": {
                "sop_chat_gate": {
                    "owns": ["content_match", "first_layer_route", "direct_reply_candidate"],
                    "must_not_own": ["tool_arguments", "final_closing_move", "complex_customer_state", "writes"],
                },
                "tool_planner": {
                    "owns": ["minimal_read_tool_plan", "required_fact_fields", "incomplete_fact_question_goal"],
                    "must_not_own": ["customer_visible_text", "sop_selection", "sales_psychology", "closing_move"],
                },
                "reply": {
                    "owns": ["final_customer_visible_messages", "complex_turn_outcome", "single_mainline_action"],
                    "must_use": ["complete_timed_chat", "authoritative_facts", "gate_content_candidates", "tool_facts"],
                },
                "code": {
                    "owns": ["schema", "tool_execution", "idempotency", "hard_safety", "non_business_fallback"],
                    "must_not_own": ["normal_sales_intent", "objection_psychology", "sales_rhythm"],
                },
            },
            "current_serial_observation": {
                "shared_context_schema": reply_chain_shadow_context.get("schema_version"),
                "shared_context_authority_audit_schema": context_authority_audit.get("schema_version"),
                "shared_context_message_count": context_authority_audit.get("timeline_message_count"),
                "shared_context_all_messages_have_sent_at": context_authority_audit.get("all_messages_have_sent_at"),
                "shared_context_complete_chat_is_primary": context_authority_audit.get("complete_chat_is_primary_authority"),
                "shared_context_soft_profile_excluded": context_authority_audit.get("soft_profile_excluded_from_authority"),
                "shared_context_fact_snapshot_schema": fact_snapshot_audit.get("schema_version"),
                "shared_context_fact_sections_with_error": fact_snapshot_audit.get("sections_with_error"),
                "gate_route": gate_router_shadow.get("route_suggestion"),
                "gate_commit_boundary_schema": gate_commit_boundary.get("schema_version"),
                "gate_shadow_output_only": gate_commit_boundary.get("shadow_output_only"),
                "gate_target_commit_owner": gate_commit_boundary.get("target_commit_owner"),
                "gate_shadow_creates_sop_task": gate_commit_boundary.get("this_shadow_creates_sop_task"),
                "gate_shadow_updates_send_once": gate_commit_boundary.get("this_shadow_updates_send_once"),
                "gate_shadow_sends_customer_messages": gate_commit_boundary.get("this_shadow_sends_customer_messages"),
                "gate_shadow_writes_database": gate_commit_boundary.get("this_shadow_writes_database"),
                "planner_fact_requirement": tool_plan_preview.get("fact_requirement"),
                "tool_planner_legacy_residue_count": (tool_plan_preview.get("migration_audit") or {}).get("legacy_residue_count"),
                "tool_planner_only_ready": (tool_plan_preview.get("migration_audit") or {}).get("tool_planner_only_ready"),
                "read_executor_mode": read_only_tool_executor_shadow.get("mode"),
                "join_final_route": reply_chain_join_shadow.get("final_route"),
                "direct_reply_allowed": reply_chain_join_shadow.get("direct_reply_allowed"),
                "reply_handoff_schema": (reply_final_brain_handoff_shadow or {}).get("schema_version"),
                "reply_legacy_business_field_count": reply_handoff_migration.get("legacy_business_field_count"),
                "reply_handoff_requires_schema": reply_handoff_migration.get("requires_reply_schema_before_activation"),
                "refactor_mode": (refactor_flags or {}).get("mode"),
            },
            "safety": {
                "no_runtime_behavior_change": True,
                "no_model_payload_consumption": True,
                "no_external_tool_calls": True,
                "no_customer_messages_sent": True,
                "no_database_writes": True,
            },
            "configuration": refactor_flags or {},
            "activation": {
                "ready_for_shadow_parallel_runner": not activation_blockers and _flags_allow_shadow_runner(refactor_flags),
                "blockers": [*activation_blockers, *_flag_blockers(refactor_flags)],
            },
            "source": "shadow_contract_only",
        }
    )


def _activation_blockers(
    *,
    reply_chain_shadow_context: dict[str, Any],
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
    read_only_tool_executor_shadow: dict[str, Any],
    reply_chain_join_shadow: dict[str, Any],
    reply_final_brain_handoff_shadow: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if reply_chain_shadow_context.get("schema_version") != "reply_chain_shadow_v1":
        blockers.append("missing_shared_reply_chain_shadow_context")
    blockers.extend(_context_authority_blockers(reply_chain_shadow_context))
    if gate_router_shadow.get("schema_version") != "chat_gate_router_shadow_v1":
        blockers.append("missing_gate_router_shadow")
    blockers.extend(_gate_commit_boundary_blockers(gate_router_shadow))
    if tool_plan_preview.get("schema_version") != "tool_plan_preview_v2":
        blockers.append("missing_tool_plan_preview")
    if read_only_tool_executor_shadow.get("schema_version") != "read_only_tool_executor_shadow_v1":
        blockers.append("missing_read_only_tool_executor_shadow")
    if reply_chain_join_shadow.get("schema_version") != "reply_chain_join_shadow_v1":
        blockers.append("missing_reply_chain_join_shadow")
    if reply_final_brain_handoff_shadow.get("schema_version") != "reply_final_brain_handoff_shadow_v1":
        blockers.append("missing_reply_final_brain_handoff_shadow")
    if read_only_tool_executor_shadow.get("blocked"):
        blockers.append("early_tool_executor_has_blocked_calls")
    return blockers


def _gate_commit_boundary_blockers(gate_router_shadow: dict[str, Any]) -> list[str]:
    boundary = gate_router_shadow.get("commit_boundary")
    if not isinstance(boundary, dict) or boundary.get("schema_version") != "chat_gate_commit_boundary_v1":
        return ["missing_gate_commit_boundary_audit"]
    blockers: list[str] = []
    if boundary.get("shadow_output_only") is not True:
        blockers.append("gate_shadow_not_marked_output_only")
    for field in (
        "this_shadow_creates_sop_task",
        "this_shadow_updates_send_once",
        "this_shadow_sends_customer_messages",
        "this_shadow_writes_database",
    ):
        if boundary.get(field) is not False:
            blockers.append(f"gate_shadow_commit_side_effect:{field}")
    if boundary.get("target_commit_owner") != "reply_chain_commit_phase_after_reply_validation":
        blockers.append("gate_target_commit_owner_not_reply_chain_commit_phase")
    return blockers


def _context_authority_blockers(reply_chain_shadow_context: dict[str, Any]) -> list[str]:
    audit = reply_chain_shadow_context.get("authority_audit")
    if not isinstance(audit, dict) or audit.get("schema_version") != "reply_chain_authority_audit_v1":
        return ["missing_reply_chain_authority_audit"]
    blockers: list[str] = []
    if audit.get("complete_chat_is_primary_authority") is not True:
        blockers.append("complete_chat_not_marked_primary_authority")
    if audit.get("soft_profile_excluded_from_authority") is not True:
        blockers.append("soft_profile_not_excluded_from_authority")
    if audit.get("all_messages_have_sent_at") is not True:
        blockers.append("incomplete_timestamped_conversation")
    fact_snapshot = audit.get("fact_snapshot")
    if not isinstance(fact_snapshot, dict) or fact_snapshot.get("schema_version") != "reply_chain_fact_snapshot_audit_v1":
        blockers.append("missing_reply_chain_fact_snapshot_audit")
    else:
        sections_with_error = fact_snapshot.get("sections_with_error")
        if isinstance(sections_with_error, list) and sections_with_error:
            blockers.append(f"authoritative_fact_snapshot_errors:{len(sections_with_error)}")
    return blockers


def _flags_allow_shadow_runner(refactor_flags: dict[str, Any] | None) -> bool:
    if not isinstance(refactor_flags, dict) or not refactor_flags:
        return True
    return bool(refactor_flags.get("safe_for_shadow_observation"))


def _flag_blockers(refactor_flags: dict[str, Any] | None) -> list[str]:
    if not isinstance(refactor_flags, dict) or not refactor_flags:
        return []
    if refactor_flags.get("safe_for_shadow_observation"):
        return []
    blockers = refactor_flags.get("activation_blockers")
    if isinstance(blockers, list) and blockers:
        return [f"flag:{item}" for item in blockers if isinstance(item, str) and item]
    return ["flag:shadow_observation_not_safe"]


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
