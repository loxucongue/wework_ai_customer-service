from __future__ import annotations

import asyncio

from scripts.run_business_rule_model_matrix import (
    _call_with_transient_retry,
    _planner_requested_tools,
    _planner_result_has_transient_recovery_failure,
    _review_messages,
)


def test_matrix_runner_retries_transient_timeout_with_per_attempt_deadline() -> None:
    async def run() -> int:
        calls = 0

        async def slow_call() -> dict[str, bool]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return {"ok": True}

        try:
            await _call_with_transient_retry(
                slow_call,
                attempts=3,
                timeout_seconds=0.01,
                retry_delay_seconds=0,
            )
        except TimeoutError:
            return calls
        raise AssertionError("expected the per-attempt timeout to fail all retries")

    assert asyncio.run(run()) == 3


def test_matrix_runner_only_injects_tool_facts_after_planner_requests_tools() -> None:
    assert not _planner_requested_tools(
        {
            "planner_decision": "direct_reply",
            "planner_tool_calls": [{"name": "customer_store_lookup"}],
        }
    )
    assert not _planner_requested_tools(
        {
            "planner_decision": "need_tools",
            "planner_tool_calls": [],
        }
    )
    assert _planner_requested_tools(
        {
            "planner_decision": "need_tools",
            "planner_tool_calls": [{"name": "customer_store_lookup"}],
        }
    )


def test_matrix_runner_retries_planner_when_transient_recovery_leaves_violations() -> None:
    assert _planner_result_has_transient_recovery_failure(
        (
            {"tool_policy_violations": [{"missing": "need_tools_requires_executable_tool"}]},
            {
                "initial_error": "Model HTTP 502",
                "nested_calls": [{"name": "planner_brain_repair", "error": "TimeoutError"}],
            },
        )
    )


def test_matrix_reviewer_receives_current_business_rules_as_fact_source() -> None:
    messages = _review_messages(
        [
            {
                "run_id": "reply:price:1",
                "current_message": "多少钱",
                "conversation_history": [],
                "semantic_goal": "准确回答当前活动价",
                "planner_plan": {},
                "reply_messages": [{"type": "text", "content": "活动价268元。"}],
                "reply_payload": {
                    "business_rules": {"offer": {"price": 268}},
                    "tool_facts": {},
                    "transaction_facts": {},
                },
                "hard_errors": [],
            }
        ]
    )

    assert "business_rules" in messages[1]["content"]
    assert "268" in messages[1]["content"]
    assert "当前 business_rules" in messages[0]["content"]
    assert not _planner_result_has_transient_recovery_failure(
        (
            {"tool_policy_violations": []},
            {"initial_error": "Model HTTP 502"},
        )
    )
    assert _planner_result_has_transient_recovery_failure(
        (
            {"planner_sub_rule_id": "PLANNER_SYSTEM_UNAVAILABLE", "tool_policy_violations": []},
            {"initial_error": "Model HTTP 502"},
        )
    )
