from __future__ import annotations

from typing import Any, Callable

from app.graph.planner.runtime_plan import planner_task_views
from app.graph.state import AgentState
from app.services.memory_store import CustomerMemoryStore
from app.services.trace_logger import TraceLogger


def create_profile_event_extractor_node(
    *,
    trace_logger: TraceLogger,
    memory_store: CustomerMemoryStore | None,
    compact_memory: Callable[[dict[str, Any]], dict[str, Any]],
    extract_event_updates: Callable[[AgentState, dict[str, Any]], list[dict[str, Any]]],
    extract_profile_update: Callable[[AgentState], dict[str, Any]],
    extract_system_action_events: Callable[[AgentState], list[dict[str, Any]]] | None = None,
) -> Callable[[AgentState], Any]:
    async def profile_event_extractor(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "profile_event_extractor",
            {
                "content": state.get("normalized_content"),
                "image_info": state.get("image_info"),
                "planner_tasks": planner_task_views(state),
            },
        ) as span:
            profile_update = extract_profile_update(state)
            event_updates = extract_event_updates(state, profile_update)
            if extract_system_action_events:
                event_updates = [*event_updates, *extract_system_action_events(state)]
            memory_error = None
            saved_memory = {}
            if memory_store:
                try:
                    saved_memory = memory_store.save_update(
                        str(state.get("customer_id") or "unknown"),
                        profile_update=profile_update,
                        event_updates=event_updates,
                    )
                except Exception as exc:
                    memory_error = f"{type(exc).__name__}: {exc}"
            output = {
                "profile_update": profile_update,
                "event_updates": event_updates,
                "saved_memory": compact_memory(saved_memory),
                "memory_error": memory_error,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return profile_event_extractor
