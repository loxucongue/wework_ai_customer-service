from __future__ import annotations

from typing import Any, Callable

from app.graph.state import AgentState
from app.services.customer_context import CustomerContextService
from app.services.memory_store import CustomerMemoryStore
from app.services.trace_logger import TraceLogger


def create_load_customer_context_node(
    *,
    trace_logger: TraceLogger,
    customer_context_service: CustomerContextService | None,
) -> Callable[[AgentState], Any]:
    async def load_customer_context(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(
            state,
            "load_customer_context",
            {
                "customer_id": state.get("customer_id"),
                "memory_loaded": bool(state.get("saved_memory")),
                "user_id": state.get("user_id"),
                "wechat": state.get("wechat"),
                "external_userid": state.get("external_userid"),
            },
        ) as span:
            context = {}
            error = None
            if customer_context_service:
                try:
                    context = customer_context_service.load(
                        customer_id=str(state.get("customer_id") or "unknown"),
                        memory=state.get("saved_memory") or {},
                        request_context=request_context_from_state(state),
                        current_message=str(state.get("normalized_content") or state.get("content") or ""),
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            output = {
                "customer_context": context,
                "appointment_cache": context.get("appointment", {}) if isinstance(context, dict) else {},
                "customer_context_error": error,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return load_customer_context


def create_load_memory_node(
    *,
    trace_logger: TraceLogger,
    memory_store: CustomerMemoryStore | None,
) -> Callable[[AgentState], Any]:
    async def load_memory(state: AgentState) -> dict[str, Any]:
        with trace_logger.node(state, "load_memory", {"customer_id": state.get("customer_id")}) as span:
            memory = memory_store.load(str(state.get("customer_id") or "unknown")) if memory_store else {}
            recent_messages = []
            repository = getattr(memory_store, "repository", None) if memory_store else None
            if repository:
                try:
                    recent_messages = repository.list_recent_messages(str(state.get("customer_id") or "unknown"))
                except Exception:
                    recent_messages = []
            output = {
                "customer_profile": memory.get("portrait", {}) if isinstance(memory, dict) else {},
                "customer_basic_info": memory.get("basic_info", {}) if isinstance(memory, dict) else {},
                "history_events": memory.get("history_events", []) if isinstance(memory, dict) else [],
                "lifecycle_stage": memory.get("lifecycle_stage", "") if isinstance(memory, dict) else "",
                "saved_memory": memory if isinstance(memory, dict) else {},
                "recent_messages": recent_messages,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return load_memory


def request_context_from_state(state: AgentState) -> dict[str, Any]:
    context = dict(state.get("request_context") or {})
    fields = {
        "user_id": state.get("user_id"),
        "corp_id": state.get("corp_id"),
        "wechat": state.get("wechat"),
        "external_userid": state.get("external_userid"),
        "customer_id": state.get("customer_id"),
        "customer_add_wechat_id": state.get("customer_add_wechat_id"),
        "confirmed_store_id": state.get("confirmed_store_id"),
        "confirmed_store_name": state.get("confirmed_store_name"),
        "store_id": state.get("store_id"),
        "store_name": state.get("store_name"),
        "appointment_id": state.get("appointment_id"),
        "appointment_time": state.get("appointment_time"),
    }
    for key, value in fields.items():
        if value not in (None, ""):
            context[key] = value
    return context
