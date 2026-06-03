from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReplyBriefCallbacks:
    appointment_context_sentence: Callable[..., Any]
    asks_price_recap: Callable[..., Any]
    asks_store_or_address_recap: Callable[..., Any]
    available_slot_list: Callable[..., Any]
    canonical_price_project: Callable[..., Any]
    contextual_price_project: Callable[..., Any]
    dedupe_strings: Callable[..., Any]
    extract_city: Callable[..., Any]
    extract_price_digits: Callable[..., Any]
    extract_project: Callable[..., Any]
    filter_pricing_rows_for_project: Callable[..., Any]
    has_actual_image_context: Callable[..., Any]
    has_confirmed_spot_goal: Callable[..., Any]
    has_pre_visit_question: Callable[..., Any]
    has_price_objection: Callable[..., Any]
    is_strong_multi_recap_request: Callable[..., Any]
    known_visible_concerns_from_state: Callable[..., Any]
    memory_context_sentence: Callable[..., Any]
    parking_text: Callable[..., Any]
    price_bits: Callable[..., Any]
    price_fact_for_brief: Callable[..., Any]
    price_summary_message: Callable[..., Any]
    pricing_rows: Callable[..., Any]
    pricing_rows_from_kb: Callable[..., Any]
    project_direction_name_candidates: Callable[..., Any]
    project_slices_from_tool_results: Callable[..., Any]
    should_show_appointment_context: Callable[..., Any]
    store_lookup_missing_city: Callable[..., Any]
    store_summary_message: Callable[..., Any]
    time_text_variants: Callable[..., Any]
