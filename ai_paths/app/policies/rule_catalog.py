from __future__ import annotations

from typing import Any


POLICY_VERSION = "2026-06-11"

TASK_TYPE_POLICY_IDS = {
    "general_consult": "S1_OPENING_GENERAL",
    "project_consult": "SF3_PROJECT_CONSULT",
    "face_consult": "SF4_IMAGE_CONSULT",
    "image_consult": "SF4_IMAGE_CONSULT",
    "case_request": "CASE_EFFECT_REFERENCE",
    "competitor_compare": "SF5_COMPETITOR_COMPARE",
    "store_inquiry": "SF6_STORE_INQUIRY",
    "price_inquiry": "SF7_PRICE_ACTIVITY",
    "campaign_inquiry": "SF8_CAMPAIGN_ACTIVITY",
    "appointment": "SF9_APPOINTMENT",
    "appointment_status": "SF9_APPOINTMENT_STATUS",
    "appointment_change": "SF9_APPOINTMENT_CHANGE",
    "appointment_cancel": "SF9_APPOINTMENT_CANCEL",
    "trust_issue": "SF10_TRUST_BUILD",
    "emotion_chat": "SF11_EMOTION_SUPPORT",
    "after_sales": "SF12_AFTER_SALES",
    "human_request": "HUMAN_HANDOFF_PROFESSIONAL_ASSIST",
    "complaint_refund": "HUMAN_HANDOFF_COMPLAINT_REFUND",
}

HANDOFF_POLICY_IDS = {
    "complaint_refund": "HUMAN_HANDOFF_COMPLAINT_REFUND",
    "after_sales": "HUMAN_HANDOFF_AFTER_SALES_RISK",
    "human_request": "HUMAN_HANDOFF_PROFESSIONAL_ASSIST",
}


def policy_id_from_state(state: dict[str, Any]) -> str:
    primary_task = state.get("primary_task") if isinstance(state, dict) else {}
    handoff = state.get("handoff") if isinstance(state, dict) else {}
    return policy_id_from_task(primary_task if isinstance(primary_task, dict) else {}, handoff if isinstance(handoff, dict) else {})


def policy_id_from_task(primary_task: dict[str, Any], handoff: dict[str, Any] | None = None) -> str:
    task_type = str(primary_task.get("type") or "").strip()
    task_subtype = str(primary_task.get("subtype") or "").strip()
    handoff = handoff or {}

    if bool(handoff.get("needed")):
        return HANDOFF_POLICY_IDS.get(task_type) or "HUMAN_HANDOFF_PROFESSIONAL_ASSIST"

    if task_subtype == "guardrail_blocked":
        return HANDOFF_POLICY_IDS.get(task_type) or "HUMAN_HANDOFF_PROFESSIONAL_ASSIST"

    return TASK_TYPE_POLICY_IDS.get(task_type) or "GENERAL_DIRECT_REPLY"

