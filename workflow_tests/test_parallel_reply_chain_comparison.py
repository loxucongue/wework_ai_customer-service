from __future__ import annotations

from app.services.parallel_reply_chain_comparison import parallel_reply_chain_comparison


def test_comparison_matches_serial_replay_without_approving_behavior_switch() -> None:
    gate = {"schema_version": "chat_gate_router_shadow_v1", "route_suggestion": "tools_only"}
    tool_plan = {
        "schema_version": "tool_plan_preview_v2",
        "fact_requirement": "required",
        "read_tool_calls": [{"name": "customer_store_lookup", "arguments": {"query": "Wuhan"}}],
    }
    comparison = parallel_reply_chain_comparison(
        gate_router_shadow=gate,
        tool_plan_preview=tool_plan,
        join_shadow={"schema_version": "reply_chain_join_shadow_v1", "final_route": "reply_with_tools"},
        runner_shadow={
            "schema_version": "parallel_gate_planner_runner_shadow_v1",
            "mode": "completed_shadow",
            "branches": {
                "sop_chat_gate": {
                    "status": "completed",
                    "output": {"gate_router_shadow": gate},
                },
                "tool_planner": {
                    "status": "completed",
                    "output": {"tool_plan_preview": tool_plan},
                },
            },
        },
    )

    assert comparison["schema_version"] == "parallel_reply_chain_comparison_v1"
    assert comparison["status"] == "matched_shadow_replay"
    assert comparison["comparable"] is True
    assert comparison["review_gate"]["requires_human_review_before_behavior_switch"] is True
    assert comparison["review_gate"]["can_enable_behavior_switch"] is False
    assert comparison["safety"]["no_model_calls"] is True
    assert comparison["safety"]["no_tool_execution"] is True


def test_comparison_reports_route_and_tool_plan_diffs() -> None:
    comparison = parallel_reply_chain_comparison(
        gate_router_shadow={"route_suggestion": "content_only_reply"},
        tool_plan_preview={
            "fact_requirement": "none",
            "read_tool_calls": [],
        },
        join_shadow={"final_route": "reply_with_content"},
        runner_shadow={
            "mode": "completed_shadow",
            "branches": {
                "sop_chat_gate": {
                    "status": "completed",
                    "output": {"gate_router_shadow": {"route_suggestion": "tools_only"}},
                },
                "tool_planner": {
                    "status": "completed",
                    "output": {
                        "tool_plan_preview": {
                            "fact_requirement": "required",
                            "read_tool_calls": [{"name": "kb_search", "arguments": {"kind": "case_studies"}}],
                        }
                    },
                },
            },
        },
    )

    assert comparison["status"] == "diffs_found"
    assert {diff["field"] for diff in comparison["diffs"]} == {
        "gate_route",
        "fact_requirement",
        "read_tool_signatures",
    }


def test_comparison_blocks_when_runner_branch_failed() -> None:
    comparison = parallel_reply_chain_comparison(
        gate_router_shadow={"route_suggestion": "tools_only"},
        tool_plan_preview={"fact_requirement": "required"},
        join_shadow={},
        runner_shadow={
            "mode": "completed_shadow",
            "branches": {
                "sop_chat_gate": {"status": "completed", "output": {}},
                "tool_planner": {"status": "error", "error": "TimeoutError: slow"},
            },
        },
    )

    assert comparison["status"] == "not_comparable"
    assert comparison["comparable"] is False
    assert comparison["parallel_replay"]["branch_errors"] == ["tool_planner:TimeoutError: slow"]
