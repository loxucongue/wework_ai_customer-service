from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReplyQualityCallbacks:
    ad_price_without_explicit_project: Callable[..., Any]
    asks_followup_question: Callable[..., Any]
    asks_other_store_options: Callable[..., Any]
    available_slot_list: Callable[..., Any]
    canonical_price_project: Callable[..., Any]
    contextual_price_project: Callable[..., Any]
    extract_city: Callable[..., Any]
    extract_price_digits: Callable[..., Any]
    extract_project: Callable[..., Any]
    has_actual_image_context: Callable[..., Any]
    has_confirmed_spot_goal: Callable[..., Any]
    has_effect_guarantee_request: Callable[..., Any]
    has_image_concern: Callable[..., Any]
    has_known_image_context: Callable[..., Any]
    has_no_price_fact_phrase: Callable[..., Any]
    has_price_objection: Callable[..., Any]
    is_broad_price_category: Callable[..., Any]
    is_direct_arrival_question: Callable[..., Any]
    is_generic_project_intro: Callable[..., Any]
    is_identity_question: Callable[..., Any]
    is_single_store_fact_query: Callable[..., Any]
    is_strong_multi_recap_request: Callable[..., Any]
    is_unclear_need: Callable[..., Any]
    known_visible_concerns_from_state: Callable[..., Any]
    lacks_price_answer_for_price_question: Callable[..., Any]
    project_direction_names_from_state: Callable[..., Any]
    rejects_more_questions: Callable[..., Any]
    should_show_appointment_context: Callable[..., Any]
    time_text_variants: Callable[..., Any]
    too_similar_to_recent_assistant_reply: Callable[..., Any]
    tool_results_contain: Callable[..., Any]
