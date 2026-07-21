from __future__ import annotations

from typing import Any


def turn_evidence_for_model(value: Any) -> dict[str, Any]:
    """Return facts that models may use without inheriting Python business decisions."""
    if not isinstance(value, dict):
        return {}

    nested = value.get("turn_evidence") if isinstance(value.get("turn_evidence"), dict) else {}
    store = _store_evidence(nested.get("store_evidence"))
    appointment = _appointment_evidence(
        nested.get("appointment_evidence"),
        fallback=value.get("confirmed_appointment"),
    )
    registration = _registration_evidence(
        nested.get("registration_evidence") or value.get("registration_evidence")
    )
    conflicts = _evidence_conflicts(nested.get("evidence_conflicts") or value.get("evidence_conflicts"))

    output = _drop_empty(
        {
            "store_evidence": store,
            "appointment_evidence": appointment,
            "registration_evidence": registration,
            "evidence_conflicts": conflicts,
        }
    )
    if output:
        output["source_policy"] = "facts_only_models_decide_business_semantics"
    return output


def _store_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates = [
        item
        for item in (_store_candidate(candidate) for candidate in value.get("candidates") or [])
        if item
    ]
    return _drop_empty(
        {
            "candidates": candidates[:8],
            "unique_recent_store": _store_candidate(value.get("unique_recent_store")),
            "profile_preference_only": value.get("profile_preference_only")
            if "profile_preference_only" in value
            else None,
            "ambiguous": value.get("ambiguous") if "ambiguous" in value else None,
            "source": _text(value.get("source")),
            "latest_store_address_delivery": _store_delivery(value.get("latest_store_address_delivery")),
            "store_anchor_fact": _store_anchor_fact(value.get("store_anchor_fact")),
        }
    )


def _store_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _drop_empty(
        {
            "store_id": _text(value.get("store_id") or value.get("id")),
            "store_name": _text(value.get("store_name") or value.get("name")),
            "city": _text(value.get("city")),
            "district": _text(value.get("district")),
            "source": _text(value.get("source")),
        }
    )


def _store_delivery(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    store_ids = [_text(item) for item in value.get("latest_batch_store_ids") or [] if _text(item)]
    return _drop_empty(
        {
            "latest_batch_store_ids": store_ids[:8],
            "unique_latest_store_id": _text(value.get("unique_latest_store_id")),
            "latest_batch_count": value.get("latest_batch_count"),
            "last_sent_at": _text(value.get("last_sent_at")),
            "batch_confidence": _text(value.get("batch_confidence")),
            "source": _text(value.get("source")),
        }
    )


def _store_anchor_fact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    store_ids = [_text(item) for item in value.get("store_ids") or [] if _text(item)]
    return _drop_empty(
        {
            "status": _text(value.get("status")),
            "store_id": _text(value.get("store_id")),
            "store_ids": store_ids[:8],
            "batch_count": value.get("batch_count"),
            "confidence": _text(value.get("confidence")),
            "last_sent_at": _text(value.get("last_sent_at")),
            "source": _text(value.get("source")),
        }
    )


def _appointment_evidence(value: Any, *, fallback: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) and value else fallback
    if not isinstance(source, dict):
        return {}
    return _drop_empty(
        {
            "date": _text(source.get("date")),
            "time": _text(source.get("time")),
            "store_id": _text(source.get("store_id")),
            "store_name": _text(source.get("store_name")),
            "source": _text(source.get("source")),
        }
    )


def _registration_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _drop_empty(
        {
            "customer_name_collected": value.get("customer_name_collected") is True,
            "phone_collected": value.get("phone_collected") is True,
            "mobile_sync_status": _text(value.get("mobile_sync_status")),
        }
    )


def _evidence_conflicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        names = [_text(name) for name in item.get("matched_store_names") or [] if _text(name)]
        conflict = _drop_empty(
            {
                "type": _text(item.get("type")),
                "source": _text(item.get("source")),
                "matched_store_names": names[:8],
            }
        )
        if conflict:
            output.append(conflict)
    return output[:5]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
