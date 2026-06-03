from __future__ import annotations

import re
from typing import Any

from app.graph import reply_filters, task_state
from app.graph.nodes.action_nodes import ActionCallbacks, create_execute_actions_node
from app.graph.nodes.action_queries import (
    ActionQueryCallbacks,
    safe_query_from_state as _safe_query_from_action_queries,
)
from app.graph.nodes.after_sales_skill_output import (
    AfterSalesSkillCallbacks,
    after_sales_skill_output as _after_sales_skill_output_from_module,
)
from app.graph.nodes.appointment_utils import (
    AppointmentQueryCallbacks,
    appointment_query_from_state as _appointment_query_from_appointment_utils,
    available_time_values as _available_time_values,
    extract_date_value as _extract_date_value,
    filter_times_by_preference as _filter_times_by_preference,
    has_explicit_location_or_store as _has_explicit_location_or_store_from_appointment_utils,
)
from app.graph.nodes.basic_skill_output import (
    BasicSkillCallbacks,
    basic_skill_output as _basic_skill_output_from_module,
)
from app.graph.nodes.common import (
    dedupe_strings as _dedupe_strings,
    intent_for_skill as _intent_for_skill,
    json_dumps,
    looks_bad_text as _looks_bad_text,
    model_usage_snapshot as _model_usage_snapshot,
    recent_assistant_replies as _recent_assistant_replies,
    renumber_messages as _renumber,
)
from app.graph.nodes.competitor_skill_output import (
    CompetitorSkillCallbacks,
    competitor_skill_output as _competitor_skill_output_from_module,
)
from app.graph.nodes.context_nodes import create_load_customer_context_node, create_load_memory_node
from app.graph.nodes.guardrail_nodes import (
    create_hard_guardrails_node,
    is_identity_question as _is_identity_question,
)
from app.graph.nodes.image_info import (
    has_image_concern as _has_image_concern,
    has_actual_image_context as _has_actual_image_context,
    has_known_image_context as _has_known_image_context,
    known_visible_concerns_from_state as _known_visible_concerns_from_state,
)
from app.graph.nodes.input_nodes import create_image_understanding_node, create_normalize_input_node
from app.graph.nodes.legacy_graph_wiring import (
    LegacyGraphWiringCallbacks,
    build_legacy_graph,
)
from app.graph.nodes.legacy_goal_context import (
    has_confirmed_spot_goal as _has_confirmed_spot_goal_from_goal_context,
    is_redundant_known_goal_question as _is_redundant_known_goal_question_from_goal_context,
)
from app.graph.nodes.legacy_flow_utils import (
    available_slot_list as _available_slot_list_from_utils,
    compact_memory as _compact_memory_from_utils,
    extract_price_digits as _extract_price_digits_from_utils,
    parking_text as _parking_text_from_utils,
)
from app.graph.nodes.legacy_kb_planning import (
    LegacyKbPlanningCallbacks,
    needs_project_price_followup as _needs_project_price_followup_from_module,
    planned_kb_searches as _planned_kb_searches_from_module,
    project_price_followup_queries as _project_price_followup_queries_from_module,
)
from app.graph.nodes.legacy_appointment_messages import (
    LegacyAppointmentMessageCallbacks,
    appointment_context_sentence as _appointment_context_sentence_from_module,
    should_show_appointment_context as _should_show_appointment_context_from_module,
)
from app.graph.nodes.legacy_context_guidance import (
    LegacyContextGuidanceCallbacks,
    context_guidance_inline as _context_guidance_inline_from_context_guidance,
    image_guidance_inline as _image_guidance_inline_from_context_guidance,
    memory_context_sentence as _memory_context_sentence_from_context_guidance,
    project_context_source as _project_context_source_from_context_guidance,
    project_guidance_inline as _project_guidance_inline_from_context_guidance,
    sanitize_project_direction as _sanitize_project_direction_from_context_guidance,
)
from app.graph.nodes.intent_signals import (
    denies_severe_after_sales as _denies_severe_after_sales,
    has_advantage_question as _has_advantage_question,
    has_appointment_change_or_cancel as _has_appointment_change_or_cancel,
    has_appointment_record_query as _has_appointment_record_query,
    has_effect_guarantee_request as _has_effect_guarantee_request,
    has_price_objection as _has_price_objection,
    has_project_consult_intent as _has_project_consult_intent,
    has_store_inquiry as _has_store_inquiry,
    is_generic_project_intro as _is_generic_project_intro,
    is_negated_symptom as _is_negated_symptom,
    is_pre_service_effect_concern as _is_pre_service_effect_concern,
    is_unclear_need as _is_unclear_need,
    recent_conversation_text as _recent_conversation_text,
)
from app.graph.nodes.planner_nodes import create_planner_brain_node
from app.graph.nodes.kb_slice_parsing import (
    extract_label as _extract_label,
    extract_label_block as _extract_label_block,
    parse_price_kb_content as _parse_price_kb_content,
    pricing_rows_from_kb as _pricing_rows_from_kb,
)
from app.graph.nodes.project_kb_context import (
    business_project_slices as _business_project_slices_from_context,
    case_request_lacks_specific_context as _case_request_lacks_specific_context,
    is_business_project_direction_name as _is_business_project_direction_name,
    project_direction_name_candidates as _project_direction_name_candidates,
    project_slices_from_tool_results as _project_slices_from_tool_results,
)
from app.graph.nodes.project_skill_output import (
    ProjectSkillCallbacks,
    project_skill_output as _project_skill_output_from_module,
)
from app.graph.nodes.price_skill_output import (
    PriceSkillCallbacks,
    price_skill_output as _price_skill_output_from_module,
)
from app.graph.nodes.store_skill_output import (
    StoreSkillCallbacks,
    store_skill_output as _store_skill_output_from_module,
)
from app.graph.nodes.trust_skill_output import trust_skill_output as _trust_skill_output_from_module
from app.graph.nodes.legacy_project_context import (
    LegacyProjectContextCallbacks,
    contextual_price_project as _contextual_price_project_from_project_context,
    project_direction_names_from_state as _project_direction_names_from_project_context,
    recent_project_from_state as _recent_project_from_project_context,
)
from app.graph.nodes.legacy_qa_slice_context import (
    clean_after_sales_text as _clean_after_sales_text_from_qa_context,
    clean_competitor_text as _clean_competitor_text_from_qa_context,
    competitor_default_reply as _competitor_default_reply_from_qa_context,
    competitor_risk_terms as _competitor_risk_terms_from_qa_context,
    competitor_scenario as _competitor_scenario_from_qa_context,
    competitor_slice_matches as _competitor_slice_matches_from_qa_context,
    first_after_sales_slice as _first_after_sales_slice_from_qa_context,
    first_competitor_slice as _first_competitor_slice_from_qa_context,
    split_collect_items as _split_collect_items_from_qa_context,
)
from app.graph.nodes.legacy_reply_callback_factories import (
    LegacyReplyCallbackFactoryCallbacks,
    reply_brief_callbacks as _reply_brief_callbacks_from_module,
    reply_postprocess_callbacks as _reply_postprocess_callbacks_from_module,
    reply_quality_callbacks as _reply_quality_callbacks_from_module,
    reply_summary_callbacks as _reply_summary_callbacks_from_module,
)
from app.graph.nodes.legacy_reply_quality_signals import (
    asks_followup_question as _asks_followup_question_from_quality_signals,
    is_single_store_fact_query as _is_single_store_fact_query_from_quality_signals,
    rejects_more_questions as _rejects_more_questions_from_quality_signals,
    time_text_variants as _time_text_variants_from_quality_signals,
    too_similar_to_recent_assistant_reply as _too_similar_to_recent_assistant_reply_from_quality_signals,
)
from app.graph.nodes.legacy_skill_dispatch import (
    LegacySkillDispatchCallbacks,
    skill_output as _skill_output_from_dispatch,
)
from app.graph.nodes.legacy_turn_planning import (
    LegacyTurnPlanningCallbacks,
    has_explicit_appointment_request as _has_explicit_appointment_request_from_module,
    should_drop_planner_notes_for_skill_output as _should_drop_planner_notes_for_skill_output,
    should_suspend_active_task_for_current_turn as _should_suspend_active_task_for_current_turn_from_module,
    with_action_planning_notes as _with_action_planning_notes,
    without_appointment_intents as _without_appointment_intents,
)
from app.graph.nodes.legacy_tool_results import (
    merge_kb_result as _merge_kb_result_from_tool_results,
    tool_results_contain as _tool_results_contain_from_tool_results,
)
from app.graph.nodes.profile_extraction import (
    ProfileExtractionCallbacks,
    customer_goal_from_content as _customer_goal_from_content,
    extract_event_updates as _extract_event_updates_from_profile,
    extract_profile_update as _extract_profile_update_from_profile,
)
from app.graph.nodes.profile_nodes import ProfileCallbacks, create_profile_event_extractor_node
from app.graph.nodes.pricing_context import (
    canonical_price_project as _canonical_price_project,
    extract_project as _extract_project,
    filter_pricing_rows_for_project as _filter_pricing_rows_for_project,
    is_broad_price_category as _is_broad_price_category,
    price_bits as _price_bits,
    price_fact_for_brief as _price_fact_for_brief,
    price_point_from_row as _price_point_from_row,
    price_risk_terms as _price_risk_terms,
    pricing_rows as _pricing_rows,
    pricing_sql_for_project,
    requires_exact_price as _requires_exact_price,
    value as _value,
)
from app.graph.nodes.reply_input import (
    ReplyInputCallbacks,
    reply_messages_for_model as _reply_messages_from_input,
    reply_model_tier as _reply_model_tier_from_input,
    reply_repair_messages_for_model as _reply_repair_messages_from_input,
    should_use_model_reply as _should_use_model_reply_from_input,
)
from app.graph.nodes.reply_context import (
    ReplyContextCallbacks,
    reply_user_payload_for_model as _reply_user_payload_from_context,
    store_lookup_missing_city as _store_lookup_missing_city,
)
from app.graph.nodes.result_compaction import ad_price_without_explicit_project as _ad_price_without_explicit_project
from app.graph.nodes.reply_nodes import ReplyCallbacks, create_synthesize_reply_node
from app.graph.nodes.reply_payloads import (
    ReplyPayloadCallbacks,
    appointment_reply_payload_for_model as _appointment_reply_payload_from_payloads,
    is_direct_arrival_question as _is_direct_arrival_question,
    reply_forced_payload_for_model as _reply_forced_payload_from_payloads,
    should_use_appointment_fact_reply as _should_use_appointment_fact_reply_from_payloads,
)
from app.graph.nodes.reply_postprocess import (
    has_no_price_fact_phrase as _has_no_price_fact_phrase_from_postprocess,
    lacks_price_answer_for_price_question as _lacks_price_answer_for_price_question_from_postprocess,
    postprocess_reply_messages as _postprocess_reply_messages_from_postprocess,
)
from app.graph.nodes.reply_quality import (
    forced_reply_safe as _forced_reply_safe_from_quality,
    model_reply_unsafe as _model_reply_unsafe_from_quality,
)
from app.graph.nodes.reply_summary_context import (
    asks_other_store_options as _asks_other_store_options_from_summary,
    asks_price_recap as _asks_price_recap_from_summary,
    asks_store_or_address_recap as _asks_store_or_address_recap_from_summary,
    has_pre_visit_question as _has_pre_visit_question_from_summary,
    is_strong_multi_recap_request as _is_strong_multi_recap_request_from_summary,
    latest_price_summary_from_history as _latest_price_summary_from_history_from_summary,
    latest_store_summary_from_history as _latest_store_summary_from_history_from_summary,
    price_summary_message as _price_summary_message_from_summary,
    store_summary_message as _store_summary_message_from_summary,
)
from app.graph.nodes.reply_brief import (
    reply_brief_for_model as _reply_brief_from_module,
)
from app.graph.nodes.reply_validation import (
    debug_message_contents as _debug_message_contents,
    extract_image_url_from_text as _extract_image_url_from_text,
    looks_like_image_url as _looks_like_image_url,
    validated_model_messages as _validated_model_messages,
)
from app.graph.nodes.store_context import (
    extract_city as _extract_city,
    extract_store_area as _extract_store_area,
    extract_time_text as _extract_time_text,
    known_city_from_state as _known_city_from_state,
    known_store_name_from_history as _known_store_name_from_history,
    known_store_name_from_text as _known_store_name_from_text,
    preferred_store_name_from_text as _preferred_store_name_from_text,
    should_use_known_store_context as _should_use_known_store_context,
    should_use_recent_store_fact_context as _should_use_recent_store_fact_context,
    store_query_from_state as _store_query_from_state,
)
from app.graph.state import AgentState
from app.policies.constants import (
    APPOINTMENT_KEYWORDS,
    CITY_NAMES,
)
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


def _compact_memory(memory: dict[str, Any]) -> dict[str, Any]:
    return _compact_memory_from_utils(memory)


def _skill_output(skill: str, content: str, tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    return _skill_output_from_dispatch(
        skill,
        content,
        tool_results,
        state,
        LegacySkillDispatchCallbacks(
            price_skill_output=_price_skill_output,
            trust_skill_output=_trust_skill_output,
            project_skill_output=_project_skill_output,
            competitor_skill_output=_competitor_skill_output,
            after_sales_skill_output=_after_sales_skill_output,
            store_skill_output=_store_skill_output,
            basic_skill_output=_basic_skill_output,
            json_dumps=json_dumps,
        ),
    )


def _legacy_kb_planning_callbacks() -> LegacyKbPlanningCallbacks:
    return LegacyKbPlanningCallbacks(
        canonical_price_project=_canonical_price_project,
        contextual_price_project=_contextual_price_project,
        dedupe_strings=_dedupe_strings,
        extract_project=_extract_project,
        is_broad_price_category=_is_broad_price_category,
        looks_bad_text=_looks_bad_text,
        pricing_rows_from_kb=_pricing_rows_from_kb,
        project_direction_name_candidates=_project_direction_name_candidates,
        project_slices_from_tool_results=_project_slices_from_tool_results,
        recent_project_from_state=_recent_project_from_state,
        safe_query_from_state=_safe_query_from_state,
    )


def _planned_kb_searches(action: dict[str, Any], state: AgentState | None = None) -> list[dict[str, str]]:
    return _planned_kb_searches_from_module(action, state, callbacks=_legacy_kb_planning_callbacks())


def _safe_query_from_state(state: AgentState, skill: str) -> str:
    return _safe_query_from_action_queries(
        state,
        skill,
        callbacks=ActionQueryCallbacks(
            canonical_price_project=_canonical_price_project,
            contextual_price_project=_contextual_price_project,
            extract_price_digits=_extract_price_digits,
            extract_project=_extract_project,
        ),
    )


def _needs_project_price_followup(actions: list[dict[str, Any]], tool_results: dict[str, Any], state: AgentState) -> bool:
    return _needs_project_price_followup_from_module(actions, tool_results, state, callbacks=_legacy_kb_planning_callbacks())


def _project_price_followup_queries(tool_results: dict[str, Any]) -> list[str]:
    return _project_price_followup_queries_from_module(tool_results, callbacks=_legacy_kb_planning_callbacks())


def _merge_kb_result(tool_results: dict[str, Any], kb_name: str, dumped: dict[str, Any]) -> None:
    _merge_kb_result_from_tool_results(tool_results, kb_name, dumped)


def _after_sales_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    return _after_sales_skill_output_from_module(
        content,
        tool_results,
        AfterSalesSkillCallbacks(
            first_after_sales_slice=_first_after_sales_slice_from_qa_context,
            clean_after_sales_text=_clean_after_sales_text_from_qa_context,
            split_collect_items=_split_collect_items_from_qa_context,
        ),
    )


def _competitor_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    return _competitor_skill_output_from_module(
        content,
        tool_results,
        CompetitorSkillCallbacks(
            first_competitor_slice=_first_competitor_slice_from_qa_context,
            competitor_scenario=_competitor_scenario_from_qa_context,
            extract_project=_extract_project,
            extract_price_digits=_extract_price_digits,
            competitor_slice_matches=_competitor_slice_matches_from_qa_context,
            clean_competitor_text=_clean_competitor_text_from_qa_context,
            competitor_default_reply=_competitor_default_reply_from_qa_context,
            split_collect_items=_split_collect_items_from_qa_context,
            competitor_risk_terms=_competitor_risk_terms_from_qa_context,
        ),
    )


def _extract_price_digits(content: str) -> list[str]:
    return _extract_price_digits_from_utils(content)


def _store_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    return _store_skill_output_from_module(
        content,
        tool_results,
        StoreSkillCallbacks(
            extract_city=_extract_city,
            parking_text=_parking_text,
        ),
    )


def _price_skill_output(content: str, tool_results: dict[str, Any], state: AgentState | None = None) -> dict[str, Any]:
    return _price_skill_output_from_module(
        content,
        tool_results,
        state,
        PriceSkillCallbacks(
            ad_price_without_explicit_project=_ad_price_without_explicit_project,
            canonical_price_project=_canonical_price_project,
            contextual_price_project=_contextual_price_project,
            extract_price_digits=_extract_price_digits,
            extract_project=_extract_project,
            filter_pricing_rows_for_project=_filter_pricing_rows_for_project,
            has_price_objection=_has_price_objection,
            is_broad_price_category=_is_broad_price_category,
            price_bits=_price_bits,
            price_risk_terms=_price_risk_terms,
            pricing_rows=_pricing_rows,
            pricing_rows_from_kb=_pricing_rows_from_kb,
        ),
    )


def _trust_skill_output(content: str, tool_results: dict[str, Any]) -> dict[str, Any]:
    return _trust_skill_output_from_module(content, tool_results)


def _project_skill_output(content: str, tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    return _project_skill_output_from_module(
        content,
        tool_results,
        state,
        ProjectSkillCallbacks(
            business_project_slices=_business_project_slices,
            case_request_lacks_specific_context=_case_request_lacks_specific_context,
            dedupe_strings=_dedupe_strings,
            has_image_concern=_has_image_concern,
            known_visible_concerns_from_state=_known_visible_concerns_from_state,
            project_direction_name_candidates=_project_direction_name_candidates,
            project_slices_from_tool_results=_project_slices_from_tool_results,
        ),
    )


def _basic_skill_output(
    skill: str,
    reply_points: list[str],
    *,
    suggested_next_step: str = "",
    facts: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    return _basic_skill_output_from_module(
        skill,
        reply_points,
        BasicSkillCallbacks(intent_for_skill=_intent_for_skill),
        suggested_next_step=suggested_next_step,
        facts=facts,
        risk_flags=risk_flags,
    )


def _postprocess_reply_messages(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _postprocess_reply_messages_from_postprocess(state, messages, _reply_postprocess_callbacks())


def _lacks_price_answer_for_price_question(state: AgentState, text: str) -> bool:
    return _lacks_price_answer_for_price_question_from_postprocess(state, text)


def _has_no_price_fact_phrase(text: str) -> bool:
    return _has_no_price_fact_phrase_from_postprocess(text)


def _looks_like_store_list_message(text: str) -> bool:
    return "匹配到" in text and "门店" in text and ("你看哪家更方便" in text or re.search(r"\n\s*1[.、]", text))


def _is_redundant_known_goal_question(state: AgentState, text: str) -> bool:
    return _is_redundant_known_goal_question_from_goal_context(
        state,
        text,
        has_known_image_context=_has_known_image_context,
        known_visible_concerns_from_state=_known_visible_concerns_from_state,
        json_dumps=json_dumps,
    )


def _has_confirmed_spot_goal(state: AgentState) -> bool:
    return _has_confirmed_spot_goal_from_goal_context(state, json_dumps)


def _has_pre_visit_question(content: str) -> bool:
    return _has_pre_visit_question_from_summary(content)


def _is_strong_multi_recap_request(content: str) -> bool:
    return _is_strong_multi_recap_request_from_summary(content)


def _asks_other_store_options(content: str) -> bool:
    return _asks_other_store_options_from_summary(content)


def _asks_store_or_address_recap(content: str) -> bool:
    return _asks_store_or_address_recap_from_summary(content)


def _asks_price_recap(content: str) -> bool:
    return _asks_price_recap_from_summary(content)


def _store_summary_message(state: AgentState) -> str:
    return _store_summary_message_from_summary(state, _reply_summary_callbacks())


def _price_summary_message(state: AgentState) -> str:
    return _price_summary_message_from_summary(state, _reply_summary_callbacks())


def _latest_store_summary_from_history(state: AgentState) -> str:
    return _latest_store_summary_from_history_from_summary(state, _reply_summary_callbacks())


def _latest_price_summary_from_history(state: AgentState) -> str:
    return _latest_price_summary_from_history_from_summary(state, _reply_summary_callbacks())


def _available_slot_list(slots_value: Any) -> list[str]:
    return _available_slot_list_from_utils(slots_value, _dedupe_strings)


def _legacy_appointment_message_callbacks() -> LegacyAppointmentMessageCallbacks:
    return LegacyAppointmentMessageCallbacks(
        recent_assistant_replies=_recent_assistant_replies,
    )


def _legacy_turn_planning_callbacks() -> LegacyTurnPlanningCallbacks:
    return LegacyTurnPlanningCallbacks(
        asks_price_recap=_asks_price_recap,
        asks_store_or_address_recap=_asks_store_or_address_recap,
        extract_date_value=_extract_date_value,
        has_appointment_change_or_cancel=_has_appointment_change_or_cancel,
        has_appointment_record_query=_has_appointment_record_query,
        has_pre_visit_question=_has_pre_visit_question,
        is_strong_multi_recap_request=_is_strong_multi_recap_request,
    )


def _should_suspend_active_task_for_current_turn(
    state: AgentState,
    active_task: dict[str, Any] | None = None,
    intents: list[dict[str, Any]] | None = None,
) -> bool:
    return _should_suspend_active_task_for_current_turn_from_module(
        state,
        active_task=active_task,
        intents=intents,
        callbacks=_legacy_turn_planning_callbacks(),
    )


def _has_explicit_appointment_request(content: str) -> bool:
    return _has_explicit_appointment_request_from_module(content, _legacy_turn_planning_callbacks())


def _legacy_reply_callback_factory_callbacks() -> LegacyReplyCallbackFactoryCallbacks:
    return LegacyReplyCallbackFactoryCallbacks(
        ad_price_without_explicit_project=_ad_price_without_explicit_project,
        appointment_context_sentence=_appointment_context_sentence,
        asks_followup_question=_asks_followup_question,
        asks_other_store_options=_asks_other_store_options,
        asks_price_recap=_asks_price_recap,
        asks_store_or_address_recap=_asks_store_or_address_recap,
        available_slot_list=_available_slot_list,
        business_project_slices=_business_project_slices,
        canonical_price_project=_canonical_price_project,
        contextual_price_project=_contextual_price_project,
        dedupe_strings=_dedupe_strings,
        extract_city=_extract_city,
        extract_price_digits=_extract_price_digits,
        extract_project=_extract_project,
        filter_pricing_rows_for_project=_filter_pricing_rows_for_project,
        has_actual_image_context=_has_actual_image_context,
        has_confirmed_spot_goal=_has_confirmed_spot_goal,
        has_effect_guarantee_request=_has_effect_guarantee_request,
        has_image_concern=_has_image_concern,
        has_known_image_context=_has_known_image_context,
        has_no_price_fact_phrase=_has_no_price_fact_phrase,
        has_pre_visit_question=_has_pre_visit_question,
        has_price_objection=_has_price_objection,
        is_broad_price_category=_is_broad_price_category,
        is_direct_arrival_question=_is_direct_arrival_question,
        is_generic_project_intro=_is_generic_project_intro,
        is_identity_question=_is_identity_question,
        is_redundant_known_goal_question=_is_redundant_known_goal_question,
        is_single_store_fact_query=_is_single_store_fact_query,
        is_strong_multi_recap_request=_is_strong_multi_recap_request,
        is_unclear_need=_is_unclear_need,
        known_visible_concerns_from_state=_known_visible_concerns_from_state,
        lacks_price_answer_for_price_question=_lacks_price_answer_for_price_question,
        looks_like_store_list_message=_looks_like_store_list_message,
        memory_context_sentence=_memory_context_sentence,
        parking_text=_parking_text,
        price_bits=_price_bits,
        price_fact_for_brief=_price_fact_for_brief,
        price_summary_message=_price_summary_message,
        pricing_rows=_pricing_rows,
        pricing_rows_from_kb=_pricing_rows_from_kb,
        project_direction_name_candidates=_project_direction_name_candidates,
        project_direction_names_from_state=_project_direction_names_from_state,
        project_slices_from_tool_results=_project_slices_from_tool_results,
        recent_assistant_replies=_recent_assistant_replies,
        rejects_more_questions=_rejects_more_questions,
        renumber_messages=_renumber,
        should_show_appointment_context=_should_show_appointment_context,
        store_lookup_missing_city=_store_lookup_missing_city,
        store_summary_message=_store_summary_message,
        time_text_variants=_time_text_variants,
        too_similar_to_recent_assistant_reply=_too_similar_to_recent_assistant_reply,
        tool_results_contain=_tool_results_contain,
    )


def _reply_brief_callbacks():
    return _reply_brief_callbacks_from_module(_legacy_reply_callback_factory_callbacks())


def _reply_brief_for_model(state: AgentState) -> dict[str, Any]:
    return _reply_brief_from_module(state, _reply_brief_callbacks())
def _reply_quality_callbacks():
    return _reply_quality_callbacks_from_module(_legacy_reply_callback_factory_callbacks())


def _reply_summary_callbacks():
    return _reply_summary_callbacks_from_module(_legacy_reply_callback_factory_callbacks())


def _reply_postprocess_callbacks():
    return _reply_postprocess_callbacks_from_module(_legacy_reply_callback_factory_callbacks())


def _is_single_store_fact_query(state: AgentState) -> bool:
    return _is_single_store_fact_query_from_quality_signals(state)


def _rejects_more_questions(content: str) -> bool:
    return _rejects_more_questions_from_quality_signals(content)


def _asks_followup_question(text: str) -> bool:
    return _asks_followup_question_from_quality_signals(text)


def _model_reply_unsafe(state: AgentState, messages: list[dict[str, Any]]) -> bool:
    return _model_reply_unsafe_from_quality(state, messages, _reply_quality_callbacks())


def _forced_reply_satisfies_hard_instruction(messages: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    return _forced_reply_safe_from_quality(messages, payload, _reply_quality_callbacks())
def _time_text_variants(time_text: str) -> list[str]:
    return _time_text_variants_from_quality_signals(time_text, _dedupe_strings)


def _too_similar_to_recent_assistant_reply(state: AgentState, text: str) -> bool:
    return _too_similar_to_recent_assistant_reply_from_quality_signals(state, text, _recent_assistant_replies)


def _tool_results_contain(state: AgentState, term: str) -> bool:
    return _tool_results_contain_from_tool_results(state, term, json_dumps)


def _business_project_slices(project_slices: list[dict[str, str]], state: AgentState | None = None) -> list[dict[str, str]]:
    return _business_project_slices_from_context(
        project_slices,
        state,
        known_visible_concerns_from_state=_known_visible_concerns_from_state,
    )


def _legacy_context_guidance_callbacks() -> LegacyContextGuidanceCallbacks:
    return LegacyContextGuidanceCallbacks(
        has_actual_image_context=_has_actual_image_context,
        has_image_concern=_has_image_concern,
        known_visible_concerns_from_state=_known_visible_concerns_from_state,
    )


def _project_guidance_inline(content: str, project: str) -> str:
    return _project_guidance_inline_from_context_guidance(content, project)


def _context_guidance_inline(state: AgentState, content: str, project: str) -> str:
    return _context_guidance_inline_from_context_guidance(
        state,
        content,
        project,
        _legacy_context_guidance_callbacks(),
    )


def _image_guidance_inline(state: AgentState, project: str = "") -> str:
    return _image_guidance_inline_from_context_guidance(
        state,
        project,
        _legacy_context_guidance_callbacks(),
    )


def _project_context_source(state: AgentState) -> str:
    return _project_context_source_from_context_guidance(state, _legacy_context_guidance_callbacks())


def _sanitize_project_direction(direction: str, state: AgentState) -> str:
    return _sanitize_project_direction_from_context_guidance(
        direction,
        state,
        _legacy_context_guidance_callbacks(),
    )


def _memory_context_sentence(state: AgentState) -> str:
    return _memory_context_sentence_from_context_guidance(state)


def _parking_text(store: dict[str, Any]) -> str:
    return _parking_text_from_utils(store)


def _appointment_context_sentence(state: AgentState) -> str:
    return _appointment_context_sentence_from_module(state)


def _should_show_appointment_context(state: AgentState) -> bool:
    return _should_show_appointment_context_from_module(state, _legacy_appointment_message_callbacks())


def _has_explicit_location_or_store(content: str) -> bool:
    return _has_explicit_location_or_store_from_appointment_utils(content, _extract_city)


def _pricing_sql_from_state(state: AgentState) -> str:
    return pricing_sql_for_project(_contextual_price_project(state))


def _legacy_project_context_callbacks() -> LegacyProjectContextCallbacks:
    return LegacyProjectContextCallbacks(
        business_project_slices=_business_project_slices,
        canonical_price_project=_canonical_price_project,
        dedupe_strings=_dedupe_strings,
        extract_project=_extract_project,
        has_image_concern=_has_image_concern,
        project_direction_name_candidates=_project_direction_name_candidates,
        project_slices_from_tool_results=_project_slices_from_tool_results,
        recent_conversation_text=_recent_conversation_text,
    )


def _recent_project_from_state(state: AgentState) -> str:
    return _recent_project_from_project_context(state, _legacy_project_context_callbacks())


def _contextual_price_project(state: AgentState) -> str:
    return _contextual_price_project_from_project_context(state, _legacy_project_context_callbacks())


def _project_direction_names_from_state(state: AgentState) -> list[str]:
    return _project_direction_names_from_project_context(state, _legacy_project_context_callbacks())
