from __future__ import annotations

from typing import Any


READ_ONLY_TOOL_NAMES = {
    "appointment_record_query",
    "available_time",
    "customer_store_lookup",
    "distance_calculate",
    "kb_search",
    "professional_assist",
}

READ_ONLY_TOOL_CONTRACTS = {
    "appointment_record_query": {
        "required_fact_fields": ["appointment_records"],
        "stop_conditions": ["appointment record cache is available"],
    },
    "available_time": {
        "required_fact_fields": ["store_id", "date", "available_time_slots"],
        "stop_conditions": ["available slots or empty slot fact returned"],
    },
    "customer_store_lookup": {
        "required_fact_fields": ["resolved_location", "visible_store_candidates"],
        "stop_conditions": ["visible store candidates or an explicit empty/ambiguous fact returned"],
    },
    "distance_calculate": {
        "required_fact_fields": ["origin", "ranked_store_candidates"],
        "stop_conditions": ["candidate stores are ranked or distance fact is unavailable"],
    },
    "kb_search": {
        "required_fact_fields": ["knowledge_facts"],
        "stop_conditions": ["matching knowledge facts or empty result returned"],
    },
    "professional_assist": {
        "required_fact_fields": ["assist_reason"],
        "stop_conditions": ["professional assist handoff fact is recorded"],
    },
}

DEFERRED_WRITE_TOOL_NAMES = {
    "add_customer_mobile",
    "check_customer",
    "create_order_plan",
    "create_work_order",
}


def tool_plan_preview_from_planner_output(output: dict[str, Any]) -> dict[str, Any]:
    """Extract a future Tool Planner view from the current Planner output."""

    tools = output.get("planner_tool_calls") if isinstance(output.get("planner_tool_calls"), list) else []
    if not tools:
        tools = output.get("required_tools") if isinstance(output.get("required_tools"), list) else []
    normalized_tools = [_normalize_tool(tool, index=index) for index, tool in enumerate(tools, start=1) if isinstance(tool, dict)]
    normalized_tools = [tool for tool in normalized_tools if tool.get("name")]

    read_tools = [_read_tool_schema(tool) for tool in normalized_tools if tool.get("name") in READ_ONLY_TOOL_NAMES]
    write_tools = [_write_tool_schema(tool) for tool in normalized_tools if tool.get("name") in DEFERRED_WRITE_TOOL_NAMES]
    unknown_tools = [
        tool
        for tool in normalized_tools
        if tool.get("name") not in READ_ONLY_TOOL_NAMES
        and tool.get("name") not in DEFERRED_WRITE_TOOL_NAMES
        and tool.get("name") != "no_tool"
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
            "source": "current_planner_output_shadow",
        }
    )


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
    contract = READ_ONLY_TOOL_CONTRACTS.get(name, {})
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
