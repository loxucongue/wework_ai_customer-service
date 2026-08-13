from __future__ import annotations

from typing import Any

from app.graph.nodes.reply_validation import (
    _validate_parallel_claimed_deposit_evidence,
    _validate_parallel_media_facts,
    _validate_parallel_payment_boundaries,
    _validate_store_address_message_facts,
    _validate_store_resolution_delivery_mode,
    _validate_store_resolution_v2_contract,
)


def validate_v2_reply_admission(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    """Validate only structures and externally consequential side effects.

    This module intentionally never reads customer-visible text. Reply owns
    meaning, sales posture, and wording. The checks below only compare emitted
    structures and model-cited provenance with authoritative runtime facts.
    """

    violations: list[str] = []
    checks = (
        lambda: _validate_selected_content_provenance(state),
        lambda: _validate_parallel_claimed_deposit_evidence(messages, state),
        lambda: _validate_parallel_payment_boundaries(messages, state),
        lambda: _validate_parallel_media_facts(messages, state),
        lambda: _validate_store_resolution_v2_contract(messages, state),
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
        raise ValueError("v2_reply_admission_violations::" + ";;".join(violations))


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
    used_refs = {
        str(item or "").strip()
        for item in state.get("reply_used_fact_refs") or []
        if str(item or "").strip()
    }
    missing_refs = {
        f"content_asset:{content_id}"
        for content_id in selected_ids
        if f"content_asset:{content_id}" not in used_refs
    }
    if missing_refs:
        raise ValueError("selected_content_missing_fact_ref:" + ",".join(sorted(missing_refs)))
