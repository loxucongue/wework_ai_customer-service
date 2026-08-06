from __future__ import annotations

from typing import Literal


ToolExecutionClass = Literal["read_only", "deferred_write", "no_tool", "unknown"]


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


def tool_execution_class(tool_name: str) -> ToolExecutionClass:
    name = str(tool_name or "").strip()
    if not name or name == "no_tool":
        return "no_tool"
    if name in READ_ONLY_TOOL_NAMES:
        return "read_only"
    if name in DEFERRED_WRITE_TOOL_NAMES:
        return "deferred_write"
    return "unknown"


def read_only_tool_contract(tool_name: str) -> dict[str, list[str]]:
    contract = READ_ONLY_TOOL_CONTRACTS.get(str(tool_name or "").strip(), {})
    return {
        "required_fact_fields": list(contract.get("required_fact_fields") or []),
        "stop_conditions": list(contract.get("stop_conditions") or []),
    }
