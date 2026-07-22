from __future__ import annotations

from typing import Any


ALLOWED_EVENT_DECISIONS = {"send", "merge", "skip", "defer", "handoff_to_ai_reply"}
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
        _payment_gate_blocks_selection(candidate_by_id.get(pack_id)) for pack_id in selected_ids
    ) and not _decision_removes_unsupported_payment_messages(
        output,
        selected_ids=selected_ids,
        candidate_by_id=candidate_by_id,
    ):
        violations.append("selected_payment_pack_not_currently_supported")
    if decision in {"send", "merge"} and any(
        pack_id in completed_ids
        or _text((candidate_by_id.get(pack_id) or {}).get("sop_category")) in completed_categories
        for pack_id in selected_ids
    ):
        violations.append("selected_sop_pack_already_completed")
    if decision in {"skip", "defer", "handoff_to_ai_reply"} and selected_ids:
        violations.append("non_send_decision_must_not_select_pack")

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
            "reason": _text(output.get("reason") or output.get("skip_reason")),
            "skip_reason": _text(output.get("skip_reason")),
            "frequency_reason": _text(output.get("frequency_reason")),
            "backlog_handling": _text(output.get("backlog_handling")) or "none",
            "suggested_next_window": _text(output.get("suggested_next_window")),
        }
    )
    return output, _unique(violations)


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
    for pack in sorted(packs, key=lambda item: (int(item.get("order") or 0), _text(item.get("id")))):
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
            key=lambda item: (int(item.get("order") or 0), _text(item.get("id"))),
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
    completed_ids: set[str],
    completed_categories: set[str],
) -> bool:
    ordered_ids = [
        _text(item.get("id"))
        for item in sorted(
            (
                item
                for item in candidates
                if isinstance(item, dict)
                and _text(item.get("id"))
                and _text(item.get("id")) not in completed_ids
                and _text(item.get("sop_category")) not in completed_categories
            ),
            key=lambda item: (int(item.get("order") or 0), _text(item.get("id"))),
        )
    ]
    if not selected_ids or not ordered_ids:
        return False
    return selected_ids == ordered_ids[: len(selected_ids)]


def _payment_gate_blocks_selection(candidate: Any) -> bool:
    if not isinstance(candidate, dict):
        return False
    gate = candidate.get("payment_collection_gate")
    if not isinstance(gate, dict):
        return False
    return _text(gate.get("status")) not in {"", "not_required", "supported"}


def _decision_removes_unsupported_payment_messages(
    output: dict[str, Any],
    *,
    selected_ids: list[str],
    candidate_by_id: dict[str, Any],
) -> bool:
    """Allow a model-selected pack only after its unsupported cards are structurally removed."""

    removable_statuses = {"missing_matching_current_order", "paid_skip_card"}
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


def _unique(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _text(value: Any) -> str:
    return str(value or "").strip()
