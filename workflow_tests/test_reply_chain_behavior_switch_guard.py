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
        "components": {
            "reply_chain_commit_shadow": {
                "required_schema_version": "reply_chain_commit_shadow_v1",
                "observed_schema_version": "reply_chain_commit_shadow_v1",
                "present": True,
                "valid": True,
            }
        },
        "review_gates": {
            "commit_phase_ready": {
                "passed": True,
                "purpose": "writes_remain_after_reply_validation",
            }
        },
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
            "can_enable_behavior_switch": False,
            "missing_or_unproven_gates": [],
            "blocker_groups": {
                "manual_review": {
                    "ready": True,
                    "blocker_count": 0,
                }
            },
        },
    }


def _simulation_ready() -> dict:
    return {
        "schema_version": "offline_reply_chain_simulation_report_v1",
        "hard_error_count": 0,
        "semantic_pass_rate": 0.93,
        "failed_critical_scenarios": [],
        "summary": {
            "infrastructure_failures": 0,
            "acceptance": {
                "hard_errors_zero": True,
                "semantic_at_least_90": True,
                "critical_all_pass": True,
                "infrastructure_failures_zero": True,
                "scenario_coverage_complete": True,
            },
        },
        "coverage": {
            "schema_version": "offline_simulation_coverage_audit_v1",
            "missing_required_categories": [],
        },
        "review_artifacts": {
            "schema_version": "offline_simulation_review_artifacts_v1",
            "request_count": 10,
            "event_count": 3,
            "tool_call_count": 5,
            "outbox_batch_count": 4,
            "simulated_write_count": 2,
            "results": [{"scenario_id": "sim_case", "request_ids": ["sim_request_1"]}],
        },
        "safety": {
            "production_customer_messages_sent": False,
            "production_writes_allowed": False,
            "virtual_outbox_only": True,
            "production_write_count": 0,
        },
    }


def _model_matrix_ready() -> dict:
    return {
        "schema_version": "reply_chain_refactor_model_matrix_v1",
        "profiles_requested": ["claude", "gemini", "openai"],
        "profiles": [
            {
                "status": "completed",
                "model_profile": {"name": "claude", "model": "claude-opus-4-7"},
                "profile_summary": {
                    "semantic_pass_rate": 0.91,
                    "p50_ms": 6200,
                    "p90_ms": 11000,
                    "infrastructure_failures": 0,
                    "accepted_by_release_thresholds": True,
                },
            },
            {
                "status": "completed",
                "model_profile": {"name": "gemini", "model": "gemini-3.5-flash"},
                "profile_summary": {
                    "semantic_pass_rate": 0.9,
                    "p50_ms": 3900,
                    "p90_ms": 7600,
                    "infrastructure_failures": 0,
                    "accepted_by_release_thresholds": True,
                },
            },
            {
                "status": "completed",
                "model_profile": {"name": "openai", "model": "gpt-5.4"},
                "profile_summary": {
                    "semantic_pass_rate": 0.94,
                    "p50_ms": 4800,
                    "p90_ms": 8200,
                    "infrastructure_failures": 0,
                    "accepted_by_release_thresholds": True,
                },
            },
        ],
        "safety": {
            "api_keys_written_to_report": False,
            "production_customer_messages_sent": False,
            "production_writes_allowed": False,
        },
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
    assert "missing_model_matrix_report" in guard["blockers"]
    assert "missing_human_review_approval" in guard["blockers"]


def test_behavior_switch_guard_blocks_shadow_bundle_without_commit_phase_evidence() -> None:
    shadow = _shadow_bundle_ready()
    shadow["components"] = {}
    shadow["review_gates"] = {}

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=shadow,
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "shadow_bundle_commit_component_not_valid" in guard["blockers"]
    assert "shadow_bundle_commit_phase_gate_not_passed" in guard["blockers"]


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
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_gate_unproven:simulation_regression_review" not in guard["blockers"]
    assert "release_review_gate_unproven:business_wording_freeze_review" in guard["blockers"]
    assert guard["diagnostic_blocker_groups"]["manual_review"]["ready"] is True


def test_behavior_switch_guard_filters_externally_proven_simulation_and_model_matrix_gates() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = [
        "simulation_regression_review",
        "model_matrix_review",
    ]
    diagnostics["release_review"]["blocker_groups"] = {
        "manual_review": {
            "ready": False,
            "blocker_count": 2,
            "blockers": [
                "gate_not_proven:simulation_regression_review",
                "gate_not_proven:model_matrix_review",
            ],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard


def test_behavior_switch_guard_exposes_release_review_blocker_groups_for_review() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = ["reply_target_input_schema_review"]
    diagnostics["release_review"]["blocker_groups"] = {
        "reply_payload_schema": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:reply_target_input_schema_review"],
        },
        "manual_review": {
            "ready": True,
            "blocker_count": 0,
        },
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_gate_unproven:reply_target_input_schema_review" in guard["blockers"]
    assert guard["diagnostic_blocker_groups"]["reply_payload_schema"]["ready"] is False
    assert guard["diagnostic_blocker_groups"]["reply_payload_schema"]["blockers"] == [
        "gate_not_proven:reply_target_input_schema_review"
    ]


def test_behavior_switch_guard_blocks_unresolved_release_review_groups_even_without_flat_gates() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["missing_or_unproven_gates"] = []
    diagnostics["release_review"]["blocker_groups"] = {
        "reply_payload_schema": {
            "ready": False,
            "blocker_count": 1,
            "blockers": ["gate_not_proven:reply_target_input_schema_review"],
        }
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_blocker_group_unresolved:reply_payload_schema" in guard["blockers"]
    assert (
        "release_review_blocker_group:reply_payload_schema:gate_not_proven:reply_target_input_schema_review"
        in guard["blockers"]
    )


def test_behavior_switch_guard_blocks_release_review_that_claims_switch_approval() -> None:
    diagnostics = _diagnostics_ready()
    diagnostics["release_review"]["can_enable_behavior_switch"] = True

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=diagnostics,
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "release_review_missing_non_approval_marker" in guard["blockers"]


def test_behavior_switch_guard_allows_only_with_complete_evidence() -> None:
    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["behavior_switch_requested"] is True
    assert guard["can_enable_behavior_switch"] is True
    assert "blockers" not in guard
    assert guard["diagnostic_blocker_groups"]["manual_review"]["ready"] is True
    assert guard["required_evidence"]["simulation_report"].startswith("offline full-chain")
    assert guard["required_evidence"]["model_matrix_report"].startswith("three-model")


def test_behavior_switch_guard_blocks_invalid_model_matrix_report() -> None:
    model_matrix = _model_matrix_ready()
    model_matrix["profiles"] = [
        item
        for item in model_matrix["profiles"]
        if item["model_profile"]["name"] != "gemini"
    ]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=_simulation_ready(),
        model_matrix_report=model_matrix,
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "model_matrix_profile_not_completed:gemini" in guard["blockers"]


def test_behavior_switch_guard_blocks_simulation_without_isolation_safety() -> None:
    simulation = _simulation_ready()
    simulation["safety"] = {
        "production_customer_messages_sent": True,
        "production_writes_allowed": True,
        "virtual_outbox_only": False,
        "production_write_count": 2,
    }

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=simulation,
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "simulation_missing_no_customer_send_safety" in guard["blockers"]
    assert "simulation_missing_no_production_write_safety" in guard["blockers"]
    assert "simulation_missing_virtual_outbox_safety" in guard["blockers"]
    assert "simulation_production_writes:2" in guard["blockers"]


def test_behavior_switch_guard_blocks_simulation_without_required_category_coverage() -> None:
    simulation = _simulation_ready()
    simulation["summary"]["acceptance"]["scenario_coverage_complete"] = False
    simulation["coverage"]["missing_required_categories"] = ["精准问答", "预约金"]

    guard = reply_chain_behavior_switch_guard(
        flag_snapshot=_active_flag_snapshot(),
        shadow_bundle_audit=_shadow_bundle_ready(),
        diagnostics=_diagnostics_ready(),
        simulation_report=simulation,
        model_matrix_report=_model_matrix_ready(),
        human_review=_human_review_approved(),
    )

    assert guard["can_enable_behavior_switch"] is False
    assert "simulation_missing_required_category:精准问答" in guard["blockers"]
    assert "simulation_missing_required_category:预约金" in guard["blockers"]
    assert "simulation_scenario_coverage_incomplete" in guard["blockers"]


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
