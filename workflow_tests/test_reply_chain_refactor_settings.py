from __future__ import annotations

import json

from app.config import Settings
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.reply_chain_refactor_flags import reply_chain_refactor_flag_snapshot


def test_reply_chain_refactor_flags_default_to_safe_shadow_mode() -> None:
    settings = Settings(_env_file=None)

    assert settings.parallel_gate_planner_enabled is False
    assert settings.parallel_gate_planner_shadow is True
    assert settings.sop_chat_gate_v2_enabled is False
    assert settings.tool_planner_v2_enabled is False
    assert settings.gate_direct_reply_enabled is False
    assert settings.read_tool_early_execution_enabled is False
    assert settings.deferred_write_execution_enabled is False


def test_reply_chain_refactor_flags_can_be_overridden_explicitly() -> None:
    settings = Settings(
        _env_file=None,
        PARALLEL_GATE_PLANNER_ENABLED=True,
        PARALLEL_GATE_PLANNER_SHADOW=False,
        SOP_CHAT_GATE_V2_ENABLED=True,
        TOOL_PLANNER_V2_ENABLED=True,
        GATE_DIRECT_REPLY_ENABLED=True,
        READ_TOOL_EARLY_EXECUTION_ENABLED=True,
        DEFERRED_WRITE_EXECUTION_ENABLED=True,
    )

    assert settings.parallel_gate_planner_enabled is True
    assert settings.parallel_gate_planner_shadow is False
    assert settings.sop_chat_gate_v2_enabled is True
    assert settings.tool_planner_v2_enabled is True
    assert settings.gate_direct_reply_enabled is True
    assert settings.read_tool_early_execution_enabled is True
    assert settings.deferred_write_execution_enabled is True


def test_reply_chain_refactor_flag_snapshot_blocks_parallel_by_default() -> None:
    snapshot = reply_chain_refactor_flag_snapshot(Settings(_env_file=None))

    assert snapshot["schema_version"] == "reply_chain_refactor_flags_v1"
    assert snapshot["mode"] == "shadow_only"
    assert snapshot["safe_for_current_runtime"] is True
    assert snapshot["safe_for_shadow_observation"] is True
    assert snapshot["can_enable_parallel_runner"] is False
    assert "parallel_runner_disabled" in snapshot["activation_blockers"]


def test_reply_chain_refactor_flag_snapshot_requires_v2_before_parallel_runner() -> None:
    snapshot = reply_chain_refactor_flag_snapshot(
        Settings(
            _env_file=None,
            PARALLEL_GATE_PLANNER_ENABLED=True,
            SOP_CHAT_GATE_V2_ENABLED=False,
            TOOL_PLANNER_V2_ENABLED=False,
        )
    )

    assert snapshot["mode"] == "parallel_runner_requested"
    assert snapshot["safe_for_current_runtime"] is False
    assert "sop_chat_gate_v2_required" in snapshot["activation_blockers"]
    assert "tool_planner_v2_required" in snapshot["activation_blockers"]
    assert snapshot["can_enable_parallel_runner"] is False


def test_reply_chain_refactor_flags_are_not_consumed_by_current_model_payloads() -> None:
    state = {
        "normalized_content": "怎么预约",
        "conversation_history": ["用户: 怎么预约"],
        "reply_chain_refactor_flags": {
            "schema_version": "reply_chain_refactor_flags_v1",
            "flags": {"shadow_only_marker": True},
            "activation_blockers": ["shadow-only-flag-marker"],
        },
        "request_context": {},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    assert "reply_chain_refactor_flags" not in planner_payload
    assert "reply_chain_refactor_flags" not in reply_payload
    assert "shadow-only-flag-marker" not in combined
