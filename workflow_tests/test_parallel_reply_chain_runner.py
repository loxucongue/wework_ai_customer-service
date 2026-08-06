from __future__ import annotations

import asyncio

from app.services.parallel_reply_chain_runner import (
    replay_parallel_gate_planner_shadow_from_serial_outputs,
    run_parallel_gate_planner_shadow,
)


def test_parallel_runner_shadow_runs_gate_and_planner_concurrently() -> None:
    asyncio.run(_parallel_runner_shadow_runs_gate_and_planner_concurrently())


async def _parallel_runner_shadow_runs_gate_and_planner_concurrently() -> None:
    gate_started = asyncio.Event()
    planner_started = asyncio.Event()

    async def gate_branch(state: dict) -> dict:
        gate_started.set()
        await planner_started.wait()
        await asyncio.sleep(0.01)
        return {"route_suggestion": "content_only_reply", "request_id": state["request_id"]}

    async def planner_branch(state: dict) -> dict:
        planner_started.set()
        await gate_started.wait()
        await asyncio.sleep(0.01)
        return {"fact_requirement": "none", "request_id": state["request_id"]}

    result = await run_parallel_gate_planner_shadow(
        initial_state={"request_id": "req-1"},
        gate_branch=gate_branch,
        planner_branch=planner_branch,
        refactor_flags={"safe_for_shadow_observation": True, "mode": "shadow_only"},
    )

    assert result["schema_version"] == "parallel_gate_planner_runner_shadow_v1"
    assert result["mode"] == "completed_shadow"
    assert result["branches"]["sop_chat_gate"]["status"] == "completed"
    assert result["branches"]["tool_planner"]["status"] == "completed"
    assert result["branches"]["sop_chat_gate"]["output"]["request_id"] == "req-1"
    assert result["branches"]["tool_planner"]["output"]["request_id"] == "req-1"
    assert result["input_isolation_audit"]["schema_version"] == "parallel_branch_input_isolation_audit_v1"
    assert result["input_isolation_audit"]["branch_states_are_distinct_objects"] is True
    assert result["input_isolation_audit"]["initial_state_unchanged_after_branches"] is True
    assert result["safety"]["no_runtime_behavior_change"] is True
    assert result["safety"]["initial_state_unchanged"] is True


def test_parallel_runner_shadow_isolates_branch_state() -> None:
    asyncio.run(_parallel_runner_shadow_isolates_branch_state())


async def _parallel_runner_shadow_isolates_branch_state() -> None:
    async def gate_branch(state: dict) -> dict:
        state["nested"]["gate"] = True
        return {"nested": state["nested"]}

    async def planner_branch(state: dict) -> dict:
        state["nested"]["planner"] = True
        return {"nested": state["nested"]}

    initial_state = {"nested": {"original": True}}
    result = await run_parallel_gate_planner_shadow(
        initial_state=initial_state,
        gate_branch=gate_branch,
        planner_branch=planner_branch,
        refactor_flags={"safe_for_shadow_observation": True},
    )

    assert initial_state == {"nested": {"original": True}}
    assert result["branches"]["sop_chat_gate"]["output"]["nested"] == {"original": True, "gate": True}
    assert result["branches"]["tool_planner"]["output"]["nested"] == {"original": True, "planner": True}
    assert result["input_isolation_audit"]["gate_state_is_not_initial_state"] is True
    assert result["input_isolation_audit"]["planner_state_is_not_initial_state"] is True
    assert result["safety"]["branch_state_isolated"] is True


def test_parallel_runner_shadow_reports_shadow_fields_present_in_initial_state() -> None:
    asyncio.run(_parallel_runner_shadow_reports_shadow_fields_present_in_initial_state())


async def _parallel_runner_shadow_reports_shadow_fields_present_in_initial_state() -> None:
    async def branch(_state: dict) -> dict:
        return {"ok": True}

    result = await run_parallel_gate_planner_shadow(
        initial_state={
            "request_id": "req-shadow-fields",
            "sop_gate_router_shadow": {"schema_version": "chat_gate_router_shadow_v1"},
            "tool_plan_preview": {"schema_version": "tool_plan_preview_v2"},
        },
        gate_branch=branch,
        planner_branch=branch,
        refactor_flags={"safe_for_shadow_observation": True},
    )

    assert result["mode"] == "completed_shadow"
    assert result["input_isolation_audit"]["shadow_only_fields_present_in_initial_state"] == [
        "sop_gate_router_shadow",
        "tool_plan_preview",
    ]
    assert result["input_isolation_audit"]["target_parallel_input_requires_no_branch_outputs"] is True


def test_parallel_runner_shadow_skips_when_flags_do_not_allow_shadow() -> None:
    asyncio.run(_parallel_runner_shadow_skips_when_flags_do_not_allow_shadow())


async def _parallel_runner_shadow_skips_when_flags_do_not_allow_shadow() -> None:
    called = False

    async def branch(_state: dict) -> dict:
        nonlocal called
        called = True
        return {}

    result = await run_parallel_gate_planner_shadow(
        initial_state={},
        gate_branch=branch,
        planner_branch=branch,
        refactor_flags={
            "safe_for_shadow_observation": False,
            "mode": "parallel_runner_requested",
            "activation_blockers": ["sop_chat_gate_v2_required"],
        },
    )

    assert called is False
    assert result["mode"] == "skipped"
    assert result["reason"] == "shadow_observation_not_allowed"
    assert result["activation_blockers"] == ["sop_chat_gate_v2_required"]


def test_parallel_runner_shadow_captures_branch_errors_without_raising() -> None:
    asyncio.run(_parallel_runner_shadow_captures_branch_errors_without_raising())


async def _parallel_runner_shadow_captures_branch_errors_without_raising() -> None:
    async def gate_branch(_state: dict) -> dict:
        raise RuntimeError("gate failed")

    async def planner_branch(_state: dict) -> dict:
        return {"fact_requirement": "none"}

    result = await run_parallel_gate_planner_shadow(
        initial_state={},
        gate_branch=gate_branch,
        planner_branch=planner_branch,
        refactor_flags={"safe_for_shadow_observation": True},
    )

    assert result["mode"] == "completed_shadow"
    assert result["branches"]["sop_chat_gate"]["status"] == "error"
    assert "RuntimeError: gate failed" in result["branches"]["sop_chat_gate"]["error"]
    assert result["branches"]["tool_planner"]["status"] == "completed"


def test_serial_output_adapter_replays_existing_shadows_without_new_work() -> None:
    asyncio.run(_serial_output_adapter_replays_existing_shadows_without_new_work())


async def _serial_output_adapter_replays_existing_shadows_without_new_work() -> None:
    gate_shadow = {"schema_version": "chat_gate_router_shadow_v1", "route_suggestion": "tools_only"}
    tool_plan = {"schema_version": "tool_plan_preview_v2", "fact_requirement": "required"}
    result = await replay_parallel_gate_planner_shadow_from_serial_outputs(
        initial_state={"request_id": "req-serial"},
        gate_router_shadow=gate_shadow,
        tool_plan_preview=tool_plan,
        refactor_flags={"safe_for_shadow_observation": True, "mode": "shadow_only"},
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
    )

    assert result["mode"] == "completed_shadow"
    assert result["input_mode"] == "serial_outputs_adapter"
    assert result["source"] == "serial_outputs_replayed_through_shadow_runner"
    assert result["branches"]["sop_chat_gate"]["output"]["source"] == "serial_gate_router_shadow"
    assert result["branches"]["sop_chat_gate"]["output"]["gate_router_shadow"] == gate_shadow
    assert result["branches"]["tool_planner"]["output"]["source"] == "serial_tool_plan_preview"
    assert result["branches"]["tool_planner"]["output"]["tool_plan_preview"] == tool_plan
    assert result["safety"]["no_new_model_calls"] is True
    assert result["safety"]["no_tool_execution"] is True


def test_serial_output_adapter_skips_when_parallel_contract_is_blocked() -> None:
    asyncio.run(_serial_output_adapter_skips_when_parallel_contract_is_blocked())


async def _serial_output_adapter_skips_when_parallel_contract_is_blocked() -> None:
    result = await replay_parallel_gate_planner_shadow_from_serial_outputs(
        initial_state={},
        gate_router_shadow={},
        tool_plan_preview={},
        refactor_flags={"safe_for_shadow_observation": True},
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {
                "ready_for_shadow_parallel_runner": False,
                "blockers": ["missing_gate_router_shadow"],
            },
        },
    )

    assert result["mode"] == "skipped"
    assert result["input_mode"] == "serial_outputs_adapter"
    assert result["reason"] == "parallel_contract_not_ready"
    assert result["activation_blockers"] == ["missing_gate_router_shadow"]
    assert result["safety"]["no_new_model_calls"] is True
