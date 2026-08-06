from __future__ import annotations

from typing import Any

from app.services.tool_registry import tool_execution_class


def read_only_tool_executor_shadow_from_plan(tool_plan_preview: dict[str, Any]) -> dict[str, Any]:
    """Build a dry-run execution view for future early read-only tools.

    This function intentionally does not call any platform, database, network,
    payment, order, or messaging tool.
    """

    read_calls = _list_of_dicts(tool_plan_preview.get("read_tool_calls"))
    deferred_writes = _list_of_dicts(tool_plan_preview.get("deferred_write_proposals"))
    unknown_tools = _list_of_dicts(tool_plan_preview.get("unknown_tools"))

    dependency_blocked = _dependency_blocked_calls(read_calls)
    blocked_call_ids = {
        str(item.get("call_id") or "").strip()
        for item in dependency_blocked
        if str(item.get("call_id") or "").strip()
    }
    would_execute = [
        _dry_run_call(tool)
        for tool in read_calls
        if _is_read_only(tool)
        and str(tool.get("call_id") or "").strip() not in blocked_call_ids
        and not _has_dependency_blocker(tool, read_calls)
    ]
    blocked = [
        *_blocked_calls(read_calls, expected_class="read_only"),
        *dependency_blocked,
        *[_blocked_call(tool, reason="write_tool_deferred_until_commit") for tool in deferred_writes],
        *[_blocked_call(tool, reason="unknown_tool_not_allowed_in_early_executor") for tool in unknown_tools],
    ]

    return _drop_empty(
        {
            "schema_version": "read_only_tool_executor_shadow_v1",
            "mode": "dry_run_no_external_calls",
            "would_execute": would_execute,
            "blocked": blocked,
            "summary": {
                "would_execute_count": len(would_execute),
                "blocked_count": len(blocked),
                "safe_to_enable_early_execution": bool(would_execute) and not blocked,
            },
            "dependency_audit": _dependency_audit(read_calls, dependency_blocked=dependency_blocked),
            "source": "tool_plan_preview_shadow",
        }
    )


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _is_read_only(tool: dict[str, Any]) -> bool:
    return tool_execution_class(str(tool.get("tool") or tool.get("name") or "")) == "read_only"


def _dependency_audit(read_calls: list[dict[str, Any]], *, dependency_blocked: list[dict[str, Any]]) -> dict[str, Any]:
    return _drop_empty(
        {
            "schema_version": "read_only_tool_dependency_audit_v1",
            "read_call_count": len(read_calls),
            "call_ids": _read_call_ids(read_calls),
            "blocked_count": len(dependency_blocked),
            "ready_for_early_execution_ordering": not dependency_blocked,
            "blockers": [str(item.get("reason") or "").strip() for item in dependency_blocked],
        }
    )


def _dependency_blocked_calls(read_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    call_ids = _read_call_ids(read_calls)
    blocked: list[dict[str, Any]] = []
    for tool in read_calls:
        if _has_duplicate_call_id(tool, call_ids):
            blocked.append(_blocked_call(tool, reason="duplicate_read_tool_call_id"))
            continue
        missing_dependencies = _missing_dependencies(tool, call_ids)
        if missing_dependencies:
            blocked.append(
                _blocked_call(
                    {
                        **tool,
                        "arguments": {
                            **(tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {}),
                            "missing_dependencies": missing_dependencies,
                        },
                    },
                    reason="missing_read_tool_dependency",
                )
            )
    return blocked


def _has_dependency_blocker(tool: dict[str, Any], read_calls: list[dict[str, Any]]) -> bool:
    call_ids = _read_call_ids(read_calls)
    return _has_duplicate_call_id(tool, call_ids) or bool(_missing_dependencies(tool, call_ids))


def _has_duplicate_call_id(tool: dict[str, Any], call_ids: list[str]) -> bool:
    call_id = str(tool.get("call_id") or "").strip()
    return bool(call_id and call_ids.count(call_id) > 1)


def _missing_dependencies(tool: dict[str, Any], call_ids: list[str]) -> list[str]:
    return [
        dependency
        for dependency in _string_list(tool.get("depends_on"))
        if dependency not in call_ids
    ]


def _read_call_ids(read_calls: list[dict[str, Any]]) -> list[str]:
    return [
        call_id
        for call_id in (str(tool.get("call_id") or "").strip() for tool in read_calls)
        if call_id
    ]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _dry_run_call(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("tool") or tool.get("name") or "").strip()
    return _drop_empty(
        {
            "call_id": str(tool.get("call_id") or "").strip(),
            "tool": name,
            "arguments": tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {},
            "purpose": str(tool.get("purpose") or "").strip(),
            "status": "would_execute_read_only",
            "depends_on": tool.get("depends_on") if isinstance(tool.get("depends_on"), list) else [],
            "required_fact_fields": tool.get("required_fact_fields") if isinstance(tool.get("required_fact_fields"), list) else [],
        }
    )


def _blocked_calls(tools: list[dict[str, Any]], *, expected_class: str) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for tool in tools:
        actual = tool_execution_class(str(tool.get("tool") or tool.get("name") or ""))
        if actual != expected_class:
            blocked.append(_blocked_call(tool, reason=f"tool_class_mismatch:{actual}"))
    return blocked


def _blocked_call(tool: dict[str, Any], *, reason: str) -> dict[str, Any]:
    name = str(tool.get("tool") or tool.get("name") or "").strip()
    return _drop_empty(
        {
            "call_id": str(tool.get("call_id") or "").strip(),
            "tool": name,
            "arguments": tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {},
            "reason": reason,
            "status": "blocked_from_early_execution",
        }
    )


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
