from __future__ import annotations

from typing import Any

from app.services import store_format, store_text
from app.services.store_catalog import StoreRecord


def sanitize_platform_result(
    result: dict[str, Any],
    *,
    requested_name: str,
    city: str,
    stores_catalog: list[StoreRecord],
    limit: int,
) -> dict[str, Any]:
    stores = result.get("stores") if isinstance(result, dict) else []
    if not isinstance(stores, list):
        return result
    target_city = city or store_text.city_for_store_name(requested_name, stores_catalog)
    aliases = store_text.store_aliases(requested_name) if requested_name else []
    clean_stores: list[dict[str, Any]] = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        if target_city and not store_text.store_matches_city(store, target_city):
            continue
        if requested_name and not store_text.store_matches_requested_name(store, requested_name, aliases):
            continue
        merged = merge_local_store_details(store, stores_catalog)
        if not store_format.is_public_store(merged):
            continue
        clean_stores.append(merged)
        if len(clean_stores) >= limit:
            break
    output = dict(result)
    output["stores"] = clean_stores
    if target_city and not output.get("city"):
        output["city"] = target_city
    return output


def merge_local_city_stores(
    result: dict[str, Any],
    *,
    city: str,
    stores_catalog: list[StoreRecord],
    limit: int,
) -> dict[str, Any]:
    stores = [
        store
        for store in result.get("stores", [])
        if isinstance(store, dict) and store_format.is_public_store(store)
    ]
    seen = {
        (str(store.get("id") or ""), str(store.get("name") or ""), str(store.get("address") or ""))
        for store in stores
    }
    for record in stores_catalog:
        if record.city != city or not record.is_public:
            continue
        item = store_format.store_record_to_dict(record)
        key = (item["id"], item["name"], item["address"])
        if key in seen:
            continue
        stores.append(item)
        seen.add(key)
        if len(stores) >= limit:
            break
    output = dict(result)
    output["stores"] = stores[:limit]
    if stores and output.get("source") and "local_fallback" not in str(output.get("source")):
        output["source"] = f"{output.get('source')}+local_store_fallback"
    return output


def merge_local_store_details(store: dict[str, Any], stores_catalog: list[StoreRecord]) -> dict[str, Any]:
    name = str(store.get("name") or "").strip()
    if not name:
        return store
    aliases = store_text.store_aliases(name)
    local = next(
        (
            record
            for record in stores_catalog
            if _local_record_matches_platform_name(record, name, aliases)
        ),
        None,
    )
    if not local:
        return store
    merged = dict(store)
    local_data = store_format.store_record_to_dict(local)
    for key in ["map_url", "parking_name", "parking_address", "parking_link", "business_hours", "status_summary", "city"]:
        if not merged.get(key) and local_data.get(key):
            merged[key] = local_data[key]
    if not merged.get("address") and local_data.get("address"):
        merged["address"] = local_data["address"]
    return merged


def _local_record_matches_platform_name(record: StoreRecord, name: str, aliases: list[str]) -> bool:
    if record.name == name or record.name in name or name in record.name:
        return True
    local_aliases = store_text.store_aliases(record.name)
    haystack = f"{name} {record.name}"
    return any(alias and alias in haystack for alias in [*aliases, *local_aliases])
