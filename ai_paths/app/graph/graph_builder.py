from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.graph.nodes.appointment_utils import appointment_query_from_state
from app.graph.nodes.context_nodes import create_load_customer_context_node, create_load_memory_node
from app.graph.nodes.guardrail_nodes import create_hard_guardrails_node
from app.graph.nodes.image_info import known_visible_concerns_from_state as _known_visible_concerns_from_state
from app.graph.nodes.input_nodes import create_image_understanding_node, create_normalize_input_node
from app.graph.nodes.intent_signals import (
    has_appointment_change_or_cancel as _has_appointment_change_or_cancel,
    has_appointment_record_query as _has_appointment_record_query,
)
from app.graph.nodes.planner_nodes import create_planner_brain_node
from app.graph.nodes.policy_nodes import create_scene_guidance_node
from app.graph.nodes.pricing_context import (
    canonical_price_project as _canonical_price_project,
    extract_project as _extract_project,
)
from app.graph.nodes.profile_extraction import extract_event_updates, extract_profile_update
from app.graph.nodes.profile_nodes import create_profile_event_extractor_node
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.reply_input import (
    reply_messages_for_model,
    reply_model_tier,
    reply_repair_messages_for_model,
    should_use_model_reply,
)
from app.graph.nodes.reply_nodes import create_synthesize_reply_node
from app.graph.nodes.reply_validation import (
    debug_message_contents as _debug_message_contents,
    validated_model_messages as _validated_model_messages,
)
from app.graph.nodes.store_context import extract_city as _extract_city, store_query_from_state as _store_query_from_state
from app.graph.nodes.action_nodes import create_execute_actions_node
from app.graph.runtime_common import compact_memory as _compact_memory, extract_price_digits as _extract_price_digits
from app.graph.runtime_context import (
    contextual_price_project as _contextual_price_project,
    pricing_sql_from_state as _pricing_sql_from_state,
    project_direction_names_from_state as _project_direction_names_from_state,
)
from app.graph.nodes.kb_planning import (
    needs_project_price_followup as _needs_project_price_followup,
    project_price_followup_queries as _project_price_followup_queries,
)
from app.graph.state import AgentState
from app.graph.nodes.reply_postprocess import postprocess_reply_messages as _postprocess_reply_messages
from app.graph.nodes.reply_quality import model_reply_unsafe as _model_reply_unsafe
from app.services.appointment_opening_service import AppointmentOpeningService
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.pricing_repository import LocalPricingRepository
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


def _route_after_hard_guardrails(state: AgentState) -> str:
    guardrail = state.get("guardrail_result") or {}
    if isinstance(guardrail, dict) and guardrail.get("blocked"):
        return "blocked"
    return "normal"


def build_graph(
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None = None,
    memory_store: CustomerMemoryStore | None = None,
    pricing_repository: LocalPricingRepository | None = None,
    customer_context_service: CustomerContextService | None = None,
    store_service: StoreService | None = None,
    appointment_opening_service: AppointmentOpeningService | None = None,
):
    graph = StateGraph(AgentState)

    normalize_input = create_normalize_input_node(trace_logger=trace_logger)
    image_understanding = create_image_understanding_node(trace_logger=trace_logger, model_client=model_client)
    hard_guardrails = create_hard_guardrails_node(trace_logger=trace_logger)
    load_memory = create_load_memory_node(trace_logger=trace_logger, memory_store=memory_store)
    load_customer_context = create_load_customer_context_node(
        trace_logger=trace_logger,
        customer_context_service=customer_context_service,
    )
    planner_brain = create_planner_brain_node(
        trace_logger=trace_logger,
        model_client=model_client,
    )
    scene_guidance = create_scene_guidance_node(trace_logger=trace_logger)

    execute_actions = create_execute_actions_node(
        coze_client=coze_client,
        trace_logger=trace_logger,
        pricing_repository=pricing_repository,
        store_service=store_service,
        appointment_opening_service=appointment_opening_service,
        appointment_query_from_state=lambda content, store_lookup, state: appointment_query_from_state(
            content,
            store_lookup,
            state,
            _extract_city,
        ),
        canonical_price_project=_canonical_price_project,
        contextual_price_project=_contextual_price_project,
        extract_project=_extract_project,
        has_appointment_change_or_cancel=_has_appointment_change_or_cancel,
        has_appointment_record_query=_has_appointment_record_query,
        needs_project_price_followup=_needs_project_price_followup,
        pricing_sql_from_state=_pricing_sql_from_state,
        project_price_followup_queries=_project_price_followup_queries,
        store_query_from_state=_store_query_from_state,
    )

    profile_event_extractor = create_profile_event_extractor_node(
        trace_logger=trace_logger,
        memory_store=memory_store,
        compact_memory=_compact_memory,
        extract_event_updates=lambda state, profile_update: extract_event_updates(
            state,
            profile_update,
            canonical_price_project=_canonical_price_project,
            contextual_price_project=_contextual_price_project,
            extract_price_digits=_extract_price_digits,
            extract_project=_extract_project,
            known_visible_concerns=_known_visible_concerns_from_state,
            project_direction_names=_project_direction_names_from_state,
        ),
        extract_profile_update=lambda state: extract_profile_update(
            state,
            contextual_price_project=_contextual_price_project,
            extract_project=_extract_project,
            known_visible_concerns=_known_visible_concerns_from_state,
            project_direction_names=_project_direction_names_from_state,
        ),
    )

    synthesize_reply = create_synthesize_reply_node(
        trace_logger=trace_logger,
        model_client=model_client,
        debug_message_contents=_debug_message_contents,
        model_reply_unsafe=_model_reply_unsafe,
        postprocess_reply_messages=_postprocess_reply_messages,
        reply_messages_for_model=lambda state: reply_messages_for_model(state, reply_user_payload_for_model(state)),
        reply_model_tier=reply_model_tier,
        reply_repair_messages_for_model=lambda state, draft_messages: reply_repair_messages_for_model(
            state,
            draft_messages,
            reply_user_payload_for_model(state),
        ),
        should_use_model_reply=should_use_model_reply,
        validated_model_messages=_validated_model_messages,
    )

    graph.add_node("normalize_input", normalize_input)
    graph.add_node("image_understanding", image_understanding)
    graph.add_node("hard_guardrails", hard_guardrails)
    graph.add_node("load_memory", load_memory)
    graph.add_node("load_customer_context", load_customer_context)
    graph.add_node("planner_brain", planner_brain)
    graph.add_node("retrieve_scene_guidance", scene_guidance)
    graph.add_node("execute_actions", execute_actions)
    graph.add_node("synthesize_reply", synthesize_reply)
    graph.add_node("profile_event_extractor", profile_event_extractor)

    graph.set_entry_point("normalize_input")
    graph.add_edge("normalize_input", "image_understanding")
    graph.add_edge("image_understanding", "hard_guardrails")
    graph.add_conditional_edges(
        "hard_guardrails",
        _route_after_hard_guardrails,
        {
            "blocked": "execute_actions",
            "normal": "load_memory",
        },
    )
    graph.add_edge("load_memory", "load_customer_context")
    graph.add_edge("load_customer_context", "planner_brain")
    graph.add_edge("planner_brain", "retrieve_scene_guidance")
    graph.add_edge("retrieve_scene_guidance", "execute_actions")
    graph.add_edge("execute_actions", "synthesize_reply")
    graph.add_edge("synthesize_reply", "profile_event_extractor")
    graph.add_edge("profile_event_extractor", END)

    return graph.compile()
