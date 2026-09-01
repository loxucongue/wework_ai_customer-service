from __future__ import annotations

from typing import Any, Callable

from app.graph.nodes.action_nodes import _create_action_executor
from app.graph.state import AgentState
from app.services.coze_client import CozeClient
from app.services.model_client import ModelClient
from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


def create_readonly_fact_actions_node(
    *,
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    store_service: StoreService | None,
    appointment_query_from_state: Callable[[str, dict[str, Any], AgentState], dict[str, Any]],
    platform_agent_client: PlatformAgentClient | None = None,
    model_client: ModelClient | None = None,
) -> Callable[[AgentState], Any]:
    return _create_action_executor(
        coze_client=coze_client,
        trace_logger=trace_logger,
        store_service=store_service,
        appointment_query_from_state=appointment_query_from_state,
        platform_agent_client=platform_agent_client,
        model_client=model_client,
        execution_mode="readonly",
    )
