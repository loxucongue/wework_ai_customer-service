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
    deferred_write_audit = _deferred_write_handoff_audit(final_state)
    planned_side_effects = {
        "conversation_assistant_message": has_reply and not test_isolated,
        "case_image_memory_record": has_reply and not test_isolated and memory_allowed,
        "activity_intro_image_memory_record": has_reply and not test_isolated and memory_allowed,
        "visible_store_fact_memory_record": has_reply and not test_isolated and memory_allowed,
        "deferred_write_tool_execution": False,
        "trace_log_write": True,
        "run_record_save": True,
    }
    write_inventory = _write_action_inventory(
        final_state=final_state,
        reply_messages=reply_messages,
        allow_empty_reply=allow_empty_reply,
        test_isolated=test_isolated,
        memory_allowed=memory_allowed,
        precommit_audit=precommit_audit,
        deferred_write_audit=deferred_write_audit,
        planned_side_effects=planned_side_effects,
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
        "deferred_write_handoff_audit": deferred_write_audit,
        "write_action_inventory": write_inventory,
        "planned_side_effects": planned_side_effects,
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


def _deferred_write_handoff_audit(final_state: dict[str, Any]) -> dict[str, Any]:
    tool_plan = final_state.get("tool_plan_preview")
    if not isinstance(tool_plan, dict):
        tool_plan = {}
    proposals = [
        proposal
        for proposal in tool_plan.get("deferred_write_proposals") or []
        if isinstance(proposal, dict)
    ]
    normalized = [_deferred_write_proposal(proposal) for proposal in proposals]
    blockers: list[str] = []
    for proposal in normalized:
        if proposal.get("execution") != "deferred_write_only":
            blockers.append(f"deferred_write_execution_not_deferred:{proposal.get('tool') or 'missing'}")
        if not proposal.get("tool"):
            blockers.append("deferred_write_missing_tool")
    return {
        "schema_version": "reply_chain_deferred_write_handoff_audit_v1",
        "proposed_write_count": len(normalized),
        "proposed_write_tools": normalized,
        "commit_phase_owner": "runtime_after_reply_validation",
        "early_execution_forbidden": True,
        "current_runtime_executes_deferred_writes": False,
        "requires_reply_validation_before_write": True,
        "requires_explicit_commit_executor_before_activation": bool(normalized),
        "ready_for_deferred_write_refactor_review": not blockers,
        "blockers": blockers,
    }


def _write_action_inventory(
    *,
    final_state: dict[str, Any],
    reply_messages: list[dict[str, Any]],
    allow_empty_reply: bool,
    test_isolated: bool,
    memory_allowed: bool,
    precommit_audit: dict[str, Any],
    deferred_write_audit: dict[str, Any],
    planned_side_effects: dict[str, bool],
) -> dict[str, Any]:
    actions = [
        _write_action(
            "conversation_assistant_message",
            category="customer_visible_history",
            runtime_write=planned_side_effects.get("conversation_assistant_message") is True,
            execution_phase="after_reply_validation",
            repository="conversation_repository.add_assistant_message",
            skipped_reason=_skip_reason(
                has_reply=bool(reply_messages),
                test_isolated=test_isolated,
                memory_allowed=True,
                requires_memory=False,
            ),
        ),
        _write_action(
            "case_image_memory_record",
            category="memory",
            runtime_write=planned_side_effects.get("case_image_memory_record") is True,
            execution_phase="after_reply_validation",
            repository="memory_store.record_sent_case_images",
            skipped_reason=_skip_reason(
                has_reply=bool(reply_messages),
                test_isolated=test_isolated,
                memory_allowed=memory_allowed,
                requires_memory=True,
            ),
        ),
        _write_action(
            "activity_intro_image_memory_record",
            category="memory",
            runtime_write=planned_side_effects.get("activity_intro_image_memory_record") is True,
            execution_phase="after_reply_validation",
            repository="memory_store.record_activity_intro_image",
            skipped_reason=_skip_reason(
                has_reply=bool(reply_messages),
                test_isolated=test_isolated,
                memory_allowed=memory_allowed,
                requires_memory=True,
            ),
        ),
        _write_action(
            "visible_store_fact_memory_record",
            category="memory",
            runtime_write=planned_side_effects.get("visible_store_fact_memory_record") is True,
            execution_phase="after_reply_validation",
            repository="memory_store.record_visible_store_facts",
            skipped_reason=_skip_reason(
                has_reply=bool(reply_messages),
                test_isolated=test_isolated,
                memory_allowed=memory_allowed,
                requires_memory=True,
            ),
        ),
        _write_action(
            "trace_log_write",
            category="audit",
            runtime_write=planned_side_effects.get("trace_log_write") is True,
            execution_phase="after_reply_validation",
            repository="trace_logger.write_run",
        ),
        _write_action(
            "run_record_save",
            category="audit",
            runtime_write=planned_side_effects.get("run_record_save") is True,
            execution_phase="after_reply_validation",
            repository="run_repository.save_run",
        ),
    ]
    for proposal in deferred_write_audit.get("proposed_write_tools") or []:
        if not isinstance(proposal, dict):
            continue
        actions.append(
            _write_action(
                f"deferred_tool:{proposal.get('tool') or 'missing'}:{proposal.get('call_id') or 'missing'}",
                category="deferred_tool_write",
                runtime_write=False,
                execution_phase="deferred_after_reply_validation",
                repository=str(proposal.get("tool") or ""),
                skipped_reason="requires_explicit_commit_executor_before_activation",
                depends_on=proposal.get("depends_on") if isinstance(proposal.get("depends_on"), list) else [],
            )
        )
    blockers = _write_inventory_blockers(
        actions=actions,
        precommit_audit=precommit_audit,
        deferred_write_audit=deferred_write_audit,
        test_isolated=test_isolated,
    )
    return {
        "schema_version": "reply_chain_write_action_inventory_v1",
        "commit_phase_owner": "runtime_after_reply_validation",
        "requires_reply_validation_before_write": True,
        "reply_validation_evidence": {
            "reply_message_count": len(reply_messages),
            "has_customer_visible_reply": bool(reply_messages),
            "allow_empty_reply": bool(allow_empty_reply),
            "ready_for_commit_shadow": precommit_audit.get("ready_for_commit_shadow") is True,
            "reply_source": str(final_state.get("reply_source") or ""),
            "sync_return_type": _sync_return_type(final_state),
        },
        "action_count": len(actions),
        "runtime_write_count": sum(1 for action in actions if action.get("runtime_write") is True),
        "actions": actions,
        "all_runtime_writes_after_reply_validation": not blockers,
        "ready_for_commit_refactor_review": not blockers,
        "blockers": blockers,
        "source": "reply_chain_commit_shadow_write_action_inventory",
    }


def _write_action(
    action_id: str,
    *,
    category: str,
    runtime_write: bool,
    execution_phase: str,
    repository: str,
    skipped_reason: str = "",
    depends_on: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "category": category,
        "runtime_write": bool(runtime_write),
        "execution_phase": execution_phase,
        "owner": "runtime_after_reply_validation",
        "repository": repository,
        "skipped_reason": skipped_reason,
        "depends_on": depends_on or ["reply_validation"],
    }


def _skip_reason(
    *,
    has_reply: bool,
    test_isolated: bool,
    memory_allowed: bool,
    requires_memory: bool,
) -> str:
    if not has_reply:
        return "no_customer_visible_reply"
    if test_isolated:
        return "test_isolated"
    if requires_memory and not memory_allowed:
        return "memory_persistence_not_allowed"
    return ""


def _write_inventory_blockers(
    *,
    actions: list[dict[str, Any]],
    precommit_audit: dict[str, Any],
    deferred_write_audit: dict[str, Any],
    test_isolated: bool,
) -> list[str]:
    blockers: list[str] = []
    allowed_phases = {"after_reply_validation", "deferred_after_reply_validation"}
    precommit_ready = precommit_audit.get("ready_for_commit_shadow") is True
    for action in actions:
        action_id = str(action.get("id") or "missing")
        phase = str(action.get("execution_phase") or "")
        if phase not in allowed_phases:
            blockers.append(f"write_action_phase_not_after_reply_validation:{action_id}")
        if action.get("owner") != "runtime_after_reply_validation":
            blockers.append(f"write_action_owner_not_runtime_after_reply_validation:{action_id}")
        if action.get("runtime_write") is True and not precommit_ready and action.get("category") != "audit":
            blockers.append(f"write_allowed_without_ready_precommit:{action_id}")
        if test_isolated and action.get("runtime_write") is True and action.get("category") != "audit":
            blockers.append(f"isolated_customer_write_allowed:{action_id}")
        if action.get("category") == "deferred_tool_write" and action.get("runtime_write") is True:
            blockers.append(f"deferred_tool_write_executes_in_current_runtime:{action_id}")
    if deferred_write_audit.get("ready_for_deferred_write_refactor_review") is not True:
        for item in deferred_write_audit.get("blockers") or []:
            if isinstance(item, str) and item:
                blockers.append(f"deferred_write_handoff:{item}")
    return blockers


def _deferred_write_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(proposal.get("tool") or proposal.get("name") or "").strip()
    return {
        "call_id": str(proposal.get("call_id") or "").strip(),
        "tool": tool_name,
        "execution": str(proposal.get("execution") or "").strip(),
        "purpose": str(proposal.get("purpose") or "").strip(),
        "depends_on": proposal.get("depends_on") if isinstance(proposal.get("depends_on"), list) else [],
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
