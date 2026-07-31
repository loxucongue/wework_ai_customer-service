from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable


_REGION_SUFFIXES = (
    "\u58ee\u65cf\u81ea\u6cbb\u533a",
    "\u56de\u65cf\u81ea\u6cbb\u533a",
    "\u7ef4\u543e\u5c14\u81ea\u6cbb\u533a",
    "\u7279\u522b\u884c\u653f\u533a",
    "\u81ea\u6cbb\u5dde",
    "\u81ea\u6cbb\u53bf",
    "\u81ea\u6cbb\u533a",
    "\u65b0\u533a",
    "\u5730\u533a",
    "\u7701",
    "\u5e02",
    "\u533a",
    "\u53bf",
    "\u65d7",
)

_PROVINCE_PATTERN = re.compile(
    r"^([\u4e00-\u9fff]{2,12}?"
    r"(?:\u58ee\u65cf\u81ea\u6cbb\u533a|\u56de\u65cf\u81ea\u6cbb\u533a|"
    r"\u7ef4\u543e\u5c14\u81ea\u6cbb\u533a|\u7279\u522b\u884c\u653f\u533a|"
    r"\u81ea\u6cbb\u533a|\u7701))"
)
_CITY_PATTERN = re.compile(
    r"^([\u4e00-\u9fff]{2,10}?"
    r"(?:\u81ea\u6cbb\u5dde|\u5730\u533a|\u5e02))"
)


def assess_store_fact_integrity(
    store: dict[str, Any],
    *,
    known_stores: Iterable[dict[str, Any]] = (),
    _catalog: dict[str, dict[str, set[str]]] | None = None,
) -> dict[str, Any]:
    """Validate contradictions inside one structured store fact.

    Parking is supporting information only. A cross-region parking address is
    reported as a warning and cannot independently invalidate a coherent store.
    """

    store_id = _text(store.get("store_id") or store.get("id"))
    store_name = _text(store.get("store_name") or store.get("name"))
    store_address = _text(store.get("store_address") or store.get("address"))
    province = _text(store.get("province"))
    city = _text(store.get("city"))
    district = _text(store.get("district"))
    parking_address = _text(store.get("parking_address"))
    geocode_address = _text(store.get("geocode_formatted_address"))

    violations: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {}

    if not store_id:
        violations.append("missing_store_id")
    if not store_name:
        violations.append("missing_store_name")
    if not store_address:
        warnings.append("missing_store_address")

    catalog = _catalog if _catalog is not None else _region_catalog(known_stores)
    own_city = _region_token(city)
    own_district = _region_token(district)

    foreign_cities = _foreign_region_mentions(
        store_name,
        {own_city, own_district},
        catalog["city"],
    )
    if foreign_cities and own_city:
        violations.append("primary_text_city_conflict")
        evidence["primary_text_foreign_cities"] = foreign_cities
        evidence["structured_city"] = city

    explicit_address_province = _explicit_region_value(store_address, "province")
    if (
        explicit_address_province
        and province
        and not _region_equal(explicit_address_province, province)
    ):
        warnings.append("store_address_province_conflict")
        evidence["store_address_province"] = explicit_address_province
        evidence["structured_province"] = province

    explicit_address_city = _explicit_region_value(store_address, "city")
    if (
        explicit_address_city
        and city
        and not _region_equal(explicit_address_city, city)
        and not _region_equal(explicit_address_city, district)
        and own_city not in _region_token(explicit_address_city)
        and own_district not in _region_token(explicit_address_city)
    ):
        violations.append("store_address_city_conflict")
        evidence["store_address_city"] = explicit_address_city
        evidence["structured_city"] = city

    explicit_geocode_city = _explicit_region_value(geocode_address, "city")
    if (
        explicit_geocode_city
        and city
        and not _region_equal(explicit_geocode_city, city)
    ):
        violations.append("geocode_city_conflict")
        evidence["geocode_city"] = explicit_geocode_city
        evidence["structured_city"] = city

    explicit_geocode_province = _explicit_region_value(
        geocode_address,
        "province",
    )
    if (
        explicit_geocode_province
        and province
        and not _region_equal(explicit_geocode_province, province)
    ):
        violations.append("geocode_province_conflict")
        evidence["geocode_province"] = explicit_geocode_province
        evidence["structured_province"] = province

    parking_city = _explicit_region_value(parking_address, "city")
    parking_province = _explicit_region_value(parking_address, "province")
    if parking_city and city and not _region_equal(parking_city, city):
        warnings.append("parking_city_conflict")
        evidence["parking_city"] = parking_city
        parking_city_token = _region_token(parking_city)
        if (
            parking_city_token
            and parking_city_token in _compact(store_name)
            and not _region_equal(parking_city, district)
        ):
            violations.append("store_name_parking_city_confirms_conflict")
    if parking_province and province and not _region_equal(
        parking_province,
        province,
    ):
        warnings.append("parking_province_conflict")
        evidence["parking_province"] = parking_province

    return {
        "status": "invalid" if violations else "valid",
        "violations": list(dict.fromkeys(violations)),
        "warnings": list(dict.fromkeys(warnings)),
        "evidence": evidence,
        "store_id": store_id,
        "store_name": store_name,
        "region": {
            "province": province,
            "city": city,
            "district": district,
        },
    }


def annotate_store_fact_integrity(
    store: dict[str, Any],
    *,
    known_stores: Iterable[dict[str, Any]] = (),
    _catalog: dict[str, dict[str, set[str]]] | None = None,
) -> dict[str, Any]:
    output = dict(store)
    assessment = assess_store_fact_integrity(
        output,
        known_stores=known_stores,
        _catalog=_catalog,
    )
    output["store_fact_integrity"] = assessment["status"]
    output["store_fact_integrity_violations"] = assessment["violations"]
    output["store_fact_integrity_warnings"] = assessment["warnings"]
    if assessment["evidence"]:
        output["store_fact_integrity_evidence"] = assessment["evidence"]
    return output


def filter_valid_store_facts(
    stores: Iterable[dict[str, Any]],
    *,
    known_stores: Iterable[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [dict(store) for store in stores if isinstance(store, dict)]
    catalog_rows = list(known_stores) if known_stores is not None else rows
    catalog = _region_catalog(catalog_rows)
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for store in rows:
        annotated = annotate_store_fact_integrity(
            store,
            known_stores=catalog_rows,
            _catalog=catalog,
        )
        if annotated["store_fact_integrity"] == "valid":
            valid.append(annotated)
            continue
        invalid.append(
            {
                "store_id": str(
                    annotated.get("store_id")
                    or annotated.get("id")
                    or ""
                ),
                "store_name": str(
                    annotated.get("store_name")
                    or annotated.get("name")
                    or ""
                ),
                "violations": list(
                    annotated.get("store_fact_integrity_violations") or []
                ),
                "warnings": list(
                    annotated.get("store_fact_integrity_warnings") or []
                ),
                "evidence": dict(
                    annotated.get("store_fact_integrity_evidence") or {}
                ),
            }
        )
    return valid, invalid


def store_fact_is_valid(
    store: dict[str, Any],
    *,
    known_stores: Iterable[dict[str, Any]] = (),
) -> bool:
    status = str(store.get("store_fact_integrity") or "").strip().lower()
    if status:
        return status != "invalid"
    store_id = _text(store.get("store_id") or store.get("id"))
    store_name = _text(store.get("store_name") or store.get("name"))
    if not store_id:
        return False
    # ID-only authorization/appointment facts do not contain enough regional
    # information to assess. They are unknown, not contradictory.
    if not store_name:
        return True
    return (
        assess_store_fact_integrity(
            store,
            known_stores=known_stores,
        )["status"]
        == "valid"
    )


def _region_catalog(
    stores: Iterable[dict[str, Any]],
) -> dict[str, dict[str, set[str]]]:
    catalog: dict[str, dict[str, set[str]]] = {
        "province": defaultdict(set),
        "city": defaultdict(set),
    }
    for store in stores:
        if not isinstance(store, dict):
            continue
        for level in ("province", "city"):
            full = _text(store.get(level))
            token = _region_token(full)
            if token and len(token) >= 2:
                catalog[level][token].add(full)
    return catalog


def _foreign_region_mentions(
    text: str,
    own_tokens: set[str],
    catalog: dict[str, set[str]],
) -> list[str]:
    compact = _compact(text)
    if not compact:
        return []
    output: list[str] = []
    for token, full_values in catalog.items():
        if token in own_tokens or len(token) < 2 or token not in compact:
            continue
        # Ambiguous aliases are not strong enough to invalidate a store.
        if len(full_values) != 1:
            continue
        output.extend(sorted(full_values))
    return list(dict.fromkeys(output))


def _explicit_region_value(text: str, level: str) -> str:
    value = _text(text)
    if not value:
        return ""
    if level == "province":
        match = _PROVINCE_PATTERN.match(value)
        return match.group(1) if match else ""

    province = _explicit_region_value(value, "province")
    remainder = value[len(province) :] if province else value
    match = _CITY_PATTERN.match(remainder)
    return match.group(1) if match else ""


def _region_equal(left: str, right: str) -> bool:
    left_token = _region_token(left)
    right_token = _region_token(right)
    return bool(
        left_token
        and right_token
        and left_token == right_token
    )


def _region_token(value: str) -> str:
    text = _compact(value)
    for suffix in _REGION_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def _text(value: Any) -> str:
    return re.sub(r"[\u200b-\u200f\ufeff]", "", str(value or "")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).lower()
