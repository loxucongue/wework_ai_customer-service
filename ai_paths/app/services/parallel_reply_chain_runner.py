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
