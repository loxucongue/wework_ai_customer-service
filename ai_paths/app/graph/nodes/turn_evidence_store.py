from __future__ import annotations

from typing import Any


PROFILE_STORE_SOURCES = {"customer_profile", "profile", "preferred_store"}


def build_store_evidence(
    store_anchor: dict[str, Any],
    *,
    store_address_delivery: dict[str, Any] | None = None,
    store_anchor_fact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    delivery = _compact_store_address_delivery(store_address_delivery or {})
    anchor_fact = _compact_store_anchor_fact(store_anchor_fact or {})
    if not isinstance(store_anchor, dict) or not store_anchor:
        return _drop_empty({"latest_store_address_delivery": delivery, "store_anchor_fact": anchor_fact})
    if store_anchor.get("ambiguous"):
        return _drop_empty(
            {
                "candidates": [
                    {"store_name": str(name or "").strip(), "source": str(store_anchor.get("source") or "")}
                    for name in store_anchor.get("matched_store_names") or []
                    if str(name or "").strip()
                ],
                "ambiguous": True,
                "source": str(store_anchor.get("source") or ""),
                "latest_store_address_delivery": delivery,
                "store_anchor_fact": anchor_fact,
            }
        )

    candidate = _compact_store_candidate(store_anchor)
    source = str(candidate.get("source") or "")
    return _drop_empty(
        {
            "candidates": [candidate] if candidate else [],
            "unique_recent_store": candidate if candidate and source not in PROFILE_STORE_SOURCES else {},
            "profile_preference_only": bool(candidate and source in PROFILE_STORE_SOURCES),
            "latest_store_address_delivery": delivery,
            "store_anchor_fact": anchor_fact,
        }
    )


def _compact_store_anchor_fact(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _drop_empty(
        {
            "status": str(value.get("status") or "").strip(),
            "store_id": str(value.get("store_id") or "").strip(),
            "store_ids": [str(item) for item in value.get("store_ids") or [] if str(item or "").strip()],
            "batch_count": value.get("batch_count"),
            "confidence": str(value.get("confidence") or "").strip(),
            "last_sent_at": str(value.get("last_sent_at") or "").strip(),
            "source": str(value.get("source") or "").strip(),
            "usage": "evidence_only_planner_decides_customer_store_acceptance",
        }
    )


def _compact_store_address_delivery(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    store_ids = [str(item or "").strip() for item in value.get("latest_batch_store_ids") or [] if str(item or "").strip()]
    return _drop_empty(
        {
            "latest_batch_store_ids": store_ids,
            "unique_latest_store_id": str(value.get("unique_latest_store_id") or "").strip(),
            "latest_batch_count": value.get("latest_batch_count"),
            "last_sent_at": str(value.get("last_sent_at") or "").strip(),
            "batch_confidence": str(value.get("batch_confidence") or "").strip(),
            "source": str(value.get("source") or "").strip(),
            "decision_policy": "evidence_only_planner_decides_store_binding",
        }
    )


def _compact_store_candidate(store: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "store_id": str(store.get("store_id") or store.get("id") or "").strip(),
            "store_name": str(store.get("store_name") or store.get("name") or "").strip(),
            "city": str(store.get("city") or "").strip(),
            "district": str(store.get("district") or "").strip(),
            "source": str(store.get("source") or "").strip(),
        }
    )


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
