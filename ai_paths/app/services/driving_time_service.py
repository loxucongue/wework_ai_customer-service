from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.services.coze_client import CozeClient


AIRPORT_HINTS = {
    "浦东": "上海浦东机场",
    "虹桥": "上海虹桥机场",
    "高崎": "厦门高崎机场",
    "厦门": "厦门高崎机场",
    "上海": "上海浦东机场",
}


def origin_from_store_query(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    if "机场" in text:
        for key, origin in AIRPORT_HINTS.items():
            if key in text:
                return origin
        city_match = re.search(r"([\u4e00-\u9fa5]{2,4})机场", text)
        if city_match:
            return f"{city_match.group(1)}机场"
        return "机场"
    for suffix in ["火车站", "高铁站", "地铁站"]:
        if suffix in text:
            match = re.search(rf"([\u4e00-\u9fa5A-Za-z0-9]{{2,12}}{suffix})", text)
            if match:
                return match.group(1)
    location_match = re.search(r"(?:我在|人在|位置在|附近在)([\u4e00-\u9fa5A-Za-z0-9路街区号附近]{2,24})", text)
    if location_match:
        return location_match.group(1).strip()
    return ""


async def enrich_store_lookup_with_driving_times(
    lookup: dict[str, Any],
    *,
    query: str,
    coze_client: CozeClient,
    limit: int = 3,
) -> dict[str, Any]:
    if not isinstance(lookup, dict) or not lookup.get("stores"):
        return lookup
    origin = origin_from_store_query(query)
    if not origin:
        return lookup
    stores = [store for store in lookup.get("stores", [])[:limit] if isinstance(store, dict)]
    if not stores:
        return lookup

    async def run_one(store: dict[str, Any]) -> dict[str, Any]:
        destination = str(store.get("address") or store.get("name") or "").strip()
        if not destination:
            return {"store_name": store.get("name"), "origin": origin, "destination": "", "error": "missing_destination"}
        try:
            raw = await coze_client.run_workflow(
                coze_client.settings.driving_time_workflow_id,
                {"origin": origin, "destination": destination},
            )
            return _normalize_driving_result(raw, origin=origin, destination=destination, store_name=str(store.get("name") or ""))
        except Exception as exc:
            return {
                "store_name": store.get("name"),
                "origin": origin,
                "destination": destination,
                "error": f"{type(exc).__name__}: {exc}",
            }

    results = await asyncio.gather(*(run_one(store) for store in stores), return_exceptions=False)
    enriched = dict(lookup)
    enriched["driving_origin"] = origin
    enriched["driving_times"] = results
    time_by_name = {str(item.get("store_name") or ""): item for item in results if isinstance(item, dict)}
    enriched_stores: list[dict[str, Any]] = []
    for store in lookup.get("stores", []):
        if not isinstance(store, dict):
            enriched_stores.append(store)
            continue
        item = dict(store)
        driving = time_by_name.get(str(item.get("name") or ""))
        if driving:
            item["driving_time"] = driving
        enriched_stores.append(item)
    enriched["stores"] = enriched_stores
    recommended = enriched.get("recommended_store")
    if isinstance(recommended, dict):
        driving = time_by_name.get(str(recommended.get("name") or ""))
        if driving:
            enriched["recommended_store"] = {**recommended, "driving_time": driving}
            summary = _driving_summary(driving)
            if summary:
                reason = str(enriched.get("recommendation_reason") or "").strip()
                enriched["recommendation_reason"] = f"{reason} {summary}".strip()
    return enriched


def _normalize_driving_result(
    raw: dict[str, Any],
    *,
    origin: str,
    destination: str,
    store_name: str,
) -> dict[str, Any]:
    payload = _parse_coze_data(raw)
    output = _coerce_output(payload.get("output", payload))
    summary = _summary_from_output(output)
    normalized: dict[str, Any] = {
        "store_name": store_name,
        "origin": origin,
        "destination": destination,
        "summary": summary,
    }
    if not summary:
        normalized["error"] = "empty_driving_summary"
    duration = _metric_value(output, DURATION_KEYS)
    distance = _metric_value(output, DISTANCE_KEYS)
    if duration:
        normalized["duration"] = duration
    if distance:
        normalized["distance"] = distance
    return {key: value for key, value in normalized.items() if value not in ("", None, [], {})}


def _parse_coze_data(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data") if isinstance(raw, dict) else None
    if isinstance(data, str) and data:
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else {"output": parsed}
        except json.JSONDecodeError:
            return {"output": data}
    if isinstance(data, dict):
        return data
    return raw if isinstance(raw, dict) else {}


SUMMARY_KEYS = [
    "summary",
    "摘要",
    "text",
    "message",
    "result",
    "description",
    "route_summary",
    "路线摘要",
]
DURATION_KEYS = [
    "duration_text",
    "duration",
    "driving_time",
    "drivingTime",
    "time_text",
    "time",
    "耗时",
    "驾车耗时",
    "预计耗时",
    "预计时间",
]
DISTANCE_KEYS = [
    "distance_text",
    "distance",
    "distanceText",
    "距离",
    "路程",
]
ERROR_KEYS = ["error", "err_msg", "errmsg", "错误", "失败"]


def _coerce_output(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return _coerce_output(parsed)
        except json.JSONDecodeError:
            return {"text": text}
    if isinstance(value, dict):
        output = value.get("output")
        if output is not None and output is not value:
            nested = _coerce_output(output)
            if nested:
                merged = dict(value)
                merged.pop("output", None)
                merged.update(nested)
                return merged
        return value
    if isinstance(value, list):
        for item in value:
            normalized = _coerce_output(item)
            if _summary_from_output(normalized):
                return normalized
        return {"items": value}
    if value is None:
        return {}
    return {"text": str(value)}


def _summary_from_output(output: dict[str, Any]) -> str:
    if not isinstance(output, dict) or _has_error(output):
        return ""
    route = _first_route(output)
    if route:
        duration = _find_direct_value(route, DURATION_KEYS)
        distance = _find_direct_value(route, DISTANCE_KEYS)
        if duration and distance:
            return f"约{_normalize_duration(duration)}，距离{_normalize_distance(distance)}"
        if duration:
            return f"约{_normalize_duration(duration)}"
        if distance:
            return f"距离{_normalize_distance(distance)}"
    explicit = _find_direct_value(output, SUMMARY_KEYS)
    if explicit and not _looks_like_json(explicit):
        return explicit
    duration = _find_direct_value(output, DURATION_KEYS)
    distance = _find_direct_value(output, DISTANCE_KEYS)
    if duration and distance:
        return f"约{_normalize_duration(duration)}，距离{_normalize_distance(distance)}"
    if duration:
        return f"约{_normalize_duration(duration)}"
    if distance:
        return f"距离{_normalize_distance(distance)}"
    return ""


def _has_error(output: dict[str, Any]) -> bool:
    return any(str(output.get(key) or "").strip() for key in ERROR_KEYS)


def _find_first_value(value: Any, keys: list[str]) -> str:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            text = _primitive_text(item)
            if text:
                return text
        for nested in value.values():
            text = _find_first_value(nested, keys)
            if text:
                return text
    elif isinstance(value, list):
        for item in value:
            text = _find_first_value(item, keys)
            if text:
                return text
    return ""


def _find_direct_value(value: Any, keys: list[str]) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        text = _primitive_text(value.get(key))
        if text:
            return text
    return ""


def _metric_value(output: dict[str, Any], keys: list[str]) -> str:
    route = _first_route(output)
    if route:
        value = _find_direct_value(route, keys)
        if value:
            return value
    return _find_direct_value(output, keys)


def _primitive_text(value: Any) -> str:
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text if text and text.lower() not in {"none", "null"} else ""
    return ""


def _first_route(output: dict[str, Any]) -> dict[str, Any]:
    for key in ["routes", "route", "paths", "path", "方案", "路线"]:
        item = output.get(key)
        if isinstance(item, dict):
            return item
        if isinstance(item, list):
            for entry in item:
                if isinstance(entry, dict):
                    return entry
    return {}


def _normalize_duration(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"(分钟|小时|min|h|hour)", text, re.IGNORECASE):
        return text
    if re.fullmatch(r"\d+(\.\d+)?", text):
        seconds = float(text)
        if seconds >= 3600:
            hours = int(seconds // 3600)
            minutes = int(round((seconds % 3600) / 60))
            if minutes == 60:
                hours += 1
                minutes = 0
            return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
        minutes = max(1, int(round(seconds / 60)))
        return f"{minutes}分钟"
    return text


def _normalize_distance(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"(公里|千米|km|米|m)", text, re.IGNORECASE):
        return text
    if re.fullmatch(r"\d+(\.\d+)?", text):
        number = float(text)
        if number > 1000:
            return f"{round(number / 1000, 1)}公里"
        return f"{int(number) if number.is_integer() else number}米"
    return text


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(("{", "[")) and stripped.endswith(("}", "]"))


def _driving_summary(driving: dict[str, Any]) -> str:
    summary = str(driving.get("summary") or "").strip()
    if not summary:
        return ""
    return f"车程参考：{summary}"
