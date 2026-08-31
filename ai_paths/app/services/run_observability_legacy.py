from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable


_NODE_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("send", ("send_reply", "async_final", "message_delivery", "dispatch")),
    ("commit", ("commit", "persist", "record", "memory_update", "event_update")),
    ("validation", ("validate", "validation", "audit", "repair", "quality")),
    ("reply", ("reply_synth", "reply_generator", "finalize_reply", "reply_node")),
    ("join", ("join", "merge_evidence", "deterministic_join")),
    ("tool", ("tool_exec", "action_node", "store_resolution", "store_workflow", "execute_tools")),
    ("planner", ("planner", "tool_plan")),
    ("gate", ("sop_gate", "chat_gate", "content_gate", "gate")),
    ("context", ("shared_context", "customer_context", "context_load", "context_build")),
    ("preprocess", ("normalization", "normalize", "preprocess", "vision", "voice", "location_card")),
)

_NODE_LABELS = {
    "preprocess": "请求预处理",
    "context": "上下文装配",
    "gate": "内容与 SOP Gate",
    "planner": "工具规划",
    "tool": "工具执行",
    "join": "证据合并",
    "reply": "最终回复",
    "validation": "结构与事实校验",
    "commit": "状态提交",
    "send": "消息发送",
    "other": "其他节点",
}

_IMPORTANT_INPUT_FIELDS = (
    ("content", "当前消息"),
    ("normalized_content", "归一消息"),
    ("msgtype", "消息类型"),
    ("conversation_history_count", "历史消息数"),
    ("customer_id", "客户 ID"),
    ("wechat", "企微账号"),
    ("deadline_remaining_seconds", "剩余预算"),
)

_IMPORTANT_OUTPUT_FIELDS = (
    ("decision", "决策"),
    ("route", "路由"),
    ("status", "状态"),
    ("planner_decision", "Planner 决策"),
    ("planner_stage", "Planner 阶段"),
    ("reply_source", "回复来源"),
    ("fallback_source", "异常恢复来源"),
    ("store_resolution_fact", "门店事实"),
    ("reply_messages", "回复消息"),
    ("warnings", "警告"),
    ("errors", "错误"),
)


def build_run_observability(
    detail: dict[str, Any],
    *,
    raw_log: dict[str, Any] | None = None,
    dispatches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    run = detail.get("run") if isinstance(detail.get("run"), dict) else {}
    traces = detail.get("node_traces") if isinstance(detail.get("node_traces"), list) else []
    clean_traces = [item for item in traces if isinstance(item, dict)]
    clean_dispatches = [item for item in (dispatches or []) if isinstance(item, dict)]
    nodes = [_node_view(trace, index) for index, trace in enumerate(clean_traces)]
    _assign_parallel_groups(nodes)

    output = run.get("output_snapshot") if isinstance(run.get("output_snapshot"), dict) else {}
    input_snapshot = run.get("input_snapshot") if isinstance(run.get("input_snapshot"), dict) else {}
    request_context = input_snapshot.get("request_context") if isinstance(input_snapshot.get("request_context"), dict) else {}
    model_calls = [call for node in nodes for call in node.get("model_calls", [])]
    errors = _as_list(_safe_load_error(run.get("error")))
    warnings = _as_list(output.get("warnings"))
    final_messages = _final_messages(output, raw_log or {})
    delivery = _delivery_view(clean_dispatches)
    fallback_detected = _fallback_detected(output, final_messages, nodes)
    wall_duration_ms = trace_wall_duration_ms(clean_traces)
    if wall_duration_ms <= 0:
        wall_duration_ms = int(run.get("duration_ms") or 0)

    status = _overall_status(
        errors=errors,
        final_messages=final_messages,
        fallback_detected=fallback_detected,
        delivery_status=str(delivery.get("status") or ""),
        node_statuses=[str(item.get("status") or "") for item in nodes],
    )
    slowest = max(nodes, key=lambda item: int(item.get("duration_ms") or 0), default={})
    retry_count = sum(max(0, int(item.get("attempts") or 0) - 1) for item in model_calls)
    fallback_count = sum(1 for item in model_calls if item.get("fallback_used") or item.get("hedge_started"))

    return {
        "contract_version": "run_observability_v1",
        "summary": {
            "status": status,
            "request_id": str(run.get("request_id") or ""),
            "created_at": str(run.get("created_at") or ""),
            "interface_version": str(request_context.get("interface_version") or request_context.get("api_version") or "v1"),
            "reply_chain_mode": str(request_context.get("reply_chain_mode") or ""),
            "message_type": str(request_context.get("msgtype") or "text"),
            "customer_message": str(input_snapshot.get("content") or ""),
            "wall_duration_ms": wall_duration_ms,
            "recorded_duration_ms": int(run.get("duration_ms") or 0),
            "slowest_node": {
                "node_name": slowest.get("node_name", ""),
                "display_name": slowest.get("display_name", ""),
                "duration_ms": int(slowest.get("duration_ms") or 0),
            },
            "model_call_count": len(model_calls),
            "model_retry_count": retry_count,
            "model_fallback_count": fallback_count,
            "total_tokens": sum(int(item.get("total_tokens") or 0) for item in model_calls),
            "fallback_detected": fallback_detected,
            "error_count": len(errors),
            "warning_count": len(warnings) + sum(len(item.get("warnings", [])) for item in nodes),
            "errors": errors,
            "warnings": warnings,
            "final_messages": final_messages,
            "http_response_messages": _reply_messages(output, ("http_response_reply_messages",)),
            "async_final_messages": _reply_messages(
                output,
                ("reply_control.async_final.reply_messages", "async_final_reply.reply_messages"),
            ),
        },
        "nodes": nodes,
        "delivery": delivery,
        "debug": {
            "snapshot_is_compacted": True,
            "snapshot_label": "调试快照（可能截断）",
        },
    }


def trace_wall_duration_ms(traces: Iterable[dict[str, Any]]) -> int:
    starts: list[datetime] = []
    finishes: list[datetime] = []
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        started = _parse_time(trace.get("started_at") or trace.get("created_at"))
        finished = _parse_time(trace.get("finished_at"))
        if started is None:
            continue
        starts.append(started)
        if finished is None:
            finished = started + timedelta(milliseconds=max(0, int(trace.get("duration_ms") or 0)))
        finishes.append(finished)
    if not starts or not finishes:
        return 0
    return max(0, int((max(finishes) - min(starts)).total_seconds() * 1000))


def _node_view(trace: dict[str, Any], index: int) -> dict[str, Any]:
    node_name = str(trace.get("node_name") or trace.get("node") or "unknown")
    node_kind = _node_kind(node_name)
    input_snapshot = trace.get("input_snapshot") if isinstance(trace.get("input_snapshot"), dict) else {}
    output_snapshot = trace.get("output_snapshot") if isinstance(trace.get("output_snapshot"), dict) else {}
    tool_calls = trace.get("tool_calls") if isinstance(trace.get("tool_calls"), list) else []
    model_calls = _collect_model_calls(tool_calls, node_name)
    regular_tools = [_tool_call_view(item) for item in tool_calls if isinstance(item, dict) and not _is_model_call(item)]
    error = str(trace.get("error") or "")
    warnings = _node_warnings(output_snapshot, model_calls, regular_tools)
    status = _node_status(error, output_snapshot, warnings)
    started_at = str(trace.get("started_at") or trace.get("created_at") or "")
    finished_at = str(trace.get("finished_at") or "")
    if not finished_at:
        started = _parse_time(started_at)
        if started is not None:
            finished_at = (started + timedelta(milliseconds=max(0, int(trace.get("duration_ms") or 0)))).isoformat()

    return {
        "id": str(trace.get("id") or f"node-{index + 1}"),
        "sequence": index + 1,
        "node_name": node_name,
        "node_kind": node_kind,
        "display_name": _display_name(node_name, node_kind),
        "status": status,
        "duration_ms": int(trace.get("duration_ms") or 0),
        "started_at": started_at,
        "finished_at": finished_at,
        "parallel_group": "",
        "summary": _node_summary(node_kind, output_snapshot, model_calls, regular_tools, error),
        "important_inputs": _important_fields(input_snapshot, _IMPORTANT_INPUT_FIELDS),
        "important_outputs": _important_fields(output_snapshot, _IMPORTANT_OUTPUT_FIELDS),
        "model_calls": model_calls,
        "tool_calls": regular_tools,
        "warnings": warnings,
        "errors": [error] if error else [],
    }


def _node_kind(node_name: str) -> str:
    normalized = node_name.lower()
    for kind, tokens in _NODE_KIND_RULES:
        if any(token in normalized for token in tokens):
            return kind
    return "other"


def _display_name(node_name: str, node_kind: str) -> str:
    base = _NODE_LABELS.get(node_kind, _NODE_LABELS["other"])
    return f"{base} · {node_name}"


def _node_summary(
    node_kind: str,
    output: dict[str, Any],
    model_calls: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    error: str,
) -> list[str]:
    if error:
        return [f"节点失败：{error}"]
    lines: list[str] = []
    messages = output.get("reply_messages") if isinstance(output.get("reply_messages"), list) else []
    if messages:
        types = [str(item.get("type") or "text") for item in messages if isinstance(item, dict)]
        lines.append(f"生成 {len(messages)} 条客户消息：{', '.join(types) or 'text'}")
    store_fact = output.get("store_resolution_fact") if isinstance(output.get("store_resolution_fact"), dict) else {}
    if store_fact:
        status = str(store_fact.get("status") or store_fact.get("delivery_mode") or "")
        store_ids = store_fact.get("delivery_store_ids") if isinstance(store_fact.get("delivery_store_ids"), list) else []
        lines.append(f"门店结果：{status or '已生成'}" + (f"，待发送 {len(store_ids)} 家" if store_ids else ""))
    decision = output.get("decision") or output.get("planner_decision") or output.get("route")
    if isinstance(decision, (str, int, float, bool)) and str(decision):
        lines.append(f"输出决策：{decision}")
    if model_calls:
        lines.append(f"完成 {len(model_calls)} 次模型调用")
    if tool_calls:
        succeeded = sum(1 for item in tool_calls if item.get("status") == "success")
        lines.append(f"工具调用 {len(tool_calls)} 次，成功 {succeeded} 次")
    if not lines:
        lines.append(_NODE_LABELS.get(node_kind, "节点") + "已完成")
    return lines[:4]


def _important_fields(snapshot: dict[str, Any], definitions: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for path, label in definitions:
        value = _path_value(snapshot, path)
        if value in (None, "", [], {}):
            continue
        if path == "conversation_history_count" and value is None:
            history = snapshot.get("conversation_history")
            value = len(history) if isinstance(history, list) else None
        values.append({"key": path, "label": label, "value": _compact_display_value(value)})
    if not any(item["key"] == "conversation_history_count" for item in values):
        history = snapshot.get("conversation_history")
        if isinstance(history, list):
            values.append({"key": "conversation_history_count", "label": "历史消息数", "value": len(history)})
    return values[:8]


def _collect_model_calls(values: list[Any], node_name: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        _collect_model_call(value, output, node_name=node_name, call_id=f"{node_name}-{index + 1}")
    return output


def _collect_model_call(value: Any, output: list[dict[str, Any]], *, node_name: str, call_id: str) -> None:
    if not isinstance(value, dict):
        return
    if _is_model_call(value):
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        model_input = value.get("input") if isinstance(value.get("input"), dict) else {}
        messages = model_input.get("messages") if isinstance(model_input.get("messages"), list) else []
        attempts = int(usage.get("attempts") or usage.get("request_attempt") or 1)
        winner_model = str(usage.get("winner_model") or usage.get("model") or model_input.get("model") or "")
        configured_model = str(usage.get("configured_model") or model_input.get("configured_model") or "")
        output.append(
            {
                "id": call_id,
                "node_name": node_name,
                "name": str(value.get("name") or "model_call"),
                "tier": str(usage.get("tier") or model_input.get("tier") or ""),
                "model": winner_model,
                "configured_model": configured_model,
                "duration_ms": int(usage.get("overall_duration_ms") or usage.get("duration_ms") or value.get("duration_ms") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "attempts": attempts,
                "hedge_started": bool(usage.get("hedge_started")),
                "fallback_used": bool(configured_model and winner_model and configured_model != winner_model),
                "timeout_stage": str(usage.get("timeout_stage") or ""),
                "error": str(value.get("error") or ""),
                "prompt_messages": [
                    {
                        "role": str(item.get("role") or "unknown") if isinstance(item, dict) else "unknown",
                        "chars": _content_chars(item.get("content") if isinstance(item, dict) else item),
                        "preview": _content_preview(item.get("content") if isinstance(item, dict) else item),
                    }
                    for item in messages
                ],
            }
        )
    nested = value.get("nested_calls") if isinstance(value.get("nested_calls"), list) else []
    for index, item in enumerate(nested):
        _collect_model_call(item, output, node_name=node_name, call_id=f"{call_id}-nested-{index + 1}")
    for key in ("retry", "recovery"):
        if isinstance(value.get(key), dict):
            _collect_model_call(value[key], output, node_name=node_name, call_id=f"{call_id}-{key}")


def _is_model_call(value: dict[str, Any]) -> bool:
    name = str(value.get("name") or "").lower()
    return (
        isinstance(value.get("usage"), dict)
        or "raw_json_output" in value
        or any(token in name for token in ("model", "planner", "reply_synthesizer", "profile_analyzer", "vision", "gate"))
    )


def _tool_call_view(value: dict[str, Any]) -> dict[str, Any]:
    name = str(value.get("name") or "tool")
    error = str(value.get("error") or "")
    tool_input = value.get("input") if isinstance(value.get("input"), dict) else {}
    tool_output = value.get("output")
    return {
        "name": name,
        "status": "failed" if error else "success",
        "duration_ms": int(value.get("duration_ms") or 0),
        "input_summary": _sanitize_tool_input(tool_input),
        "output_summary": _tool_output_summary(tool_output),
        "error": error,
    }


def _sanitize_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    blocked_tokens = ("token", "secret", "password", "authorization", "api_key", "apikey")
    output: dict[str, Any] = {}
    for key, item in list(value.items())[:12]:
        if any(token in key.lower() for token in blocked_tokens):
            output[key] = "[已隐藏]"
        elif isinstance(item, str) and (item.startswith("http://") or item.startswith("https://")):
            output[key] = item.split("?", 1)[0]
        else:
            output[key] = _compact_display_value(item)
    return output


def _tool_output_summary(value: Any) -> Any:
    if isinstance(value, list):
        return {"item_count": len(value), "sample": [_compact_display_value(item) for item in value[:2]]}
    if isinstance(value, dict):
        preferred = {}
        for key in ("status", "success", "count", "store_id", "store_ids", "delivery_store_ids", "error"):
            if key in value:
                preferred[key] = _compact_display_value(value[key])
        return preferred or {"field_count": len(value), "fields": list(value.keys())[:12]}
    return _compact_display_value(value)


def _node_warnings(
    output: dict[str, Any],
    model_calls: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> list[str]:
    warnings = [str(item) for item in _as_list(output.get("warnings")) if str(item)]
    for call in model_calls:
        if call.get("attempts", 1) > 1:
            warnings.append(f"模型重试 {call['attempts']} 次")
        if call.get("hedge_started"):
            warnings.append("启动 hedge/fallback 竞速")
        if call.get("timeout_stage"):
            warnings.append(f"模型超时阶段：{call['timeout_stage']}")
    for call in tool_calls:
        if call.get("status") == "failed":
            warnings.append(f"工具失败：{call.get('name')}")
    return list(dict.fromkeys(warnings))[:12]


def _node_status(error: str, output: dict[str, Any], warnings: list[str]) -> str:
    if error:
        return "failed"
    status = str(output.get("status") or "").lower()
    if status in {"skipped", "superseded", "filtered"}:
        return "skipped"
    if status in {"pending", "scheduled", "sending", "platform_accepted"}:
        return "pending"
    if warnings:
        return "warning"
    return "success"


def _assign_parallel_groups(nodes: list[dict[str, Any]]) -> None:
    intervals: list[tuple[datetime, datetime, int]] = []
    for index, node in enumerate(nodes):
        start = _parse_time(node.get("started_at"))
        finish = _parse_time(node.get("finished_at"))
        if start is not None and finish is not None:
            intervals.append((start, finish, index))
    group_number = 0
    for position, (start, finish, index) in enumerate(intervals):
        overlaps = [other_index for other_start, other_finish, other_index in intervals[position + 1 :] if other_start < finish and other_finish > start]
        if not overlaps:
            continue
        existing = str(nodes[index].get("parallel_group") or "")
        if existing:
            group = existing
        else:
            group_number += 1
            group = f"parallel-{group_number}"
            nodes[index]["parallel_group"] = group
        for other_index in overlaps:
            if not nodes[other_index].get("parallel_group"):
                nodes[other_index]["parallel_group"] = group


def _delivery_view(dispatches: list[dict[str, Any]]) -> dict[str, Any]:
    if not dispatches:
        return {"status": "not_recorded", "dispatches": [], "expected_count": 0, "succeeded_count": 0, "failed_count": 0}
    statuses = [str(item.get("status") or "created") for item in dispatches]
    if any(status == "partial_failed" for status in statuses):
        status = "partial_failed"
    elif any(status == "send_failed" for status in statuses):
        status = "send_failed"
    elif all(status == "send_succeeded" for status in statuses):
        status = "send_succeeded"
    elif any(status in {"sending", "platform_accepted", "submission_unknown", "submitting", "created"} for status in statuses):
        status = "pending"
    else:
        status = statuses[-1]
    return {
        "status": status,
        "expected_count": sum(int(item.get("expected_count") or 0) for item in dispatches),
        "succeeded_count": sum(int(item.get("succeeded_count") or 0) for item in dispatches),
        "failed_count": sum(int(item.get("failed_count") or 0) for item in dispatches),
        "dispatches": [
            {
                "dispatch_id": str(item.get("id") or ""),
                "source_channel": str(item.get("source_channel") or ""),
                "source_kind": str(item.get("source_kind") or ""),
                "status": str(item.get("status") or ""),
                "expected_count": int(item.get("expected_count") or 0),
                "succeeded_count": int(item.get("succeeded_count") or 0),
                "failed_count": int(item.get("failed_count") or 0),
                "platform_request_id": str(item.get("platform_request_id") or ""),
                "system_msgid": str(item.get("system_msgid") or ""),
                "error_code": str(item.get("error_code") or ""),
                "error_message": str(item.get("error_message") or ""),
                "submitted_at": str(item.get("submitted_at") or ""),
                "confirmed_at": str(item.get("confirmed_at") or ""),
                "last_callback_at": str(item.get("last_callback_at") or ""),
                "items": [
                    {
                        "message_index": int(child.get("message_index") or 0),
                        "message_type": str(child.get("message_type") or ""),
                        "status": str(child.get("status") or ""),
                        "platform_message_id": str(child.get("platform_message_id") or ""),
                        "error_code": str(child.get("error_code") or ""),
                        "error_message": str(child.get("error_message") or ""),
                        "sent_at": str(child.get("sent_at") or ""),
                    }
                    for child in item.get("items", [])
                    if isinstance(child, dict)
                ],
            }
            for item in dispatches
        ],
    }


def _overall_status(
    *,
    errors: list[Any],
    final_messages: list[Any],
    fallback_detected: bool,
    delivery_status: str,
    node_statuses: list[str],
) -> str:
    if delivery_status == "send_failed":
        return "delivery_failed"
    if delivery_status == "partial_failed":
        return "partial_failed"
    if errors and not final_messages:
        return "failed"
    if any(status == "failed" for status in node_statuses) and not final_messages:
        return "failed"
    if fallback_detected:
        return "fallback"
    if errors or any(status == "warning" for status in node_statuses):
        return "warning"
    if delivery_status == "pending":
        return "delivery_pending"
    if delivery_status == "send_succeeded":
        return "delivered"
    return "success"


def _fallback_detected(output: dict[str, Any], final_messages: list[Any], nodes: list[dict[str, Any]]) -> bool:
    if output.get("fallback_source"):
        return True
    for node in nodes:
        if any(item.get("key") == "fallback_source" for item in node.get("important_outputs", [])):
            return True
    texts = [str(item.get("content") or "") for item in final_messages if isinstance(item, dict) and item.get("type") == "text"]
    return bool(texts) and all(text.strip() == "您稍等一下" for text in texts)


def _final_messages(output: dict[str, Any], raw_log: dict[str, Any]) -> list[Any]:
    for record in (output, raw_log):
        messages = _reply_messages(
            record,
            (
                "reply_control.async_final.reply_messages",
                "async_final_reply.reply_messages",
                "http_response_reply_messages",
                "http_response_body.reply_messages",
                "reply_messages",
            ),
        )
        if messages:
            return messages
    return []


def _reply_messages(record: dict[str, Any], paths: tuple[str, ...]) -> list[Any]:
    for path in paths:
        value = _path_value(record, path)
        if isinstance(value, list):
            return value
    return []


def _path_value(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _safe_load_error(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        import json

        return json.loads(value)
    except (TypeError, ValueError):
        return [value]


def _as_list(value: Any) -> list[Any]:
    if value in (None, "", {}):
        return []
    return value if isinstance(value, list) else [value]


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compact_display_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 260 else value[:257] + "..."
    if isinstance(value, list):
        return {"count": len(value), "sample": [_compact_display_value(item) for item in value[:3]]}
    if isinstance(value, dict):
        return {key: _compact_display_value(item) for key, item in list(value.items())[:10]}
    return value


def _content_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    try:
        import json

        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(value or ""))


def _content_preview(value: Any) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        try:
            import json

            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value or "")
    return text[:180] + ("..." if len(text) > 180 else "")
