from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.nodes.reply_postprocess import ReplyPostprocessCallbacks
from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.nodes.reply_summary_context import ReplySummaryCallbacks


@dataclass(frozen=True)
class LegacyReplyCallbackFactoryCallbacks:
    ad_price_without_explicit_project: Callable[..., Any]
    appointment_context_sentence: Callable[..., Any]
    asks_followup_question: Callable[..., Any]
    asks_other_store_options: Callable[..., Any]
    asks_price_recap: Callable[..., Any]
    asks_store_or_address_recap: Callable[..., Any]
    available_slot_list: Callable[..., Any]
    business_project_slices: Callable[..., Any]
    canonical_price_project: Callable[..., Any]
    contextual_price_project: Callable[..., Any]
    dedupe_strings: Callable[..., Any]
    extract_city: Callable[..., Any]
    extract_price_digits: Callable[..., Any]
    extract_project: Callable[..., Any]
    filter_pricing_rows_for_project: Callable[..., Any]
    has_actual_image_context: Callable[..., Any]
    has_confirmed_spot_goal: Callable[..., Any]
    has_effect_guarantee_request: Callable[..., Any]
    has_image_concern: Callable[..., Any]
    has_known_image_context: Callable[..., Any]
    has_no_price_fact_phrase: Callable[..., Any]
    has_pre_visit_question: Callable[..., Any]
    has_price_objection: Callable[..., Any]
    is_broad_price_category: Callable[..., Any]
    is_direct_arrival_question: Callable[..., Any]
    is_generic_project_intro: Callable[..., Any]
    is_identity_question: Callable[..., Any]
    is_redundant_known_goal_question: Callable[..., Any]
    is_single_store_fact_query: Callable[..., Any]
    is_strong_multi_recap_request: Callable[..., Any]
    is_unclear_need: Callable[..., Any]
    known_visible_concerns_from_state: Callable[..., Any]
    lacks_price_answer_for_price_question: Callable[..., Any]
    looks_like_store_list_message: Callable[..., Any]
    memory_context_sentence: Callable[..., Any]
    parking_text: Callable[..., Any]
    price_bits: Callable[..., Any]
    price_fact_for_brief: Callable[..., Any]
    price_summary_message: Callable[..., Any]
    pricing_rows: Callable[..., Any]
    pricing_rows_from_kb: Callable[..., Any]
    project_direction_name_candidates: Callable[..., Any]
    project_direction_names_from_state: Callable[..., Any]
    project_slices_from_tool_results: Callable[..., Any]
    recent_assistant_replies: Callable[..., Any]
    rejects_more_questions: Callable[..., Any]
    renumber_messages: Callable[..., Any]
    should_show_appointment_context: Callable[..., Any]
    store_lookup_missing_city: Callable[..., Any]
    store_summary_message: Callable[..., Any]
    time_text_variants: Callable[..., Any]
    too_similar_to_recent_assistant_reply: Callable[..., Any]
    tool_results_contain: Callable[..., Any]


def reply_brief_callbacks(callbacks: LegacyReplyCallbackFactoryCallbacks) -> ReplyBriefCallbacks:
    return ReplyBriefCallbacks(
        appointment_context_sentence=callbacks.appointment_context_sentence,
        asks_price_recap=callbacks.asks_price_recap,
        asks_store_or_address_recap=callbacks.asks_store_or_address_recap,
        available_slot_list=callbacks.available_slot_list,
        canonical_price_project=callbacks.canonical_price_project,
        contextual_price_project=callbacks.contextual_price_project,
        dedupe_strings=callbacks.dedupe_strings,
        extract_city=callbacks.extract_city,
        extract_price_digits=callbacks.extract_price_digits,
        extract_project=callbacks.extract_project,
        filter_pricing_rows_for_project=callbacks.filter_pricing_rows_for_project,
        has_actual_image_context=callbacks.has_actual_image_context,
        has_confirmed_spot_goal=callbacks.has_confirmed_spot_goal,
        has_pre_visit_question=callbacks.has_pre_visit_question,
        has_price_objection=callbacks.has_price_objection,
        is_strong_multi_recap_request=callbacks.is_strong_multi_recap_request,
        known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state,
        memory_context_sentence=callbacks.memory_context_sentence,
        parking_text=callbacks.parking_text,
        price_bits=callbacks.price_bits,
        price_fact_for_brief=callbacks.price_fact_for_brief,
        price_summary_message=callbacks.price_summary_message,
        pricing_rows=callbacks.pricing_rows,
        pricing_rows_from_kb=callbacks.pricing_rows_from_kb,
        project_direction_name_candidates=callbacks.project_direction_name_candidates,
        project_slices_from_tool_results=callbacks.project_slices_from_tool_results,
        should_show_appointment_context=callbacks.should_show_appointment_context,
        store_lookup_missing_city=callbacks.store_lookup_missing_city,
        store_summary_message=callbacks.store_summary_message,
        time_text_variants=callbacks.time_text_variants,
    )


def reply_quality_callbacks(callbacks: LegacyReplyCallbackFactoryCallbacks) -> ReplyQualityCallbacks:
    return ReplyQualityCallbacks(
        ad_price_without_explicit_project=callbacks.ad_price_without_explicit_project,
        asks_followup_question=callbacks.asks_followup_question,
        asks_other_store_options=callbacks.asks_other_store_options,
        available_slot_list=callbacks.available_slot_list,
        canonical_price_project=callbacks.canonical_price_project,
        contextual_price_project=callbacks.contextual_price_project,
        extract_city=callbacks.extract_city,
        extract_price_digits=callbacks.extract_price_digits,
        extract_project=callbacks.extract_project,
        has_actual_image_context=callbacks.has_actual_image_context,
        has_confirmed_spot_goal=callbacks.has_confirmed_spot_goal,
        has_effect_guarantee_request=callbacks.has_effect_guarantee_request,
        has_image_concern=callbacks.has_image_concern,
        has_known_image_context=callbacks.has_known_image_context,
        has_no_price_fact_phrase=callbacks.has_no_price_fact_phrase,
        has_price_objection=callbacks.has_price_objection,
        is_broad_price_category=callbacks.is_broad_price_category,
        is_direct_arrival_question=callbacks.is_direct_arrival_question,
        is_generic_project_intro=callbacks.is_generic_project_intro,
        is_identity_question=callbacks.is_identity_question,
        is_single_store_fact_query=callbacks.is_single_store_fact_query,
        is_strong_multi_recap_request=callbacks.is_strong_multi_recap_request,
        is_unclear_need=callbacks.is_unclear_need,
        known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state,
        lacks_price_answer_for_price_question=callbacks.lacks_price_answer_for_price_question,
        project_direction_names_from_state=callbacks.project_direction_names_from_state,
        rejects_more_questions=callbacks.rejects_more_questions,
        should_show_appointment_context=callbacks.should_show_appointment_context,
        time_text_variants=callbacks.time_text_variants,
        too_similar_to_recent_assistant_reply=callbacks.too_similar_to_recent_assistant_reply,
        tool_results_contain=callbacks.tool_results_contain,
    )


def reply_summary_callbacks(callbacks: LegacyReplyCallbackFactoryCallbacks) -> ReplySummaryCallbacks:
    return ReplySummaryCallbacks(
        recent_assistant_replies=callbacks.recent_assistant_replies,
        canonical_price_project=callbacks.canonical_price_project,
        contextual_price_project=callbacks.contextual_price_project,
        extract_project=callbacks.extract_project,
        filter_pricing_rows_for_project=callbacks.filter_pricing_rows_for_project,
        pricing_rows_from_kb=callbacks.pricing_rows_from_kb,
        pricing_rows=callbacks.pricing_rows,
        price_bits=callbacks.price_bits,
        business_project_slices=callbacks.business_project_slices,
        project_slices_from_tool_results=callbacks.project_slices_from_tool_results,
        project_direction_name_candidates=callbacks.project_direction_name_candidates,
        dedupe_strings=callbacks.dedupe_strings,
    )


def reply_postprocess_callbacks(callbacks: LegacyReplyCallbackFactoryCallbacks) -> ReplyPostprocessCallbacks:
    return ReplyPostprocessCallbacks(
        contextual_price_project=callbacks.contextual_price_project,
        has_actual_image_context=callbacks.has_actual_image_context,
        has_confirmed_spot_goal=callbacks.has_confirmed_spot_goal,
        has_known_image_context=callbacks.has_known_image_context,
        has_price_objection=callbacks.has_price_objection,
        is_redundant_known_goal_question=callbacks.is_redundant_known_goal_question,
        looks_like_store_list_message=callbacks.looks_like_store_list_message,
        renumber_messages=callbacks.renumber_messages,
        should_show_appointment_context=callbacks.should_show_appointment_context,
    )
