from __future__ import annotations

from typing import Any


def collect_model_usage(trace: list[dict[str, Any]]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    summary = {
        "planner_tokens": 0,
        "reply_tokens": 0,
        "vision_tokens": 0,
        "other_tokens": 0,
        "total_tokens": 0,
    }

    def add_call(node: str, call: dict[str, Any]) -> None:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        total = int(usage.get("total_tokens") or usage.get("token_count") or 0)
        if total > 0:
            item = {
                "node": node,
                "name": call.get("name", ""),
                "provider": usage.get("provider", ""),
                "model": usage.get("model", ""),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": total,
            }
            calls.append(item)
            if node == "planner_brain":
                summary["planner_tokens"] += total
            elif node == "synthesize_reply":
                summary["reply_tokens"] += total
            elif node == "image_understanding":
                summary["vision_tokens"] += total
            else:
                summary["other_tokens"] += total
            summary["total_tokens"] += total
        for nested in call.get("nested_calls", []) or []:
            if isinstance(nested, dict):
                add_call(node, nested)

    for entry in trace or []:
        node = str(entry.get("node") or "")
        for call in entry.get("tool_calls", []) or []:
            if isinstance(call, dict):
                add_call(node, call)
    return {"calls": calls, "summary": summary}


def collect_tool_calls(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def add_call(node: str, call: dict[str, Any]) -> None:
        calls.append(
            {
                "node": node,
                "name": call.get("name", ""),
                "input": call.get("input", {}),
                "output": call.get("output", {}),
                "error": call.get("error", ""),
                "usage": call.get("usage", {}),
            }
        )
        for nested in call.get("nested_calls", []) or []:
            if isinstance(nested, dict):
                add_call(node, nested)

    for entry in trace or []:
        node = str(entry.get("node") or "")
        for call in entry.get("tool_calls", []) or []:
            if isinstance(call, dict):
                add_call(node, call)
    return calls[:30]
