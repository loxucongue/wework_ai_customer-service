from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from app.graph.nodes.action_nodes import create_execute_actions_node
from app.graph.nodes.appointment_utils import appointment_query_from_state
from app.graph.nodes.layer_nodes import create_background_context_layer, create_input_normalization_layer
from app.graph.nodes.planner_nodes import create_planner_brain_node
from app.graph.nodes.profile_nodes import create_profile_event_extractor_node
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.reply_input import reply_messages_for_model, should_use_model_reply
from app.graph.nodes.reply_nodes import create_synthesize_reply_node
from app.graph.nodes.reply_validation import (
    debug_message_contents as _debug_message_contents,
    validated_model_messages as _validated_model_messages,
)
from app.graph.nodes.store_context import extract_city as _extract_city
from app.graph.runtime_common import compact_memory as _compact_memory
from app.graph.state import AgentState
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


@dataclass(frozen=True)
class ReplyGraphs:
    full_graph: Any
    planner_graph: Any
    finalize_graph: Any


def _schedule_background_profile_extraction(profile_event_extractor, state: AgentState) -> None:
    async def runner() -> None:
        try:
            detached_state = dict(state)
            detached_state["trace"] = list(state.get("trace") or [])
            await profile_event_extractor(detached_state)
        except Exception:
            return

    asyncio.create_task(runner())


def build_graph(
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None = None,
    memory_store: CustomerMemoryStore | None = None,
    customer_context_service: CustomerContextService | None = None,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None = None,
    store_service: StoreService | None = None,
):
    return build_reply_graphs(
        coze_client,
        trace_logger,
        model_client,
        memory_store,
        customer_context_service,
        customer_store_knowledge_service,
        store_service,
    ).full_graph


def build_reply_graphs(
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None = None,
    memory_store: CustomerMemoryStore | None = None,
    customer_context_service: CustomerContextService | None = None,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None = None,
    store_service: StoreService | None = None,
) -> ReplyGraphs:
    nodes = _build_nodes(
        coze_client=coze_client,
        trace_logger=trace_logger,
        model_client=model_client,
        memory_store=memory_store,
        customer_context_service=customer_context_service,
        customer_store_knowledge_service=customer_store_knowledge_service,
        store_service=store_service,
    )
    return ReplyGraphs(
        full_graph=_compile_full_graph(nodes),
        planner_graph=_compile_planner_graph(nodes),
        finalize_graph=_compile_finalize_graph(nodes),
    )


def _build_nodes(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None,
    memory_store: CustomerMemoryStore | None,
    customer_context_service: CustomerContextService | None,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None,
    store_service: StoreService | None,
) -> dict[str, Any]:
    layer_1_input_normalization = create_input_normalization_layer(
        trace_logger=trace_logger,
        model_client=model_client,
    )
    layer_2_background_context = create_background_context_layer(
        trace_logger=trace_logger,
        memory_store=memory_store,
        customer_context_service=customer_context_service,
        customer_store_knowledge_service=customer_store_knowledge_service,
        coze_client=coze_client,
    )
    planner_brain = create_planner_brain_node(
        trace_logger=trace_logger,
        model_client=model_client,
    )
    execute_actions = create_execute_actions_node(
        coze_client=coze_client,
        trace_logger=trace_logger,
        store_service=store_service,
        appointment_query_from_state=lambda content, store_lookup, state: appointment_query_from_state(
            content,
            store_lookup,
            state,
            _extract_city,
        ),
    )

    profile_event_extractor = create_profile_event_extractor_node(
        trace_logger=trace_logger,
        memory_store=memory_store,
        compact_memory=_compact_memory,
    )

    synthesize_reply = create_synthesize_reply_node(
        trace_logger=trace_logger,
        model_client=model_client,
        debug_message_contents=_debug_message_contents,
        reply_messages_for_model=lambda state: reply_messages_for_model(state, reply_user_payload_for_model(state)),
        should_use_model_reply=should_use_model_reply,
        validated_model_messages=_validated_model_messages,
        schedule_background_task=lambda state: _schedule_background_profile_extraction(profile_event_extractor, state),
    )
    return {
        "layer_1_input_normalization": layer_1_input_normalization,
        "layer_2_background_context": layer_2_background_context,
        "planner_brain": planner_brain,
        "execute_actions": execute_actions,
        "synthesize_reply": synthesize_reply,
    }


def _compile_full_graph(nodes: dict[str, Any]):
    graph = StateGraph(AgentState)
    for name in ("layer_1_input_normalization", "layer_2_background_context", "planner_brain", "execute_actions", "synthesize_reply"):
        graph.add_node(name, nodes[name])
    graph.set_entry_point("layer_1_input_normalization")
    graph.add_edge("layer_1_input_normalization", "layer_2_background_context")
    graph.add_edge("layer_2_background_context", "planner_brain")
    graph.add_edge("planner_brain", "execute_actions")
    graph.add_edge("execute_actions", "synthesize_reply")
    graph.add_edge("synthesize_reply", END)
    return graph.compile()


def _compile_planner_graph(nodes: dict[str, Any]):
    graph = StateGraph(AgentState)
    for name in ("layer_1_input_normalization", "layer_2_background_context", "planner_brain"):
        graph.add_node(name, nodes[name])
    graph.set_entry_point("layer_1_input_normalization")
    graph.add_edge("layer_1_input_normalization", "layer_2_background_context")
    graph.add_edge("layer_2_background_context", "planner_brain")
    graph.add_edge("planner_brain", END)
    return graph.compile()


def _compile_finalize_graph(nodes: dict[str, Any]):
    graph = StateGraph(AgentState)
    for name in ("execute_actions", "synthesize_reply"):
        graph.add_node(name, nodes[name])
    graph.set_entry_point("execute_actions")
    graph.add_edge("execute_actions", "synthesize_reply")
    graph.add_edge("synthesize_reply", END)
    return graph.compile()
