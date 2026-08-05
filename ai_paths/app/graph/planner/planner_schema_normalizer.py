from __future__ import annotations

from typing import Any

from app.graph.planner.planner_contract import ALLOWED_KBS, ALLOWED_TOOLS


def normalize_enum(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


def clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text[:180])
    return output


def normalize_tools(raw_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if not isinstance(raw_tools, list):
        return tools
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in ALLOWED_TOOLS:
            continue
        tool = {"name": name, "purpose": str(item.get("purpose") or "").strip()}
        kb_name = str(item.get("kb_name") or "").strip()
        if kb_name:
            if name != "kb_search" or kb_name not in ALLOWED_KBS:
                continue
            tool["kb_name"] = kb_name
        query = str(item.get("query") or "").strip()
        if query:
            tool["query"] = query
        if name == "customer_store_lookup":
            location_candidates = _normalize_location_candidates(item.get("location_candidates"))
            if location_candidates:
                tool["location_candidates"] = location_candidates
            location_specificity = str(item.get("location_specificity") or "").strip()
            if location_specificity in {
                "confirmed_region",
                "specific_place",
                "typo_or_alias",
                "generic_landmark_without_region",
                "ambiguous_place_without_region",
            }:
                tool["location_specificity"] = location_specificity
        for key in (
            "reason",
            "origin",
            "destination",
            "candidate_store_ids",
            "candidate_source",
            "store_id",
            "store_name",
            "date",
            "target_time",
            "time",
            "appointment_time",
            "address",
            "scope",
            "need_fields",
            "for_distance",
            "order_id",
            "category_id",
            "prepay",
            "amount",
            "mobile",
            "customer_name",
            "user_id",
            "teacher_id",
            "seat_check",
            "store_confirmation_source",
            "confirmed_by_customer",
            "availability_source",
        ):
            if key in item:
                tool[key] = item[key]
        tools.append(tool)
    return tools


def _normalize_location_candidates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value[:3]:
        if isinstance(item, str):
            query = item.strip()
            candidate: dict[str, Any] = {"query": query}
        elif isinstance(item, dict):
            query = str(item.get("query") or item.get("normalized_query") or "").strip()
            candidate = {
                "query": query,
                "reason": str(item.get("reason") or "").strip()[:180],
                "confidence": str(item.get("confidence") or "").strip(),
                "requires_confirmation": bool(item.get("requires_confirmation", True)),
            }
        else:
            continue
        if not query or query in seen:
            continue
        seen.add(query)
        output.append({key: item for key, item in candidate.items() if item not in (None, "")})
    return output


def dedupe_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        query = str(item.get("query") or "").strip()
        key = (
            name,
            kb_name,
            query,
            str(item.get("store_id") or ""),
            str(item.get("date") or item.get("appointment_time") or ""),
            str(item.get("order_id") or ""),
            str(item.get("prepay") or item.get("amount") or ""),
        )
        if name not in ALLOWED_TOOLS or key in seen:
            continue
        seen.add(key)
        unique.append(dict(item))
    return unique
