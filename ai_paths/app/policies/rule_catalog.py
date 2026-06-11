from __future__ import annotations

from typing import Any


POLICY_VERSION = "2026-06-11"


TASK_TYPE_POLICY_FAMILY_IDS = {
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

POLICIES: dict[str, dict[str, Any]] = {
    "S1_OPENING_GENERAL": {"family": "S1_OPENING_GENERAL", "task_type": "general_consult"},
    "SF3_PROJECT_NEED_DIRECTION": {"family": "SF3_PROJECT_CONSULT", "task_type": "project_consult"},
    "SF3_PROJECT_DETAIL_EXPLAIN": {"family": "SF3_PROJECT_CONSULT", "task_type": "project_consult"},
    "SF3_PROJECT_UNSUPPORTED_NEED": {"family": "SF3_PROJECT_CONSULT", "task_type": "project_consult"},
    "SF4_IMAGE_VISIBLE_OBSERVATION": {"family": "SF4_IMAGE_CONSULT", "task_type": "image_consult"},
    "CASE_EFFECT_REFERENCE": {"family": "CASE_EFFECT_REFERENCE", "task_type": "case_request"},
    "CASE_EFFECT_TIMES": {"family": "CASE_EFFECT_REFERENCE", "task_type": "case_request"},
    "SF5_COMPETITOR_LOW_PRICE": {"family": "SF5_COMPETITOR_COMPARE", "task_type": "competitor_compare"},
    "SF5_COMPETITOR_HIGH_PRICE": {"family": "SF5_COMPETITOR_COMPARE", "task_type": "competitor_compare"},
    "SF5_COMPETITOR_SAME_PRICE": {"family": "SF5_COMPETITOR_COMPARE", "task_type": "competitor_compare"},
    "SF6_STORE_NEAREST": {"family": "SF6_STORE_INQUIRY", "task_type": "store_inquiry"},
    "SF6_STORE_ADDRESS_DETAIL": {"family": "SF6_STORE_INQUIRY", "task_type": "store_inquiry"},
    "SF6_STORE_BUSINESS_HOURS": {"family": "SF6_STORE_INQUIRY", "task_type": "store_inquiry"},
    "SF6_STORE_PARKING_NAVIGATION": {"family": "SF6_STORE_INQUIRY", "task_type": "store_inquiry"},
    "SF6_STORE_LOCATION_CONFLICT": {"family": "SF6_STORE_INQUIRY", "task_type": "store_inquiry"},
    "SF7_PRICE_FIRST_ASK": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_PRICE_CONFIRM_199": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_PRICE_CONFIRM_268": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_PRICE_ONCE_FEE": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_HIDDEN_FEE_WORRY": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_DEPOSIT_EXPLAIN": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_PAYMENT_TIMING": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_PRICE_DIFFERENCE": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF7_LOWEST_PRICE_HANDOFF": {"family": "SF7_PRICE_ACTIVITY", "task_type": "price_inquiry"},
    "SF8_CAMPAIGN_ACTIVITY": {"family": "SF8_CAMPAIGN_ACTIVITY", "task_type": "campaign_inquiry"},
    "SF9_APPOINTMENT_TIME_CHECK": {"family": "SF9_APPOINTMENT", "task_type": "appointment"},
    "SF9_APPOINTMENT_CREATE_INFO": {"family": "SF9_APPOINTMENT", "task_type": "appointment"},
    "SF9_APPOINTMENT_STATUS": {"family": "SF9_APPOINTMENT_STATUS", "task_type": "appointment_status"},
    "SF9_APPOINTMENT_CHANGE": {"family": "SF9_APPOINTMENT_CHANGE", "task_type": "appointment_change"},
    "SF9_APPOINTMENT_CANCEL": {"family": "SF9_APPOINTMENT_CANCEL", "task_type": "appointment_cancel"},
    "SF10_TRUST_QUALIFICATION": {"family": "SF10_TRUST_BUILD", "task_type": "trust_issue"},
    "SF10_TRUST_HIDDEN_CHARGE": {"family": "SF10_TRUST_BUILD", "task_type": "trust_issue"},
    "SF10_TRUST_EFFECT_WORRY": {"family": "SF10_TRUST_BUILD", "task_type": "trust_issue"},
    "SF10_TRUST_IDENTITY": {"family": "SF10_TRUST_BUILD", "task_type": "trust_issue"},
    "SF10_TRUST_SAFETY_WORRY": {"family": "SF10_TRUST_BUILD", "task_type": "trust_issue"},
    "SF11_EMOTION_SUPPORT": {"family": "SF11_EMOTION_SUPPORT", "task_type": "emotion_chat"},
    "SF12_AFTER_SALES_EFFECT_FEEDBACK": {"family": "SF12_AFTER_SALES", "task_type": "after_sales"},
    "SF12_AFTER_SALES_DISCOMFORT": {"family": "SF12_AFTER_SALES", "task_type": "after_sales"},
    "HUMAN_HANDOFF_PROFESSIONAL_ASSIST": {"family": "HUMAN_HANDOFF", "task_type": "human_request"},
    "HUMAN_HANDOFF_COMPLAINT_REFUND": {"family": "HUMAN_HANDOFF", "task_type": "complaint_refund"},
    "HUMAN_HANDOFF_AFTER_SALES_RISK": {"family": "HUMAN_HANDOFF", "task_type": "after_sales"},
    "GENERAL_DIRECT_REPLY": {"family": "GENERAL_DIRECT_REPLY", "task_type": "general_consult"},
}

SUBTYPE_POLICY_IDS = {
    ("general_consult", "open_consult"): "S1_OPENING_GENERAL",
    ("project_consult", "need_direction"): "SF3_PROJECT_NEED_DIRECTION",
    ("project_consult", "project_detail"): "SF3_PROJECT_DETAIL_EXPLAIN",
    ("project_consult", "unsupported_need"): "SF3_PROJECT_UNSUPPORTED_NEED",
    ("face_consult", "visible_observation"): "SF4_IMAGE_VISIBLE_OBSERVATION",
    ("image_consult", "visible_observation"): "SF4_IMAGE_VISIBLE_OBSERVATION",
    ("case_request", "effect_reference"): "CASE_EFFECT_REFERENCE",
    ("case_request", "effect_times"): "CASE_EFFECT_TIMES",
    ("competitor_compare", "lower_price"): "SF5_COMPETITOR_LOW_PRICE",
    ("competitor_compare", "higher_price"): "SF5_COMPETITOR_HIGH_PRICE",
    ("competitor_compare", "same_price"): "SF5_COMPETITOR_SAME_PRICE",
    ("store_inquiry", "nearest_store"): "SF6_STORE_NEAREST",
    ("store_inquiry", "address_detail"): "SF6_STORE_ADDRESS_DETAIL",
    ("store_inquiry", "business_hours"): "SF6_STORE_BUSINESS_HOURS",
    ("store_inquiry", "parking_navigation"): "SF6_STORE_PARKING_NAVIGATION",
    ("store_inquiry", "location_conflict"): "SF6_STORE_LOCATION_CONFLICT",
    ("price_inquiry", "first_ask"): "SF7_PRICE_FIRST_ASK",
    ("price_inquiry", "confirm_199"): "SF7_PRICE_CONFIRM_199",
    ("price_inquiry", "confirm_268"): "SF7_PRICE_CONFIRM_268",
    ("price_inquiry", "once_fee"): "SF7_PRICE_ONCE_FEE",
    ("price_inquiry", "hidden_fee_worry"): "SF7_HIDDEN_FEE_WORRY",
    ("price_inquiry", "deposit_explain"): "SF7_DEPOSIT_EXPLAIN",
    ("price_inquiry", "payment_timing"): "SF7_PAYMENT_TIMING",
    ("price_inquiry", "price_difference"): "SF7_PRICE_DIFFERENCE",
    ("price_inquiry", "lowest_price"): "SF7_LOWEST_PRICE_HANDOFF",
    ("appointment", "time_check"): "SF9_APPOINTMENT_TIME_CHECK",
    ("appointment", "create_info"): "SF9_APPOINTMENT_CREATE_INFO",
    ("appointment_status", "status_query"): "SF9_APPOINTMENT_STATUS",
    ("appointment_change", "change_time"): "SF9_APPOINTMENT_CHANGE",
    ("appointment_cancel", "cancel_request"): "SF9_APPOINTMENT_CANCEL",
    ("trust_issue", "qualification"): "SF10_TRUST_QUALIFICATION",
    ("trust_issue", "hidden_charge"): "SF10_TRUST_HIDDEN_CHARGE",
    ("trust_issue", "effect_worry"): "SF10_TRUST_EFFECT_WORRY",
    ("trust_issue", "identity"): "SF10_TRUST_IDENTITY",
    ("trust_issue", "safety_worry"): "SF10_TRUST_SAFETY_WORRY",
    ("after_sales", "effect_feedback"): "SF12_AFTER_SALES_EFFECT_FEEDBACK",
    ("after_sales", "discomfort"): "SF12_AFTER_SALES_DISCOMFORT",
}


def policy_id_from_state(state: dict[str, Any]) -> str:
    return policy_selection_from_state(state)["policy_id"]


def policy_family_id_from_state(state: dict[str, Any]) -> str:
    return policy_selection_from_state(state)["policy_family_id"]


def policy_selection_from_state(state: dict[str, Any]) -> dict[str, str]:
    primary_task = state.get("primary_task") if isinstance(state, dict) else {}
    handoff = state.get("handoff") if isinstance(state, dict) else {}
    return policy_selection_from_task(primary_task if isinstance(primary_task, dict) else {}, handoff if isinstance(handoff, dict) else {})


def policy_id_from_task(primary_task: dict[str, Any], handoff: dict[str, Any] | None = None) -> str:
    return policy_selection_from_task(primary_task, handoff)["policy_id"]


def policy_family_id_from_task(primary_task: dict[str, Any], handoff: dict[str, Any] | None = None) -> str:
    return policy_selection_from_task(primary_task, handoff)["policy_family_id"]


def policy_selection_from_task(primary_task: dict[str, Any], handoff: dict[str, Any] | None = None) -> dict[str, str]:
    task_type = _clean_token(primary_task.get("type"))
    task_subtype = _clean_token(primary_task.get("subtype"))
    policy_hint = _clean_token(primary_task.get("policy_hint")).upper()
    handoff = handoff or {}

    if bool(handoff.get("needed")):
        policy_id = HANDOFF_POLICY_IDS.get(task_type) or "HUMAN_HANDOFF_PROFESSIONAL_ASSIST"
        return _selection(policy_id, match_level="hard_guard", exact_policy_id=policy_id)

    if task_subtype == "guardrail_blocked":
        policy_id = HANDOFF_POLICY_IDS.get(task_type) or "HUMAN_HANDOFF_PROFESSIONAL_ASSIST"
        return _selection(policy_id, match_level="hard_guard", exact_policy_id=policy_id)

    if policy_hint in POLICIES:
        return _selection(policy_hint, match_level="exact_policy", exact_policy_id=policy_hint)

    policy_id = SUBTYPE_POLICY_IDS.get((task_type, task_subtype))
    if policy_id:
        return _selection(policy_id, match_level="exact_policy", exact_policy_id=policy_id)

    family_id = TASK_TYPE_POLICY_FAMILY_IDS.get(task_type) or "GENERAL_DIRECT_REPLY"
    match_level = "general_fallback" if family_id == "GENERAL_DIRECT_REPLY" else "family_policy"
    return {
        "policy_id": family_id,
        "policy_family_id": family_id,
        "exact_policy_id": "",
        "policy_match_level": match_level,
        "policy_version": POLICY_VERSION,
    }


def _selection(policy_id: str, *, match_level: str, exact_policy_id: str) -> dict[str, str]:
    definition = POLICIES.get(policy_id) or POLICIES["GENERAL_DIRECT_REPLY"]
    family_id = str(definition.get("family") or policy_id)
    return {
        "policy_id": policy_id,
        "policy_family_id": family_id,
        "exact_policy_id": exact_policy_id,
        "policy_match_level": match_level,
        "policy_version": POLICY_VERSION,
    }


def _clean_token(value: Any) -> str:
    return str(value or "").strip()
