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
    precommit_audit = _precommit_validation_audit(
        final_state=final_state,
        reply_messages=reply_messages,
        allow_empty_reply=allow_empty_reply,
        test_isolated=test_isolated,
        memory_allowed=memory_allowed,
    )
    return {
        "schema_version": "reply_chain_commit_shadow_v1",
        "mode": "observed_current_runtime_commit_plan",
        "reply_message_count": len(reply_messages),
        "allow_empty_reply": bool(allow_empty_reply),
        "test_isolated": test_isolated,
        "memory_persistence_allowed": memory_allowed,
        "commit_phase_owner": "runtime_after_reply_validation",
        "requires_reply_validation_before_commit": True,
        "precommit_validation_audit": precommit_audit,
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


def _precommit_validation_audit(
    *,
    final_state: dict[str, Any],
    reply_messages: list[dict[str, Any]],
    allow_empty_reply: bool,
    test_isolated: bool,
    memory_allowed: bool,
) -> dict[str, Any]:
    has_reply = bool(reply_messages)
    blockers: list[str] = []
    if not has_reply and not allow_empty_reply:
        blockers.append("empty_reply_not_allowed_before_commit")
    return {
        "schema_version": "reply_chain_precommit_validation_audit_v1",
        "reply_message_count": len(reply_messages),
        "has_customer_visible_reply": has_reply,
        "allow_empty_reply": bool(allow_empty_reply),
        "empty_reply_permitted": has_reply or bool(allow_empty_reply),
        "reply_source": str(final_state.get("reply_source") or ""),
        "sync_return_type": _sync_return_type(final_state),
        "test_isolated": test_isolated,
        "conversation_write_allowed": has_reply and not test_isolated,
        "memory_write_allowed": has_reply and not test_isolated and memory_allowed,
        "memory_persistence_blockers": _memory_persistence_blockers(final_state, memory_allowed=memory_allowed),
        "ready_for_commit_shadow": not blockers,
        "blockers": blockers,
        "source": "reply_chain_commit_shadow_precommit_audit",
    }


def _sync_return_type(state: dict[str, Any]) -> str:
    reply_control = state.get("reply_control") if isinstance(state.get("reply_control"), dict) else {}
    sync_return = reply_control.get("sync_return") if isinstance(reply_control.get("sync_return"), dict) else {}
    return str(sync_return.get("type") or "")


def _memory_persistence_blockers(state: dict[str, Any], *, memory_allowed: bool) -> list[str]:
    if memory_allowed:
        return []
    blockers: list[str] = []
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    if not request_context.get("memory_persist_allowed"):
        blockers.append("memory_persist_not_allowed_by_request_context")
    if not str(state.get("sales_contact_key") or "").strip():
        blockers.append("missing_sales_contact_key")
    return blockers


def _memory_persistence_allowed(state: dict[str, Any]) -> bool:
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    return bool(request_context.get("memory_persist_allowed")) and bool(str(state.get("sales_contact_key") or "").strip())
