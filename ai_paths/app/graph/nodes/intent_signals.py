from __future__ import annotations

from app.graph.signals.dispute import recent_conversation_text
from app.graph.signals.project import (
    has_ad_price_check,
    has_advantage_question,
    has_case_request,
    has_effect_guarantee_request,
    has_price_objection,
    has_project_consult_intent,
    has_project_process_question,
)
from app.graph.signals.store import (
    has_appointment_change_or_cancel,
    has_appointment_record_query,
    has_store_inquiry,
)

__all__ = [
    "has_ad_price_check",
    "has_advantage_question",
    "has_appointment_change_or_cancel",
    "has_appointment_record_query",
    "has_case_request",
    "has_effect_guarantee_request",
    "has_price_objection",
    "has_project_consult_intent",
    "has_project_process_question",
    "has_store_inquiry",
    "recent_conversation_text",
]
