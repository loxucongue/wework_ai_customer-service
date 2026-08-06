from __future__ import annotations

from typing import Any


def reply_chain_commit_shadow(
    *,
    final_state: dict[str, Any],
    reply_messages: list[dict[str, Any]],
    allow_empty_reply: bool,
) -> dict[str, Any]:
    """Describe final commit side effects without changing runtime behavior."""

    has_reply = bool(reply_messages)
    test_isolated = bool(final_state.get("test_isolated"))
    memory_allowed = _memory_persistence_allowed(final_state)
    return {
        "schema_version": "reply_chain_commit_shadow_v1",
        "mode": "observed_current_runtime_commit_plan",
        "reply_message_count": len(reply_messages),
        "allow_empty_reply": bool(allow_empty_reply),
        "test_isolated": test_isolated,
        "memory_persistence_allowed": memory_allowed,
        "commit_phase_owner": "runtime_after_reply_validation",
        "requires_reply_validation_before_commit": True,
        "planned_side_effects": {
            "conversation_assistant_message": has_reply and not test_isolated,
            "case_image_memory_record": has_reply and not test_isolated and memory_allowed,
            "activity_intro_image_memory_record": has_reply and not test_isolated and memory_allowed,
            "visible_store_fact_memory_record": has_reply and not test_isolated and memory_allowed,
            "trace_log_write": True,
            "run_record_save": True,
        },
        "must_not_be_owned_by": ["sop_chat_gate", "tool_planner", "reply_chain_join"],
        "source": "chat_runtime_persist_and_build_response_shadow",
    }


def _memory_persistence_allowed(state: dict[str, Any]) -> bool:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    return bool(request_context.get("memory_persist_allowed")) and bool(str(state.get("sales_contact_key") or "").strip())
