from __future__ import annotations

import asyncio
import json
import math
from typing import Any


DRIVING_ROUTE_SHORTLIST_SIZE = 8
DRIVING_ROUTE_MAX_CONCURRENCY = 5
DRIVING_ROUTE_CALL_TIMEOUT_SECONDS = 5.0


def parse_driving_route_workflow_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract one usable route. Distances are meters and durations are seconds."""

    payload = _json_object(raw.get("data")) or raw
    output = payload.get("output") if isinstance(payload, dict) else None
    if output in (None, "") and isinstance(payload, dict):
        output = payload.get("outputoutput")
    output = _json_object(output) if isinstance(output, str) else output
    if not isinstance(output, dict):
        return {"status": "invalid_output", "error": "missing_route_output"}

    paths = output.get("paths") if isinstance(output.get("paths"), list) else []
    normalized_paths: list[dict[str, int]] = []
    for position, path in enumerate(paths):
        if not isinstance(path, dict):
            continue
        cost = path.get("cost") if isinstance(path.get("cost"), dict) else {}
        distance_meters = _positive_int(path.get("distance"))
        duration_seconds = _positive_int(path.get("duration") or cost.get("duration"))
        if distance_meters is None or duration_seconds is None:
            continue
        normalized_paths.append(
            {
                "route_index": _non_negative_int(path.get("index"), default=position),
                "distance_meters": distance_meters,
                "duration_seconds": duration_seconds,
            }
        )

    if not normalized_paths:
        return {"status": "invalid_output", "error": "missing_valid_paths"}

    # The workflow's top-level distance/duration currently sum every alternative.
    # Only one path can represent an actual customer journey.
    best = min(
        normalized_paths,
        key=lambda item: (
            item["distance_meters"],
            item["duration_seconds"],
            item["route_index"],
        ),
    )
    return {
        "status": "ok",
        **best,
        "path_count": len(normalized_paths),
    }


async def rerank_stores_by_driving_route(
    *,
    coze_client: Any,
    workflow_id: str,
    origin_location: str,
    ranked_stores: list[dict[str, Any]],
    shortlist_size: int = DRIVING_ROUTE_SHORTLIST_SIZE,
    max_concurrency: int = DRIVING_ROUTE_MAX_CONCURRENCY,
    timeout_seconds: float = DRIVING_ROUTE_CALL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Rerank a Haversine shortlist without changing the authorized store set."""

    if not workflow_id:
        return _fallback_result(ranked_stores, status="disabled")
    origin = _coordinate_text(origin_location)
    if not origin:
        return _fallback_result(ranked_stores, status="invalid_origin")

    comparable = [
        (position, store, _store_coordinate_text(store))
        for position, store in enumerate(ranked_stores)
        if store.get("distance_km") is not None and not store.get("distance_error")
    ]
    comparable = [item for item in comparable if item[2]]
    shortlist = comparable[: max(1, int(shortlist_size))]
    if not shortlist:
        return _fallback_result(ranked_stores, status="no_comparable_candidates")

    semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def route_one(position: int, store: dict[str, Any], destination: str) -> dict[str, Any]:
        try:
            async with semaphore:
                raw = await asyncio.wait_for(
                    coze_client.run_workflow(
                        workflow_id,
                        {"origin": origin, "destination": destination},
                    ),
                    timeout=max(0.1, float(timeout_seconds)),
                )
            parsed = parse_driving_route_workflow_result(raw)
            if parsed.get("status") != "ok":
                return {
                    "position": position,
                    "store_id": _store_id(store),
                    "status": "failed",
                    "error": str(parsed.get("error") or "invalid_route_result"),
                }
            return {
                "position": position,
                "store_id": _store_id(store),
                **parsed,
            }
        except Exception as exc:
            return {
                "position": position,
                "store_id": _store_id(store),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }

    results = await asyncio.gather(
        *(route_one(position, store, destination) for position, store, destination in shortlist)
    )
    successful = [item for item in results if item.get("status") == "ok"]
    if len(successful) != len(shortlist):
        return {
            **_fallback_result(ranked_stores, status="fallback_haversine"),
            "route_candidate_count": len(shortlist),
            "route_success_count": len(successful),
            "route_errors": [
                {
                    "store_id": item.get("store_id"),
                    "error": item.get("error"),
                }
                for item in results
                if item.get("status") != "ok"
            ],
        }

    route_by_position = {int(item["position"]): item for item in successful}
    routed_shortlist: list[dict[str, Any]] = []
    shortlisted_positions = {position for position, _, _ in shortlist}
    for position, store, _ in shortlist:
        route = route_by_position[position]
        routed_shortlist.append(
            {
                **store,
                "distance_km": round(int(route["distance_meters"]) / 1000, 2),
                "distance_source": "driving_route",
                "driving_distance_meters": int(route["distance_meters"]),
                "driving_duration_seconds": int(route["duration_seconds"]),
                "driving_route_index": int(route["route_index"]),
                "driving_route_path_count": int(route["path_count"]),
            }
        )
    routed_shortlist.sort(
        key=lambda item: (
            int(item["driving_distance_meters"]),
            int(item["driving_duration_seconds"]),
            _store_id(item),
        )
    )
    remaining = [
        store
        for position, store in enumerate(ranked_stores)
        if position not in shortlisted_positions
    ]
    route_ranking_complete = len(shortlist) == len(comparable)
    return {
        "status": "ok",
        "ranking_method": "driving_route" if route_ranking_complete else "driving_route_shortlist",
        "ranked_stores": [*routed_shortlist, *remaining],
        "route_candidate_count": len(shortlist),
        "route_success_count": len(successful),
        "route_ranking_complete": route_ranking_complete,
        "route_shortlist_size": len(shortlist),
    }


def _fallback_result(ranked_stores: list[dict[str, Any]], *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "ranking_method": "haversine",
        "ranked_stores": ranked_stores,
        "route_candidate_count": 0,
        "route_success_count": 0,
        "route_ranking_complete": False,
        "route_shortlist_size": 0,
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _coordinate_text(value: Any) -> str:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        parts = value
    else:
        parts = str(value or "").split(",")
    if len(parts) != 2:
        return ""
    try:
        longitude = float(parts[0])
        latitude = float(parts[1])
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        return ""
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        return ""
    return f"{longitude:.6f},{latitude:.6f}"


def _store_coordinate_text(store: dict[str, Any]) -> str:
    geocode = store.get("geocode") if isinstance(store.get("geocode"), dict) else {}
    location = geocode.get("location") or store.get("location")
    if location:
        return _coordinate_text(location)
    longitude = store.get("longitude") or store.get("lng")
    latitude = store.get("latitude") or store.get("lat")
    if longitude not in (None, "") and latitude not in (None, ""):
        return _coordinate_text((longitude, latitude))
    return ""


def _store_id(store: dict[str, Any]) -> str:
    return str(store.get("store_id") or store.get("id") or "").strip()
