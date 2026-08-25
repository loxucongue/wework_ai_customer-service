from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.graph.planner.runtime_plan import planner_public_route
from app.graph.planner.runtime_plan import planner_task_views


logger = logging.getLogger(__name__)
_RUNNING_STALE_AFTER_SECONDS = 15 * 60


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_dict(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Invalid persisted JSON object ignored: %s", exc)
        return {}


def loads_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Invalid persisted JSON list ignored: %s", exc)
        return []


def decode_run(row: dict[str, Any]) -> dict[str, Any]:
    for key in ["input_snapshot", "output_snapshot"]:
        row[key] = loads_dict(row.get(key))
    for key in ["intents", "tags"]:
        row[key] = loads_list(row.get(key))
    row["token_usage"] = loads_dict(row.get("token_usage"))
    row["interface_version"] = interface_version_from_run(row)
    output_snapshot = row.get("output_snapshot") if isinstance(row.get("output_snapshot"), dict) else {}
    runtime_status = str(output_snapshot.get("runtime_status") or "").strip()
    started_at = str(output_snapshot.get("runtime_started_at") or row.get("created_at") or "")
    finished_at = str(output_snapshot.get("runtime_finished_at") or "")
    if not runtime_status:
        runtime_status = "completed_with_errors" if str(row.get("error") or "") else "completed"
    if runtime_status == "running" and _age_seconds(started_at) > _RUNNING_STALE_AFTER_SECONDS:
        runtime_status = "interrupted"
    row["runtime_status"] = runtime_status
    row["runtime_phase"] = str(output_snapshot.get("runtime_phase") or "")
    row["started_at"] = started_at
    row["finished_at"] = finished_at
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


def interface_version_from_run(run: dict[str, Any]) -> str:
    input_snapshot = run.get("input_snapshot") if isinstance(run.get("input_snapshot"), dict) else {}
    output_snapshot = run.get("output_snapshot") if isinstance(run.get("output_snapshot"), dict) else {}
    request_context = input_snapshot.get("request_context") if isinstance(input_snapshot.get("request_context"), dict) else {}
    raw_http = output_snapshot.get("http_response_body") if isinstance(output_snapshot.get("http_response_body"), dict) else {}
    candidates = [
        input_snapshot.get("interface_version"),
        request_context.get("interface_version"),
        request_context.get("api_version"),
        raw_http.get("interface_version"),
        raw_http.get("api_version"),
        run.get("interface_version"),
        output_snapshot.get("interface_version"),
        request_context.get("source_protocol"),
    ]
    for value in candidates:
        text = str(value or "").strip().lower()
        if text == "v3" or text.endswith("-v3") or "/v3" in text:
            return "v3"
        if text == "v2" or text.endswith("-v2") or "/v2" in text:
            return "v2"
        if text == "v1":
            return "v1"
    return "v1"


def _age_seconds(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
    except (TypeError, ValueError):
        return float("inf")
