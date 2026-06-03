from __future__ import annotations

from app.graph.nodes.legacy_flow import (
    _available_slot_list,
    _canonical_price_project,
    _compact_memory,
    _contextual_price_project,
    _extract_city,
    _extract_price_digits,
    _extract_project,
    _forced_reply_satisfies_hard_instruction,
    _has_appointment_change_or_cancel,
    _has_appointment_record_query,
    _is_broad_price_category,
    _merge_kb_result,
    _model_reply_unsafe,
    _needs_project_price_followup,
    _planned_kb_searches,
    _postprocess_reply_messages,
    _pricing_sql_from_state,
    _project_direction_names_from_state,
    _project_price_followup_queries,
    _recent_assistant_replies,
    _reply_brief_for_model,
    _should_suspend_active_task_for_current_turn,
    _skill_output,
    json_dumps,
)
from app.graph.nodes.image_info import known_visible_concerns_from_state as _known_visible_concerns_from_state
from app.graph.nodes.intent_signals import has_store_inquiry as _has_store_inquiry
from app.graph.nodes.legacy_graph_wiring import LegacyGraphWiringCallbacks, build_legacy_graph
from app.graph.nodes.legacy_turn_planning import (
    should_drop_planner_notes_for_skill_output as _should_drop_planner_notes_for_skill_output,
    with_action_planning_notes as _with_action_planning_notes,
    without_appointment_intents as _without_appointment_intents,
)
from app.graph.nodes.reply_input import (
    reply_model_tier as _reply_model_tier_from_input,
    should_use_model_reply as _should_use_model_reply_from_input,
)
from app.graph.nodes.reply_validation import (
    debug_message_contents as _debug_message_contents,
    validated_model_messages as _validated_model_messages,
)
from app.graph.nodes.store_context import store_query_from_state as _store_query_from_state
from app.services.coze_client import CozeClient
from app.services.customer_context import CustomerContextService
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.pricing_repository import LocalPricingRepository
from app.services.store_service import StoreService
from app.services.trace_logger import TraceLogger


def build_graph(
    coze_client: CozeClient,
    trace_logger: TraceLogger,
    model_client: ModelClient | None = None,
    memory_store: CustomerMemoryStore | None = None,
    pricing_repository: LocalPricingRepository | None = None,
    customer_context_service: CustomerContextService | None = None,
    store_service: StoreService | None = None,
):
    return build_legacy_graph(
        coze_client=coze_client,
        trace_logger=trace_logger,
        model_client=model_client,
        memory_store=memory_store,
        pricing_repository=pricing_repository,
        customer_context_service=customer_context_service,
        store_service=store_service,
        callbacks=LegacyGraphWiringCallbacks(
            available_slot_list=_available_slot_list,
            canonical_price_project=_canonical_price_project,
            compact_memory=_compact_memory,
            contextual_price_project=_contextual_price_project,
            debug_message_contents=_debug_message_contents,
            extract_city=_extract_city,
            extract_price_digits=_extract_price_digits,
            extract_project=_extract_project,
            forced_reply_satisfies_hard_instruction=_forced_reply_satisfies_hard_instruction,
            has_appointment_change_or_cancel=_has_appointment_change_or_cancel,
            has_appointment_record_query=_has_appointment_record_query,
            has_store_inquiry=_has_store_inquiry,
            is_broad_price_category=_is_broad_price_category,
            json_dumps=json_dumps,
            known_visible_concerns=_known_visible_concerns_from_state,
            merge_kb_result=_merge_kb_result,
            model_reply_unsafe=_model_reply_unsafe,
            needs_project_price_followup=_needs_project_price_followup,
            planned_kb_searches=_planned_kb_searches,
            postprocess_reply_messages=_postprocess_reply_messages,
            pricing_sql_from_state=_pricing_sql_from_state,
            project_direction_names=_project_direction_names_from_state,
            project_price_followup_queries=_project_price_followup_queries,
            recent_assistant_replies=_recent_assistant_replies,
            reply_brief=_reply_brief_for_model,
            reply_model_tier=_reply_model_tier_from_input,
            should_drop_planner_notes_for_skill_output=_should_drop_planner_notes_for_skill_output,
            should_suspend_active_task=_should_suspend_active_task_for_current_turn,
            should_use_model_reply=_should_use_model_reply_from_input,
            skill_output=_skill_output,
            store_query_from_state=_store_query_from_state,
            validated_model_messages=_validated_model_messages,
            with_action_planning_notes=_with_action_planning_notes,
            without_appointment_intents=_without_appointment_intents,
        ),
    )
