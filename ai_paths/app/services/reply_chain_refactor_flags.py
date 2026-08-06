from __future__ import annotations

from typing import Any


FLAG_NAMES = (
    "parallel_gate_planner_enabled",
    "parallel_gate_planner_shadow",
    "sop_chat_gate_v2_enabled",
    "tool_planner_v2_enabled",
    "reply_final_brain_v2_enabled",
    "gate_direct_reply_enabled",
    "read_tool_early_execution_enabled",
    "deferred_write_execution_enabled",
)


def reply_chain_refactor_flag_snapshot(settings: Any | None) -> dict[str, Any]:
    """Return a centralized safety view for reply-chain refactor flags."""

    flags = {name: bool(getattr(settings, name, _default_for(name))) for name in FLAG_NAMES}
    blockers = _flag_blockers(flags)
    return {
        "schema_version": "reply_chain_refactor_flags_v1",
        "flags": flags,
        "mode": _mode(flags),
        "safe_for_current_runtime": not flags["parallel_gate_planner_enabled"],
        "safe_for_shadow_observation": flags["parallel_gate_planner_shadow"] and not flags["parallel_gate_planner_enabled"],
        "activation_blockers": blockers,
        "can_enable_parallel_runner": not blockers,
        "source": "settings",
    }


def _flag_blockers(flags: dict[str, bool]) -> list[str]:
    blockers: list[str] = []
    if not flags["parallel_gate_planner_enabled"]:
        blockers.append("parallel_runner_disabled")
    if flags["parallel_gate_planner_enabled"] and not flags["sop_chat_gate_v2_enabled"]:
        blockers.append("sop_chat_gate_v2_required")
    if flags["parallel_gate_planner_enabled"] and not flags["tool_planner_v2_enabled"]:
        blockers.append("tool_planner_v2_required")
    if flags["parallel_gate_planner_enabled"] and not flags["reply_final_brain_v2_enabled"]:
        blockers.append("reply_final_brain_v2_required")
    if flags["gate_direct_reply_enabled"] and not flags["parallel_gate_planner_enabled"]:
        blockers.append("gate_direct_reply_requires_parallel_runner")
    if flags["read_tool_early_execution_enabled"] and not flags["parallel_gate_planner_enabled"]:
        blockers.append("read_tool_early_execution_requires_parallel_runner")
    if flags["deferred_write_execution_enabled"] and not flags["parallel_gate_planner_enabled"]:
        blockers.append("deferred_write_execution_requires_parallel_runner")
    if flags["deferred_write_execution_enabled"] and flags["parallel_gate_planner_shadow"]:
        blockers.append("deferred_writes_forbidden_in_shadow")
    return blockers


def _mode(flags: dict[str, bool]) -> str:
    if flags["parallel_gate_planner_enabled"]:
        return "parallel_runner_requested"
    if flags["parallel_gate_planner_shadow"]:
        return "shadow_only"
    return "legacy_serial_only"


def _default_for(name: str) -> bool:
    return name == "parallel_gate_planner_shadow"
