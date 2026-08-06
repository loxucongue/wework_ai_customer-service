from __future__ import annotations

from typing import Any

from app.services.tool_registry import read_only_tool_contract, tool_execution_class


CUSTOMER_VISIBLE_FIELDS = ("planner_reply_messages",)
BUSINESS_SEMANTIC_FIELDS = (
    "conversion_stage",
    "customer_type",
    "main_blocker",
    "next_step",
    "payment_state",
    "payment_action",
    "payment_decision",
    "store_binding_decision",
    "order_decision",
    "appointment_decision",
    "sales_progression",
    "precision_qa_decision",
    "reply_strategy",
    "primary_task",
    "secondary_tasks",
    "handoff",
    "memory_update_hint",
)
TOOL_PLANNER_TARGET_FIELDS = (
    "planner_tool_calls",
    "required_tools",
    "tool_policy_violations",
)


def tool_plan_preview_from_planner_output(output: dict[str, Any]) -> dict[str, Any]:
    """Extract a future Tool Planner view from the current Planner output."""

    tools = output.get("planner_tool_calls") if isinstance(output.get("planner_tool_calls"), list) else []
    if not tools:
        tools = output.get("required_tools") if isinstance(output.get("required_tools"), list) else []
    normalized_tools = [_normalize_tool(tool, index=index) for index, tool in enumerate(tools, start=1) if isinstance(tool, dict)]
    normalized_tools = [tool for tool in normalized_tools if tool.get("name")]

    read_tools = [_read_tool_schema(tool) for tool in normalized_tools if tool_execution_class(str(tool.get("name") or "")) == "read_only"]
    write_tools = [_write_tool_schema(tool) for tool in normalized_tools if tool_execution_class(str(tool.get("name") or "")) == "deferred_write"]
    unknown_tools = [
        tool
        for tool in normalized_tools
        if tool_execution_class(str(tool.get("name") or "")) == "unknown"
    ]

    if read_tools:
        fact_requirement = "required"
    elif any(tool.get("name") == "no_tool" for tool in normalized_tools) or not normalized_tools:
        fact_requirement = "none"
    else:
        fact_requirement = "required" if unknown_tools else "none"

    return _drop_empty(
        {
            "schema_version": "tool_plan_preview_v2",
            "fact_requirement": fact_requirement,
            "read_tool_calls": read_tools,
            "required_fact_fields": _unique_field_list(read_tools),
            "stop_conditions": _unique_stop_conditions(read_tools),
            "customer_question_if_incomplete": {
                "required": False,
                "field": "",
                "goal": "",
            },
            "deferred_write_proposals": write_tools,
            "unknown_tools": unknown_tools,
            "tool_policy_violations": output.get("tool_policy_violations") or [],
            "migration_audit": _migration_audit(output),
            "source": "current_planner_output_shadow",
        }
    )


def _migration_audit(output: dict[str, Any]) -> dict[str, Any]:
    customer_visible = _present_fields(output, CUSTOMER_VISIBLE_FIELDS)
    business_semantics = _present_fields(output, BUSINESS_SEMANTIC_FIELDS)
    tool_fields = _present_fields(output, TOOL_PLANNER_TARGET_FIELDS)
    residue = [*customer_visible, *business_semantics]
    return _drop_empty(
        {
            "schema_version": "tool_planner_migration_audit_v1",
            "purpose": "shadow_only_identify_legacy_planner_semantic_residue",
            "target_contract": {
                "tool_planner_must_not_own": [
                    "customer_visible_text",
                    "sales_psychology",
                    "closing_move",
                    "complex_customer_state",
                ],
                "reply_final_brain_owns": [
                    "final_customer_visible_messages",
                    "complex_turn_outcome",
                    "single_mainline_action",
                ],
            },
            "customer_visible_fields_present": customer_visible,
            "business_semantic_fields_present": business_semantics,
            "tool_planner_fields_present": tool_fields,
            "legacy_residue_count": len(residue),
            "tool_planner_only_ready": not residue,
            "review_required_before_migration": bool(residue),
        }
    )


def _present_fields(output: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if _has_value(output.get(field))]


def _has_value(value: Any) -> bool:
    if value in ("", None, [], {}):
        return False
    return True


def _normalize_tool(tool: dict[str, Any], *, index: int) -> dict[str, Any]:
    name = str(tool.get("name") or "").strip()
    purpose = str(tool.get("purpose") or tool.get("reason") or "").strip()
    arguments = {key: value for key, value in tool.items() if key not in {"name", "purpose", "reason"}}
    return _drop_empty(
        {
            "call_id": str(tool.get("call_id") or f"{name or 'tool'}_{index}"),
            "name": name,
            "purpose": purpose,
            "arguments": arguments,
            "depends_on": tool.get("depends_on") if isinstance(tool.get("depends_on"), list) else [],
        }
    )


def _read_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "")
    contract = read_only_tool_contract(name)
    return _drop_empty(
        {
            "call_id": tool.get("call_id"),
            "name": name,
            "tool": name,
            "arguments": tool.get("arguments") or {},
            "purpose": tool.get("purpose") or "",
            "depends_on": tool.get("depends_on") or [],
            "required_fact_fields": contract.get("required_fact_fields") or [],
            "stop_conditions": contract.get("stop_conditions") or [],
        }
    )


def _write_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "")
    return _drop_empty(
        {
            "call_id": tool.get("call_id"),
            "name": name,
            "tool": name,
            "arguments": tool.get("arguments") or {},
            "purpose": tool.get("purpose") or "deferred write proposed by current Planner output",
            "depends_on": tool.get("depends_on") or [],
            "execution": "deferred_write_only",
        }
    )


def _unique_field_list(read_tools: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for tool in read_tools:
        for field in tool.get("required_fact_fields") or []:
            if isinstance(field, str) and field and field not in fields:
                fields.append(field)
    return fields


def _unique_stop_conditions(read_tools: list[dict[str, Any]]) -> list[str]:
    conditions: list[str] = []
    for tool in read_tools:
        for condition in tool.get("stop_conditions") or []:
            if isinstance(condition, str) and condition and condition not in conditions:
                conditions.append(condition)
    return conditions


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
