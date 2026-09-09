from __future__ import annotations

from typing import Any

from app.graph.nodes.material_selection import parallel_reply_payload
from app.graph.nodes.reply_validation import (
    _validate_no_placeholder_facts,
    _validate_parallel_claimed_deposit_evidence,
    _validate_parallel_appointment_confirmation_facts,
    _validate_parallel_business_hours_facts,
    _validate_parallel_media_facts,
    _validate_parallel_payment_boundaries,
    _validate_parallel_selected_content_delivery,
    _validate_structured_delivery_promises,
    _validate_unconfirmed_store_availability_claim,
    _validate_store_address_message_facts,
    _validate_store_resolution_delivery_mode,
    _validate_store_resolution_contract,
)
from app.graph.nodes.sales_fact_validation import (
    validate_customer_visible_identity_boundaries,
    validate_sales_price_fact_boundaries,
)


def validate_model_led_reply_admission(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    """Validate structures and externally consequential factual claims.

    Reply owns meaning, sales posture, and wording.  The checks below only
    compare emitted structures, model-cited provenance, and narrow completed
    state claims with authoritative runtime facts.
    """

    violations: list[str] = []
    checks = (
        lambda: _validate_structured_delivery_conversation_shape(messages),
        lambda: _validate_no_placeholder_facts(messages),
        lambda: _validate_selected_content_provenance(state),
        lambda: _validate_parallel_claimed_deposit_evidence(messages, state),
        lambda: _validate_parallel_payment_boundaries(messages, state),
        lambda: _validate_parallel_media_facts(messages, state),
        lambda: _validate_parallel_selected_content_delivery(messages, state),
        lambda: _validate_structured_delivery_promises(messages, state),
        lambda: _validate_store_resolution_contract(messages, state),
        lambda: _validate_store_resolution_delivery_mode(messages, state),
        lambda: _validate_store_address_message_facts(
            messages,
            state,
            check_visible_text=False,
        ),
        lambda: _validate_parallel_appointment_confirmation_facts(messages, state),
        lambda: _validate_parallel_business_hours_facts(messages, state),
        lambda: validate_customer_visible_identity_boundaries(messages),
        lambda: validate_sales_price_fact_boundaries(messages),
        lambda: _validate_mainline_sales_action(state),
        lambda: _validate_unconfirmed_store_availability_claim(messages, state),
    )
    for check in checks:
        try:
            check()
        except (TypeError, ValueError) as exc:
            detail = str(exc).strip()
            if detail and detail not in violations:
                violations.append(detail)
    if violations:
        raise ValueError("reply_admission_violations::" + ";;".join(violations))


def _validate_mainline_sales_action(state: dict[str, Any]) -> None:
    """Keep the model's declared next action inside delivered-stage bounds.

    This validates an explicit enum against deterministic delivery facts.  It
    does not infer sales intent from customer words or inspect visible reply
    copy.  The one permitted repair remains responsible for making the model's
    wording match a valid action.
    """

    if not isinstance(state.get("evidence_join"), dict):
        return
    mainline = (
        state.get("mainline_delivery_state")
        if isinstance(state.get("mainline_delivery_state"), dict)
        else parallel_reply_payload(state).get("mainline_delivery_state", {})
    )
    allowed = {
        str(item or "").strip()
        for item in mainline.get("allowed_next_sales_action_types") or []
        if str(item or "").strip()
    }
    if not allowed:
        return
    sales = (
        state.get("reply_sales_judgment")
        if isinstance(state.get("reply_sales_judgment"), dict)
        else {}
    )
    next_action = (
        sales.get("next_sales_action")
        if isinstance(sales.get("next_sales_action"), dict)
        else {}
    )
    action_type = str(next_action.get("type") or "").strip()
    policy = (
        state.get("reply_policy_decision")
        if isinstance(state.get("reply_policy_decision"), dict)
        else {}
    )
    closing = (
        policy.get("closing_decision")
        if isinstance(policy.get("closing_decision"), dict)
        else {}
    )
    customer_state = str(closing.get("customer_state") or "").strip()
    if customer_state in {"continue_sales", "pause_current_turn"} and not action_type:
        raise ValueError("next_sales_action_required")
    if customer_state == "pause_current_turn" and action_type in {
        "invite_booking",
        "send_payment",
    }:
        raise ValueError("paused_turn_cannot_advance_transaction")
    if customer_state == "hard_stop_marketing" and action_type and action_type != "stop":
        raise ValueError("hard_stop_requires_stop_action")
    if customer_state == "post_payment_service" and action_type not in {
        "post_payment_service",
        "keep_open",
        "ask_missing_fact",
    }:
        raise ValueError("post_payment_requires_service_action")
    if customer_state in {"continue_sales", "pause_current_turn"} and action_type not in allowed:
        raise ValueError(
            "next_sales_action_exceeds_delivered_mainline:"
            f"{action_type}:{mainline.get('next_missing_stage') or ''}"
        )


def _validate_structured_delivery_conversation_shape(
    messages: list[dict[str, Any]],
) -> None:
    """Require a text turn around customer-visible structured delivery.

    This is a message-shape contract only. It does not inspect text meaning or
    choose the sales action; Reply must produce the complete conversation.
    """

    message_types = {
        str(item.get("type") or "").strip()
        for item in messages
        if isinstance(item, dict)
    }
    structured_types = {"store_address", "payment_collection", "image", "video"}
    if message_types.intersection(structured_types) and "text" not in message_types:
        raise ValueError("structured_delivery_requires_text_message")


def _validate_selected_content_provenance(state: dict[str, Any]) -> None:
    joined = state.get("evidence_join") if isinstance(state.get("evidence_join"), dict) else {}
    allowed_ids = {
        str(item.get("content_id") or item.get("id") or "").strip()
        for item in joined.get("content_candidates") or []
        if isinstance(item, dict)
        and str(item.get("content_id") or item.get("id") or "").strip()
    }
    selected_ids = {
        str(item or "").strip()
        for item in state.get("reply_selected_content_ids") or []
        if str(item or "").strip()
    }
    if not selected_ids.issubset(allowed_ids):
        raise ValueError("selected_content_id_not_nominated")
