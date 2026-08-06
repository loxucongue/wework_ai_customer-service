from __future__ import annotations

from app.config import Settings


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
