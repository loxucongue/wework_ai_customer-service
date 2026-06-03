from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def loads_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def decode_run(row: dict[str, Any]) -> dict[str, Any]:
    for key in ["input_snapshot", "output_snapshot"]:
        row[key] = loads_dict(row.get(key))
    for key in ["intents", "tags"]:
        row[key] = loads_list(row.get(key))
    row["token_usage"] = loads_dict(row.get("token_usage"))
    return row


def decode_trace(row: dict[str, Any]) -> dict[str, Any]:
    for key in ["input_snapshot", "output_snapshot"]:
        row[key] = loads_dict(row.get(key))
    row["tool_calls"] = loads_list(row.get("tool_calls"))
    return row


def tags_from_state(state: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for item in state.get("intents") or []:
        if isinstance(item, dict) and item.get("intent"):
            tags.append(str(item["intent"]))
    route = state.get("route_result") or {}
    if route.get("subflow"):
        tags.append(str(route["subflow"]))
    if state.get("image_info", {}).get("has_image"):
        tags.append("has_image")
    return list(dict.fromkeys(tags))
