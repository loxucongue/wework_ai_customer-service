from __future__ import annotations

from typing import Any


CUSTOMER_MESSAGE_FIELDS = ("planner_reply_messages",)
TURN_OUTCOME_FIELDS = (
    "planner_decision",
    "planner_stage",
    "planner_sub_rule_id",
    "conversion_stage",
    "customer_type",
    "main_blocker",
    "next_step",
    "primary_task",
    "secondary_tasks",
)
SALES_DECISION_FIELDS = (
    "payment_state",
    "payment_action",
    "payment_decision",
    "store_binding_decision",
    "order_decision",
    "appointment_decision",
    "sales_progression",
    "precision_qa_decision",
    "reply_strategy",
    "handoff",
    "memory_update_hint",
)
FACT_AND_TOOL_FIELDS = (
    "tool_plan_preview",
    "read_only_tool_executor_shadow",
    "reply_chain_join_shadow",
)


def reply_final_brain_handoff_shadow_from_planner_output(
    output: dict[str, Any],
    *,
    reply_chain_shadow_context: dict[str, Any] | None = None,
    gate_router_shadow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the future Reply-final-brain handoff from current Planner output.

    This is shadow-only migration evidence. It does not approve, alter, or
    generate customer-visible replies.
    """

    customer_message_fields = _present_fields(output, CUSTOMER_MESSAGE_FIELDS)
    turn_outcome_fields = _present_fields(output, TURN_OUTCOME_FIELDS)
    sales_decision_fields = _present_fields(output, SALES_DECISION_FIELDS)
    fact_and_tool_fields = _present_fields(output, FACT_AND_TOOL_FIELDS)
    handoff_readiness_audit = _handoff_readiness_audit(
        output=output,
        reply_chain_shadow_context=reply_chain_shadow_context or {},
        gate_router_shadow=gate_router_shadow or {},
    )
    return _drop_empty(
        {
            "schema_version": "reply_final_brain_handoff_shadow_v1",
            "mode": "shadow_only_no_runtime_behavior_change",
            "purpose": "map_legacy_planner_business_outputs_to_future_reply_inputs",
            "target_owner": "reply",
            "input_groups": {
                "customer_message_candidates": _pick(output, customer_message_fields),
                "turn_outcome_signals": _pick(output, turn_outcome_fields),
                "sales_decision_signals": _pick(output, sales_decision_fields),
                "fact_and_tool_evidence": _fact_and_tool_summary(output, fact_and_tool_fields),
            },
            "migration_audit": {
                "customer_message_field_count": len(customer_message_fields),
                "turn_outcome_field_count": len(turn_outcome_fields),
                "sales_decision_field_count": len(sales_decision_fields),
                "fact_and_tool_field_count": len(fact_and_tool_fields),
                "legacy_business_field_count": len(customer_message_fields) + len(turn_outcome_fields) + len(sales_decision_fields),
                "requires_reply_schema_before_activation": True,
            },
            "handoff_readiness_audit": handoff_readiness_audit,
            "ownership_contract": {
                "reply_owns": [
                    "final_customer_visible_messages",
                    "complex_turn_outcome",
                    "single_mainline_action",
                    "closing_move",
                ],
                "tool_planner_must_not_own": [
                    "customer_visible_text",
                    "sales_psychology",
                    "closing_move",
                    "complex_customer_state",
                ],
            },
            "safety": {
                "shadow_only": True,
                "no_model_payload_consumption": True,
                "no_customer_messages_sent": True,
                "no_database_writes": True,
            },
        }
    )


def _handoff_readiness_audit(
    *,
    output: dict[str, Any],
    reply_chain_shadow_context: dict[str, Any],
    gate_router_shadow: dict[str, Any],
) -> dict[str, Any]:
    authority_audit = reply_chain_shadow_context.get("authority_audit")
    if not isinstance(authority_audit, dict):
        authority_audit = {}
    fact_snapshot = authority_audit.get("fact_snapshot")
    if not isinstance(fact_snapshot, dict):
        fact_snapshot = {}
    join_shadow = output.get("reply_chain_join_shadow")
    if not isinstance(join_shadow, dict):
        join_shadow = {}
    final_expression_boundary = join_shadow.get("final_expression_boundary")
    if not isinstance(final_expression_boundary, dict):
        final_expression_boundary = {}
    tool_plan_preview = output.get("tool_plan_preview")
    if not isinstance(tool_plan_preview, dict):
        tool_plan_preview = {}

    blockers: list[str] = []
    if reply_chain_shadow_context.get("schema_version") != "reply_chain_shadow_v1":
        blockers.append("missing_complete_timed_chat_context")
    if authority_audit.get("schema_version") != "reply_chain_authority_audit_v1":
        blockers.append("missing_authority_audit")
    if authority_audit.get("complete_chat_is_primary_authority") is not True:
        blockers.append("complete_chat_not_primary_authority")
    if authority_audit.get("all_messages_have_sent_at") is not True:
        blockers.append("incomplete_message_timestamps")
    if fact_snapshot.get("schema_version") != "reply_chain_fact_snapshot_audit_v1":
        blockers.append("missing_authoritative_fact_snapshot")
    if gate_router_shadow.get("schema_version") != "chat_gate_router_shadow_v1":
        blockers.append("missing_gate_content_candidates")
    if tool_plan_preview.get("schema_version") != "tool_plan_preview_v2":
        blockers.append("missing_tool_plan_preview")
    if join_shadow.get("schema_version") != "reply_chain_join_shadow_v1":
        blockers.append("missing_reply_chain_join_shadow")
    if final_expression_boundary.get("schema_version") != "reply_final_expression_boundary_v1":
        blockers.append("missing_final_expression_boundary")
    if final_expression_boundary.get("join_generates_customer_visible_text") is not False:
        blockers.append("join_may_generate_customer_visible_text")
    if final_expression_boundary.get("join_decides_sales_psychology") is not False:
        blockers.append("join_may_decide_sales_psychology")

    return _drop_empty(
        {
            "schema_version": "reply_final_brain_handoff_readiness_audit_v1",
            "target_reply_input_contract": {
                "complete_timed_chat_required": True,
                "authoritative_facts_required": True,
                "gate_candidates_reference_only": True,
                "tool_facts_authoritative_reference_only": True,
                "final_reply_schema_required_before_activation": True,
                "commit_phase_must_follow_reply_validation": True,
                "legacy_planner_outputs_shadow_only": True,
            },
            "observed_inputs": {
                "reply_chain_context_schema": reply_chain_shadow_context.get("schema_version"),
                "authority_audit_schema": authority_audit.get("schema_version"),
                "all_messages_have_sent_at": authority_audit.get("all_messages_have_sent_at"),
                "complete_chat_is_primary_authority": authority_audit.get("complete_chat_is_primary_authority"),
                "fact_snapshot_schema": fact_snapshot.get("schema_version"),
                "gate_router_schema": gate_router_shadow.get("schema_version"),
                "tool_plan_schema": tool_plan_preview.get("schema_version"),
                "join_schema": join_shadow.get("schema_version"),
                "final_expression_boundary_schema": final_expression_boundary.get("schema_version"),
                "join_generates_customer_visible_text": final_expression_boundary.get("join_generates_customer_visible_text"),
                "join_decides_sales_psychology": final_expression_boundary.get("join_decides_sales_psychology"),
            },
            "ready_for_reply_payload_switch_shadow": not blockers,
            "blockers": blockers,
        }
    )


def _present_fields(output: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if _has_value(output.get(field))]


def _pick(output: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: output[field] for field in fields}


def _fact_and_tool_summary(output: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if "tool_plan_preview" in fields:
        tool_plan = output.get("tool_plan_preview") if isinstance(output.get("tool_plan_preview"), dict) else {}
        summary["tool_plan_preview"] = _drop_empty(
            {
                "fact_requirement": tool_plan.get("fact_requirement"),
                "read_tool_count": len(tool_plan.get("read_tool_calls") or []),
                "deferred_write_count": len(tool_plan.get("deferred_write_proposals") or []),
                "unknown_tool_count": len(tool_plan.get("unknown_tools") or []),
                "migration_audit": tool_plan.get("migration_audit") if isinstance(tool_plan.get("migration_audit"), dict) else {},
            }
        )
    if "read_only_tool_executor_shadow" in fields:
        executor = output.get("read_only_tool_executor_shadow") if isinstance(output.get("read_only_tool_executor_shadow"), dict) else {}
        summary["read_only_tool_executor_shadow"] = _drop_empty(
            {
                "mode": executor.get("mode"),
                "would_execute_count": (executor.get("summary") or {}).get("would_execute_count"),
                "blocked_count": (executor.get("summary") or {}).get("blocked_count"),
            }
        )
    if "reply_chain_join_shadow" in fields:
        join = output.get("reply_chain_join_shadow") if isinstance(output.get("reply_chain_join_shadow"), dict) else {}
        summary["reply_chain_join_shadow"] = _drop_empty(
            {
                "final_route": join.get("final_route"),
                "direct_reply_allowed": join.get("direct_reply_allowed"),
                "read_tool_count": join.get("read_tool_count"),
            }
        )
    return _drop_empty(summary)


def _has_value(value: Any) -> bool:
    if value in ("", None, [], {}):
        return False
    return True


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
