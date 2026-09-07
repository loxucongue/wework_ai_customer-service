from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "secret",
    "client_secret",
    "password",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "bearer_token",
    "x_api_key",
    "ossaccesskeyid",
}
_SIGNED_QUERY_KEYS = {
    "ossaccesskeyid",
    "signature",
    "x-signature",
    "x-expires",
    "expires",
    "security-token",
    "x-oss-security-token",
    "token",
    "access_token",
    "api_key",
    "apikey",
}
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_first_day_log_value(value: Any) -> Any:
    """Return a JSON-compatible copy with credentials and signed URL secrets removed."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in {item.replace("-", "_") for item in _SECRET_KEYS}:
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact_first_day_log_value(item)
        return result
    if isinstance(value, list):
        return [redact_first_day_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_first_day_log_value(item) for item in value]
    if isinstance(value, str):
        return _URL_RE.sub(lambda match: _redact_url(match.group(0)), value)
    return value


def build_first_day_run_business_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, customer-safe summary for the outreach run list."""
    snapshot = _dict(run.get("input_snapshot"))
    workflow = _dict(run.get("workflow"))
    scene = _scene_analysis(workflow)
    mainline = _dict(scene.get("customer_mainline"))
    last_customer = _last_customer_message(snapshot.get("recent_messages"))
    assets = _dict(snapshot.get("asset_availability_summary"))
    required = _dict(scene.get("required_assets"))
    return {
        "last_customer_message": last_customer.get("text", ""),
        "last_customer_message_at": last_customer.get("created_at", ""),
        "customer_need": _string(mainline.get("latest_customer_main_need")),
        "silence_barrier": _string(mainline.get("silence_barrier")),
        "precedence": _string(_dict(scene.get("precedence_decision")).get("row_id")),
        "eligible": scene.get("eligible") if isinstance(scene.get("eligible"), bool) else None,
        "planned_media_count": sum(
            1
            for key in ("step1", "step2")
            if _string(_dict(required.get(key)).get("asset_id"))
        ),
        "available_media_count": _int(assets.get("available_count")),
    }


def build_first_day_run_observability(run: dict[str, Any]) -> dict[str, Any]:
    """Present one run as business decisions, material flow, nodes, and delivery facts."""
    snapshot = _dict(run.get("input_snapshot"))
    workflow = _dict(run.get("workflow"))
    scene = _scene_analysis(workflow)
    mainline = _dict(scene.get("customer_mainline"))
    asset_catalog = [
        dict(item)
        for item in _list(snapshot.get("asset_catalog"))
        if isinstance(item, dict)
    ]
    tasks = [dict(item) for item in _list(run.get("tasks")) if isinstance(item, dict)]
    selected_sources = _dict(scene.get("selected_source_ids"))
    required_assets = _dict(scene.get("required_assets"))
    material_steps: list[dict[str, Any]] = []
    for index, key in enumerate(("step1", "step2"), start=1):
        source_ids = {
            _string(value) for value in _list(selected_sources.get(key)) if _string(value)
        }
        options = [
            item
            for item in asset_catalog
            if _string(item.get("asset_id")) in source_ids
            or _asset_source_id(item) in source_ids
        ]
        task = next(
            (item for item in tasks if _int(item.get("step_index")) == index),
            {},
        )
        reply_messages = [
            item
            for item in _list(task.get("reply_messages"))
            if isinstance(item, dict)
        ]
        media_messages = [
            item for item in reply_messages if _string(item.get("type")) in {"image", "video"}
        ]
        required = _dict(required_assets.get(key))
        material_steps.append(
            {
                "step": index,
                "scene": _string(scene.get(f"{key}_scene")) or _task_scene(task),
                "source_ids": sorted(source_ids),
                "required_asset": required,
                "source_asset_options": options,
                "available_asset_count": sum(
                    1 for item in options if item.get("available_to_send") is not False
                ),
                "planned_media_count": len(media_messages),
                "planned_text_count": sum(
                    1 for item in reply_messages if _string(item.get("type")) == "text"
                ),
                "task_status": _string(task.get("status")),
                "delivery_state": (
                    "sent"
                    if media_messages and _string(task.get("status")) == "sent"
                    else "planned"
                    if media_messages
                    else "missing"
                    if _string(required.get("asset_id"))
                    else "text_only"
                ),
            }
        )
    return {
        "decision": {
            "eligible": scene.get("eligible") if isinstance(scene.get("eligible"), bool) else None,
            "final_decision": _string(run.get("final_decision")),
            "reason_code": _string(run.get("reason_code")),
            "current_scene": _string(scene.get("current_scene")),
            "first_scene": _string(scene.get("step1_scene")) or _string(run.get("first_scene")),
            "second_scene": _string(scene.get("step2_scene")) or _string(run.get("second_scene")),
            "first_objective": _string(scene.get("step1_objective")),
            "second_objective": _string(scene.get("step2_objective")),
            "customer_need": _string(mainline.get("latest_customer_main_need")),
            "silence_barrier": _string(mainline.get("silence_barrier")),
            "next_business_action": _string(mainline.get("next_business_action")),
            "precedence": _dict(scene.get("precedence_decision")),
            "hard_boundary": _dict(scene.get("hard_boundary")),
            "confidence": scene.get("confidence"),
        },
        "customer_context": {
            "recent_message_count": len(_list(snapshot.get("recent_messages"))),
            "last_customer_message": _last_customer_message(snapshot.get("recent_messages")),
            "conversation_activity": _dict(snapshot.get("conversation_activity")),
            "customer_relation": _dict(snapshot.get("customer_relation")),
        },
        "materials": {
            "summary": _dict(snapshot.get("asset_availability_summary"))
            or _asset_summary(asset_catalog),
            "recent_delivery": _dict(snapshot.get("recent_media_delivery")),
            "steps": material_steps,
        },
        "workflow_nodes": _workflow_nodes(workflow, run),
        "data_availability": {
            "raw_redacted": bool(_string(run.get("raw_redacted_at"))),
            "has_messages": bool(_list(snapshot.get("recent_messages"))),
            "has_material_catalog": bool(asset_catalog),
            "has_scene_analysis": bool(scene),
            "has_plan": bool(_dict(run.get("final_plan"))),
            "has_tasks": bool(tasks),
        },
    }


def _workflow_nodes(workflow: dict[str, Any], run: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = (
        ("scene_analyst", "场景分析"),
        ("scene_analyst_schema_repair", "场景结构修复"),
        ("scene_analyst_schema_repair_2", "场景二次结构修复"),
        ("plan_writer", "计划写作"),
        ("contract_verifier", "合同审核"),
        ("contract_verifier_schema_repair", "审核结构修复"),
        ("plan_writer_repair", "受限写作修复"),
        ("scene_analyst_replan", "场景重选"),
        ("plan_writer_after_replan", "重选后写作"),
        ("contract_verifier_after_replan", "重选后审核"),
    )
    error_node = _string(run.get("error_node"))
    output: list[dict[str, Any]] = []
    reached_failure = False
    for key, label in definitions:
        value = _dict(workflow.get(key))
        if value:
            trace = _dict(value.get("trace"))
            usage = _dict(trace.get("model_usage"))
            status = "failed" if error_node == key else "warning" if not value.get("output") else "completed"
            reached_failure = reached_failure or status == "failed"
            output.append(
                {
                    "key": key,
                    "label": label,
                    "status": status,
                    "elapsed_ms": trace.get("elapsed_ms") or usage.get("overall_duration_ms"),
                    "model": _string(usage.get("winner_model") or usage.get("model")),
                    "prompt_version": _string(trace.get("prompt_version")),
                    "attempt_count": _int(trace.get("attempt_count")),
                    "input": value.get("input") or {},
                    "output": value.get("output") or {},
                }
            )
        else:
            output.append(
                {
                    "key": key,
                    "label": label,
                    "status": "not_reached" if reached_failure else "skipped",
                    "elapsed_ms": None,
                    "model": "",
                    "prompt_version": "",
                    "attempt_count": 0,
                    "input": {},
                    "output": {},
                }
            )
    return output


def _scene_analysis(workflow: dict[str, Any]) -> dict[str, Any]:
    summary = _dict(workflow.get("summary"))
    return (
        _dict(summary.get("scene_analysis"))
        or _dict(workflow.get("scene_analysis"))
        or _dict(_dict(workflow.get("scene_analyst")).get("output"))
    )


def _last_customer_message(messages: Any) -> dict[str, Any]:
    values = [item for item in _list(messages) if isinstance(item, dict)]
    for message in reversed(values):
        role = _string(message.get("role") or message.get("sender_type")).lower()
        direction = _string(message.get("direction")).lower()
        if role not in {"user", "customer", "external"} and direction not in {"customer", "inbound"}:
            continue
        return {
            "text": _message_text(message),
            "created_at": _string(
                message.get("created_at") or message.get("msgtime") or message.get("timestamp")
            ),
            "message_type": _string(message.get("msgtype") or message.get("type") or "text"),
        }
    return {}


def _message_text(message: dict[str, Any]) -> str:
    value = message.get("content", message.get("text", ""))
    if isinstance(value, dict):
        return _string(value.get("text") or value.get("content") or value.get("url"))
    return _string(value)


def _task_scene(task: dict[str, Any]) -> str:
    for item in _list(task.get("content_source_metadata")):
        if isinstance(item, dict) and _string(item.get("scene")):
            return _string(item.get("scene"))
    return ""


def _asset_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total_count": len(items),
        "available_count": sum(1 for item in items if item.get("available_to_send") is not False),
        "recently_sent_count": sum(1 for item in items if item.get("available_to_send") is False),
        "image_count": sum(1 for item in items if _string(item.get("type")) == "image"),
        "video_count": sum(1 for item in items if _string(item.get("type")) == "video"),
    }


def _asset_source_id(item: dict[str, Any]) -> str:
    asset_id = _string(item.get("asset_id"))
    return _string(item.get("source_id")) or asset_id.rsplit(":", 1)[0]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    query = []
    changed = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        normalized = key.lower()
        if normalized in _SIGNED_QUERY_KEYS or normalized.startswith("x-amz-"):
            query.append((key, "[REDACTED]"))
            changed = True
        else:
            query.append((key, value))
    if not changed:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
