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

PAID_ONLY_DETAIL_KEYS = {"floor", "room", "arrival_guidance", "reception"}
PRIVATE_ARRIVAL_FACT_KEYS = ("floor", "room", "arrival_guidance", "reception")

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


def unique_delivery_store_id(store_resolution_fact: dict[str, Any] | None) -> str:
    """Return the only store authorized by a completed single-store decision."""

    resolution = store_resolution_fact if isinstance(store_resolution_fact, dict) else {}
    status = str(resolution.get("status") or "").strip()
    raw_ids = (
        resolution.get("delivery_store_ids")
        if status == "send_single"
        else resolution.get("already_delivered_store_ids")
        if status == "reuse_confirmed_store"
        else []
    )
    delivery_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in raw_ids or []
            if str(item or "").strip()
        )
    )
    if status not in {"send_single", "reuse_confirmed_store"}:
        return ""
    return delivery_ids[0] if len(delivery_ids) == 1 else ""


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
    unique_store_id = unique_delivery_store_id(resolution)
    unique_store = copy.deepcopy(by_id.get(unique_store_id) or {})
    detail_kind = str(
        requested_detail_kind
        or resolution.get("requested_detail_kind")
        or ((resolution.get("destination_resolution") or {}).get("detail_kind") if isinstance(resolution.get("destination_resolution"), dict) else "")
    ).strip()
    configured_detail_keys = DETAIL_FACT_KEYS.get(detail_kind, ())
    paid_only_request = bool(
        detail_kind == "arrival_guidance" and not authoritative_paid
    )
    detail_keys = (
        configured_detail_keys
        if authoritative_paid
        else tuple(key for key in configured_detail_keys if key not in PAID_ONLY_DETAIL_KEYS)
    )
    detail_facts = (
        {}
        if paid_only_request
        else _selected_facts(unique_store, detail_keys)
    )
    detail_status = (
        "not_requested"
        if not detail_kind
        else "payment_required"
        if paid_only_request
        else "available"
        if detail_facts
        else "missing"
    )
    arrival_facts = (
        _selected_facts(unique_store, ARRIVAL_FACT_KEYS)
        if authoritative_paid and unique_store_id and unique_store
        else {}
    )
    private_arrival_facts = _selected_facts(unique_store, PRIVATE_ARRIVAL_FACT_KEYS)
    arrival_guidance_available = bool(
        authoritative_paid and unique_store_id and private_arrival_facts
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
        if unique_store_id and detail_kind and detail_status == "missing"
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
            "missing_fact_keys": list(detail_keys) if detail_status == "missing" else [],
        },
        "post_payment_arrival_guidance": {
            "status": "available" if arrival_guidance_available else "unavailable",
            "store_id": unique_store_id if arrival_guidance_available else "",
            "facts": arrival_facts if arrival_guidance_available else {},
            "reason": (
                "paid_and_unique_store_facts_available"
                if arrival_guidance_available
                else "authoritative_payment_required"
                if not authoritative_paid
                else "private_arrival_guidance_facts_required"
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
