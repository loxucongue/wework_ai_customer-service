from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.graph.planner.runtime_plan import planner_public_route
from app.graph.planner.runtime_plan import planner_task_views


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
    policy_id = str(state.get("policy_id") or "").strip()
    if policy_id:
        tags.append(policy_id)
    policy_family_id = str(state.get("policy_family_id") or "").strip()
    if policy_family_id:
        tags.append(policy_family_id)
    exact_policy_id = str(state.get("exact_policy_id") or "").strip()
    if exact_policy_id:
        tags.append(exact_policy_id)
    for item in planner_task_views(state):
        intent = str(item.get("intent") or "").strip()
        if intent:
            tags.append(intent)
    for key in ("conversion_stage", "customer_type", "main_blocker", "next_step"):
        value = str(state.get(key) or "").strip()
        if value:
            tags.append(value)
    route = planner_public_route(state)
    if route.get("subflow"):
        tags.append(str(route["subflow"]))
    if state.get("image_info", {}).get("has_image"):
        tags.append("has_image")
    reply_control = state.get("reply_control") if isinstance(state.get("reply_control"), dict) else {}
    mode = str(reply_control.get("mode") or "").strip()
    if mode in {"filtered", "superseded", "merged_latest"}:
        tags.append("merged" if mode == "merged_latest" else mode)
    async_final = reply_control.get("async_final") if isinstance(reply_control.get("async_final"), dict) else {}
    async_status = str(async_final.get("status") or "").strip()
    if async_status == "sent":
        tags.append("async_sent")
    elif async_status in {"skipped", "superseded", "error"}:
        tags.append("async_skipped" if async_status in {"skipped", "superseded"} else "async_error")
    return list(dict.fromkeys(tags))
