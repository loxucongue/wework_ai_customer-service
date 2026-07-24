from __future__ import annotations

from typing import Any

from app.policies.sales_flow import mainline_pack_sort_key, mainline_stage_for_event_pack


ALLOWED_EVENT_DECISIONS = {
    "send",
    "merge",
    "send_ai_touch",
    "handoff_or_safety_notice",
    "skip",
    "defer",
    "handoff_to_ai_reply",
}
MAX_MERGED_SOP_PACKS = 2


def normalize_event_decision(
    raw: dict[str, Any],
    selector_input: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Normalize the model contract without choosing a business action for it."""

    output = dict(raw) if isinstance(raw, dict) else {}
    decision = _text(output.get("decision")).lower()
    if not decision:
        decision = "send" if bool(output.get("send_sop")) else "skip"

    violations: list[str] = []
    if decision not in ALLOWED_EVENT_DECISIONS:
        violations.append("invalid_event_decision")

    candidate_sops = selector_input.get("candidate_sops") if isinstance(selector_input.get("candidate_sops"), list) else []
    candidate_by_id = {
        _text(item.get("id")): item
        for item in candidate_sops
        if isinstance(item, dict) and _text(item.get("id"))
    }
    completed_ids = {
        _text(item)
        for item in selector_input.get("completed_sop_pack_ids") or []
        if _text(item)
    }
    completed_categories = {
        _text(item)
        for item in selector_input.get("completed_sop_categories") or []
        if _text(item)
    }
    mode = _text(selector_input.get("mode"))
    selected_ids = _selected_pack_ids(output)
    if mode == "platform_actions" and decision in {"skip", "defer", "send_ai_touch"} and _platform_actions_have_sendable_content(selector_input):
        decision = "send"
        output["decision"] = "send"
        output["strategy"] = _text(output.get("strategy")) or "platform_actions"
        output["reason"] = (
            _text(output.get("reason") or output.get("skip_reason"))
            or "platform_actions_have_sendable_content"
        )
        output["ai_touch_messages"] = []
        output["reply_messages"] = []

    if mode == "platform_actions":
        if selected_ids:
            violations.append("platform_actions_must_not_select_configured_pack")
        selected_ids = []
    elif decision in {"send", "merge"}:
        if not selected_ids:
            violations.append("selected_pack_ids_required")
        invalid_ids = [pack_id for pack_id in selected_ids if pack_id not in candidate_by_id]
        if invalid_ids:
            violations.append("selected_pack_id_not_in_candidates")
        elif selected_ids and not _packs_start_at_first_candidate(
            selected_ids,
            candidate_sops,
            selector_input=selector_input,
            model_output=output,
            completed_ids=completed_ids,
            completed_categories=completed_categories,
        ):
            violations.append("selected_packs_must_start_with_earliest_candidate")

    if decision == "send" and mode != "platform_actions" and len(selected_ids) != 1:
        violations.append("send_requires_exactly_one_pack")
    if decision == "merge":
        if mode == "platform_actions":
            violations.append("platform_actions_cannot_merge_configured_packs")
        if len(selected_ids) != MAX_MERGED_SOP_PACKS:
            violations.append("merge_requires_exactly_two_packs")
        elif not _packs_are_adjacent(selected_ids, candidate_sops):
            violations.append("merge_requires_adjacent_mainline_packs")
    if decision in {"send", "merge"} and any(
        _payment_gate_blocks_selection(candidate_by_id.get(pack_id), model_output=output)
        for pack_id in selected_ids
    ) and not _decision_removes_unsupported_payment_messages(
        output,
        selected_ids=selected_ids,
        candidate_by_id=candidate_by_id,
    ):
        violations.append("selected_payment_pack_not_currently_supported")
    if decision in {"send", "merge"} and _decision_removes_supported_payment_messages(
        output,
        selected_ids=selected_ids,
        candidate_by_id=candidate_by_id,
    ):
        violations.append("supported_payment_collection_must_not_be_removed")
    if decision in {"send", "merge"} and any(
        pack_id in completed_ids
        or _text((candidate_by_id.get(pack_id) or {}).get("sop_category")) in completed_categories
        for pack_id in selected_ids
    ):
        violations.append("selected_sop_pack_already_completed")
    if decision in {"skip", "defer", "handoff_to_ai_reply", "send_ai_touch", "handoff_or_safety_notice"} and selected_ids:
        violations.append("non_send_decision_must_not_select_pack")
    ai_touch_messages = _ai_touch_messages(output)
    if decision in {"send_ai_touch", "handoff_or_safety_notice"} and not ai_touch_messages:
        violations.append("ai_touch_messages_required")
    if decision not in {"send_ai_touch", "handoff_or_safety_notice"} and ai_touch_messages:
        violations.append("ai_touch_messages_only_allowed_for_touch_decision")

    event_policy = (
        selector_input.get("event_policy_evidence")
        if isinstance(selector_input.get("event_policy_evidence"), dict)
        else {}
    )
    ai_reply_policy = (
        event_policy.get("ai_reply_policy")
        if isinstance(event_policy.get("ai_reply_policy"), dict)
        else selector_input.get("ai_reply_policy")
        if isinstance(selector_input.get("ai_reply_policy"), dict)
        else {}
    )
    if decision == "handoff_to_ai_reply" and not bool(ai_reply_policy.get("allowed")):
        violations.append("handoff_to_ai_reply_not_allowed_for_proactive_event")
    strategy = _text(output.get("strategy"))
    if decision in {"skip", "defer"} and strategy in {"continue_mainline", "recover_backlog"}:
        violations.append("non_send_decision_conflicts_with_send_strategy")
    if (
        decision in {"skip", "defer"}
        and strategy == "frequency_guard"
        and not _frequency_guard_supported(event_policy)
    ):
        violations.append("frequency_guard_not_supported_by_event_evidence")
    if (
        decision in {"skip", "defer"}
        and strategy == "conflict_guard"
        and not _conflict_guard_supported(selector_input, candidate_sops, completed_ids, completed_categories)
    ):
        violations.append("conflict_guard_missing_evidence_source")
    if decision in {"skip", "defer"} and _repeated_candidates_should_be_ai_touch(
        selector_input=selector_input,
        candidates=candidate_sops,
        completed_ids=completed_ids,
        completed_categories=completed_categories,
        event_policy=event_policy,
        strategy=strategy,
    ):
        violations.append("repeated_candidates_should_use_ai_touch")
    if decision in {"skip", "defer"} and _completed_activity_with_deposit_candidate_should_continue(
        selector_input=selector_input,
        candidates=candidate_sops,
        event_policy=event_policy,
        strategy=strategy,
    ):
        violations.append("completed_activity_with_deposit_candidate_should_continue")
    if decision == "send_ai_touch" and _backlog_should_use_mainline_candidate(
        selector_input=selector_input,
        candidates=candidate_sops,
        completed_ids=completed_ids,
        completed_categories=completed_categories,
        event_policy=event_policy,
    ):
        violations.append("backlog_should_use_mainline_candidate")

    if decision not in {"send", "merge"}:
        output["text_adjustments"] = []
        output["message_operations"] = []

    output.update(
        {
            "decision": decision,
            "strategy": strategy,
            "selected_pack_ids": selected_ids,
            "merge_pack_ids": selected_ids if decision == "merge" else [],
            "send_sop": decision in {"send", "merge"},
            "sop_pack_id": selected_ids[0] if selected_ids else "",
            "need_ai_reply": decision == "handoff_to_ai_reply",
            "ai_touch_messages": ai_touch_messages if decision in {"send_ai_touch", "handoff_or_safety_notice"} else [],
            "touch_goal": _text(output.get("touch_goal")),
            "reason": _text(output.get("reason") or output.get("skip_reason")),
            "stage_skip_evidence": _stage_skip_evidence(output),
            "skip_reason": _text(output.get("skip_reason")),
            "frequency_reason": _text(output.get("frequency_reason")),
            "backlog_handling": _text(output.get("backlog_handling")) or "none",
            "suggested_next_window": _text(output.get("suggested_next_window")),
        }
    )
    return output, _unique(violations)


def _ai_touch_messages(output: dict[str, Any]) -> list[dict[str, Any]]:
    raw = output.get("ai_touch_messages")
    if not isinstance(raw, list):
        raw = output.get("reply_messages")
    if not isinstance(raw, list):
        return []
    messages: list[dict[str, Any]] = []
    for index, item in enumerate(raw[:2], start=1):
        if not isinstance(item, dict):
            continue
        message_type = _text(item.get("type")) or "text"
        if message_type != "text":
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = _text(content.get("text") or content.get("content"))
        else:
            text = _text(content)
        if not text:
            continue
        messages.append({"type": "text", "order": index, "content": {"text": text[:500]}})
    return messages


def _platform_actions_have_sendable_content(selector_input: dict[str, Any]) -> bool:
    raw = selector_input.get("platform_actions_summary")
    if isinstance(raw, dict) and _positive_int(raw.get("message_count")) > 0:
        return True
    actions = selector_input.get("platform_actions")
    if not isinstance(actions, dict):
        return False
    editable = actions.get("editable_text_messages")
    readonly = actions.get("readonly_messages")
    return bool(
        (isinstance(editable, list) and editable)
        or (isinstance(readonly, list) and readonly)
    )


def build_event_ai_reply_policy(
    conversation_activity: dict[str, Any],
    *,
    runtime_handoff_available: bool = False,
) -> dict[str, Any]:
    """Expose interface ownership facts; semantic routing remains a model decision."""

    has_unhandled_customer_message = bool(conversation_activity.get("latest_customer_pending_ai_reply"))
    allowed = bool(has_unhandled_customer_message and runtime_handoff_available)
    if not has_unhandled_customer_message:
        reason = "no_unhandled_customer_message"
    elif not runtime_handoff_available:
        reason = "ordinary_ai_reply_runtime_not_attached"
    else:
        reason = "unhandled_customer_message_and_runtime_available"
    return {
        "allowed": allowed,
        "has_unhandled_customer_message": has_unhandled_customer_message,
        "runtime_handoff_available": bool(runtime_handoff_available),
        "reason": reason,
    }


def combine_selected_pack_messages(packs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine configured messages while preserving pack and message order."""

    output: list[dict[str, Any]] = []
    order = 1
    for pack in sorted(packs, key=mainline_pack_sort_key):
        messages = pack.get("reply_messages") if isinstance(pack.get("reply_messages"), list) else []
        for message in sorted(
            (item for item in messages if isinstance(item, dict)),
            key=lambda item: int(item.get("order") or 0),
        ):
            item = dict(message)
            item["content"] = dict(message.get("content")) if isinstance(message.get("content"), dict) else message.get("content")
            item["order"] = order
            output.append(item)
            order += 1
    return output


def selected_candidate_packs(decision: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {_text(item.get("id")): item for item in candidates if isinstance(item, dict)}
    selected_ids = decision.get("selected_pack_ids") if isinstance(decision.get("selected_pack_ids"), list) else []
    if not selected_ids and _text(decision.get("sop_pack_id")):
        selected_ids = [_text(decision.get("sop_pack_id"))]
    return [by_id[pack_id] for pack_id in selected_ids if pack_id in by_id]


def _selected_pack_ids(output: dict[str, Any]) -> list[str]:
    raw = output.get("selected_pack_ids")
    if not isinstance(raw, list) or not raw:
        raw = output.get("merge_pack_ids")
    values = [_text(item) for item in raw] if isinstance(raw, list) else []
    pack_id = _text(output.get("sop_pack_id"))
    if not values and pack_id:
        values = [pack_id]
    return _unique([item for item in values if item])


def _packs_are_adjacent(selected_ids: list[str], candidates: list[dict[str, Any]]) -> bool:
    ordered_ids = [
        _text(item.get("id"))
        for item in sorted(
            (item for item in candidates if isinstance(item, dict) and _text(item.get("id"))),
            key=mainline_pack_sort_key,
        )
    ]
    try:
        positions = sorted(ordered_ids.index(pack_id) for pack_id in selected_ids)
    except ValueError:
        return False
    return len(positions) == 2 and positions[1] == positions[0] + 1


def _packs_start_at_first_candidate(
    selected_ids: list[str],
    candidates: list[dict[str, Any]],
    *,
    selector_input: dict[str, Any],
    model_output: dict[str, Any],
    completed_ids: set[str],
    completed_categories: set[str],
) -> bool:
    stage_status = _stage_status(selector_input)
    stage_skip_evidence = _stage_skip_evidence(model_output)
    ordered_ids = []
    for item in sorted(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and _text(item.get("id"))
            and _text(item.get("id")) not in completed_ids
            and _text(item.get("sop_category")) not in completed_categories
        ),
        key=mainline_pack_sort_key,
    ):
        pack_id = _text(item.get("id"))
        stage_id = mainline_stage_for_event_pack(item)
        if _stage_structurally_completed(stage_id, stage_status):
            continue
        ordered_ids.append(pack_id)
    if not selected_ids or not ordered_ids:
        return False
    if selected_ids == ordered_ids[: len(selected_ids)]:
        return True
    try:
        first_selected_position = ordered_ids.index(selected_ids[0])
    except ValueError:
        return False
    skipped = ordered_ids[:first_selected_position]
    if not skipped:
        return False
    candidate_by_id = {
        _text(item.get("id")): item
        for item in candidates
        if isinstance(item, dict) and _text(item.get("id"))
    }
    for pack_id in skipped:
        stage_id = mainline_stage_for_event_pack(candidate_by_id.get(pack_id) or {})
        if not _has_stage_skip_evidence(stage_skip_evidence, stage_id=stage_id, pack_id=pack_id):
            return False
    return selected_ids == ordered_ids[first_selected_position : first_selected_position + len(selected_ids)]


def _stage_status(selector_input: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = selector_input.get("mainline_stage_status")
    if not isinstance(raw, list):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        stage_id = _text(item.get("stage_id"))
        if stage_id:
            output[stage_id] = item
    return output


def _stage_structurally_completed(stage_id: str, stage_status: dict[str, dict[str, Any]]) -> bool:
    if not stage_id:
        return False
    item = stage_status.get(stage_id)
    return isinstance(item, dict) and bool(item.get("structural_completed"))


def _stage_skip_evidence(output: dict[str, Any]) -> list[dict[str, str]]:
    raw = output.get("stage_skip_evidence")
    if not isinstance(raw, list):
        return []
    evidence: list[dict[str, str]] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        stage_id = _text(item.get("stage_id"))
        pack_id = _text(item.get("pack_id"))
        text = _text(item.get("evidence") or item.get("reason"))
        if not text:
            continue
        if stage_id or pack_id:
            evidence.append({"stage_id": stage_id, "pack_id": pack_id, "evidence": text[:240]})
    return evidence


def _has_stage_skip_evidence(
    evidence: list[dict[str, str]],
    *,
    stage_id: str,
    pack_id: str,
) -> bool:
    for item in evidence:
        if pack_id and _text(item.get("pack_id")) == pack_id:
            return True
        if stage_id and _text(item.get("stage_id")) == stage_id:
            return True
    return False


def _payment_gate_blocks_selection(candidate: Any, *, model_output: dict[str, Any] | None = None) -> bool:
    if not isinstance(candidate, dict):
        return False
    gate = candidate.get("payment_collection_gate")
    if not isinstance(gate, dict):
        return False
    status = _text(gate.get("status"))
    if status in {"", "not_required", "supported"}:
        return False
    if status == "activity_intro_required" and _activity_intro_skip_evidence(model_output or {}):
        return False
    return True


def _activity_intro_skip_evidence(output: dict[str, Any]) -> bool:
    evidence = _stage_skip_evidence(output)
    return _has_stage_skip_evidence(
        evidence,
        stage_id="activity_and_price",
        pack_id="event_s10_price_quote_60min",
    ) or _has_stage_skip_evidence(
        evidence,
        stage_id="activity_and_price",
        pack_id="s10_activity_intro",
    )


def _decision_removes_unsupported_payment_messages(
    output: dict[str, Any],
    *,
    selected_ids: list[str],
    candidate_by_id: dict[str, Any],
) -> bool:
    """Allow a model-selected pack only after its unsupported cards are structurally removed."""

    removable_statuses = {"paid_skip_card"}
    unsupported_orders: set[int] = set()
    combined_order = 0
    has_editable_text = False

    for pack_id in selected_ids:
        candidate = candidate_by_id.get(pack_id)
        if not isinstance(candidate, dict):
            return False
        gate = candidate.get("payment_collection_gate")
        gate_status = _text(gate.get("status")) if isinstance(gate, dict) else ""
        if gate_status not in {"", "not_required", "supported"} and gate_status not in removable_statuses:
            return False

        messages: list[tuple[int, str]] = []
        for item in candidate.get("editable_text_messages") or []:
            if isinstance(item, dict):
                messages.append((_positive_int(item.get("order")), "text"))
                has_editable_text = True
        for item in candidate.get("readonly_messages") or []:
            if isinstance(item, dict):
                messages.append((_positive_int(item.get("order")), _text(item.get("type"))))

        for _, message_type in sorted(messages, key=lambda item: item[0]):
            combined_order += 1
            if gate_status in removable_statuses and message_type == "payment_collection":
                unsupported_orders.add(combined_order)

    if not unsupported_orders:
        return False

    operations = output.get("message_operations") if isinstance(output.get("message_operations"), list) else []
    removed_orders = {
        _positive_int(item.get("order"))
        for item in operations
        if isinstance(item, dict) and _text(item.get("op") or item.get("operation")) == "remove_message"
    }
    if not unsupported_orders.issubset(removed_orders):
        return False

    if not has_editable_text:
        return True
    text_adjustments = output.get("text_adjustments") if isinstance(output.get("text_adjustments"), list) else []
    text_operations = {
        "replace_text",
        "insert_text_before",
        "insert_text_after",
        "remove_text",
        "merge_text",
        "split_text",
    }
    return bool(text_adjustments) or any(
        isinstance(item, dict) and _text(item.get("op") or item.get("operation")) in text_operations
        for item in operations
    )


def _decision_removes_supported_payment_messages(
    output: dict[str, Any],
    *,
    selected_ids: list[str],
    candidate_by_id: dict[str, Any],
) -> bool:
    removed_orders = {
        _positive_int(item.get("order"))
        for item in output.get("message_operations") or []
        if isinstance(item, dict) and _text(item.get("op") or item.get("operation")) == "remove_message"
    }
    if not removed_orders:
        return False

    protected_orders: set[int] = set()
    combined_order = 0
    for pack_id in selected_ids:
        candidate = candidate_by_id.get(pack_id)
        if not isinstance(candidate, dict):
            continue
        gate = candidate.get("payment_collection_gate")
        gate_status = _text(gate.get("status")) if isinstance(gate, dict) else ""
        messages: list[tuple[int, str]] = []
        for item in candidate.get("editable_text_messages") or []:
            if isinstance(item, dict):
                messages.append((_positive_int(item.get("order")), "text"))
        for item in candidate.get("readonly_messages") or []:
            if isinstance(item, dict):
                messages.append((_positive_int(item.get("order")), _text(item.get("type"))))
        for _, message_type in sorted(messages, key=lambda item: item[0]):
            combined_order += 1
            if message_type == "payment_collection" and gate_status != "paid_skip_card":
                protected_orders.add(combined_order)
    return bool(removed_orders & protected_orders)


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _frequency_guard_supported(event_policy: dict[str, Any]) -> bool:
    touch_frequency = (
        event_policy.get("touch_frequency")
        if isinstance(event_policy.get("touch_frequency"), dict)
        else {}
    )
    pending_backlog = (
        event_policy.get("pending_backlog")
        if isinstance(event_policy.get("pending_backlog"), dict)
        else {}
    )
    return bool(
        touch_frequency.get("daily_soft_limit_reached")
        and touch_frequency.get("silent_soft_limit_reached")
        and not touch_frequency.get("has_new_customer_progress_since_last_touch")
        and not pending_backlog.get("has_pending")
    )


def _conflict_guard_supported(
    selector_input: dict[str, Any],
    candidates: list[dict[str, Any]],
    completed_ids: set[str],
    completed_categories: set[str],
) -> bool:
    recent = selector_input.get("recent_conversation")
    if isinstance(recent, list) and any(
        isinstance(item, dict)
        and _text(item.get("role") or item.get("direction")) == "customer"
        and bool(_text(item.get("content")))
        for item in recent
    ):
        return True
    if not candidates:
        return True
    if any(
        _text(item.get("id")) in completed_ids
        or _text(item.get("sop_category")) in completed_categories
        or _payment_gate_blocks_selection(item)
        for item in candidates
        if isinstance(item, dict)
    ):
        return True
    platform_gate = selector_input.get("platform_payment_collection_gate")
    return isinstance(platform_gate, dict) and _text(platform_gate.get("status")) not in {
        "",
        "not_required",
        "supported",
    }


def _repeated_candidates_should_be_ai_touch(
    *,
    selector_input: dict[str, Any],
    candidates: list[dict[str, Any]],
    completed_ids: set[str],
    completed_categories: set[str],
    event_policy: dict[str, Any],
    strategy: str,
) -> bool:
    if _text(selector_input.get("mode")) != "first_add_flow":
        return False
    if not candidates:
        return False
    if strategy == "frequency_guard" and _frequency_guard_supported(event_policy):
        return False
    if _has_customer_or_policy_block(selector_input, event_policy):
        return False
    candidate_ids = [
        _text(item.get("id"))
        for item in candidates
        if isinstance(item, dict) and _text(item.get("id"))
    ]
    if not candidate_ids:
        return False
    candidate_by_id = {
        _text(item.get("id")): item
        for item in candidates
        if isinstance(item, dict) and _text(item.get("id"))
    }
    completed_stages = _completed_mainline_stages(selector_input)
    return all(
        pack_id in completed_ids
        or _text((candidate_by_id.get(pack_id) or {}).get("sop_category")) in completed_categories
        or mainline_stage_for_event_pack(candidate_by_id.get(pack_id) or {}) in completed_stages
        for pack_id in candidate_ids
    )


def _backlog_should_use_mainline_candidate(
    *,
    selector_input: dict[str, Any],
    candidates: list[dict[str, Any]],
    completed_ids: set[str],
    completed_categories: set[str],
    event_policy: dict[str, Any],
) -> bool:
    if _text(selector_input.get("mode")) != "first_add_flow":
        return False
    if _has_customer_or_policy_block(selector_input, event_policy):
        return False
    pending_backlog = (
        event_policy.get("pending_backlog")
        if isinstance(event_policy.get("pending_backlog"), dict)
        else {}
    )
    backlog_count = _positive_int(event_policy.get("backlog_count"))
    has_backlog = bool(event_policy.get("quiet_hour_backlog")) or backlog_count >= 2 or bool(
        pending_backlog.get("has_pending")
    )
    if not has_backlog:
        return False
    completed_stages = _completed_mainline_stages(selector_input)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        pack_id = _text(item.get("id"))
        if not pack_id or pack_id in completed_ids:
            continue
        if _text(item.get("sop_category")) in completed_categories:
            continue
        if mainline_stage_for_event_pack(item) in completed_stages:
            continue
        if _payment_gate_blocks_selection(item):
            continue
        return True
    return False


def _has_customer_or_policy_block(selector_input: dict[str, Any], event_policy: dict[str, Any]) -> bool:
    activity = selector_input.get("conversation_activity")
    if not isinstance(activity, dict):
        activity = {}
    if any(
        bool(activity.get(key))
        for key in (
            "latest_customer_pending_ai_reply",
            "recent_active_chat",
            "active_chat_window",
        )
    ):
        return True
    if any(
        bool(event_policy.get(key))
        for key in (
            "customer_rejection",
            "active_chat_window",
            "health_or_medical_risk",
            "complaint_or_payment_risk",
            "payment_anomaly",
        )
    ):
        return True
    ai_reply_policy = event_policy.get("ai_reply_policy") if isinstance(event_policy.get("ai_reply_policy"), dict) else {}
    return bool(ai_reply_policy.get("has_unhandled_customer_message"))


def _completed_activity_with_deposit_candidate_should_continue(
    *,
    selector_input: dict[str, Any],
    candidates: list[dict[str, Any]],
    event_policy: dict[str, Any],
    strategy: str,
) -> bool:
    if _text(selector_input.get("mode")) != "first_add_flow":
        return False
    if strategy == "frequency_guard" and _frequency_guard_supported(event_policy):
        return False
    if _has_customer_or_policy_block(selector_input, event_policy):
        return False
    stages = selector_input.get("mainline_stage_status")
    if not isinstance(stages, dict):
        return False
    activity = stages.get("activity_and_price")
    if not isinstance(activity, dict):
        return False
    activity_completed = bool(
        activity.get("structural_completed")
        or activity.get("semantic_completed")
        or activity.get("completed")
    )
    if not activity_completed:
        return False
    return any(
        isinstance(item, dict) and mainline_stage_for_event_pack(item) == "deposit_decision"
        for item in candidates
    )


def _completed_mainline_stages(selector_input: dict[str, Any]) -> set[str]:
    stages = selector_input.get("mainline_stage_status")
    if not isinstance(stages, dict):
        return set()
    output: set[str] = set()
    for stage_id, payload in stages.items():
        if not isinstance(payload, dict):
            continue
        if payload.get("structural_completed") or payload.get("semantic_completed") or payload.get("completed"):
            stage = _text(stage_id)
            if stage:
                output.add(stage)
    return output


def _unique(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _text(value: Any) -> str:
    return str(value or "").strip()
