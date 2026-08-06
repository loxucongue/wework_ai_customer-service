from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any


Branch = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


SHADOW_ONLY_FIELDS = (
    "sop_gate_preview",
    "sop_gate_router_shadow",
    "reply_chain_shadow_context",
    "tool_plan_preview",
    "read_only_tool_executor_shadow",
    "reply_chain_join_shadow",
    "reply_final_brain_handoff_shadow",
    "parallel_reply_chain_shadow",
    "reply_chain_refactor_flags",
    "parallel_gate_planner_runner_shadow",
    "parallel_reply_chain_diagnostics",
    "parallel_reply_chain_comparison",
)

REQUIRED_BRANCH_OUTPUT_SCHEMAS = {
    "sop_chat_gate": ("gate_router_shadow", "chat_gate_router_shadow_v1"),
    "tool_planner": ("tool_plan_preview", "tool_plan_preview_v2"),
}


async def run_parallel_gate_planner_shadow(
    *,
    initial_state: dict[str, Any],
    gate_branch: Branch,
    planner_branch: Branch,
    refactor_flags: dict[str, Any],
) -> dict[str, Any]:
    """Run Gate and Tool Planner shadow branches concurrently.

    The production runtime does not call this yet. This runner exists to
    validate orchestration semantics before any behavior switch.
    """

    if not _shadow_allowed(refactor_flags):
        return {
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "mode": "skipped",
            "reason": "shadow_observation_not_allowed",
            "refactor_mode": refactor_flags.get("mode"),
            "activation_blockers": refactor_flags.get("activation_blockers") or [],
        }

    started = time.perf_counter()
    initial_snapshot = copy.deepcopy(initial_state)
    gate_state = _branch_state(initial_state)
    planner_state = _branch_state(initial_state)
    gate_task = asyncio.create_task(_run_branch("sop_chat_gate", gate_branch, gate_state))
    planner_task = asyncio.create_task(_run_branch("tool_planner", planner_branch, planner_state))
    gate_result, planner_result = await asyncio.gather(gate_task, planner_task)
    branches = {
        "sop_chat_gate": gate_result,
        "tool_planner": planner_result,
    }
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "schema_version": "parallel_gate_planner_runner_shadow_v1",
        "mode": "completed_shadow",
        "input_isolation_audit": _input_isolation_audit(
            initial_state=initial_state,
            initial_snapshot=initial_snapshot,
            gate_state=gate_state,
            planner_state=planner_state,
        ),
        "branch_output_contract_audit": _branch_output_contract_audit(branches),
        "branches": branches,
        "metrics": {
            "elapsed_ms": elapsed_ms,
            "sum_branch_duration_ms": _duration(gate_result) + _duration(planner_result),
            "estimated_serial_savings_ms": max(0, _duration(gate_result) + _duration(planner_result) - elapsed_ms),
        },
        "safety": {
            "no_runtime_behavior_change": True,
            "branch_state_isolated": True,
            "initial_state_unchanged": initial_state == initial_snapshot,
            "no_customer_messages_sent": True,
            "no_database_writes": True,
        },
    }


async def replay_parallel_gate_planner_shadow_from_serial_outputs(
    *,
    initial_state: dict[str, Any],
    gate_router_shadow: dict[str, Any],
    tool_plan_preview: dict[str, Any],
    refactor_flags: dict[str, Any],
    parallel_reply_chain_shadow: dict[str, Any],
) -> dict[str, Any]:
    """Replay existing serial shadow outputs through the parallel runner contract.

    This adapter is intentionally not a new business path. It does not call
    models, execute tools, write state, or generate customer-visible messages.
    It only validates runner orchestration shape using outputs already produced
    by the current serial runtime.
    """

    if not _parallel_contract_ready(parallel_reply_chain_shadow):
        return {
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "mode": "skipped",
            "input_mode": "serial_outputs_adapter",
            "reason": "parallel_contract_not_ready",
            "activation_blockers": _parallel_contract_blockers(parallel_reply_chain_shadow),
            "safety": _serial_adapter_safety(),
        }

    async def gate_branch(_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "serial_gate_router_shadow",
            "gate_router_shadow": copy.deepcopy(gate_router_shadow),
        }

    async def planner_branch(_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "serial_tool_plan_preview",
            "tool_plan_preview": copy.deepcopy(tool_plan_preview),
        }

    result = await run_parallel_gate_planner_shadow(
        initial_state=initial_state,
        gate_branch=gate_branch,
        planner_branch=planner_branch,
        refactor_flags=refactor_flags,
    )
    result["input_mode"] = "serial_outputs_adapter"
    result["source"] = "serial_outputs_replayed_through_shadow_runner"
    result["safety"] = {
        **dict(result.get("safety") or {}),
        **_serial_adapter_safety(),
    }
    return result


async def _run_branch(name: str, branch: Branch, state: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        output = await branch(state)
        return {
            "status": "completed",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "output": output if isinstance(output, dict) else {"value": output},
        }
    except Exception as exc:
        return {
            "status": "error",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _branch_state(initial_state: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(initial_state)


def _input_isolation_audit(
    *,
    initial_state: dict[str, Any],
    initial_snapshot: dict[str, Any],
    gate_state: dict[str, Any],
    planner_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "parallel_branch_input_isolation_audit_v1",
        "branch_states_are_distinct_objects": gate_state is not planner_state,
        "gate_state_is_not_initial_state": gate_state is not initial_state,
        "planner_state_is_not_initial_state": planner_state is not initial_state,
        "initial_state_unchanged_after_branches": initial_state == initial_snapshot,
        "shadow_only_fields_present_in_initial_state": [
            field for field in SHADOW_ONLY_FIELDS if field in initial_state
        ],
        "target_parallel_input_requires_no_branch_outputs": True,
        "source": "runner_shadow_input_copy_audit",
    }


def _branch_output_contract_audit(branches: dict[str, dict[str, Any]]) -> dict[str, Any]:
    blockers: list[str] = []
    required_outputs: dict[str, dict[str, Any]] = {}
    for branch_name, (field_name, schema_version) in REQUIRED_BRANCH_OUTPUT_SCHEMAS.items():
        branch = branches.get(branch_name)
        output = branch.get("output") if isinstance(branch, dict) else None
        field = output.get(field_name) if isinstance(output, dict) else None
        observed_schema = field.get("schema_version") if isinstance(field, dict) else None
        status = branch.get("status") if isinstance(branch, dict) else None
        valid = status == "completed" and observed_schema == schema_version
        if not valid:
            if status != "completed":
                blockers.append(f"branch_not_completed:{branch_name}")
            elif not isinstance(output, dict):
                blockers.append(f"branch_output_not_dict:{branch_name}")
            elif not isinstance(field, dict):
                blockers.append(f"branch_missing_required_output:{branch_name}.{field_name}")
            else:
                blockers.append(f"branch_output_schema_mismatch:{branch_name}.{field_name}:{observed_schema or 'missing'}")
        required_outputs[branch_name] = {
            "required_field": field_name,
            "required_schema_version": schema_version,
            "observed_schema_version": observed_schema,
            "valid": valid,
        }
    return {
        "schema_version": "parallel_branch_output_contract_audit_v1",
        "ready": not blockers,
        "blockers": blockers,
        "required_outputs": required_outputs,
        "source": "runner_shadow_branch_output_contract_audit",
    }


def _duration(result: dict[str, Any]) -> int:
    try:
        return int(result.get("duration_ms") or 0)
    except (TypeError, ValueError):
        return 0


def _shadow_allowed(refactor_flags: dict[str, Any]) -> bool:
    return bool(refactor_flags.get("safe_for_shadow_observation"))


def _parallel_contract_ready(parallel_reply_chain_shadow: dict[str, Any]) -> bool:
    if parallel_reply_chain_shadow.get("schema_version") != "parallel_reply_chain_shadow_v1":
        return False
    return bool((parallel_reply_chain_shadow.get("activation") or {}).get("ready_for_shadow_parallel_runner"))


def _parallel_contract_blockers(parallel_reply_chain_shadow: dict[str, Any]) -> list[str]:
    if parallel_reply_chain_shadow.get("schema_version") != "parallel_reply_chain_shadow_v1":
        return ["missing_parallel_reply_chain_shadow"]
    blockers = (parallel_reply_chain_shadow.get("activation") or {}).get("blockers")
    if not isinstance(blockers, list):
        return []
    return [item for item in blockers if isinstance(item, str) and item]


def _serial_adapter_safety() -> dict[str, bool]:
    return {
        "serial_outputs_replayed": True,
        "no_new_model_calls": True,
        "no_tool_execution": True,
        "no_runtime_behavior_change": True,
        "no_customer_messages_sent": True,
        "no_database_writes": True,
    }
