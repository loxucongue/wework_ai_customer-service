from __future__ import annotations


ALLOWED_TOOLS = (
    "kb_search",
    "customer_store_lookup",
    "distance_calculate",
    "available_time",
    "appointment_record_query",
    "professional_assist",
    "no_tool",
)

ALLOWED_KBS = (
    "case_studies",
)

ALLOWED_CONVERSION_STAGES = (
    "interest_capture",
    "objection_resolution",
    "store_match",
    "time_confirm",
    "deposit_push",
)

ALLOWED_CUSTOMER_TYPES = (
    "price",
    "effect",
    "distance",
    "time",
    "risk",
    "accompany",
    "unknown",
)

ALLOWED_MAIN_BLOCKERS = (
    "price",
    "effect",
    "distance",
    "time",
    "risk",
    "trust",
    "logistics",
    "none",
)

ALLOWED_NEXT_STEPS = (
    "ask_intent",
    "solve_blocker",
    "lookup_store",
    "confirm_time",
    "send_deposit",
    "no_action",
)

SHORT_GREETING_TOKENS = {
    "你好",
    "您好",
    "在吗",
    "有人吗",
    "哈喽",
    "嗨",
    "好",
    "好的",
    "可以",
}
