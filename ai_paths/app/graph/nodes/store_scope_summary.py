from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


def build_store_scope_summary(
    raw: dict[str, Any],
    *,
    location_hints: Iterable[Any] = (),
) -> dict[str, Any]:
    """Build compact geographic facts for the customer-visible store scope."""
    if not isinstance(raw, dict) or not raw:
        return {}

    stores = [item for item in raw.get("stores") or [] if isinstance(item, dict)]
    province_counts: Counter[str] = Counter()
    city_counts: Counter[tuple[str, str]] = Counter()
    district_counts: Counter[tuple[str, str, str]] = Counter()
    for store in stores:
        province = _region_value(store.get("province"), fallback="未识别省份")
        city = _region_value(store.get("city"), fallback="未识别城市")
        district = _region_value(store.get("district"), fallback="未识别区域")
        province_counts[province] += 1
        city_counts[(province, city)] += 1
        district_counts[(province, city, district)] += 1

    hints = _location_hints(location_hints)
    relevant_city_keys = _relevant_city_keys(stores, hints)
    relevant_city_key_set = set(relevant_city_keys)
    relevant_regions = [
        _relevant_city_summary(stores, province=province, city=city, hints=hints)
        for province, city in relevant_city_keys[:4]
    ]

    return _drop_empty(
        {
            "source": raw.get("source"),
            "store_count": raw.get("store_count", len(stores)),
            "snapshot_generated_at": raw.get("snapshot_generated_at"),
            "store_scope_error": raw.get("store_scope_error") or raw.get("error") or "",
            "cache": raw.get("cache") if isinstance(raw.get("cache"), dict) else {},
            "missing_snapshot_store_ids": raw.get("missing_snapshot_store_ids", []),
            "province_counts": [
                {"province": province, "store_count": count}
                for province, count in sorted(province_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            "city_counts": [
                {"province": province, "city": city, "store_count": count}
                for (province, city), count in sorted(city_counts.items(), key=lambda item: (-item[1], item[0]))
                if (province, city) in relevant_city_key_set
            ],
            "district_counts": [
                {"province": province, "city": city, "district": district, "store_count": count}
                for (province, city, district), count in sorted(
                    district_counts.items(), key=lambda item: (-item[1], item[0])
                )
                if (province, city) in relevant_city_key_set
            ],
            "relevant_regions": [item for item in relevant_regions if item],
        }
    )


def store_scope_ids(raw: dict[str, Any]) -> set[str]:
    stores = raw.get("stores") if isinstance(raw, dict) and isinstance(raw.get("stores"), list) else []
    return {
        str(item.get("store_id") or item.get("id") or "").strip()
        for item in stores
        if isinstance(item, dict) and str(item.get("store_id") or item.get("id") or "").strip()
    }


def region_mentioned_in_text(region: str, text: str) -> bool:
    compact_region = "".join(str(region or "").split()).lower()
    compact_text = "".join(str(text or "").split()).lower()
    if not compact_region or not compact_text:
        return False
    if compact_region in compact_text:
        return True
    normalized_region = _normalize_region(compact_region)
    return len(normalized_region) >= 2 and normalized_region in compact_text


def _relevant_city_summary(
    stores: list[dict[str, Any]],
    *,
    province: str,
    city: str,
    hints: list[str],
) -> dict[str, Any]:
    city_stores = [
        store
        for store in stores
        if _region_value(store.get("province"), fallback="未识别省份") == province
        and _region_value(store.get("city"), fallback="未识别城市") == city
    ]
    if not city_stores:
        return {}
    area_hints = [
        hint
        for hint in hints
        if not _region_matches_hint(province, hint)
        and not _region_matches_hint(city, hint)
        and len(_normalize_region(hint)) <= 8
    ]
    exact_area_stores = [
        store for store in city_stores if any(_region_matches_hint(str(store.get("district") or ""), hint) for hint in area_hints)
    ]
    districts: Counter[str] = Counter(
        _region_value(store.get("district"), fallback="未识别区域") for store in city_stores
    )
    return {
        "province": province,
        "city": city,
        "store_count": len(city_stores),
        "district_counts": [
            {"district": district, "store_count": count}
            for district, count in sorted(districts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "requested_areas": area_hints[:4],
        "exact_area_store_count": len(exact_area_stores),
        "requested_district_stores": [_compact_store(store) for store in exact_area_stores],
        "stores": [_compact_store(store) for store in city_stores[:12]],
    }


def _relevant_city_keys(stores: list[dict[str, Any]], hints: list[str]) -> list[tuple[str, str]]:
    specific_matches: list[tuple[str, str]] = []
    province_matches: list[tuple[str, str]] = []
    for store in stores:
        province = _region_value(store.get("province"), fallback="未识别省份")
        city = _region_value(store.get("city"), fallback="未识别城市")
        district = _region_value(store.get("district"), fallback="未识别区域")
        key = (province, city)
        if any(_region_matches_hint(value, hint) for value in (city, district) for hint in hints):
            if key not in specific_matches:
                specific_matches.append(key)
            continue
        if any(_region_matches_hint(province, hint) for hint in hints) and key not in province_matches:
            province_matches.append(key)
    return specific_matches or province_matches


def _location_hints(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in output:
            continue
        output.append(text)
    return output


def _region_matches_hint(region: str, hint: str) -> bool:
    left = _normalize_region(region)
    right = _normalize_region(hint)
    return bool(left and right and (left in right or right in left))


def _normalize_region(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "")
    for suffix in ("特别行政区", "自治区", "自治州", "地区", "省", "市", "区", "县", "旗"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def _region_value(value: Any, *, fallback: str) -> str:
    return str(value or "").strip() or fallback


def _compact_store(store: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "store_id": str(store.get("store_id") or store.get("id") or "").strip(),
            "store_name": str(store.get("store_name") or store.get("name") or "").strip(),
            "province": str(store.get("province") or "").strip(),
            "city": str(store.get("city") or "").strip(),
            "district": str(store.get("district") or "").strip(),
        }
    )


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
