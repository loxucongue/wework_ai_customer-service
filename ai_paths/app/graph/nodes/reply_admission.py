from __future__ import annotations

from typing import Any

from app.graph.nodes.reply_validation import (
    _validate_parallel_claimed_deposit_evidence,
    _validate_parallel_media_facts,
    _validate_parallel_payment_boundaries,
    _validate_parallel_selected_content_delivery,
    _validate_store_address_message_facts,
    _validate_store_resolution_delivery_mode,
    _validate_store_resolution_contract,
)


def validate_model_led_reply_admission(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    """Validate only structures and externally consequential side effects.

    This module intentionally never reads customer-visible text. Reply owns
    meaning, sales posture, and wording. The checks below only compare emitted
    structures and model-cited provenance with authoritative runtime facts.
    """

    violations: list[str] = []
    checks = (
        lambda: _validate_structured_delivery_conversation_shape(messages),
        lambda: _validate_selected_content_provenance(state),
        lambda: _validate_parallel_claimed_deposit_evidence(messages, state),
        lambda: _validate_parallel_payment_boundaries(messages, state),
        lambda: _validate_parallel_media_facts(messages, state),
        lambda: _validate_parallel_selected_content_delivery(messages, state),
        lambda: _validate_store_resolution_contract(messages, state),
        lambda: _validate_store_resolution_delivery_mode(messages, state),
        lambda: _validate_store_address_message_facts(
            messages,
            state,
            check_visible_text=False,
        ),
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
