from __future__ import annotations

from app.graph.nodes.common import json_dumps, recent_assistant_replies as _recent_assistant_replies
from app.graph.nodes.legacy_flow_utils import compact_memory as _compact_memory, extract_price_digits as _extract_price_digits
from app.graph.nodes.legacy_flow import (
    _available_slot_list,
    _forced_reply_satisfies_hard_instruction,
    _model_reply_unsafe,
    _postprocess_reply_messages,
    _reply_brief_for_model,
    _should_suspend_active_task_for_current_turn,
)
from app.graph.nodes.image_info import known_visible_concerns_from_state as _known_visible_concerns_from_state
from app.graph.nodes.intent_signals import (
    has_appointment_change_or_cancel as _has_appointment_change_or_cancel,
    has_appointment_record_query as _has_appointment_record_query,
    has_store_inquiry as _has_store_inquiry,
)
from app.graph.nodes.legacy_graph_wiring import LegacyGraphWiringCallbacks, build_legacy_graph
from app.graph.nodes.legacy_kb_bridge import (
    merge_kb_result as _merge_kb_result,
    needs_project_price_followup as _needs_project_price_followup,
    planned_kb_searches as _planned_kb_searches,
    project_price_followup_queries as _project_price_followup_queries,
)
from app.graph.nodes.legacy_context_bridge import (
    contextual_price_project as _contextual_price_project,
    pricing_sql_from_state as _pricing_sql_from_state,
    project_direction_names_from_state as _project_direction_names_from_state,
)
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
from app.graph.nodes.legacy_skill_bridge import skill_output as _skill_output
from app.graph.nodes.pricing_context import (
    canonical_price_project as _canonical_price_project,
    extract_project as _extract_project,
    is_broad_price_category as _is_broad_price_category,
)
from app.graph.nodes.store_context import extract_city as _extract_city, store_query_from_state as _store_query_from_state
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
