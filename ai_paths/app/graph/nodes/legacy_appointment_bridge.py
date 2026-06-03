from __future__ import annotations

from typing import Any

from app.graph.nodes.appointment_utils import (
    extract_date_value,
    has_explicit_location_or_store as has_explicit_location_or_store_from_module,
)
from app.graph.nodes.common import dedupe_strings, recent_assistant_replies
from app.graph.nodes.intent_signals import (
    has_appointment_change_or_cancel,
    has_appointment_record_query,
)
from app.graph.nodes.legacy_appointment_messages import (
    LegacyAppointmentMessageCallbacks,
    appointment_context_sentence as appointment_context_sentence_from_module,
    should_show_appointment_context as should_show_appointment_context_from_module,
)
from app.graph.nodes.legacy_flow_utils import available_slot_list as available_slot_list_from_utils
from app.graph.nodes.legacy_turn_planning import (
    LegacyTurnPlanningCallbacks,
    has_explicit_appointment_request as has_explicit_appointment_request_from_module,
    should_suspend_active_task_for_current_turn as should_suspend_active_task_from_module,
)
from app.graph.nodes.store_context import extract_city
from app.graph.state import AgentState


def available_slot_list(slots_value: Any) -> list[str]:
    return available_slot_list_from_utils(slots_value, dedupe_strings)


def appointment_message_callbacks() -> LegacyAppointmentMessageCallbacks:
    return LegacyAppointmentMessageCallbacks(
        recent_assistant_replies=recent_assistant_replies,
    )


def appointment_context_sentence(state: AgentState) -> str:
    return appointment_context_sentence_from_module(state)


def should_show_appointment_context(state: AgentState) -> bool:
    return should_show_appointment_context_from_module(state, appointment_message_callbacks())


def turn_planning_callbacks() -> LegacyTurnPlanningCallbacks:
    from app.graph.nodes.reply_summary_context import (
        asks_price_recap,
        asks_store_or_address_recap,
        has_pre_visit_question,
        is_strong_multi_recap_request,
    )

    return LegacyTurnPlanningCallbacks(
        asks_price_recap=asks_price_recap,
        asks_store_or_address_recap=asks_store_or_address_recap,
        extract_date_value=extract_date_value,
        has_appointment_change_or_cancel=has_appointment_change_or_cancel,
        has_appointment_record_query=has_appointment_record_query,
        has_pre_visit_question=has_pre_visit_question,
        is_strong_multi_recap_request=is_strong_multi_recap_request,
    )


def should_suspend_active_task_for_current_turn(
    state: AgentState,
    active_task: dict[str, Any] | None = None,
    intents: list[dict[str, Any]] | None = None,
) -> bool:
    return should_suspend_active_task_from_module(
        state,
        active_task=active_task,
        intents=intents,
        callbacks=turn_planning_callbacks(),
    )


def has_explicit_appointment_request(content: str) -> bool:
    return has_explicit_appointment_request_from_module(content, turn_planning_callbacks())


def has_explicit_location_or_store(content: str) -> bool:
    return has_explicit_location_or_store_from_module(content, extract_city)
