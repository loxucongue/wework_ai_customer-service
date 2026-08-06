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
    assert shadow["planned_side_effects"]["conversation_assistant_message"] is True
    assert shadow["planned_side_effects"]["case_image_memory_record"] is True
    assert shadow["planned_side_effects"]["activity_intro_image_memory_record"] is True
    assert shadow["planned_side_effects"]["visible_store_fact_memory_record"] is True
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
