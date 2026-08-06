from __future__ import annotations

import json

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import _planner_payload_for_model
from app.services.parallel_reply_chain_diagnostics import parallel_reply_chain_diagnostics


def test_diagnostics_reports_runner_integration_as_next_step_when_contract_ready() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
            "target_topology": {"final_expression_owner": "reply"},
        },
    )

    assert diagnostics["schema_version"] == "parallel_reply_chain_diagnostics_v1"
    assert diagnostics["phase"] == "ready_for_runner_integration"
    assert diagnostics["next_safe_step"] == "wire_shadow_runner_without_runtime_behavior_change"
    assert diagnostics["contract"]["final_expression_owner"] == "reply"
    assert diagnostics["safety"]["diagnostic_only"] is True


def test_diagnostics_reports_contract_blockers_before_runner_work() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {
                "ready_for_shadow_parallel_runner": False,
                "blockers": ["missing_gate_router_shadow"],
            },
        },
    )

    assert diagnostics["phase"] == "contract_blocked"
    assert diagnostics["next_safe_step"] == "fix_shadow_contract_or_flag_blockers"
    assert diagnostics["contract"]["blockers"] == ["missing_gate_router_shadow"]


def test_diagnostics_reports_runner_branch_errors() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow={
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "mode": "completed_shadow",
            "branches": {
                "sop_chat_gate": {"status": "completed"},
                "tool_planner": {"status": "error"},
            },
        },
    )

    assert diagnostics["phase"] == "runner_blocked"
    assert "branch_error:tool_planner" in diagnostics["runner"]["blockers"]
    assert diagnostics["runner"]["branch_status"]["tool_planner"] == "error"


def test_diagnostics_reports_ready_for_shadow_comparison_after_runner_success() -> None:
    diagnostics = parallel_reply_chain_diagnostics(
        parallel_reply_chain_shadow={
            "schema_version": "parallel_reply_chain_shadow_v1",
            "activation": {"ready_for_shadow_parallel_runner": True, "blockers": []},
        },
        runner_shadow={
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "mode": "completed_shadow",
            "branches": {
                "sop_chat_gate": {"status": "completed"},
                "tool_planner": {"status": "completed"},
            },
        },
    )

    assert diagnostics["phase"] == "ready_for_shadow_comparison"
    assert diagnostics["next_safe_step"] == "collect_old_vs_new_shadow_diffs_before_behavior_switch"


def test_diagnostics_are_not_consumed_by_current_model_payloads() -> None:
    state = {
        "normalized_content": "怎么预约",
        "conversation_history": ["用户: 怎么预约"],
        "parallel_reply_chain_diagnostics": {
            "schema_version": "parallel_reply_chain_diagnostics_v1",
            "next_safe_step": "shadow-only-diagnostics-marker",
        },
        "request_context": {},
    }

    planner_payload = _planner_payload_for_model(state)
    reply_payload = reply_user_payload_for_model(state)
    combined = json.dumps([planner_payload, reply_payload], ensure_ascii=False)

    assert "parallel_reply_chain_diagnostics" not in planner_payload
    assert "parallel_reply_chain_diagnostics" not in reply_payload
    assert "shadow-only-diagnostics-marker" not in combined
