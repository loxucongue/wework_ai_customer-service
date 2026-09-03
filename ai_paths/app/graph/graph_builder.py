from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from app.graph.nodes.fact_actions import create_readonly_fact_actions_node
from app.graph.nodes.transaction_actions import create_transaction_actions_node
from app.graph.nodes.appointment_utils import appointment_query_from_state
from app.graph.nodes.layer_nodes import create_background_context_layer, create_input_normalization_layer
from app.graph.nodes.transaction_commit import (
    create_commit_coordinator_node,
    create_prepare_commit_node,
)
from app.graph.nodes.material_selection import (
    create_evidence_join_node,
    parallel_reply_payload,
)
from app.graph.nodes.semantic_evidence import (
    create_post_fact_semantic_evidence_node,
    create_semantic_evidence_node,
)
from app.graph.nodes.authoritative_context import (
    create_shared_context_node,
)
from app.graph.nodes.reply_input import should_use_model_reply
from app.graph.nodes.reply_generation import (
    create_synthesize_reply_node,
)
from app.graph.nodes.reply_validation import (
    debug_message_contents as _debug_message_contents,
    validated_model_messages as _validated_model_messages,
)
from app.graph.nodes.store_context import extract_city as _extract_city
from app.graph.state import AgentState
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.customer_store_knowledge import CustomerStoreKnowledgeService
from app.services.v3_semantic_router_service import V3SemanticRouterService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.outreach_send_client import OutreachSendClient
from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_service import StoreService
from app.services.sales_strategy_service import SalesStrategyService
from app.services.v3_sop_execution_service import SopExecutionService
from app.services.trace_logger import TraceLogger
from app.graph.nodes.common import json_dumps
from app.prompts.reply_synthesizer import build_parallel_reply_messages


@dataclass(frozen=True)
class ReplyGraphs:
    full_graph: Any
    commit_graph: Any


def build_graph(
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None = None,
    memory_store: CustomerMemoryStore | None = None,
    customer_context_service: CustomerContextService | None = None,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None = None,
    store_service: StoreService | None = None,
    outreach_send_client: OutreachSendClient | None = None,
    platform_agent_client: PlatformAgentClient | None = None,
    sop_execution_service: SopExecutionService | None = None,
    semantic_router_service: V3SemanticRouterService | None = None,
    sales_strategy_service: SalesStrategyService | None = None,
):
    return build_reply_graphs(
        coze_client,
        trace_logger,
        model_client,
        memory_store,
        customer_context_service,
        customer_store_knowledge_service,
        store_service,
        outreach_send_client,
        platform_agent_client,
        sop_execution_service,
        semantic_router_service,
        sales_strategy_service,
    ).full_graph


def build_reply_graphs(
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None = None,
    memory_store: CustomerMemoryStore | None = None,
    customer_context_service: CustomerContextService | None = None,
    customer_store_knowledge_service: CustomerStoreKnowledgeService | None = None,
    store_service: StoreService | None = None,
    outreach_send_client: OutreachSendClient | None = None,
    platform_agent_client: PlatformAgentClient | None = None,
    sop_execution_service: SopExecutionService | None = None,
    semantic_router_service: V3SemanticRouterService | None = None,
    sales_strategy_service: SalesStrategyService | None = None,
) -> ReplyGraphs:
    nodes = _build_nodes(
        coze_client=coze_client,
        trace_logger=trace_logger,
        model_client=model_client,
        memory_store=memory_store,
        customer_context_service=customer_context_service,
        customer_store_knowledge_service=customer_store_knowledge_service,
        store_service=store_service,
        outreach_send_client=outreach_send_client,
        platform_agent_client=platform_agent_client,
        sop_execution_service=sop_execution_service,
        semantic_router_service=semantic_router_service,
        sales_strategy_service=sales_strategy_service,
    )
    return ReplyGraphs(
        full_graph=_compile_full_graph(nodes),
        commit_graph=_compile_commit_graph(nodes),
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
    outreach_send_client: OutreachSendClient | None,
    platform_agent_client: PlatformAgentClient | None,
    sop_execution_service: SopExecutionService | None,
    semantic_router_service: V3SemanticRouterService | None,
    sales_strategy_service: SalesStrategyService | None,
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
        conversation_fetcher=outreach_send_client.fetch_conversation if outreach_send_client else None,
        follow_sequence_fetcher=(
            semantic_router_service.load_sequence_index if semantic_router_service is not None else None
        ),
        follow_taxonomy_fetcher=(
            semantic_router_service.load_checkpoint_taxonomy if semantic_router_service is not None else None
        ),
        closing_catalog_fetcher=(
            semantic_router_service.load_closing_catalog if semantic_router_service is not None else None
        ),
    )
    shared_context = create_shared_context_node(
        trace_logger=trace_logger,
        sop_execution_service=sop_execution_service,
    )
    semantic_evidence = create_semantic_evidence_node(
        trace_logger=trace_logger,
        model_client=model_client,
        sop_execution_service=sop_execution_service,
        coze_client=coze_client,
        semantic_router_service=semantic_router_service,
        sales_strategy_service=sales_strategy_service,
    )
    execute_readonly_actions = create_readonly_fact_actions_node(
        coze_client=coze_client,
        trace_logger=trace_logger,
        store_service=store_service,
        platform_agent_client=platform_agent_client,
        appointment_query_from_state=lambda content, store_lookup, state: appointment_query_from_state(
            content,
            store_lookup,
            state,
            _extract_city,
        ),
        model_client=model_client,
    )
    post_fact_semantic_evidence = create_post_fact_semantic_evidence_node(
        trace_logger=trace_logger,
        semantic_router_service=semantic_router_service,
        sales_strategy_service=sales_strategy_service,
    )
    evidence_join = create_evidence_join_node(trace_logger=trace_logger)

    synthesize_reply = create_synthesize_reply_node(
        trace_logger=trace_logger,
        model_client=model_client,
        debug_message_contents=_debug_message_contents,
        reply_messages_for_model=lambda state: build_parallel_reply_messages(
            parallel_reply_payload(state),
            json_dumps=json_dumps,
        ),
        should_use_model_reply=should_use_model_reply,
        validated_model_messages=_validated_model_messages,
        schedule_background_task=None,
    )
    prepare_commit = create_prepare_commit_node(trace_logger=trace_logger)
    execute_commit_actions = create_transaction_actions_node(
        coze_client=coze_client,
        trace_logger=trace_logger,
        store_service=store_service,
        platform_agent_client=platform_agent_client,
        appointment_query_from_state=lambda content, store_lookup, state: appointment_query_from_state(
            content,
            store_lookup,
            state,
            _extract_city,
        ),
    )
    commit_coordinator = create_commit_coordinator_node(
        trace_logger=trace_logger,
        sop_execution_service=sop_execution_service,
    )
    return {
        "layer_1_input_normalization": layer_1_input_normalization,
        "layer_2_background_context": layer_2_background_context,
        "authoritative_context": shared_context,
        "semantic_evidence": semantic_evidence,
        "readonly_facts": execute_readonly_actions,
        "semantic_evidence_after_facts": post_fact_semantic_evidence,
        "material_selection": evidence_join,
        "reply_decision": synthesize_reply,
        "prepare_transaction": prepare_commit,
        "transaction_actions": execute_commit_actions,
        "commit_result": commit_coordinator,
    }


def _compile_full_graph(nodes: dict[str, Any]):
    graph = StateGraph(AgentState)
    node_order = (
        "layer_1_input_normalization",
        "layer_2_background_context",
        "authoritative_context",
        "semantic_evidence",
        "readonly_facts",
        "semantic_evidence_after_facts",
        "material_selection",
        "reply_decision",
    )
    for name in node_order:
        graph.add_node(name, nodes[name])
    graph.set_entry_point("layer_1_input_normalization")
    for left, right in zip(node_order, node_order[1:]):
        graph.add_edge(left, right)
    graph.add_edge(node_order[-1], END)
    return graph.compile()


def _compile_commit_graph(nodes: dict[str, Any]):
    graph = StateGraph(AgentState)
    node_order = (
        "prepare_transaction",
        "transaction_actions",
        "commit_result",
    )
    for name in node_order:
        graph.add_node(name, nodes[name])
    graph.set_entry_point(node_order[0])
    for left, right in zip(node_order, node_order[1:]):
        graph.add_edge(left, right)
    graph.add_edge(node_order[-1], END)
    return graph.compile()
