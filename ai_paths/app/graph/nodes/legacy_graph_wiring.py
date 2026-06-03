from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph

from app.graph.nodes.action_nodes import ActionCallbacks, create_execute_actions_node
from app.graph.nodes.action_queries import ActionQueryCallbacks, safe_query_from_state
from app.graph.nodes.appointment_utils import AppointmentQueryCallbacks, appointment_query_from_state
from app.graph.nodes.context_nodes import create_load_customer_context_node, create_load_memory_node
from app.graph.nodes.guardrail_nodes import create_hard_guardrails_node
from app.graph.nodes.input_nodes import create_image_understanding_node, create_normalize_input_node
from app.graph.nodes.legacy_graph_callback_types import LegacyGraphWiringCallbacks
from app.graph.nodes.legacy_graph_edges import add_legacy_nodes_and_edges
from app.graph.nodes.planner_nodes import create_planner_brain_node
from app.graph.nodes.profile_extraction import ProfileExtractionCallbacks, extract_event_updates, extract_profile_update
from app.graph.nodes.profile_nodes import ProfileCallbacks, create_profile_event_extractor_node
from app.graph.nodes.reply_context import ReplyContextCallbacks, reply_user_payload_for_model
from app.graph.nodes.reply_input import (
    ReplyInputCallbacks,
    reply_messages_for_model,
    reply_repair_messages_for_model,
)
from app.graph.nodes.reply_nodes import ReplyCallbacks, create_synthesize_reply_node
from app.graph.nodes.reply_payloads import (
    ReplyPayloadCallbacks,
    appointment_reply_payload_for_model,
    reply_forced_payload_for_model,
    should_use_appointment_fact_reply,
)
from app.graph.state import AgentState


def build_legacy_graph(
    *,
    coze_client: Any,
    trace_logger: Any,
    model_client: Any | None = None,
    memory_store: Any | None = None,
    pricing_repository: Any | None = None,
    customer_context_service: Any | None = None,
    store_service: Any | None = None,
    callbacks: LegacyGraphWiringCallbacks,
):
    graph = StateGraph(AgentState)
    normalize_input = create_normalize_input_node(trace_logger=trace_logger)
    image_understanding = create_image_understanding_node(trace_logger=trace_logger, model_client=model_client)
    load_memory = create_load_memory_node(trace_logger=trace_logger, memory_store=memory_store)
    load_customer_context = create_load_customer_context_node(
        trace_logger=trace_logger,
        customer_context_service=customer_context_service,
    )
    hard_guardrails = create_hard_guardrails_node(trace_logger=trace_logger)
    planner_brain = create_planner_brain_node(
        trace_logger=trace_logger,
        model_client=model_client,
        should_suspend_active_task=callbacks.should_suspend_active_task,
        without_appointment_intents=callbacks.without_appointment_intents,
    )
    action_query_callbacks = ActionQueryCallbacks(
        canonical_price_project=callbacks.canonical_price_project,
        contextual_price_project=callbacks.contextual_price_project,
        extract_price_digits=callbacks.extract_price_digits,
        extract_project=callbacks.extract_project,
    )
    appointment_query_callbacks = AppointmentQueryCallbacks(extract_city=callbacks.extract_city)
    execute_actions = create_execute_actions_node(
        coze_client=coze_client,
        trace_logger=trace_logger,
        pricing_repository=pricing_repository,
        store_service=store_service,
        callbacks=ActionCallbacks(
            appointment_query_from_state=lambda content, store_lookup, state: appointment_query_from_state(
                content,
                store_lookup,
                state,
                appointment_query_callbacks,
            ),
            canonical_price_project=callbacks.canonical_price_project,
            contextual_price_project=callbacks.contextual_price_project,
            extract_project=callbacks.extract_project,
            has_appointment_change_or_cancel=callbacks.has_appointment_change_or_cancel,
            has_appointment_record_query=callbacks.has_appointment_record_query,
            has_store_inquiry=callbacks.has_store_inquiry,
            is_broad_price_category=callbacks.is_broad_price_category,
            json_dumps=callbacks.json_dumps,
            merge_kb_result=callbacks.merge_kb_result,
            needs_project_price_followup=callbacks.needs_project_price_followup,
            planned_kb_searches=callbacks.planned_kb_searches,
            pricing_sql_from_state=callbacks.pricing_sql_from_state,
            project_price_followup_queries=callbacks.project_price_followup_queries,
            safe_query_from_state=lambda state, skill: safe_query_from_state(state, skill, action_query_callbacks),
            should_drop_planner_notes_for_skill_output=callbacks.should_drop_planner_notes_for_skill_output,
            should_suspend_active_task=callbacks.should_suspend_active_task,
            skill_output=callbacks.skill_output,
            store_query_from_state=callbacks.store_query_from_state,
            with_action_planning_notes=callbacks.with_action_planning_notes,
        ),
    )
    profile_extraction_callbacks = ProfileExtractionCallbacks(
        canonical_price_project=callbacks.canonical_price_project,
        contextual_price_project=callbacks.contextual_price_project,
        extract_price_digits=callbacks.extract_price_digits,
        extract_project=callbacks.extract_project,
        known_visible_concerns=callbacks.known_visible_concerns,
        project_direction_names=callbacks.project_direction_names,
    )
    profile_event_extractor = create_profile_event_extractor_node(
        trace_logger=trace_logger,
        memory_store=memory_store,
        callbacks=ProfileCallbacks(
            compact_memory=callbacks.compact_memory,
            extract_event_updates=lambda state, profile_update: extract_event_updates(
                state,
                profile_update,
                profile_extraction_callbacks,
            ),
            extract_profile_update=lambda state: extract_profile_update(state, profile_extraction_callbacks),
        ),
    )
    reply_context_callbacks = ReplyContextCallbacks(
        canonical_price_project=callbacks.canonical_price_project,
        contextual_price_project=callbacks.contextual_price_project,
        is_broad_price_category=callbacks.is_broad_price_category,
        recent_assistant_replies=callbacks.recent_assistant_replies,
        reply_brief=callbacks.reply_brief,
        should_suspend_active_task=callbacks.should_suspend_active_task,
    )
    reply_input_callbacks = ReplyInputCallbacks(
        json_dumps=callbacks.json_dumps,
        reply_user_payload=lambda state: reply_user_payload_for_model(state, reply_context_callbacks),
    )
    reply_payload_callbacks = ReplyPayloadCallbacks(
        available_slot_list=callbacks.available_slot_list,
        recent_assistant_replies=callbacks.recent_assistant_replies,
        reply_brief=callbacks.reply_brief,
        should_suspend_active_task=callbacks.should_suspend_active_task,
    )
    synthesize_reply = create_synthesize_reply_node(
        trace_logger=trace_logger,
        model_client=model_client,
        callbacks=ReplyCallbacks(
            appointment_reply_payload_for_model=lambda state: appointment_reply_payload_for_model(
                state,
                reply_payload_callbacks,
            ),
            debug_message_contents=callbacks.debug_message_contents,
            forced_reply_satisfies_hard_instruction=callbacks.forced_reply_satisfies_hard_instruction,
            json_dumps=callbacks.json_dumps,
            model_reply_unsafe=callbacks.model_reply_unsafe,
            postprocess_reply_messages=callbacks.postprocess_reply_messages,
            reply_forced_payload_for_model=lambda state: reply_forced_payload_for_model(
                state,
                reply_payload_callbacks,
            ),
            reply_messages_for_model=lambda state: reply_messages_for_model(state, reply_input_callbacks),
            reply_model_tier=callbacks.reply_model_tier,
            reply_repair_messages_for_model=lambda state, draft_messages: reply_repair_messages_for_model(
                state,
                draft_messages,
                reply_input_callbacks,
            ),
            should_use_appointment_fact_reply=lambda state: should_use_appointment_fact_reply(
                state,
                reply_payload_callbacks,
            ),
            should_use_model_reply=callbacks.should_use_model_reply,
            validated_model_messages=callbacks.validated_model_messages,
        ),
    )

    add_legacy_nodes_and_edges(
        graph,
        {
            "normalize_input": normalize_input,
            "image_understanding": image_understanding,
            "hard_guardrails": hard_guardrails,
            "load_memory": load_memory,
            "load_customer_context": load_customer_context,
            "planner_brain": planner_brain,
            "execute_actions": execute_actions,
            "synthesize_reply": synthesize_reply,
            "profile_event_extractor": profile_event_extractor,
        },
    )

    return graph.compile()
