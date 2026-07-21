from __future__ import annotations

from typing import Any

from app.graph.planner.planner_contract import ALLOWED_KBS


def rejected_tool_violations(raw_tools: Any) -> list[dict[str, str]]:
    """Report unsupported structured tools without inferring customer intent."""
    if not isinstance(raw_tools, list):
        return []
    violations: list[dict[str, str]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kb_name = str(item.get("kb_name") or "").strip()
        if name == "kb_search" and kb_name and kb_name not in ALLOWED_KBS:
            violations.append(
                {
                    "task_type": "planner_tool_rejected",
                    "subtype": "kb_search",
                    "missing": f"unsupported_kb:{kb_name}",
                    "note": "Planner may only call an enabled knowledge base from the tool contract.",
                }
            )
    return violations
