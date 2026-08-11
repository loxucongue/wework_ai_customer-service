from __future__ import annotations

import re
from typing import Any


STORE_RESOLUTION_STATUSES = {
    "need_location",
    "need_location_confirmation",
    "ambiguous_location",
    "send_single",
    "send_multiple",
    "no_valid_candidate",
    "reuse_confirmed_store",
}


def customer_location_hint_texts(state: dict[str, Any], *, limit: int = 6) -> list[str]:
    """Return current and recent customer-authored location evidence only."""

    current = str(state.get("normalized_content") or state.get("content") or "").strip()
    customer_messages, _ = _recent_message_evidence(state)
    values = [current, *[text for _, text in customer_messages[-max(1, limit) :]]]
    return list(dict.fromkeys(text for text in values if text))


def build_location_evidence(
    state: dict[str, Any],
    *,
    raw_text: str,
    query: str,
    geocode: dict[str, Any],
    confirmed_by_customer: bool = False,
) -> dict[str, Any]:
    """Build auditable location facts without deciding customer intent in code."""

    location_card = state.get("location_card") if isinstance(state.get("location_card"), dict) else {}
    card_text = " ".join(
        str(location_card.get(key) or "").strip()
        for key in ("title", "address", "location_title", "location_address", "location")
        if str(location_card.get(key) or "").strip()
    ).strip()
    card_point = _location_card_point(location_card)

    customer_messages, assistant_messages = _recent_message_evidence(state)
    current_text = str(state.get("normalized_content") or state.get("content") or raw_text or "").strip()
    evidence_rows = [("current_message", current_text), *customer_messages]
    if card_text:
        evidence_rows.insert(0, ("location_card", card_text))

    province = str(geocode.get("province") or "").strip()
    city = str(geocode.get("city") or "").strip()
    district = str(geocode.get("district") or geocode.get("township") or "").strip()
    detail = _location_detail(query, province=province, city=city, district=district)
    source_refs = _matching_source_refs(
        evidence_rows,
        values=(province, city, district, detail),
    )
    mentioned = {
        "province": _value_mentioned(province, evidence_rows),
        "city": _value_mentioned(city, evidence_rows),
        "district": _value_mentioned(district, evidence_rows),
        "detail": _value_mentioned(detail, evidence_rows),
    }

    unique_geocode = _geocode_has_unique_point(geocode)
    explicit_resolved_area = bool(
        unique_geocode
        and (
            mentioned["city"]
            or mentioned["district"]
            or (
                mentioned["detail"]
                and _detail_is_specific_place(detail, geocode)
            )
        )
    )

    if card_point:
        confirmation_status = "confirmed"
        confidence = "high"
        confirmation_mode = "authoritative_location_card"
        source_refs = ["location_card", *[ref for ref in source_refs if ref != "location_card"]]
    elif mentioned["province"] and not (
        mentioned["city"] or mentioned["district"] or mentioned["detail"]
    ):
        confirmation_status = "incomplete"
        confidence = "medium"
        confirmation_mode = "blocking_more_location"
    elif (
        mentioned["province"]
        and mentioned["city"]
        and mentioned["district"]
    ) or (
        mentioned["city"]
        and mentioned["district"]
        and mentioned["detail"]
        and len(_compact(detail)) >= 2
    ) or (
        mentioned["district"]
        and mentioned["detail"]
        and len(_compact(detail)) >= 2
        and unique_geocode
    ) or explicit_resolved_area:
        # A unique, internally consistent geocode can be used immediately. Reply may
        # echo the parsed area naturally, but matching does not wait for another turn.
        confirmation_status = "confirmed"
        confidence = "high"
        confirmation_mode = "informational_echo"
    elif confirmed_by_customer and _assistant_proposed_location(query, assistant_messages):
        confirmation_status = "confirmed"
        confidence = "high"
        confirmation_mode = "customer_confirmed"
        source_refs.extend(ref for ref, _ in assistant_messages[-3:] if ref not in source_refs)
        if "current_message" not in source_refs:
            source_refs.insert(0, "current_message")
    elif not (province or city or district or card_point):
        confirmation_status = "incomplete"
        confidence = "low"
        confirmation_mode = "blocking_more_location"
    else:
        confirmation_status = "needs_confirmation"
        confidence = "medium" if geocode else "low"
        confirmation_mode = "blocking_confirmation"

    longitude, latitude = card_point or _parse_lng_lat(str(geocode.get("location") or "")) or (None, None)
    return {
        "raw_text": raw_text,
        "normalized_query": query,
        "source_message_refs": _dedupe(source_refs),
        "province": province,
        "city": city,
        "district": district,
        "detail": detail,
        "longitude": longitude,
        "latitude": latitude,
        "confirmation_status": confirmation_status,
        "confirmation_mode": confirmation_mode,
        "confirmation_required_before_match": confirmation_status != "confirmed",
        "confidence": confidence,
        "geocode_candidate_count": int(geocode.get("candidate_count") or 0),
        "geocode_candidate_regions": list(geocode.get("candidate_regions") or [])[:6],
        "geocode_ambiguous_regions": bool(geocode.get("ambiguous_regions")),
        "geocode_first_region": {
            "province": province,
            "city": city,
            "district": district,
        },
    }


def resolution_status_for_location(evidence: dict[str, Any], *, ambiguous: bool = False) -> str:
    if ambiguous:
        return "ambiguous_location"
    confirmation_status = str(evidence.get("confirmation_status") or "")
    if confirmation_status == "confirmed":
        return ""
    if confirmation_status == "incomplete":
        return "need_location"
    return "need_location_confirmation"


def legacy_delivery_mode(status: str) -> str:
    return {
        "need_location": "clarify_location",
        "need_location_confirmation": "clarify_location",
        "ambiguous_location": "clarify_location",
        "send_single": "send_recommended",
        "send_multiple": "send_all_candidates",
        "no_valid_candidate": "clarify_service_area",
        "reuse_confirmed_store": "none",
    }.get(status, "none")


def _recent_message_evidence(state: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    customer: list[tuple[str, str]] = []
    assistant: list[tuple[str, str]] = []
    start = max(0, len(history) - 20)
    for index, item in enumerate(history[start:], start=start):
        ref = f"conv_{index + 1}"
        role, text = _history_role_text(item)
        if not text:
            continue
        if role == "customer":
            customer.append((ref, text))
        elif role == "assistant":
            assistant.append((ref, text))
    return customer, assistant


def _history_role_text(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        text = str(item.get("content") or item.get("text") or item.get("message") or "").strip()
        role = str(item.get("role") or item.get("sender_type") or item.get("direction") or "").lower()
        if role in {"user", "customer", "external", "inbound"}:
            return "customer", text
        if role in {"assistant", "staff", "employee", "ai", "outbound"}:
            return "assistant", text
        return "", text
    text = str(item or "").strip()
    lowered = text.lower()
    for prefix in ("用户:", "客户:", "user:", "customer:"):
        if lowered.startswith(prefix.lower()):
            return "customer", text[len(prefix) :].strip()
    for prefix in ("小贝:", "助手:", "员工:", "assistant:", "staff:"):
        if lowered.startswith(prefix.lower()):
            return "assistant", text[len(prefix) :].strip()
    return "", text


def _location_card_point(card: dict[str, Any]) -> tuple[float, float] | None:
    longitude = card.get("longitude") or card.get("lng")
    latitude = card.get("latitude") or card.get("lat")
    if longitude not in (None, "") and latitude not in (None, ""):
        try:
            return float(longitude), float(latitude)
        except (TypeError, ValueError):
            pass
    # The workflow-compatible platform contract uses `latitude,longitude` for
    # location-card `coordinates`. Older internal `location` values follow the
    # geocoder's `longitude,latitude` contract, so the two aliases cannot share
    # one parser.
    coordinates = str(card.get("coordinates") or "").strip()
    if coordinates:
        return _parse_platform_lat_lng(coordinates)
    return _parse_lng_lat(str(card.get("location") or ""))


def _parse_platform_lat_lng(value: str) -> tuple[float, float] | None:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return None
    try:
        first = float(parts[0])
        second = float(parts[1])
    except (TypeError, ValueError):
        return None
    # Normal workflow payloads are latitude,longitude. Keep a narrow fallback
    # for already-normalized callers whose first value can only be longitude.
    if abs(first) > 90 >= abs(second):
        return first, second
    return second, first


def _parse_lng_lat(value: str) -> tuple[float, float] | None:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None


def _geocode_has_unique_point(geocode: dict[str, Any]) -> bool:
    if not _parse_lng_lat(str(geocode.get("location") or "")):
        return False
    try:
        candidate_count = int(geocode.get("candidate_count") or 0)
    except (TypeError, ValueError):
        candidate_count = 0
    return candidate_count in {0, 1}


def _location_detail(query: str, *, province: str, city: str, district: str) -> str:
    detail = str(query or "")
    for value in (province, city, district):
        if value:
            detail = detail.replace(value, "")
            detail = detail.replace(_region_alias(value), "")
    return re.sub(r"[\s,，。]+", "", detail).strip()


def _detail_is_specific_place(detail: str, geocode: dict[str, Any]) -> bool:
    """Distinguish a concrete place from an isolated short name such as '东坑'."""

    compact = _compact(detail)
    if len(compact) >= 4:
        return True
    township = str(geocode.get("township") or "").strip()
    if township and _region_or_text_mentioned(township, detail):
        return True
    return any(
        compact.endswith(suffix)
        for suffix in (
            "区",
            "县",
            "市",
            "镇",
            "乡",
            "村",
            "街道",
            "路",
            "街",
            "大道",
            "广场",
            "公园",
            "车站",
            "机场",
        )
    )


def _matching_source_refs(rows: list[tuple[str, str]], *, values: tuple[str, ...]) -> list[str]:
    refs: list[str] = []
    for ref, text in rows:
        if any(_region_or_text_mentioned(value, text) for value in values if value):
            refs.append(ref)
    return refs


def _value_mentioned(value: str, rows: list[tuple[str, str]]) -> bool:
    return bool(value) and any(_region_or_text_mentioned(value, text) for _, text in rows)


def _region_or_text_mentioned(value: str, text: str) -> bool:
    value_norm = _compact(value)
    text_norm = _compact(text)
    if not value_norm or not text_norm:
        return False
    alias = _compact(_region_alias(value))
    return (
        value_norm in text_norm
        or (len(alias) >= 2 and alias in text_norm)
        or _autonomous_prefecture_prefix_mentioned(value_norm, text_norm)
    )


def _autonomous_prefecture_prefix_mentioned(value: str, text: str) -> bool:
    """Recognize the place-name prefix before autonomous ethnic designations."""

    if not value.endswith("自治州"):
        return False
    core = value[: -len("自治州")]
    for index in range(2, len(core)):
        place_name = core[:index]
        ethnic_suffix = core[index:]
        if ethnic_suffix.endswith("族") and "族" in ethnic_suffix and place_name in text:
            return True
    return False


def _assistant_proposed_location(query: str, messages: list[tuple[str, str]]) -> bool:
    query_norm = _compact(query)
    if not query_norm:
        return False
    return any(
        query_norm in _compact(text) or _compact(text) in query_norm
        for _, text in messages[-3:]
        if len(_compact(text)) >= 2
    )


def _region_alias(value: str) -> str:
    output = str(value or "").strip()
    # Administrative "new districts" are usually supplied without the full
    # composite suffix (for example, 浦东 -> 浦东新区). Preserve one-character
    # roots such as 高新区 so their useful alias remains 高新.
    if output.endswith("新区") and len(output[: -len("新区")]) >= 2:
        return output[: -len("新区")]
    for suffix in ("特别行政区", "自治区", "自治州", "省", "市", "区", "县", "镇", "乡", "村", "街道"):
        if output.endswith(suffix) and len(output) > len(suffix):
            return output[: -len(suffix)]
    return output


def _compact(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).lower()


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output
