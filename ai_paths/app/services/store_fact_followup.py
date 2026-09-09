from __future__ import annotations

import copy
from typing import Any


DETAIL_FACT_KEYS: dict[str, tuple[str, ...]] = {
    "address": ("store_name", "address", "map_url"),
    "navigation": ("store_name", "address", "map_url", "arrival_guidance"),
    "parking": ("parking", "parking_name", "parking_address", "parking_url"),
    "hours": ("business_hours",),
    "arrival_guidance": ("floor", "room", "arrival_guidance", "reception"),
}

ARRIVAL_FACT_KEYS = (
    "store_id",
    "store_name",
    "address",
    "map_url",
    "business_hours",
    "parking",
    "parking_name",
    "parking_address",
    "parking_url",
    "floor",
    "room",
    "arrival_guidance",
    "reception",
)


def build_store_fact_followup(
    *,
    store_resolution_fact: dict[str, Any] | None,
    store_facts: list[dict[str, Any]] | dict[str, Any] | None,
    requested_detail_kind: str = "",
    authoritative_paid: bool = False,
) -> dict[str, Any]:
    """Expose store follow-up capabilities without writing customer copy.

    The caller still owns workflow and reply decisions.  This helper only joins
    a unique structured store result with known detail facts, and deliberately
    omits values that were not supplied by the store source.
    """

    resolution = store_resolution_fact if isinstance(store_resolution_fact, dict) else {}
    rows = _store_rows(store_facts)
    by_id = {
        str(item.get("store_id") or item.get("id") or "").strip(): item
        for item in rows
        if str(item.get("store_id") or item.get("id") or "").strip()
    }
    delivery_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in resolution.get("delivery_store_ids") or []
            if str(item or "").strip()
        )
    )
    unique_store_id = (
        delivery_ids[0]
        if str(resolution.get("status") or "").strip() == "send_single" and len(delivery_ids) == 1
        else ""
    )
    unique_store = copy.deepcopy(by_id.get(unique_store_id) or {})
    detail_kind = str(
        requested_detail_kind
        or resolution.get("requested_detail_kind")
        or ((resolution.get("destination_resolution") or {}).get("detail_kind") if isinstance(resolution.get("destination_resolution"), dict) else "")
    ).strip()
    detail_keys = DETAIL_FACT_KEYS.get(detail_kind, ())
    detail_facts = _selected_facts(unique_store, detail_keys)
    detail_status = (
        "not_requested"
        if not detail_kind
        else "available"
        if detail_facts
        else "missing"
    )
    arrival_facts = (
        _selected_facts(unique_store, ARRIVAL_FACT_KEYS)
        if authoritative_paid and unique_store_id and unique_store
        else {}
    )
    missing_detail_task = (
        {
            "task_type": "store_fact_followup",
            "status": "pending",
            "store_id": unique_store_id,
            "detail_kind": detail_kind,
            "missing_fact_keys": list(detail_keys),
            "customer_send_authorized": False,
        }
        if unique_store_id and detail_kind and not detail_facts
        else {}
    )
    return {
        "schema_version": "store_fact_followup_v1",
        "source": "authoritative_store_resolution_and_facts",
        "unique_store_delivery": {
            "status": "available" if unique_store_id and unique_store else "unavailable",
            "store_id": unique_store_id if unique_store else "",
            "reason": (
                "single_delivery_store_with_facts"
                if unique_store_id and unique_store
                else "single_store_fact_missing"
                if unique_store_id
                else "store_resolution_is_not_unique"
            ),
        },
        "requested_detail": {
            "kind": detail_kind,
            "status": detail_status,
            "facts": detail_facts,
            "missing_fact_keys": list(detail_keys) if detail_kind and not detail_facts else [],
        },
        "post_payment_arrival_guidance": {
            "status": "available" if arrival_facts else "unavailable",
            "store_id": unique_store_id if arrival_facts else "",
            "facts": arrival_facts,
            "reason": (
                "paid_and_unique_store_facts_available"
                if arrival_facts
                else "authoritative_payment_required"
                if not authoritative_paid
                else "unique_store_facts_required"
            ),
        },
        "internal_followup": missing_detail_task,
        "constraints": {
            "customer_copy_generated": False,
            "unknown_values_omitted": True,
            "business_hours_must_be_present_to_claim": True,
            "does_not_authorize_visit_or_appointment": True,
        },
    }


def _store_rows(value: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [copy.deepcopy(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested = value.get("stores")
        if isinstance(nested, list):
            return [copy.deepcopy(item) for item in nested if isinstance(item, dict)]
        return [copy.deepcopy(value)] if value else []
    return []


def _selected_facts(store: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(store.get(key))
        for key in keys
        if store.get(key) not in (None, "", [], {})
    }
