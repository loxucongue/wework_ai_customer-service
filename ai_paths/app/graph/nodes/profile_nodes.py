from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState
from app.services.memory_store import CustomerMemoryStore
from app.services.trace_logger import TraceLogger


@dataclass(frozen=True)
class ProfileCallbacks:
    compact_memory: Callable[[dict[str, Any]], dict[str, Any]]
    extract_event_updates: Callable[[AgentState, dict[str, Any]], list[dict[str, Any]]]
    extract_profile_update: Callable[[AgentState], dict[str, Any]]


def create_profile_event_extractor_node(
    *,
    trace_logger: TraceLogger,
    memory_store: CustomerMemoryStore | None,
    callbacks: ProfileCallbacks,
) -> Callable[[AgentState], Any]:
    async def profile_event_extractor(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "profile_event_extractor",
            {"content": state.get("normalized_content"), "image_info": state.get("image_info"), "intents": state.get("intents")},
        ) as span:
            profile_update = callbacks.extract_profile_update(state)
            event_updates = callbacks.extract_event_updates(state, profile_update)
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
                "saved_memory": callbacks.compact_memory(saved_memory),
                "memory_error": memory_error,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return profile_event_extractor
