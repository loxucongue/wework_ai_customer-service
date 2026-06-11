from __future__ import annotations

from typing import Any

ActionToolTask = tuple[str, dict[str, Any], Any]


def merge_action_task_results(
    *,
    tool_tasks: list[ActionToolTask],
    results: list[Any],
    tool_results: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> None:
    for (key, call, _), result in zip(tool_tasks, results):
        if isinstance(result, Exception):
            call["error"] = f"{type(result).__name__}: {result}"
            if key == "pricing_db":
                tool_results[key] = {"rows": [], "error": call["error"]}
            else:
                tool_results[key] = {"kb_name": key, "items": [], "error": call["error"]}
        elif key == "pricing_db":
            rows = result if isinstance(result, list) else []
            tool_results[key] = {"rows": rows[:10]}
            call["output"] = {"rows": len(rows)}
        else:
            dumped = result.model_dump()
            tool_results[key] = dumped
            call["output"] = {"items": len(result.items)}
        tool_calls.append(call)
