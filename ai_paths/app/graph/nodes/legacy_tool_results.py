from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Callable

from app.graph.state import AgentState


def merge_kb_result(tool_results: dict[str, Any], kb_name: str, dumped: dict[str, Any]) -> None:
    existing = tool_results.get(kb_name)
    if not isinstance(existing, dict):
        tool_results[kb_name] = dumped
        return
    existing_items = existing.get("items") if isinstance(existing.get("items"), list) else []
    new_items = dumped.get("items") if isinstance(dumped.get("items"), list) else []
    seen = {str(item.get("content") or "") for item in existing_items if isinstance(item, dict)}
    merged = list(existing_items)
    for item in new_items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if content and content not in seen:
            merged.append(item)
            seen.add(content)
    existing["items"] = merged[:8]
    existing["kb_name"] = kb_name


def tool_results_contain(state: AgentState, term: str | Iterable[str], json_dumps: Callable[[Any], str]) -> bool:
    tool_results = state.get("tool_results") or {}
    terms = [str(item) for item in term] if isinstance(term, Iterable) and not isinstance(term, str) else [str(term)]
    for value in tool_results.values():
        if isinstance(value, dict):
            dumped = json_dumps(value)
            if any(item and item in dumped for item in terms):
                return True
        elif any(item and item in str(value) for item in terms):
            return True
    return False
