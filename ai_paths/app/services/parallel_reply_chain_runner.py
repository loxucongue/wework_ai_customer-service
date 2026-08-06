from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any


Branch = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


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
    gate_state = _branch_state(initial_state)
    planner_state = _branch_state(initial_state)
    gate_task = asyncio.create_task(_run_branch("sop_chat_gate", gate_branch, gate_state))
    planner_task = asyncio.create_task(_run_branch("tool_planner", planner_branch, planner_state))
    gate_result, planner_result = await asyncio.gather(gate_task, planner_task)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "schema_version": "parallel_gate_planner_runner_shadow_v1",
        "mode": "completed_shadow",
        "branches": {
            "sop_chat_gate": gate_result,
            "tool_planner": planner_result,
        },
        "metrics": {
            "elapsed_ms": elapsed_ms,
            "sum_branch_duration_ms": _duration(gate_result) + _duration(planner_result),
            "estimated_serial_savings_ms": max(0, _duration(gate_result) + _duration(planner_result) - elapsed_ms),
        },
        "safety": {
            "no_runtime_behavior_change": True,
            "branch_state_isolated": True,
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
