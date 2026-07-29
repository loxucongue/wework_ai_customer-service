from __future__ import annotations

from typing import Any


DELETED_CUSTOMER_RELATION_STATUSES = {"deleted", "removed"}


def normalize_customer_relation(payload: Any) -> dict[str, Any]:
    """Extract the platform customer-relation fact from a conversation response."""
    if not isinstance(payload, dict):
        return _unknown_relation()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    raw = data.get("customer_relation") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return _unknown_relation()
    status = str(raw.get("status") or "").strip().lower()
    is_deleted = _as_bool(raw.get("is_deleted")) or status in DELETED_CUSTOMER_RELATION_STATUSES
    return {
        "available": True,
        "status": status or ("deleted" if is_deleted else "unknown"),
        "is_deleted": is_deleted,
        "deleted_at": str(raw.get("deleted_at") or "").strip(),
        "updated_at": str(raw.get("updated_at") or "").strip(),
    }


def customer_relation_is_deleted(relation: Any) -> bool:
    return bool(isinstance(relation, dict) and relation.get("available") and relation.get("is_deleted"))


def _unknown_relation() -> dict[str, Any]:
    return {
        "available": False,
        "status": "unknown",
        "is_deleted": False,
        "deleted_at": "",
        "updated_at": "",
    }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
