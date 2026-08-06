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
    assert shadow["planned_side_effects"]["conversation_assistant_message"] is False
    assert shadow["planned_side_effects"]["case_image_memory_record"] is False
    assert shadow["planned_side_effects"]["activity_intro_image_memory_record"] is False
    assert shadow["planned_side_effects"]["visible_store_fact_memory_record"] is False
    assert shadow["planned_side_effects"]["trace_log_write"] is True
    assert shadow["planned_side_effects"]["run_record_save"] is True
