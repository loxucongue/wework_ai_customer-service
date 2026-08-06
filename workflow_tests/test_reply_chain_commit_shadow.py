from __future__ import annotations

from app.services.reply_chain_commit_shadow import reply_chain_commit_shadow


def test_commit_shadow_describes_non_isolated_reply_side_effects() -> None:
    shadow = reply_chain_commit_shadow(
        final_state={
            "test_isolated": False,
            "sales_contact_key": "sales-contact-1",
            "request_context": {"memory_persist_allowed": True},
        },
        reply_messages=[{"type": "text", "content": {"text": "ok"}}],
        allow_empty_reply=False,
    )

    assert shadow["schema_version"] == "reply_chain_commit_shadow_v1"
    assert shadow["commit_phase_owner"] == "runtime_after_reply_validation"
    assert shadow["requires_reply_validation_before_commit"] is True
    assert shadow["precommit_validation_audit"]["schema_version"] == "reply_chain_precommit_validation_audit_v1"
    assert shadow["precommit_validation_audit"]["ready_for_commit_shadow"] is True
    assert shadow["precommit_validation_audit"]["has_customer_visible_reply"] is True
    assert shadow["precommit_validation_audit"]["conversation_write_allowed"] is True
    assert shadow["precommit_validation_audit"]["memory_write_allowed"] is True
    inventory = shadow["write_action_inventory"]
    assert inventory["schema_version"] == "reply_chain_write_action_inventory_v1"
    assert inventory["commit_phase_owner"] == "runtime_after_reply_validation"
    assert inventory["requires_reply_validation_before_write"] is True
    assert inventory["reply_validation_evidence"]["has_customer_visible_reply"] is True
    assert inventory["ready_for_commit_refactor_review"] is True
    assert inventory["blockers"] == []
    runtime_action_ids = {
        action["id"]
        for action in inventory["actions"]
        if action["runtime_write"] is True
    }
    assert {
        "conversation_assistant_message",
        "case_image_memory_record",
        "activity_intro_image_memory_record",
        "visible_store_fact_memory_record",
        "trace_log_write",
        "run_record_save",
    }.issubset(runtime_action_ids)
    assert shadow["planned_side_effects"]["conversation_assistant_message"] is True
    assert shadow["planned_side_effects"]["case_image_memory_record"] is True
    assert shadow["planned_side_effects"]["activity_intro_image_memory_record"] is True
    assert shadow["planned_side_effects"]["visible_store_fact_memory_record"] is True
    assert shadow["planned_side_effects"]["deferred_write_tool_execution"] is False
    assert shadow["planned_side_effects"]["trace_log_write"] is True
    assert shadow["planned_side_effects"]["run_record_save"] is True
    assert "sop_chat_gate" in shadow["must_not_be_owned_by"]
    assert "tool_planner" in shadow["must_not_be_owned_by"]


def test_commit_shadow_blocks_customer_writes_for_isolated_reply() -> None:
    shadow = reply_chain_commit_shadow(
        final_state={
            "test_isolated": True,
            "sales_contact_key": "sales-contact-1",
            "request_context": {"memory_persist_allowed": True},
        },
        reply_messages=[{"type": "text", "content": {"text": "ok"}}],
        allow_empty_reply=False,
    )

    assert shadow["test_isolated"] is True
    assert shadow["precommit_validation_audit"]["ready_for_commit_shadow"] is True
    assert shadow["precommit_validation_audit"]["conversation_write_allowed"] is False
    assert shadow["precommit_validation_audit"]["memory_write_allowed"] is False
    assert shadow["planned_side_effects"]["conversation_assistant_message"] is False
    assert shadow["planned_side_effects"]["case_image_memory_record"] is False
    assert shadow["planned_side_effects"]["activity_intro_image_memory_record"] is False
    assert shadow["planned_side_effects"]["visible_store_fact_memory_record"] is False
    assert shadow["planned_side_effects"]["trace_log_write"] is True
    assert shadow["planned_side_effects"]["run_record_save"] is True
    inventory = shadow["write_action_inventory"]
    customer_writes = [
        action
        for action in inventory["actions"]
        if action["category"] in {"customer_visible_history", "memory"} and action["runtime_write"] is True
    ]
    assert customer_writes == []
    skipped = {action["id"]: action["skipped_reason"] for action in inventory["actions"]}
    assert skipped["conversation_assistant_message"] == "test_isolated"
    assert skipped["case_image_memory_record"] == "test_isolated"


def test_commit_shadow_records_memory_persistence_blockers_without_blocking_commit() -> None:
    shadow = reply_chain_commit_shadow(
        final_state={
            "test_isolated": False,
            "request_context": {"memory_persist_allowed": True},
        },
        reply_messages=[{"type": "text", "content": {"text": "ok"}}],
        allow_empty_reply=False,
    )

    audit = shadow["precommit_validation_audit"]
    assert audit["ready_for_commit_shadow"] is True
    assert audit["memory_write_allowed"] is False
    assert audit["memory_persistence_blockers"] == ["missing_sales_contact_key"]
    assert shadow["planned_side_effects"]["conversation_assistant_message"] is True
    assert shadow["planned_side_effects"]["case_image_memory_record"] is False


def test_commit_shadow_blocks_unpermitted_empty_reply_before_commit_switch() -> None:
    shadow = reply_chain_commit_shadow(
        final_state={"test_isolated": False, "request_context": {"memory_persist_allowed": False}},
        reply_messages=[],
        allow_empty_reply=False,
    )

    audit = shadow["precommit_validation_audit"]
    assert audit["ready_for_commit_shadow"] is False
    assert audit["blockers"] == ["empty_reply_not_allowed_before_commit"]
    assert shadow["planned_side_effects"]["conversation_assistant_message"] is False


def test_commit_shadow_allows_empty_reply_when_runtime_control_permits_it() -> None:
    shadow = reply_chain_commit_shadow(
        final_state={
            "test_isolated": False,
            "reply_source": "platform_superseded",
            "reply_control": {"sync_return": {"type": "empty"}},
        },
        reply_messages=[],
        allow_empty_reply=True,
    )

    audit = shadow["precommit_validation_audit"]
    assert audit["ready_for_commit_shadow"] is True
    assert audit["empty_reply_permitted"] is True
    assert audit["reply_source"] == "platform_superseded"
    assert audit["sync_return_type"] == "empty"


def test_commit_shadow_hands_deferred_write_tools_to_post_reply_commit_phase() -> None:
    shadow = reply_chain_commit_shadow(
        final_state={
            "test_isolated": False,
            "request_context": {"memory_persist_allowed": False},
            "tool_plan_preview": {
                "schema_version": "tool_plan_preview_v2",
                "deferred_write_proposals": [
                    {
                        "call_id": "create_work_1",
                        "tool": "create_work_order",
                        "execution": "deferred_write_only",
                        "purpose": "create order after payment registration",
                    },
                    {
                        "call_id": "mobile_1",
                        "tool": "add_customer_mobile",
                        "execution": "deferred_write_only",
                    },
                ],
            },
        },
        reply_messages=[{"type": "text", "content": {"text": "ok"}}],
        allow_empty_reply=False,
    )

    audit = shadow["deferred_write_handoff_audit"]
    assert audit["schema_version"] == "reply_chain_deferred_write_handoff_audit_v1"
    assert audit["commit_phase_owner"] == "runtime_after_reply_validation"
    assert audit["proposed_write_count"] == 2
    assert [tool["tool"] for tool in audit["proposed_write_tools"]] == ["create_work_order", "add_customer_mobile"]
    assert audit["early_execution_forbidden"] is True
    assert audit["current_runtime_executes_deferred_writes"] is False
    assert audit["requires_reply_validation_before_write"] is True
    assert audit["requires_explicit_commit_executor_before_activation"] is True
    assert audit["ready_for_deferred_write_refactor_review"] is True
    assert shadow["planned_side_effects"]["deferred_write_tool_execution"] is False
    inventory = shadow["write_action_inventory"]
    deferred_actions = [
        action
        for action in inventory["actions"]
        if action["category"] == "deferred_tool_write"
    ]
    assert [action["repository"] for action in deferred_actions] == ["create_work_order", "add_customer_mobile"]
    assert all(action["runtime_write"] is False for action in deferred_actions)
    assert all(action["execution_phase"] == "deferred_after_reply_validation" for action in deferred_actions)


def test_commit_shadow_flags_deferred_write_handoff_contract_violations() -> None:
    shadow = reply_chain_commit_shadow(
        final_state={
            "tool_plan_preview": {
                "deferred_write_proposals": [
                    {"tool": "create_work_order", "execution": "execute_now"},
                    {"execution": "deferred_write_only"},
                ]
            }
        },
        reply_messages=[{"type": "text", "content": {"text": "ok"}}],
        allow_empty_reply=False,
    )

    audit = shadow["deferred_write_handoff_audit"]
    assert audit["ready_for_deferred_write_refactor_review"] is False
    assert "deferred_write_execution_not_deferred:create_work_order" in audit["blockers"]
    assert "deferred_write_missing_tool" in audit["blockers"]
    inventory = shadow["write_action_inventory"]
    assert inventory["ready_for_commit_refactor_review"] is False
    assert "deferred_write_handoff:deferred_write_execution_not_deferred:create_work_order" in inventory["blockers"]
