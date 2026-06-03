from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def safe_repository_call(func: Any, **kwargs: Any) -> None:
    try:
        func(**kwargs)
    except Exception:
        return


def failed_state_from_exception(initial_state: AgentState, exc: Exception) -> AgentState:
    error = f"{type(exc).__name__}: {exc}"
    state: AgentState = dict(initial_state)
    state["reply_messages"] = []
    state["errors"] = [
        *list(state.get("errors") or []),
        {
            "stage": "run_chat",
            "error": error,
        },
    ]
    state["trace"] = [
        *list(state.get("trace") or []),
        {
            "node": "run_chat",
            "started_at": "",
            "finished_at": "",
            "duration_ms": 0,
            "input_snapshot": {
                "content": state.get("content", ""),
                "customer_id": state.get("customer_id", ""),
                "corp_id": state.get("corp_id", ""),
                "file_image": bool(state.get("file_image")),
            },
            "output_snapshot": {},
            "tool_calls": [],
            "error": error,
        },
    ]
    return state
