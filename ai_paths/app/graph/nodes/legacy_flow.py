from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.action_queries import (
    ActionQueryCallbacks,
    safe_query_from_state as _safe_query_from_action_queries,
)
from app.graph.nodes.appointment_utils import (
    extract_date_value as _extract_date_value,
    has_explicit_location_or_store as _has_explicit_location_or_store_from_appointment_utils,
)
from app.graph.nodes.common import (
    dedupe_strings as _dedupe_strings,
    json_dumps,
    looks_bad_text as _looks_bad_text,
    recent_assistant_replies as _recent_assistant_replies,
    renumber_messages as _renumber,
)
from app.graph.nodes.guardrail_nodes import is_identity_question as _is_identity_question
from app.graph.nodes.image_info import (
    has_image_concern as _has_image_concern,
    has_actual_image_context as _has_actual_image_context,
    has_known_image_context as _has_known_image_context,
    known_visible_concerns_from_state as _known_visible_concerns_from_state,
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
    has_appointment_change_or_cancel as _has_appointment_change_or_cancel,
    has_appointment_record_query as _has_appointment_record_query,
    has_effect_guarantee_request as _has_effect_guarantee_request,
    has_price_objection as _has_price_objection,
    is_generic_project_intro as _is_generic_project_intro,
    is_unclear_need as _is_unclear_need,
    recent_conversation_text as _recent_conversation_text,
)
from app.graph.nodes.kb_slice_parsing import (
    pricing_rows_from_kb as _pricing_rows_from_kb,
)
from app.graph.nodes.project_kb_context import (
    business_project_slices as _business_project_slices_from_context,
    project_direction_name_candidates as _project_direction_name_candidates,
    project_slices_from_tool_results as _project_slices_from_tool_results,
)
from app.graph.nodes.legacy_project_context import (
    LegacyProjectContextCallbacks,
    contextual_price_project as _contextual_price_project_from_project_context,
    project_direction_names_from_state as _project_direction_names_from_project_context,
    recent_project_from_state as _recent_project_from_project_context,
)
from app.graph.nodes.legacy_qa_slice_context import (
    clean_after_sales_text as _clean_after_sales_text_from_qa_context,
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
from app.graph.nodes.legacy_turn_planning import (
    LegacyTurnPlanningCallbacks,
    has_explicit_appointment_request as _has_explicit_appointment_request_from_module,
    should_suspend_active_task_for_current_turn as _should_suspend_active_task_for_current_turn_from_module,
)
from app.graph.nodes.legacy_tool_results import (
    merge_kb_result as _merge_kb_result_from_tool_results,
    tool_results_contain as _tool_results_contain_from_tool_results,
)
from app.graph.nodes.pricing_context import (
    canonical_price_project as _canonical_price_project,
    extract_project as _extract_project,
    filter_pricing_rows_for_project as _filter_pricing_rows_for_project,
    is_broad_price_category as _is_broad_price_category,
    price_bits as _price_bits,
    price_fact_for_brief as _price_fact_for_brief,
    pricing_rows as _pricing_rows,
    pricing_sql_for_project,
)
from app.graph.nodes.reply_context import (
    store_lookup_missing_city as _store_lookup_missing_city,
)
from app.graph.nodes.result_compaction import ad_price_without_explicit_project as _ad_price_without_explicit_project
from app.graph.nodes.reply_payloads import (
    is_direct_arrival_question as _is_direct_arrival_question,
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
from app.graph.nodes.store_context import (
    extract_city as _extract_city,
)
from app.graph.state import AgentState

def _compact_memory(memory: dict[str, Any]) -> dict[str, Any]:
    return _compact_memory_from_utils(memory)


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


def _extract_price_digits(content: str) -> list[str]:
    return _extract_price_digits_from_utils(content)


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
