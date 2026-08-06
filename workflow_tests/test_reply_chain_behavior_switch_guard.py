from __future__ import annotations

import json

from app.config import Settings
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.reply_chain_behavior_switch_guard import reply_chain_behavior_switch_guard
from app.services.reply_chain_refactor_flags import reply_chain_refactor_flag_snapshot


def _active_flag_snapshot() -> dict:
    return reply_chain_refactor_flag_snapshot(
        Settings(
            _env_file=None,
            PARALLEL_GATE_PLANNER_ENABLED=True,
            PARALLEL_GATE_PLANNER_SHADOW=False,
            SOP_CHAT_GATE_V2_ENABLED=True,
            TOOL_PLANNER_V2_ENABLED=True,
            REPLY_FINAL_BRAIN_V2_ENABLED=True,
        )
    )


def _shadow_bundle_ready() -> dict:
    return {
        "schema_version": "reply_chain_shadow_bundle_audit_v1",
        "phase": "postcommit",
        "ready_for_refactor_review": True,
        "blockers": [],
        "safety": {
            "does_not_approve_behavior_switch": True,
        },
    }


def _diagnostics_ready() -> dict:
    return {
        "schema_version": "parallel_reply_chain_diagnostics_v1",
        "phase": "ready_for_human_review",
        "release_review": {
            "schema_version": "reply_chain_release_review_checklist_v1",
            "missing_or_unproven_gates": [],
        },
    }


def _simulation_ready() -> dict:
    return {
        "schema_version": "offline_reply_chain_simulation_report_v1",
        "hard_error_count": 0,
        "semantic_pass_rate": 0.93,
        "failed_critical_scenarios": [],
    }


def _human_review_approved() -> dict:
    return {
        "schema_version": "reply_chain_human_review_approval_v1",
        "approved": True,
        "branch": "codex/reply-chain-refactor",
        "commit_sha": "abc123",
        "scope": "parallel_gate_planner_behavior_switch",
    }


def test_behavior_switch_guard_blocks_default_shadow_mode() -> None:
    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=reply_chain_refactor_flag_snapshot(Settings(_env_file=None)),
    )

    assert guard["schema_version"] == "reply_chain_behavior_switch_guard_v1"
    assert guard["behavior_switch_requested"] is False
    assert guard["can_enable_behavior_switch"] is False
    assert "behavior_switch_not_requested" in guard["blockers"]
    assert "flag_snapshot:parallel_runner_disabled" in guard["blockers"]
    assert "required_active_flag_missing:parallel_gate_planner_enabled" in guard["blockers"]
    assert "missing_reply_chain_shadow_bundle_audit" in guard["blockers"]
    assert guard["safety"]["does_not_enable_flags"] is True


def test_behavior_switch_guard_blocks_without_simulation_and_human_review() -> None:
    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "missing_offline_simulation_report" in guard["blockers"]
    assert "missing_human_review_approval" in guard["blockers"]


def test_behavior_switch_guard_blocks_unproven_release_review_gates() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = [
        "simulation_regression_review",
        "business_wording_freeze_review",
    ]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_gate_unproven:simulation_regression_review" in guard["blockers"]
    assert "release_review_gate_unproven:business_wording_freeze_review" in guard["blockers"]


def test_behavior_switch_guard_allows_only_with_complete_evidence() -> None:
    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["behavior_switch_requested"] is True
    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard
    assert guard["required_evidence"]["simulation_report"].startswith("offline full-chain")


def test_behavior_switch_guard_is_not_consumed_by_current_model_payloads() -> None:
    state = {
        "normalized_content": "how to book",
        "conversation_history": ["user: how to book"],
        "reply_chain_behavior_switch_guard": {
            "schema_version": "reply_chain_behavior_switch_guard_v1",
            "source": "behavior-switch-guard-marker",
        },
        "request_context": {},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    assert "reply_chain_behavior_switch_guard" not in planner_payload
    assert "reply_chain_behavior_switch_guard" not in reply_payload
    assert "behavior-switch-guard-marker" not in combined
