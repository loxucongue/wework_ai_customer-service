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
    normalized_tools = [_normalize_tool(tool) for tool in tools if isinstance(tool, dict)]
    normalized_tools = [tool for tool in normalized_tools if tool.get("name")]

    read_tools = [tool for tool in normalized_tools if tool.get("name") in READ_ONLY_TOOL_NAMES]
    write_tools = [tool for tool in normalized_tools if tool.get("name") in DEFERRED_WRITE_TOOL_NAMES]
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
        fact_requirement = "write_deferred"

    return _drop_empty(
        {
            "schema_version": "tool_plan_preview_v1",
            "fact_requirement": fact_requirement,
            "read_tool_calls": read_tools,
            "deferred_write_proposals": write_tools,
            "unknown_tools": unknown_tools,
            "tool_policy_violations": output.get("tool_policy_violations") or [],
            "source": "current_planner_output_shadow",
        }
    )


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "").strip()
    purpose = str(tool.get("purpose") or tool.get("reason") or "").strip()
    arguments = {key: value for key, value in tool.items() if key not in {"name", "purpose", "reason"}}
    return _drop_empty({"name": name, "purpose": purpose, "arguments": arguments})


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
