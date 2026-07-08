from __future__ import annotations

from typing import Any


PROFILE_STORE_SOURCES = {"customer_profile", "profile", "preferred_store"}


def build_store_evidence(store_anchor: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(store_anchor, dict) or not store_anchor:
        return {}
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
            }
        )

    candidate = _compact_store_candidate(store_anchor)
    source = str(candidate.get("source") or "")
    return _drop_empty(
        {
            "candidates": [candidate] if candidate else [],
            "unique_recent_store": candidate if candidate and source not in PROFILE_STORE_SOURCES else {},
            "profile_preference_only": bool(candidate and source in PROFILE_STORE_SOURCES),
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
