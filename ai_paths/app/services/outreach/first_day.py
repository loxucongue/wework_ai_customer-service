from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.config import get_settings
from app.services.customer_context import CustomerContextService
from app.services.customer_relation import (
    customer_relation_is_deleted,
    normalize_customer_relation,
)
from app.services.customer_scope import build_customer_scope
from app.services.coze_client import CozeClient
from app.services.model_client import ModelClient
from app.services.payment_collection import unanswered_payment_collection
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.sales_strategy_service import SalesStrategyService
from app.services.outreach_first_day_prompts import (
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION,
    FIRST_DAY_PLAN_WRITER_PROMPT,
    FIRST_DAY_PLAN_WRITER_PROMPT_VERSION,
    FIRST_DAY_SCENE_ANALYST_PROMPT,
    FIRST_DAY_SCENE_ANALYST_PROMPT_VERSION,
    FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT,
    FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT_VERSION,
)
from app.services.outreach_assets import (
    appointment_blocker_materials,
    asset_reply_message,
    build_appointment_blocker_asset_catalog,
    build_appointment_blocker_scene_index,
    enrich_recent_outreach_media,
    recent_outreach_media,
    resolve_case_asset,
    resolve_configured_asset,
)
from app.services.outreach_prompts import (
    OUTREACH_MESSAGE_SYSTEM_PROMPT,
    OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT,
    OUTREACH_PLAN_SCHEMA_REPAIR_SYSTEM_PROMPT,
    OUTREACH_PLAN_SYSTEM_PROMPT,
    S10_OUTREACH_CONTEXT,
)
from app.services.outreach_system_client import OutreachSystemClient
from app.services.sop_platform_task_policy import (
    personalized_order_eligibility,
    personalized_payment_collection_eligibility,
)
from app.services.storage import AppRepository
from app.services.storage.serialization import dumps, utc_now_iso


OUTREACH_PERSUASION_ANGLES = {
    "education",
    "proof",
    "professionalism",
    "empathy",
    "self_image",
    "convenience",
    "scarcity",
    "low_risk_action",
}
OUTREACH_ASSET_STRATEGIES = {"none", "configured_image", "operation_video", "case_search"}
OUTREACH_CONTENT_MODES = {"value_only", "soft_conversion", "transaction"}
OUTREACH_URGENCY_LEVELS = {"immediate", "same_day", "normal", "slow"}
OUTREACH_NO_REPLY_ACTIONS = {"advance_to_next_step", "end_plan"}
OUTREACH_FIRST_STEP_MAX_MINUTES = 12 * 60
OUTREACH_MIN_STEP_GAP_MINUTES = 6 * 60
OUTREACH_MAX_STEP_GAP_MINUTES = 72 * 60
OUTREACH_MAX_PLAN_MINUTES = 7 * 24 * 60
OUTREACH_DAILY_TASK_LIMIT = 2
FIRST_DAY_DAILY_PLAN_LIMIT = 2
FIRST_DAY_DAILY_TASK_LIMIT = FIRST_DAY_DAILY_PLAN_LIMIT * 2
OUTREACH_BEIJING_TIMEZONE = timezone(timedelta(hours=8))
FIRST_DAY_WINDOW_MINUTES = 24 * 60
FIRST_DAY_SILENCE_TRIGGER_TYPE = "first_day_opened_silence"
FIRST_DAY_SOP_PLAN_ID = "first_day_opened_silence"
FIRST_DAY_STALE_RUNNING_RETRY_MINUTES = 15
FIRST_DAY_RETRYABLE_SOFT_BLOCK_REASONS = {
    "customer_never_spoke",
}
FIRST_DAY_NON_RETRYABLE_RUN_REASONS = {
    "customer_deleted",
    "first_day_daily_plan_limit_reached",
    "outreach_cycle_completed_without_new_customer_reply",
    "customer_replied",
    "order_state_changed",
    "health_risk",
    "stop_contact",
    "manual_takeover_active",
}
FIRST_DAY_SCENES = {
    "store_area_request",
    "effect_proof",
    "activity_intro",
    "objection_resolution",
    "deposit_close",
    "trust_repair",
    "health_hold",
    "suppress",
}
FIRST_DAY_COMPLETION_SCENES = {
    "store_area_request",
    "effect_proof",
    "activity_intro",
    "objection_resolution",
    "deposit_close",
    "trust_repair",
}
FIRST_DAY_COMPLETION_STATUSES = {"completed", "partial", "not_delivered", "not_applicable"}
FIRST_DAY_HARD_BOUNDARY_TYPES = {
    "health_risk",
    "paid",
    "booked",
    "complaint_refund",
    "deleted_relation",
    "manual_takeover",
    "stop_contact",
    "unreliable_conversation",
}
FIRST_DAY_PRECEDENCE_ROWS = {
    "hard_boundary",
    "no_blocker_sop_progression",
    "effect_saturated",
    "effect_need",
    "symptom_without_effect_proof",
    "payment_intent",
    "effect_to_activity",
    "store_to_effect",
    "distance_after_store",
    "time_deposit_objection",
    "distance_soft_objection",
    "out_of_scope_pullback",
    "consider_after_full_pitch",
    "city_store_question",
    "full_funnel_payment_blocked",
    "freeform",
}
FIRST_DAY_SOP_SCENE_BY_CATEGORY = {
    "s10_need_and_case": "effect_proof",
    "s10_activity_intro": "activity_intro",
    "s10_objection_resolution": "objection_resolution",
    "store_prompt": "store_area_request",
    "effect_case": "effect_proof",
    "activity_intro": "activity_intro",
    "price_quote": "activity_intro",
    "deposit_push": "deposit_close",
    "payment_followup": "deposit_close",
    "operation_video": "effect_proof",
    "final_close": "deposit_close",
}
FIRST_DAY_SCENE_ALIASES = {
    **FIRST_DAY_SOP_SCENE_BY_CATEGORY,
    "s10_activity_intro": "activity_intro",
    "s10_need_and_case": "effect_proof",
    "s10_deposit_close": "deposit_close",
    "s10_objection_resolution": "objection_resolution",
    "activity": "activity_intro",
    "quote": "activity_intro",
    "price": "activity_intro",
    "price_intro": "activity_intro",
    "case": "effect_proof",
    "case_study": "effect_proof",
    "effect": "effect_proof",
    "store": "store_area_request",
    "store_request": "store_area_request",
    "deposit": "deposit_close",
    "payment": "deposit_close",
    "payment_collection": "deposit_close",
    "objection": "objection_resolution",
    "trust": "trust_repair",
    "risk": "health_hold",
    "health": "health_hold",
}
FIRST_DAY_SOP_CATEGORY_ORDER = {
    "s10_need_and_case": 20,
    "effect_case": 20,
    "s10_activity_intro": 30,
    "activity_intro": 30,
    "price_quote": 30,
    "store_prompt": 35,
    "s10_objection_resolution": 40,
    "deposit_push": 50,
    "payment_followup": 50,
    "operation_video": 60,
    "final_close": 70,
}
FIRST_DAY_ACTIVITY_SOP_CATEGORIES = {
    "s10_activity_intro",
    "activity_intro",
    "price_quote",
}
FIRST_DAY_REPEAT_SIMILARITY_LIMIT = 0.85
FIRST_DAY_GENDERED_TERMS = (
    "女孩子",
    "美女",
    "姐妹",
    "女士",
    "先生",
    "帅哥",
    "哥哥",
    "姐姐",
    "妹妹",
    "男士",
)
FIRST_DAY_PROCESS_TAIL_TERMS = (
    "回我",
    "回复我",
    "回一句",
    "回复一个",
    "回复关键词",
    "想看就回",
    "如果想继续了解",
    "如果您想继续了解",
    "如果您想",
    "如果你想",
    "如果需要",
    "如果您需要",
    "如果你需要",
    "想继续看",
    "我再发",
    "我可以继续",
    "我也可以",
    "可以继续给您",
    "继续给您说",
    "给您说下",
    "给您说一下",
    "给您讲下",
    "给您讲一下",
    "我先把活动信息发",
    "先不打扰",
    "慢慢看",
    "方便时再说",
    "想继续了解时",
    "按您目前的情况先放着",
    "支付条件方便了",
    "后面想继续",
    "以后需要再",
)
FIRST_DAY_UNSUPPORTED_STORE_ACTIONS = (
    "把到店路径接上",
    "到店路径接上",
    "帮您查",
    "帮您匹配",
    "给您匹配",
    "匹配最近",
    "帮您看位置",
    "帮您看下",
    "给您推荐",
    "推荐附近",
    "按附近看",
    "往就近",
    "帮您看怎么方便",
    "缩小到更近",
    "缩小到最近",
    "最近的门店",
    "更近的门店",
    "帮您看怎么安排",
    "我直接帮您看",
    "给您对接",
)
OUTREACH_DURABLE_EVENT_TYPES = {
    "voice_transcript_received",
    "image_facts_received",
    "store_matched",
    "store_address_sent",
    "store_confirmed",
    "case_image_sent",
    "activity_intro_image_sent",
    "sop_pack_sent",
    "activity_quote_completed",
    "offer_explained",
    "payment_collection_sent",
    "deposit_payment_confirmed",
    "appointment_confirmed",
    "customer_relation_changed",
    "complaint_or_refund_risk",
    "health_risk",
    "manual_handoff",
    "stop_contact_confirmed",
}


class OutreachMessagePolicyError(RuntimeError):
    pass


def classify_conversation_refresh_error(error: Exception | str) -> tuple[str, str]:
    detail = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error or "")
    normalized = detail.lower()
    if "40401" in normalized or "account not found" in normalized:
        return (
            "conversation_account_not_found",
            "平台未找到当前客服账号，已显示本地缓存记录；请检查企微账号配置。",
        )
    if "401" in normalized or "403" in normalized or "unauthorized" in normalized or "forbidden" in normalized:
        return (
            "conversation_refresh_unauthorized",
            "平台聊天记录接口鉴权失败，已显示本地缓存记录。",
        )
    if any(
        marker in normalized
        for marker in ("readtimeout", "connecttimeout", "pooltimeout", "timeouterror", "timeout")
    ):
        return (
            "conversation_refresh_timeout",
            "平台历史聊天查询超时，已显示本地缓存记录。",
        )
    return (
        "conversation_refresh_failed",
        "平台历史聊天查询失败，已显示本地缓存记录。",
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _message_time_iso(value: Any) -> str:
    raw = _string(value)
    if not raw:
        return ""
    if raw.isdigit():
        number = int(raw)
        if number > 10_000_000_000:
            number = number // 1000
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    parsed = _parse_iso(raw)
    return parsed.isoformat() if parsed else raw


def _conversation_activity_from_context(
    *,
    existing: dict[str, Any] | None,
    memory: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    activity = dict(existing or {})
    memory_fact = dict(memory or {})
    messages = list(recent_messages or [])

    latest_customer_text = _string(
        activity.get("latest_customer_message_at")
        or memory_fact.get("last_customer_message_at")
    )
    latest_staff_text = _string(
        activity.get("latest_staff_message_at")
        or memory_fact.get("last_staff_message_at")
        or memory_fact.get("last_ai_reply_at")
    )
    customer_count = 0
    for message in messages:
        direction = _string(
            message.get("direction")
            or message.get("from")
            or message.get("sender_type")
        ).lower()
        message_time_text = _message_time_iso(
            message.get("msgtime")
            or message.get("timestamp")
            or message.get("created_at")
        )
        message_time = _parse_iso(message_time_text)
        if direction in {"customer", "user", "external"}:
            customer_count += 1
            known_customer_time = _parse_iso(_message_time_iso(latest_customer_text))
            if message_time and (known_customer_time is None or message_time > known_customer_time):
                latest_customer_text = message_time_text
        elif direction in {"staff", "assistant", "ai", "employee"}:
            known_staff_time = _parse_iso(_message_time_iso(latest_staff_text))
            if message_time and (known_staff_time is None or message_time > known_staff_time):
                latest_staff_text = message_time_text

    latest_customer = _parse_iso(_message_time_iso(latest_customer_text))
    latest_staff = _parse_iso(_message_time_iso(latest_staff_text))
    awaiting_customer_reply = bool(
        latest_staff and (latest_customer is None or latest_staff > latest_customer)
    )
    if "reply_wait_minutes" not in activity or activity.get("reply_wait_minutes") in {None, ""}:
        current = now or datetime.now(timezone.utc)
        activity["reply_wait_minutes"] = (
            max(0, int((current - latest_staff.astimezone(timezone.utc)).total_seconds() // 60))
            if awaiting_customer_reply and latest_staff
            else 0
        )
    if "customer_silence_minutes" not in activity or activity.get("customer_silence_minutes") in {
        None,
        "",
    }:
        current = now or datetime.now(timezone.utc)
        activity["customer_silence_minutes"] = (
            max(0, int((current - latest_customer.astimezone(timezone.utc)).total_seconds() // 60))
            if latest_customer
            else 0
        )
    activity.setdefault("latest_customer_message_at", _message_time_iso(latest_customer_text))
    activity.setdefault("latest_staff_message_at", _message_time_iso(latest_staff_text))
    activity.setdefault("real_customer_message_count", customer_count)
    activity.setdefault("awaiting_customer_reply", awaiting_customer_reply)
    return activity


def _dedupe_outreach_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        key = (
            _string(candidate.get("corp_id")).lower(),
            _string(candidate.get("wechat")).lower(),
            _string(candidate.get("external_userid")).lower(),
            _string(candidate.get("customer_id")),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def _missing_outreach_identity_fields(identity: dict[str, Any]) -> list[str]:
    return [
        key
        for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat")
        if not _string(identity.get(key))
    ]


def _first_day_wechat_allowlist(value: str | None) -> set[str]:
    return {
        token.strip().lower()
        for token in re.split(r"[,;\s]+", _string(value))
        if token.strip()
    }


def _first_day_wechat_allowed(wechat: str, allowlist: str | None) -> bool:
    allowed = _first_day_wechat_allowlist(allowlist)
    if not allowed:
        return True
    return _string(wechat).lower() in allowed


def _terminal_outreach_send_failure_reason(error: str) -> str:
    normalized = _string(error).lower()
    if "invalid_outreach_identity" in normalized:
        return "invalid_outreach_identity"
    if "outreach_system_http_422" in normalized or "contract validation failed" in normalized:
        return "send_contract_validation_failed"
    if "outreach_system_http_409" in normalized:
        if "manual handoff" in normalized or "ai_mode_manual" in normalized:
            return "manual_handoff_active"
        if "outside enabled ai scope" in normalized or "40908" in normalized:
            return "ai_outreach_scope_blocked"
        return "outreach_system_conflict"
    return ""


def _conversation_fingerprint(
    *,
    corp_id: str,
    wechat: str,
    external_userid: str,
    customer_id: str,
    latest_customer_message_at: str,
    latest_staff_message_at: str,
) -> str:
    raw = "|".join(
        (
            _string(corp_id).lower(),
            _string(wechat).lower(),
            _string(external_userid).lower(),
            _string(customer_id),
            _message_time_iso(latest_customer_message_at),
            _message_time_iso(latest_staff_message_at),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _conversation_response_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _authoritative_first_added_at(payload: Any) -> str:
    data = _conversation_response_data(payload)
    friend_added_event = data.get("friend_added_event")
    if isinstance(friend_added_event, dict):
        added_at = _string(friend_added_event.get("added_at"))
        if added_at:
            return added_at
    return _string(data.get("added_at"))


def _conversation_id_from_response(payload: Any) -> str:
    return _string(_conversation_response_data(payload).get("conversation_id"))


def _first_day_full_retry_delay_seconds(error: str, retry_count: int) -> int | None:
    normalized = _string(error).lower()
    if "first_day_scene_analysis_invalid" in normalized:
        return 60 if retry_count < 1 else None
    transient = any(
        marker in normalized
        for marker in ("timeout", "timed out", "http_500", "http_502", "http_503", "http_504", "5xx")
    )
    if not transient:
        return None
    delays = (60, 300)
    return delays[retry_count] if retry_count < len(delays) else None


def _completed_cycle_blocks_automatic_replan(
    completed_plan: dict[str, Any],
    *,
    latest_customer_message_at: str,
) -> bool:
    if not completed_plan:
        return False
    completed_at = _parse_iso(_string(completed_plan.get("completed_at")))
    latest_customer_at = _parse_iso(_string(latest_customer_message_at))
    if not completed_at:
        return False
    return latest_customer_at is None or latest_customer_at <= completed_at


def _is_second_beijing_day(contact_started_at: str, *, now: datetime | None = None) -> bool:
    started = _parse_iso(_string(contact_started_at))
    if not started:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(OUTREACH_BEIJING_TIMEZONE)
    return current.date() > started.astimezone(OUTREACH_BEIJING_TIMEZONE).date()


def _is_within_first_day(contact_started_at: str, *, now: datetime | None = None) -> bool:
    started = _parse_iso(_string(contact_started_at))
    if not started:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_minutes = (current - started.astimezone(timezone.utc)).total_seconds() / 60
    return 0 <= age_minutes <= FIRST_DAY_WINDOW_MINUTES


def _add_minutes(value: str, minutes: int) -> str:
    start = _parse_iso(value) or datetime.now(timezone.utc)
    return (start + timedelta(minutes=max(0, int(minutes)))).isoformat()


def _scheduled_at_for_strategy_step(
    value: str,
    step: dict[str, Any],
    *,
    appointment_at: str = "",
) -> str:
    start = _parse_iso(value) or datetime.now(timezone.utc)
    trigger = _string(step.get("trigger_base"))
    if trigger in {"same_day_18_00", "same_day_20_00"}:
        hour = 18 if trigger == "same_day_18_00" else 20
        scheduled = start.astimezone(OUTREACH_BEIJING_TIMEZONE).replace(
            hour=hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if scheduled <= start.astimezone(OUTREACH_BEIJING_TIMEZONE):
            scheduled += timedelta(days=1)
        return scheduled.astimezone(timezone.utc).isoformat()
    if trigger == "appointment_previous_day_20_00":
        appointment = _parse_iso(appointment_at)
        if appointment is None:
            return ""
        scheduled = appointment.astimezone(OUTREACH_BEIJING_TIMEZONE).replace(
            hour=20,
            minute=0,
            second=0,
            microsecond=0,
        ) - timedelta(days=1)
        return scheduled.astimezone(timezone.utc).isoformat()
    return _add_minutes(value, _int(step.get("delay_minutes"), 0))


def _selected_strategy_steps(
    response: dict[str, Any],
    strategy: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not strategy:
        return []
    selected_keys = set(_list_strings(response.get("selected_step_keys")))
    if not selected_keys:
        return []
    return [
        item
        for item in strategy.get("steps") or []
        if isinstance(item, dict) and _string(item.get("step_key")) in selected_keys
    ][:3]


def _selected_strategy(
    response: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    selected_key = _string(response.get("selected_strategy_key"))
    return next(
        (
            item
            for item in candidates
            if _string(item.get("strategy_key")) == selected_key
        ),
        None,
    )


def _shift_outreach_quiet_hours(value: datetime) -> datetime:
    local = value.astimezone(OUTREACH_BEIJING_TIMEZONE)
    if local.hour >= 22:
        local = (local + timedelta(days=1)).replace(hour=8, minute=30, second=0, microsecond=0)
    elif local.hour < 8:
        local = local.replace(hour=8, minute=30, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def _next_outreach_day_start(value: datetime | None = None) -> datetime:
    current = (value or datetime.now(timezone.utc)).astimezone(OUTREACH_BEIJING_TIMEZONE)
    return (current + timedelta(days=1)).replace(
        hour=8,
        minute=30,
        second=0,
        microsecond=0,
    ).astimezone(timezone.utc)


def _normalize_outreach_schedule(
    start_at: str,
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start = _parse_iso(start_at) or datetime.now(timezone.utc)
    start = start.astimezone(timezone.utc)
    plan_deadline = start + timedelta(minutes=OUTREACH_MAX_PLAN_MINUTES)
    previous = start
    per_day_counts: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []

    for index, step in enumerate(steps):
        requested_delay = _int(step.get("delay_minutes"), 0)
        if index == 0:
            requested_delay = min(max(requested_delay, 0), OUTREACH_FIRST_STEP_MAX_MINUTES)
            target = start + timedelta(minutes=requested_delay)
        else:
            requested_target = start + timedelta(minutes=max(0, requested_delay))
            earliest = previous + timedelta(minutes=OUTREACH_MIN_STEP_GAP_MINUTES)
            latest = previous + timedelta(minutes=OUTREACH_MAX_STEP_GAP_MINUTES)
            target = min(max(requested_target, earliest), latest)

        target = _shift_outreach_quiet_hours(min(target, plan_deadline))
        local_day = target.astimezone(OUTREACH_BEIJING_TIMEZONE).date().isoformat()
        if per_day_counts.get(local_day, 0) >= OUTREACH_DAILY_TASK_LIMIT:
            next_local = target.astimezone(OUTREACH_BEIJING_TIMEZONE) + timedelta(days=1)
            target = next_local.replace(hour=8, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
            target = min(target, plan_deadline)
            target = _shift_outreach_quiet_hours(target)
            local_day = target.astimezone(OUTREACH_BEIJING_TIMEZONE).date().isoformat()

        per_day_counts[local_day] = per_day_counts.get(local_day, 0) + 1
        normalized_delay = max(0, int((target - start).total_seconds() // 60))
        normalized.append(
            {
                "scheduled_at": target.isoformat(),
                "requested_delay_minutes": _int(step.get("delay_minutes"), 0),
                "normalized_delay_minutes": normalized_delay,
            }
        )
        previous = target
    return normalized


def _next_first_day_free_window(value: datetime) -> datetime:
    local = value.astimezone(OUTREACH_BEIJING_TIMEZONE)
    windows = ((11, 30, 12, 30), (17, 0, 18, 0), (20, 0, 21, 0))
    for start_hour, start_minute, end_hour, end_minute in windows:
        start = local.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end = local.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        if local <= end:
            return max(local, start).astimezone(timezone.utc)
    tomorrow = (local + timedelta(days=1)).replace(hour=11, minute=30, second=0, microsecond=0)
    return tomorrow.astimezone(timezone.utc)


def _normalize_first_day_outreach_schedule(
    start_at: str,
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start = (_parse_iso(start_at) or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized = [
        {
            "scheduled_at": start.isoformat(),
            "requested_delay_minutes": _int((steps[0] if steps else {}).get("delay_minutes"), 0),
            "normalized_delay_minutes": 0,
        }
    ]
    second = steps[1] if len(steps) > 1 else {}
    requested_delay = max(0, _int(second.get("delay_minutes"), 0))
    normalized_delay = min(20, max(15, requested_delay or 15))
    target = start + timedelta(minutes=normalized_delay)
    normalized.append(
        {
            "scheduled_at": target.isoformat(),
            "requested_delay_minutes": requested_delay,
            "normalized_delay_minutes": normalized_delay,
        }
    )
    return normalized


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string(item) for item in value if _string(item)]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _valid_activity_quote_evidence(
    quote_fact: dict[str, Any],
) -> bool:
    return bool(isinstance(quote_fact, dict) and quote_fact.get("completed"))


def _activity_quote_text_is_complete(text: str) -> bool:
    compact = "".join(_string(text).split())
    if not compact:
        return False
    return (
        "268" in compact
        and "10" in compact
        and "到店" in compact
        and "抵扣" in compact
        and "未做或不满意可退" in compact
        and "付款记录核对" in compact
    )


def _first_day_internal_activity_quote_evidence(
    steps: list[dict[str, Any]],
    *,
    before_step_index: int,
) -> bool:
    for step in steps[:before_step_index]:
        if _string(step.get("scene")) != "activity_intro":
            continue
        texts = _plan_step_texts(step)
        if any(_activity_quote_text_is_complete(text) for text in texts):
            return True
        if _activity_quote_text_is_complete(" ".join(texts)):
            return True
    return False


def _first_day_default_asset_id_for_sources(
    assets: dict[str, dict[str, Any]],
    source_ids: list[str],
) -> str:
    for source_id in source_ids:
        prefix = f"{_string(source_id)}:"
        for asset_id, asset in assets.items():
            if asset_id.startswith(prefix) and _string(asset.get("type")) == "image":
                return asset_id
    return ""


def _first_day_sop_source_ids(source_snapshot: dict[str, Any] | None) -> set[str]:
    return {
        _string(item.get("source_id"))
        for item in (source_snapshot or {}).get("first_day_sop_sequence") or []
        if isinstance(item, dict) and _string(item.get("source_id"))
    }


def _first_day_sop_source_aliases(source_snapshot: dict[str, Any] | None) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in (source_snapshot or {}).get("first_day_sop_sequence") or []:
        if not isinstance(item, dict):
            continue
        source_id = _string(item.get("source_id"))
        if not source_id:
            continue
        for alias in (
            _string(item.get("pack_id")),
            _string(item.get("sop_category")),
            _string(item.get("mapped_scene")),
        ):
            if alias and alias not in aliases:
                aliases[alias] = source_id
            if alias:
                prefixed = f"sop-pack:{alias}"
                if prefixed not in aliases:
                    aliases[prefixed] = source_id
    return aliases


def _first_day_available_sources_by_scene(source_snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    available: dict[str, list[dict[str, Any]]] = {
        scene: [] for scene in FIRST_DAY_SCENES if scene not in {"suppress", "health_hold"}
    }
    for item in source_snapshot.get("first_day_sop_sequence") or []:
        if not isinstance(item, dict):
            continue
        scene = _string(item.get("mapped_scene"))
        source_id = _string(item.get("source_id"))
        if scene in available and source_id:
            available[scene].append(
                {
                    "source_id": source_id,
                    "source_kind": "mainline_sop",
                    "requires_customer_evidence": False,
                }
            )
    for item in source_snapshot.get("appointment_blocker_scene_index") or []:
        if not isinstance(item, dict):
            continue
        for source_id in item.get("source_ids") or []:
            source_id = _string(source_id)
            if not source_id:
                continue
            source = {
                "source_id": source_id,
                "source_kind": "appointment_blocker",
                "applicable_scene": _string(item.get("applicable_scene")),
                "blocker_types": _list_strings(item.get("blocker_types")),
                "requires_customer_evidence": True,
            }
            available["objection_resolution"].append(dict(source))
            available["trust_repair"].append(dict(source))
    return available


def _first_day_normalize_selected_source_ids(
    response: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
) -> None:
    selected = response.get("selected_source_ids")
    if not isinstance(selected, dict):
        return
    available_source_ids = {
        _string(source_id)
        for item in (source_snapshot or {}).get("appointment_blocker_scene_index") or []
        if isinstance(item, dict)
        for source_id in item.get("source_ids") or []
        if _string(source_id)
    }
    available_source_ids.update(
        _string(item.get("asset_id"))
        for item in (source_snapshot or {}).get("asset_catalog") or []
        if isinstance(item, dict) and _string(item.get("asset_id"))
    )
    available_source_ids.update(_first_day_sop_source_ids(source_snapshot))
    aliases = _first_day_sop_source_aliases(source_snapshot)
    if not available_source_ids:
        return
    for key in ("step1", "step2"):
        normalized_ids: list[str] = []
        for raw_id in selected.get(key) or []:
            source_id = _string(raw_id)
            if source_id in available_source_ids:
                normalized_ids.append(source_id)
                continue
            alias_target = aliases.get(source_id)
            if alias_target:
                normalized_ids.append(alias_target)
                continue
            suffix_matches = [
                candidate
                for candidate in available_source_ids
                if candidate.endswith(f":{source_id}")
            ]
            if len(suffix_matches) == 1:
                normalized_ids.append(suffix_matches[0])
        step_scene = _string(response.get(f"{key}_scene"))
        if not normalized_ids and step_scene:
            alias_target = aliases.get(step_scene) or aliases.get(f"sop-pack:{step_scene}")
            if alias_target:
                normalized_ids.append(alias_target)
        selected[key] = list(dict.fromkeys(normalized_ids))


def _first_day_sop_pack_for_step(
    source_snapshot: dict[str, Any],
    *,
    step_index: int,
    scene: str,
) -> dict[str, Any]:
    workflow = source_snapshot.get("first_day_workflow")
    scene_analysis = workflow.get("scene_analysis") if isinstance(workflow, dict) else {}
    selected_ids = {
        _string(source_id)
        for source_id in (scene_analysis.get("selected_source_ids") or {}).get(f"step{step_index}") or []
        if _string(source_id)
    }
    if not selected_ids:
        return {}
    for pack in source_snapshot.get("first_day_sop_sequence") or []:
        if not isinstance(pack, dict):
            continue
        if _string(pack.get("source_id")) not in selected_ids:
            continue
        if _string(pack.get("mapped_scene")) != scene:
            continue
        return dict(pack)
    return {}


def _first_day_sop_pack_messages_for_step(
    source_snapshot: dict[str, Any],
    *,
    step_index: int,
    scene: str,
) -> list[dict[str, Any]]:
    pack = _first_day_sop_pack_for_step(
        source_snapshot,
        step_index=step_index,
        scene=scene,
    )
    messages = [
        dict(message)
        for message in pack.get("reply_messages") or []
        if isinstance(message, dict)
    ]
    return messages


def _first_day_activity_sop_payment_step(
    scene_analysis: dict[str, Any],
    source_snapshot: dict[str, Any] | None,
) -> int:
    snapshot = source_snapshot or {}
    selected = scene_analysis.get("selected_source_ids") or {}
    packs_by_source = {
        _string(pack.get("source_id")): pack
        for pack in snapshot.get("first_day_sop_sequence") or []
        if isinstance(pack, dict) and _string(pack.get("source_id"))
    }
    for step_index, scene in enumerate(
        (_string(scene_analysis.get("step1_scene")), _string(scene_analysis.get("step2_scene"))),
        start=1,
    ):
        if scene != "activity_intro":
            continue
        for source_id in selected.get(f"step{step_index}") or []:
            pack = packs_by_source.get(_string(source_id)) or {}
            if _string(pack.get("sop_category")) not in FIRST_DAY_ACTIVITY_SOP_CATEGORIES:
                continue
            if any(
                _string(message.get("type")) == "payment_collection"
                for message in pack.get("reply_messages") or []
                if isinstance(message, dict)
            ):
                return step_index
    return 0


def _first_day_materialized_sop_messages(
    messages: list[dict[str, Any]],
    *,
    allow_payment_collection: bool,
    text_overrides: list[str] | None = None,
    sent_urls: set[str] | None = None,
    used_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    override_texts = [text for text in text_overrides or [] if _string(text)]
    overrides_emitted = False
    sent = {
        identity
        for identity in (_media_url_identity(url) for url in sent_urls or set())
        if identity
    }
    used = used_urls if used_urls is not None else set()
    for message in sorted(messages, key=lambda item: _int(item.get("order"), 9999)):
        message_type = _string(message.get("type"))
        if message_type == "text":
            if override_texts:
                if not overrides_emitted:
                    output.extend(
                        {"type": "text", "content": {"text": text}}
                        for text in override_texts
                    )
                    overrides_emitted = True
                continue
            text = _string(message.get("text"))
            if text:
                output.append({"type": "text", "content": {"text": text}})
        elif message_type in {"image", "video"}:
            url = _string(message.get("url"))
            identity = _media_url_identity(url)
            if url and identity not in sent and identity not in used:
                output.append({"type": message_type, "content": {"url": url}})
                if identity:
                    used.add(identity)
        elif message_type == "payment_collection" and allow_payment_collection:
            output.append(
                {
                    "type": "payment_collection",
                    "content": {
                        "amount": _int(message.get("amount"), 10),
                        "remark": "",
                    },
                }
            )
    if override_texts and not overrides_emitted:
        output = [
            {"type": "text", "content": {"text": text}}
            for text in override_texts
        ] + output
    for order, message in enumerate(output, start=1):
        message["order"] = order
    return output


def _first_day_sop_pack_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for message in sorted(messages, key=lambda item: _int(item.get("order"), 9999)):
        if _string(message.get("type")) != "text":
            continue
        text = _string(message.get("text"))
        if not text and isinstance(message.get("content"), dict):
            text = _string(message["content"].get("text"))
        if text:
            texts.append(text)
    return texts


def build_outreach_activity_quote_fact(
    recent_messages: list[dict[str, Any]],
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build structural evidence that the current activity quote was delivered."""
    message_indexes: list[int] = []
    for index, message in enumerate(recent_messages):
        if not isinstance(message, dict) or _message_party(message) != "staff":
            continue
        compact = "".join(_message_text(message).split())
        if "268" not in compact:
            continue
        if any(marker in compact for marker in ("活动价", "活动总价", "预约金", "到店抵扣", "线上活动")):
            message_indexes.append(index)

    structured_sources: list[str] = []
    memory_value = memory if isinstance(memory, dict) else {}
    for value in (
        memory_value.get("sop_progress_evidence"),
        memory_value.get("sop_progress"),
        memory_value,
    ):
        if not isinstance(value, dict):
            continue
        completed_ids = {_string(item).lower() for item in value.get("completed_pack_ids") or []}
        completed_categories = {_string(item).lower() for item in value.get("completed_categories") or []}
        if "s10_activity_intro" in completed_ids or completed_categories.intersection(
            {"activity_intro", "s10_activity_intro", "price_quote"}
        ):
            structured_sources.append("sop_progress")
            break
    for event in memory_value.get("history_events") or []:
        if not isinstance(event, dict):
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        pack_id = _string(
            facts.get("sop_pack_id")
            or facts.get("pack_id")
            or event.get("pack_id")
            or event.get("sop_pack_id")
        ).lower()
        category = _string(
            facts.get("sop_category")
            or facts.get("category")
            or event.get("sop_category")
            or event.get("category")
        ).lower()
        if pack_id in {"s10_activity_intro", "event_s10_price_quote_60min"} or category in {
            "activity_intro",
            "s10_activity_intro",
            "price_quote",
        }:
            structured_sources.append("history_event")
            break
    return {
        "completed": bool(message_indexes or structured_sources),
        "message_indexes": message_indexes,
        "structured_sources": sorted(set(structured_sources)),
    }


def outreach_customer_fact_snapshot(memory: dict[str, Any] | None) -> dict[str, Any]:
    """Remove model-authored portrait prose while retaining durable operational facts."""

    value = memory if isinstance(memory, dict) else {}
    basic = value.get("basic_info") if isinstance(value.get("basic_info"), dict) else {}
    basic_keys = {
        "name",
        "phone",
        "city",
        "province",
        "district",
        "confirmed_store_id",
        "confirmed_store_name",
        "appointment_id",
        "appointment_time",
        "appointment",
        "deposit_state",
        "order_id",
        "order_status",
    }
    basic_facts = {
        key: basic.get(key)
        for key in basic_keys
        if basic.get(key) not in (None, "", [], {})
    }
    events: list[dict[str, Any]] = []
    for event in value.get("history_events") or []:
        if not isinstance(event, dict):
            continue
        event_type = _string(event.get("event_type"))
        if event_type not in OUTREACH_DURABLE_EVENT_TYPES:
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        events.append(
            {
                "event_type": event_type,
                "event_time": _string(event.get("event_time") or event.get("created_at")),
                "facts": facts,
                "source": _string(event.get("source")),
            }
        )
    return {
        "last_customer_message_at": _string(value.get("last_customer_message_at")),
        "last_staff_message_at": _string(value.get("last_staff_message_at")),
        "last_ai_reply_at": _string(value.get("last_ai_reply_at")),
        "basic_facts": basic_facts,
        "sop_progress": value.get("sop_progress") if isinstance(value.get("sop_progress"), dict) else {},
        "sop_progress_evidence": (
            value.get("sop_progress_evidence")
            if isinstance(value.get("sop_progress_evidence"), dict)
            else {}
        ),
        "history_events": events[-50:],
    }


def _message_text(message: dict[str, Any]) -> str:
    parts = [_string(message.get("content"))]
    for item in message.get("reply_messages") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, dict):
            parts.append(_string(content.get("text") or content.get("content")))
        else:
            parts.append(_string(content))
    return " ".join(part for part in parts if part)


def _message_party(message: dict[str, Any]) -> str:
    raw = _string(
        message.get("direction")
        or message.get("sender_type")
        or message.get("role")
        or message.get("from")
    ).lower()
    if raw in {"staff", "assistant", "service", "ai"}:
        return "staff"
    if raw in {"customer", "user", "external"}:
        return "customer"
    return "unknown"


def _is_wecom_auto_opening_message(content: str) -> bool:
    normalized = re.sub(r"[\s，,。.!！?？:：；;、\"'“”‘’（）()【】\[\]《》<>-]+", "", str(content or ""))
    return normalized in {
        "我已经添加了你现在我们可以开始聊天了",
        "我已经添加了你现在可以开始聊天了",
    }


def _real_customer_message_count(messages: list[Any]) -> int:
    count = 0
    for message in messages:
        if not isinstance(message, dict) or _message_party(message) != "customer":
            continue
        if _is_wecom_auto_opening_message(_message_text(message)):
            continue
        count += 1
    return count


def _latest_real_customer_message_time(messages: list[Any]) -> str:
    candidates = []
    for message in messages:
        if not isinstance(message, dict) or _message_party(message) != "customer":
            continue
        if _is_wecom_auto_opening_message(_message_text(message)):
            continue
        value = _message_time_iso(
            message.get("msgtime")
            or message.get("timestamp")
            or message.get("created_at")
            or message.get("send_time")
        )
        if value:
            candidates.append(value)
    return max(candidates) if candidates else ""


def _first_day_configured_assets_for_step(
    source_snapshot: dict[str, Any],
    *,
    step_index: int,
    asset_catalog: list[dict[str, Any]],
    recent_media: dict[str, list[str]],
) -> list[dict[str, Any]]:
    workflow = source_snapshot.get("first_day_workflow")
    scene_analysis = workflow.get("scene_analysis") if isinstance(workflow, dict) else {}
    selected_ids = [
        _string(source_id)
        for source_id in (scene_analysis.get("selected_source_ids") or {}).get(f"step{step_index}") or []
        if _string(source_id)
    ]
    configured_main_sources = [
        source_id
        for source_id in selected_ids
        if (
            source_id.startswith("appointment-blocker:")
            and source_id.count(":") == 1
        )
        or source_id in _first_day_sop_source_ids(source_snapshot)
    ]
    explicit_asset_ids = set(selected_ids)
    sent_urls = set(recent_media.get("urls") or [])
    output: list[dict[str, Any]] = []
    for asset in asset_catalog:
        if not isinstance(asset, dict):
            continue
        asset_id = _string(asset.get("asset_id"))
        selected = asset_id in explicit_asset_ids or any(
            asset_id.startswith(f"{source_id}:") for source_id in configured_main_sources
        )
        if not selected:
            continue
        resolved = resolve_configured_asset(
            asset_catalog,
            asset_id,
            sent_urls=sent_urls,
        )
        if resolved:
            output.append(resolved)
    return output


def _task_content_sources(
    raw_sources: Any,
    *,
    should_send_payment_collection: bool,
    task_metadata: dict[str, Any],
    resolved_asset: dict[str, Any],
    resolved_assets: list[dict[str, Any]] | None = None,
) -> list[Any]:
    sources = _list_strings(raw_sources) or ["s10_offer"]
    sources.extend(
        [
            {"should_send_payment_collection": bool(should_send_payment_collection)},
            {"outreach_task_metadata": task_metadata},
            {"resolved_asset": resolved_asset},
            {"resolved_assets": [dict(asset) for asset in resolved_assets or [] if isinstance(asset, dict)]},
        ]
    )
    return sources


def _compose_outreach_messages(
    texts: str | list[str],
    *,
    resolved_asset: dict[str, Any] | None = None,
    resolved_assets: list[dict[str, Any]] | None = None,
    should_send_payment_collection: bool = False,
    text_limit: int | None = 2,
) -> list[dict[str, Any]]:
    normalized_texts = [_string(item) for item in ([texts] if isinstance(texts, str) else texts)]
    visible_texts = normalized_texts if text_limit is None else normalized_texts[: max(0, text_limit)]
    output = [
        {"type": "text", "order": index, "content": {"text": text}}
        for index, text in enumerate(visible_texts, start=1)
        if text
    ]
    assets = [dict(asset) for asset in resolved_assets or [] if isinstance(asset, dict)]
    if not assets and resolved_asset:
        assets = [dict(resolved_asset)]
    seen_assets: set[str] = set()
    for asset in assets:
        identity = _string(asset.get("document_id") or asset.get("url") or asset.get("asset_id"))
        if identity and identity in seen_assets:
            continue
        if identity:
            seen_assets.add(identity)
        asset_message = asset_reply_message(asset, order=len(output) + 1)
        if asset_message:
            output.append(asset_message)
    if should_send_payment_collection:
        output.append(
            {
                "type": "payment_collection",
                "order": len(output) + 1,
                "content": {"amount": 10, "remark": ""},
            }
        )
    return output


def _media_url_identity(value: Any) -> str:
    raw = _string(value)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return raw
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))


def _filter_recently_sent_outreach_media(
    reply_messages: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    recent = recent_outreach_media(recent_messages, hours=72)
    sent_identities = {
        identity
        for identity in (_media_url_identity(url) for url in recent.get("urls") or [])
        if identity
    }
    output: list[dict[str, Any]] = []
    duplicate_urls: list[str] = []
    emitted_media: set[str] = set()
    for message in reply_messages:
        if not isinstance(message, dict):
            continue
        message_type = _string(message.get("type"))
        content = message.get("content")
        url = _string(content.get("url")) if isinstance(content, dict) else ""
        if message_type in {"image", "video"} and url:
            identity = _media_url_identity(url)
            if identity in sent_identities or identity in emitted_media:
                duplicate_urls.append(url)
                continue
            if identity:
                emitted_media.add(identity)
        output.append(dict(message))
    for order, message in enumerate(output, start=1):
        message["order"] = order
    return output, list(dict.fromkeys(duplicate_urls))


def _first_reply_text(messages: Any) -> str:
    texts = _reply_texts(messages)
    return texts[0] if texts else ""


def _reply_texts(messages: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(messages, list):
        return []
    output: list[str] = []
    for item in messages:
        if not isinstance(item, dict) or _string(item.get("type")) != "text":
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = _string(content.get("text"))
        else:
            text = _string(content)
        if text:
            output.append(text)
            if len(output) >= limit:
                break
    return output


def _normalize_repeat_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", _string(value).lower())


def _message_visible_texts(message: Any) -> list[str]:
    if not isinstance(message, dict):
        return []
    output: list[str] = []
    content = message.get("content")
    if isinstance(content, dict):
        text = _string(content.get("text"))
    else:
        text = _string(content)
    if text:
        output.append(text)
    output.extend(_reply_texts(message.get("reply_messages"), limit=20))
    return output


def _nested_sop_texts(value: Any) -> list[str]:
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            output.extend(_nested_sop_texts(item))
        return output
    if not isinstance(value, dict):
        return []
    output = _message_visible_texts(value)
    for key, item in value.items():
        if key in {"content", "reply_messages"}:
            continue
        if isinstance(item, (dict, list)):
            output.extend(_nested_sop_texts(item))
    return output


def _first_day_history_texts(plan: dict[str, Any], context: dict[str, Any]) -> list[str]:
    source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
    messages: list[Any] = []
    for candidate in (source_snapshot.get("recent_messages"), context.get("recent_messages")):
        if isinstance(candidate, list):
            messages.extend(candidate)
    output: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = _string(
            message.get("direction")
            or message.get("role")
            or message.get("sender_type")
            or message.get("from")
        ).lower()
        if role not in {"staff", "assistant", "service", "ai", "bot"}:
            continue
        output.extend(_message_visible_texts(message))
    output.extend(_nested_sop_texts(source_snapshot.get("recent_sop_delivery")))
    return list(dict.fromkeys(text for text in output if _normalize_repeat_text(text)))


def _first_day_message_policy_error(
    texts: list[str],
    *,
    step_index: int,
    plan: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, str]:
    visible_text = " ".join(_string(text) for text in texts if _string(text))
    for term in FIRST_DAY_GENDERED_TERMS:
        if term in visible_text:
            return "first_day_gendered_language", term
    for term in FIRST_DAY_PROCESS_TAIL_TERMS:
        if term in visible_text:
            return "first_day_process_tail", term
    for term in FIRST_DAY_UNSUPPORTED_STORE_ACTIONS:
        if term in visible_text:
            return "first_day_unsupported_store_action", term
    if step_index != 1:
        return "", ""
    candidates = [visible_text, *texts]
    for candidate in candidates:
        normalized_candidate = _normalize_repeat_text(candidate)
        if len(normalized_candidate) < 8:
            continue
        for history_text in _first_day_history_texts(plan, context):
            normalized_history = _normalize_repeat_text(history_text)
            if min(len(normalized_candidate), len(normalized_history)) < 8:
                continue
            similarity = SequenceMatcher(None, normalized_history, normalized_candidate).ratio()
            if similarity > FIRST_DAY_REPEAT_SIMILARITY_LIMIT:
                return "first_day_message_too_similar_to_history", history_text
    return "", ""


def _plan_step_text(step: dict[str, Any]) -> str:
    texts = _plan_step_texts(step)
    return texts[0] if texts else ""


def _plan_step_texts(step: dict[str, Any]) -> list[str]:
    texts = _reply_texts(step.get("reply_messages"))
    if texts:
        return texts
    draft_text = _string(step.get("draft_text"))
    return [draft_text] if draft_text else []


def _normalize_outreach_plan_response(response: dict[str, Any]) -> dict[str, Any]:
    for step in response.get("steps") or []:
        if not isinstance(step, dict):
            continue
        messages = step.get("reply_messages")
        if not isinstance(messages, list):
            continue
        for index, message in enumerate(messages, start=1):
            if not isinstance(message, dict) or _string(message.get("type")) != "text":
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = {"text": content}
            message["order"] = index
    return response


def _task_metadata(task: dict[str, Any]) -> dict[str, Any]:
    items = task.get("content_source_metadata")
    if not isinstance(items, list):
        items = [item for item in task.get("content_sources", []) if isinstance(item, dict)]
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("outreach_task_metadata"), dict):
            return dict(item["outreach_task_metadata"])
    return {}


def _task_resolved_asset(task: dict[str, Any]) -> dict[str, Any]:
    assets = _task_resolved_assets(task)
    return assets[0] if assets else {}


def _task_resolved_assets(task: dict[str, Any]) -> list[dict[str, Any]]:
    items = task.get("content_source_metadata")
    if not isinstance(items, list):
        items = [item for item in task.get("content_sources", []) if isinstance(item, dict)]
    output: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("resolved_assets"), list):
            output.extend(
                dict(asset)
                for asset in item["resolved_assets"]
                if isinstance(asset, dict)
            )
        if isinstance(item, dict) and isinstance(item.get("resolved_asset"), dict):
            output.append(dict(item["resolved_asset"]))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset in output:
        identity = _string(asset.get("document_id") or asset.get("url") or asset.get("asset_id"))
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        deduped.append(asset)
    return deduped


def _outreach_plan_structure_error(response: dict[str, Any]) -> str:
    if not bool(response.get("should_create_plan", True)):
        if response.get("steps") or _string(response.get("plan_arc")):
            return "should_create_plan=false cannot include plan_arc or steps"
        return ""
    steps = [step for step in response.get("steps") or [] if isinstance(step, dict)][:3]
    if len(steps) not in {2, 3}:
        return "plan must contain 2 to 3 steps"
    angles = [_string(step.get("persuasion_angle")) for step in steps]
    if any(angle not in OUTREACH_PERSUASION_ANGLES for angle in angles):
        return "every step must use one allowed persuasion_angle"
    if any(current == previous for previous, current in zip(angles, angles[1:])):
        return "adjacent steps must use different persuasion_angle values"
    content_modes = [_string(step.get("content_mode")) for step in steps]
    if any(mode not in OUTREACH_CONTENT_MODES for mode in content_modes):
        return "every step must use one allowed content_mode"
    if "value_only" not in content_modes:
        return "plan must contain at least one value_only step"
    urgency_levels = [_string(step.get("urgency_level")) for step in steps]
    if any(level not in OUTREACH_URGENCY_LEVELS for level in urgency_levels):
        return "every step must use one allowed urgency_level"
    if any(not _string(step.get("timing_reason")) for step in steps):
        return "every step must contain timing_reason"
    no_reply_actions = [_string(step.get("no_reply_action")) for step in steps]
    if any(action not in OUTREACH_NO_REPLY_ACTIONS for action in no_reply_actions):
        return "every step must use one allowed no_reply_action"
    if any(not _string(step.get("no_reply_strategy")) for step in steps):
        return "every step must contain no_reply_strategy"
    if any(action != "advance_to_next_step" for action in no_reply_actions[:-1]):
        return "non-final steps must advance to the next step when there is no reply"
    if no_reply_actions[-1] != "end_plan":
        return "final step must end the current plan when there is no reply"
    if _string(steps[-1].get("cta")).lower() in {"", "none"}:
        return "final step must contain one explicit customer action"
    asset_strategies = [_string(step.get("asset_strategy")) or "none" for step in steps]
    if any(strategy not in OUTREACH_ASSET_STRATEGIES for strategy in asset_strategies):
        return "every step must use one allowed asset_strategy"
    for step in steps:
        messages = step.get("reply_messages")
        if not isinstance(messages, list) or not messages:
            return "every step must contain at least one reply_messages text item"
        if any(
            not isinstance(message, dict)
            or _string(message.get("type")) != "text"
            for message in messages
        ) or any(not isinstance(message.get("content"), dict) for message in messages) or len(
            _reply_texts(messages)
        ) != len(messages):
            return "plan step reply_messages must contain non-empty text items"
        if _string(step.get("content_mode")) == "value_only" and _bool(
            step.get("should_send_payment_collection")
        ):
            return "value_only step cannot send payment_collection"
        if _bool(step.get("should_send_payment_collection")):
            payment_text = " ".join(_plan_step_texts(step))
            compact_payment_text = "".join(payment_text.split())
            if not (
                "10" in compact_payment_text
                and any(marker in compact_payment_text for marker in ("锁", "留", "保留"))
                and "到店" in compact_payment_text
                and "抵扣" in compact_payment_text
                and "未做或不满意可退" in compact_payment_text
                and "付款记录核对" in compact_payment_text
            ):
                return (
                    "first-day payment_collection text must explain 10 yuan locks the quota, "
                    "deducts in store, and uses the unified refund wording"
                )
    payment_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if _bool(step.get("should_send_payment_collection"))
    ]
    if len(payment_steps) > 1:
        return "plan can contain at most one payment_collection step"
    if payment_steps:
        payment_index, payment_step = payment_steps[0]
        if payment_index != len(steps) - 1:
            return "payment_collection step must be final"
        if _string(payment_step.get("content_mode")) != "transaction":
            return "payment_collection step must use transaction content_mode"
        if _string(payment_step.get("payment_collection_basis")) != "model_selected_after_quote":
            return "payment_collection step must use model_selected_after_quote basis"
    delays = [_int(step.get("delay_minutes"), -1) for step in steps]
    if delays[0] < 0 or delays[0] > OUTREACH_FIRST_STEP_MAX_MINUTES:
        return "first step delay_minutes must be between 0 and 720"
    for previous, current in zip(delays, delays[1:]):
        gap = current - previous
        if gap < OUTREACH_MIN_STEP_GAP_MINUTES or gap > OUTREACH_MAX_STEP_GAP_MINUTES:
            return "adjacent step delay must be between 360 and 4320 minutes"
    if delays[-1] > OUTREACH_MAX_PLAN_MINUTES:
        return "plan duration cannot exceed 7 days"
    return ""


def _outreach_plan_context_error(
    response: dict[str, Any],
    *,
    activity_quote_fact: dict[str, Any],
    reply_wait_minutes: int = 0,
    customer_silence_minutes: int = 0,
    allow_first_day_internal_activity_quote: bool = False,
) -> str:
    steps = [step for step in response.get("steps") or [] if isinstance(step, dict)]
    if bool(response.get("should_create_plan", True)) and customer_silence_minutes >= 4320 and steps:
        delays = [_int(step.get("delay_minutes"), -1) for step in steps]
        if not 0 <= delays[0] <= 180:
            return (
                "customer_silence_minutes is at least 4320; first step delay_minutes "
                "must be between 0 and 180"
            )
        if any(current - previous < 1440 for previous, current in zip(delays, delays[1:])):
            return (
                "customer_silence_minutes is at least 4320; adjacent steps must be "
                "at least 1440 minutes apart"
            )
    if bool(response.get("should_create_plan", True)) and reply_wait_minutes >= 1440 and steps:
        first_step = steps[0]
        first_step_is_value_delivery = (
            _string(first_step.get("cta")).lower() in {"", "none"}
            and _string(first_step.get("persuasion_angle"))
            in {
                "education",
                "proof",
                "professionalism",
                "self_image",
            }
            and not any(marker in _plan_step_text(first_step) for marker in ("?", "？"))
        )
        if not first_step_is_value_delivery:
            return (
                "reply_wait_minutes is at least 1440; rewrite the first step with cta exactly 'none', "
                "persuasion_angle one of education/proof/professionalism/self_image, and a declarative "
                "customer text with no question mark that directly delivers useful value"
            )
    if not _valid_activity_quote_evidence(activity_quote_fact):
        for index, step in enumerate(steps):
            if not _bool(step.get("should_send_payment_collection")):
                continue
            if allow_first_day_internal_activity_quote and _first_day_internal_activity_quote_evidence(
                steps,
                before_step_index=index + 1,
            ):
                continue
            return "activity quote is incomplete; payment_collection must be disabled"
    return ""


def _first_day_outreach_plan_error(response: dict[str, Any]) -> str:
    steps = [step for step in response.get("steps") or [] if isinstance(step, dict)]
    if not bool(response.get("should_create_plan", True)):
        if steps or _string(response.get("plan_arc")):
            return "should_create_plan=false cannot include plan_arc or steps"
        return ""
    if len(steps) != 2:
        return "first-day opened silence plan must contain exactly 2 steps"
    content_modes = [_string(step.get("content_mode")) for step in steps]
    if any(mode not in OUTREACH_CONTENT_MODES for mode in content_modes):
        return "every step must use one allowed content_mode"
    if "value_only" not in content_modes:
        return "plan must contain at least one value_only step"
    if any(_string(step.get("urgency_level")) not in OUTREACH_URGENCY_LEVELS for step in steps):
        return "every step must use one allowed urgency_level"
    if any(not _string(step.get("timing_reason")) for step in steps):
        return "every step must contain timing_reason"
    no_reply_actions = [_string(step.get("no_reply_action")) for step in steps]
    if no_reply_actions != ["advance_to_next_step", "end_plan"]:
        return "first-day steps must advance once and then end the plan"
    if any(not _string(step.get("no_reply_strategy")) for step in steps):
        return "every step must contain no_reply_strategy"
    for step in steps:
        delivery_check = step.get("scene_delivery_check")
        if not isinstance(delivery_check, dict) or any(
            not _string(delivery_check.get(key))
            for key in ("new_value_delivered", "historical_difference", "objective_match")
        ):
            return "every first-day step must contain a complete scene_delivery_check"
        messages = step.get("reply_messages")
        if not isinstance(messages, list) or not messages:
            return "every step must contain at least one reply_messages text item"
        if any(
            not isinstance(message, dict)
            or _string(message.get("type")) != "text"
            or not isinstance(message.get("content"), dict)
            for message in messages
        ) or len(_reply_texts(messages)) != len(messages):
            return "plan step reply_messages must contain non-empty text items"
        asset_strategy = _string(step.get("asset_strategy")) or "none"
        if asset_strategy not in OUTREACH_ASSET_STRATEGIES:
            return "every step must use one allowed asset_strategy"
        if _string(step.get("content_mode")) == "value_only" and _bool(
            step.get("should_send_payment_collection")
        ):
            return "value_only step cannot send payment_collection"
    if _int(steps[0].get("delay_minutes"), -1) != 0:
        return "first-day first step must be immediate with delay_minutes=0"
    second_delay = _int(steps[1].get("delay_minutes"), -1)
    if second_delay < 15 or second_delay > 20:
        return "first-day second step delay_minutes must be between 15 and 20"
    payment_steps = [
        step
        for step in steps
        if _bool(step.get("should_send_payment_collection"))
    ]
    if payment_steps:
        return "first-day outreach cannot send payment_collection"
    return ""


def _first_day_scene_analysis_error(
    response: dict[str, Any],
    *,
    source_snapshot: dict[str, Any],
) -> str:
    if not isinstance(response, dict):
        return "scene analysis must be a json object"
    eligible = response.get("eligible")
    if not isinstance(eligible, bool):
        return "scene analysis eligible must be boolean"
    hard_boundary = response.get("hard_boundary")
    if not isinstance(hard_boundary, dict) or not isinstance(hard_boundary.get("active"), bool):
        return "scene analysis requires a hard_boundary object"
    hard_boundary_active = bool(hard_boundary.get("active"))
    hard_boundary_type = _string(hard_boundary.get("type"))
    hard_boundary_indexes = hard_boundary.get("message_indexes")
    if not isinstance(hard_boundary_indexes, list):
        return "scene analysis hard_boundary message_indexes must be a list"
    precedence_decision = response.get("precedence_decision")
    if not isinstance(precedence_decision, dict):
        return "scene analysis requires precedence_decision"
    if _string(precedence_decision.get("row_id")) not in FIRST_DAY_PRECEDENCE_ROWS:
        return "scene analysis precedence_decision row_id is invalid"
    if not _string(precedence_decision.get("reason")):
        return "scene analysis precedence_decision requires reason"
    precedence_indexes = precedence_decision.get("message_indexes")
    if not isinstance(precedence_indexes, list):
        return "scene analysis precedence_decision message_indexes must be a list"
    step1_scene = _string(response.get("step1_scene"))
    step2_scene = _string(response.get("step2_scene"))
    current_scene = _string(response.get("current_scene"))
    if current_scene not in FIRST_DAY_SCENES:
        return "scene analysis current_scene is invalid"
    completion_matrix = response.get("scene_completion_matrix")
    if not isinstance(completion_matrix, dict) or set(completion_matrix) != FIRST_DAY_COMPLETION_SCENES:
        return "scene analysis must contain the complete scene_completion_matrix"
    message_count = len(source_snapshot.get("recent_messages") or [])
    for scene in FIRST_DAY_COMPLETION_SCENES:
        completion = completion_matrix.get(scene)
        if not isinstance(completion, dict):
            return f"scene analysis completion entry {scene} must be an object"
        if _string(completion.get("status")) not in FIRST_DAY_COMPLETION_STATUSES:
            return f"scene analysis completion entry {scene} has invalid status"
        if not _string(completion.get("summary")):
            return f"scene analysis completion entry {scene} requires summary"
        indexes = completion.get("message_indexes")
        if not isinstance(indexes, list) or any(
            not isinstance(index, int) or not 0 <= index < message_count for index in indexes
        ):
            return f"scene analysis completion entry {scene} has invalid message indexes"
        asset_ids = completion.get("asset_ids")
        if not isinstance(asset_ids, list) or any(not _string(asset_id) for asset_id in asset_ids):
            return f"scene analysis completion entry {scene} has invalid asset ids"
    customer_mainline = response.get("customer_mainline")
    if not isinstance(customer_mainline, dict) or any(
        not _string(customer_mainline.get(key))
        for key in (
            "latest_customer_main_need",
            "silence_barrier",
            "symptom_role",
            "next_business_action",
        )
    ):
        return "scene analysis requires a complete customer_mainline"
    writer_indexes = response.get("writer_context_message_indexes")
    if not isinstance(writer_indexes, list) or any(
        not isinstance(index, int) or not 0 <= index < message_count for index in writer_indexes
    ):
        return "scene analysis writer_context_message_indexes are invalid"
    if message_count and not writer_indexes:
        return "scene analysis requires writer context messages"
    selected_source_ids = response.get("selected_source_ids")
    if not isinstance(selected_source_ids, dict):
        return "scene analysis selected_source_ids must be an object"
    available_source_ids = {
        _string(source_id)
        for item in source_snapshot.get("appointment_blocker_scene_index") or []
        if isinstance(item, dict)
        for source_id in item.get("source_ids") or []
        if _string(source_id)
    }
    available_source_ids.update(
        _string(item.get("asset_id"))
        for item in source_snapshot.get("asset_catalog") or []
        if isinstance(item, dict) and _string(item.get("asset_id"))
    )
    available_source_ids.update(
        _string(item.get("source_id"))
        for item in source_snapshot.get("first_day_sop_sequence") or []
        if isinstance(item, dict) and _string(item.get("source_id"))
    )
    available_asset_ids = {
        _string(item.get("asset_id"))
        for item in source_snapshot.get("asset_catalog") or []
        if isinstance(item, dict) and _string(item.get("asset_id"))
    }
    available_main_source_ids = {
        _string(source_id)
        for item in source_snapshot.get("appointment_blocker_scene_index") or []
        if isinstance(item, dict)
        for source_id in item.get("source_ids") or []
        if _string(source_id)
    }
    available_main_source_ids.update(
        _string(item.get("source_id"))
        for item in source_snapshot.get("first_day_sop_sequence") or []
        if isinstance(item, dict) and _string(item.get("source_id"))
    )
    sop_scene_by_source = {
        _string(item.get("source_id")): _string(item.get("mapped_scene"))
        for item in source_snapshot.get("first_day_sop_sequence") or []
        if isinstance(item, dict) and _string(item.get("source_id"))
    }
    sources_by_scene = source_snapshot.get("available_sources_by_scene") or {}
    for key in ("step1", "step2"):
        source_ids = selected_source_ids.get(key)
        if not isinstance(source_ids, list) or any(
            not _string(source_id) or _string(source_id) not in available_source_ids
            for source_id in source_ids
        ):
            return f"scene analysis selected_source_ids.{key} contains unavailable source"
        selected_main_sources = [
            _string(source_id)
            for source_id in source_ids
            if _string(source_id) in available_main_source_ids
        ]
        if eligible and available_main_source_ids and len(set(selected_main_sources)) != 1:
            return (
                f"scene analysis selected_source_ids.{key} must select exactly one "
                "main SOP or appointment-blocker source"
            )
        if eligible and selected_main_sources:
            main_source = selected_main_sources[0]
            locked_scene = _string(response.get(f"{key}_scene"))
            legal_scene_sources = {
                _string(item.get("source_id"))
                for item in sources_by_scene.get(locked_scene) or []
                if isinstance(item, dict) and _string(item.get("source_id"))
            }
            if sources_by_scene and main_source not in legal_scene_sources:
                return (
                    f"scene analysis selected_source_ids.{key} source is not available "
                    f"for locked scene {locked_scene}"
                )
            if (
                _string(precedence_decision.get("row_id")) == "no_blocker_sop_progression"
                and not main_source.startswith("sop-pack:")
            ):
                return (
                    f"scene analysis selected_source_ids.{key} must use a main SOP source "
                    "when precedence_decision is no_blocker_sop_progression"
                )
            selected_sop_scene = sop_scene_by_source.get(main_source)
            if selected_sop_scene and selected_sop_scene != _string(response.get(f"{key}_scene")):
                return (
                    f"scene analysis selected_source_ids.{key} SOP source does not match "
                    "the locked step scene"
                )
            if any(
                _string(source_id) in available_asset_ids
                and not _string(source_id).startswith(f"{main_source}:")
                for source_id in source_ids
            ):
                return (
                    f"scene analysis selected_source_ids.{key} contains media outside "
                    "the selected main source"
                )
    if any(
        not isinstance(index, int) or not 0 <= index < message_count
        for index in hard_boundary_indexes
    ):
        return "scene analysis hard_boundary contains invalid message indexes"
    if any(
        not isinstance(index, int) or not 0 <= index < message_count
        for index in precedence_indexes
    ):
        return "scene analysis precedence_decision contains invalid message indexes"
    if not eligible:
        unopened_first_day = _int(
            (source_snapshot.get("conversation_activity") or {}).get(
                "real_customer_message_count"
            ),
            -1,
        ) == 0
        if unopened_first_day:
            if step1_scene != "suppress" or step2_scene != "suppress":
                return "unopened first-day scene analysis must suppress both steps"
            if not _string(response.get("suppress_reason")):
                return "unopened first-day scene analysis requires suppress_reason"
            return ""
        if (
            not hard_boundary_active
            or hard_boundary_type not in FIRST_DAY_HARD_BOUNDARY_TYPES
            or not _string(hard_boundary.get("fact"))
        ):
            return "suppressed scene analysis requires an allowed hard boundary and evidence"
        if step1_scene != "suppress" or step2_scene != "suppress":
            return "suppressed scene analysis must lock both steps to suppress"
        if not _string(response.get("suppress_reason")):
            return "suppressed scene analysis requires suppress_reason"
        return ""
    if hard_boundary_active or hard_boundary_type not in {"", "none"}:
        return "eligible scene analysis cannot contain an active hard boundary"
    if step1_scene not in FIRST_DAY_SCENES - {"suppress", "health_hold"}:
        return "scene analysis step1_scene is invalid"
    if step2_scene not in FIRST_DAY_SCENES - {"suppress", "health_hold"}:
        return "scene analysis step2_scene is invalid"
    if step1_scene == step2_scene:
        return "first-day scene analysis must select two different scenes"
    for step_key, scene in (("step1", step1_scene), ("step2", step2_scene)):
        completion_status = _string((completion_matrix.get(scene) or {}).get("status"))
        if completion_status in {"completed", "not_applicable"}:
            return (
                f"scene analysis {step_key}_scene cannot select a scene whose completion "
                f"status is {completion_status}"
            )
    if not _string(response.get("step1_objective")) or not _string(response.get("step2_objective")):
        return "scene analysis requires both step objectives"
    required_assets = response.get("required_assets")
    if not isinstance(required_assets, dict):
        return "scene analysis required_assets must be an object"
    available_asset_ids = {
        _string(asset.get("asset_id"))
        for asset in source_snapshot.get("asset_catalog") or []
        if isinstance(asset, dict) and _string(asset.get("asset_id"))
    }
    for key in ("step1", "step2"):
        asset = required_assets.get(key)
        if not isinstance(asset, dict):
            return f"scene analysis required_assets.{key} must be an object"
        strategy = _string(asset.get("strategy")) or "none"
        if strategy not in OUTREACH_ASSET_STRATEGIES:
            return f"scene analysis required_assets.{key}.strategy is invalid"
        if strategy == "case_search":
            return (
                f"scene analysis required_assets.{key}.strategy must use media from the "
                "selected SOP or appointment-blocker source"
            )
        asset_id = _string(asset.get("asset_id"))
        if strategy in {"configured_image", "operation_video"} and (
            not asset_id or asset_id not in available_asset_ids
        ):
            return f"scene analysis required_assets.{key}.asset_id is unavailable"
    payment_action = response.get("payment_action")
    if not isinstance(payment_action, dict):
        return "scene analysis payment_action must be an object"
    payment_step = _int(payment_action.get("step"), -1)
    if payment_step not in {0, 1, 2}:
        return "scene analysis payment_action.step must be 0, 1, or 2"
    payment_allowed = _bool(payment_action.get("allowed"))
    if payment_allowed != (payment_step in {1, 2}):
        return "scene analysis payment_action allowed and step disagree"
    if payment_step != 0 or payment_allowed:
        return "first-day scene analysis cannot authorize payment_collection"
    for evidence in response.get("evidence") or []:
        if not isinstance(evidence, dict):
            return "scene analysis evidence items must be objects"
        message_index = evidence.get("message_index")
        if not isinstance(message_index, int) or not 0 <= message_index < message_count:
            return "scene analysis evidence message_index is outside recent_messages"
    return ""


def _normalize_first_day_scene_analysis(
    response: dict[str, Any],
    *,
    message_count: int,
    source_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return response

    def _normalize_scene_name(value: Any) -> str:
        scene = _string(value)
        return FIRST_DAY_SCENE_ALIASES.get(scene, scene)

    for key in ("current_scene", "step1_scene", "step2_scene"):
        scene = _normalize_scene_name(response.get(key))
        if scene:
            response[key] = scene
    _first_day_normalize_selected_source_ids(response, source_snapshot)
    payment_action = response.get("payment_action")
    if not isinstance(payment_action, dict):
        payment_action = {"step": 0, "allowed": False, "reason": "未选择预约金卡动作"}
        response["payment_action"] = payment_action
    payment_action.update(
        {
            "step": 0,
            "allowed": False,
            "reason": "首日主动唤醒仅允许文字引导转账或红包预约",
        }
    )
    if message_count <= 0:
        return response
    conversation_activity = (source_snapshot or {}).get("conversation_activity") or {}
    unopened_first_day = _int(conversation_activity.get("real_customer_message_count"), -1) == 0
    completion_matrix = response.get("scene_completion_matrix")
    if isinstance(completion_matrix, dict):
        nested_source_ids = completion_matrix.pop("selected_source_ids", None)
        if not isinstance(response.get("selected_source_ids"), dict) and isinstance(
            nested_source_ids,
            dict,
        ):
            response["selected_source_ids"] = nested_source_ids
        response["scene_completion_matrix"] = {
            scene: completion_matrix[scene]
            for scene in FIRST_DAY_COMPLETION_SCENES
            if scene in completion_matrix
        }
        for completion in response["scene_completion_matrix"].values():
            if isinstance(completion, dict):
                completion["asset_ids"] = [
                    _string(asset_id)
                    for asset_id in completion.get("asset_ids") or []
                    if _string(asset_id)
                ]
    raw_evidence = response.get("evidence") or []
    evidence_items = [
        item
        for item in raw_evidence
        if isinstance(item, dict) and isinstance(item.get("message_index"), int)
    ]
    delivered_items = [
        item
        for item in response.get("delivered_scenes") or []
        if isinstance(item, dict)
    ]
    completion_items = [
        item
        for item in (response.get("scene_completion_matrix") or {}).values()
        if isinstance(item, dict)
    ]
    indexes = [item["message_index"] for item in evidence_items]
    for item in delivered_items:
        indexes.extend(index for index in item.get("message_indexes") or [] if isinstance(index, int))
    for item in completion_items:
        indexes.extend(index for index in item.get("message_indexes") or [] if isinstance(index, int))
    writer_indexes = [
        index
        for index in response.get("writer_context_message_indexes") or []
        if isinstance(index, int)
    ]
    indexes.extend(writer_indexes)
    hard_boundary = response.get("hard_boundary") if isinstance(response.get("hard_boundary"), dict) else {}
    hard_boundary_indexes = [
        index for index in hard_boundary.get("message_indexes") or [] if isinstance(index, int)
    ]
    indexes.extend(hard_boundary_indexes)
    precedence_decision = (
        response.get("precedence_decision")
        if isinstance(response.get("precedence_decision"), dict)
        else {}
    )
    if precedence_decision and _string(precedence_decision.get("row_id")) not in FIRST_DAY_PRECEDENCE_ROWS:
        precedence_decision["row_id"] = (
            "hard_boundary"
            if _bool((response.get("hard_boundary") or {}).get("active"))
            else "freeform"
        )
    precedence_indexes = [
        index for index in precedence_decision.get("message_indexes") or [] if isinstance(index, int)
    ]
    indexes.extend(precedence_indexes)
    index_base = _int(response.get("message_index_base"), 0)
    inferred_one_based = bool(indexes) and any(index == message_count for index in indexes)
    use_one_based_indexes = index_base == 1 or inferred_one_based

    def _normalize_message_index(index: Any) -> Any:
        if not isinstance(index, int):
            return index
        if not use_one_based_indexes:
            return index
        # Models occasionally emit 0 for the first message while otherwise using
        # one-based indexes. Treat both 0 and 1 as the first message, then dedupe.
        return max(0, index - 1)

    def _normalize_message_indexes(values: Any) -> list[int]:
        return list(
            dict.fromkeys(
                normalized
                for normalized in (_normalize_message_index(value) for value in values or [])
                if isinstance(normalized, int) and 0 <= normalized < message_count
            )
        )

    if use_one_based_indexes:
        for item in evidence_items:
            item["message_index"] = _normalize_message_index(item["message_index"])
        for item in delivered_items:
            item["message_indexes"] = _normalize_message_indexes(item.get("message_indexes"))
        for item in completion_items:
            item["message_indexes"] = _normalize_message_indexes(item.get("message_indexes"))
        writer_indexes = _normalize_message_indexes(writer_indexes)
        hard_boundary_indexes = _normalize_message_indexes(hard_boundary_indexes)
        precedence_indexes = _normalize_message_indexes(precedence_indexes)
    else:
        for item in delivered_items:
            item["message_indexes"] = _normalize_message_indexes(item.get("message_indexes"))
        for item in completion_items:
            item["message_indexes"] = _normalize_message_indexes(item.get("message_indexes"))
    response["message_index_base"] = 0
    if unopened_first_day:
        response.update(
            {
                "eligible": False,
                "suppress_reason": "first_day_customer_not_opened",
                "current_scene": "suppress",
                "step1_scene": "suppress",
                "step2_scene": "suppress",
                "step1_objective": "",
                "step2_objective": "",
                "hard_boundary": {
                    "active": False,
                    "type": "none",
                    "message_indexes": [],
                    "fact": "客户尚未发送真实消息",
                },
                "precedence_decision": {
                    "row_id": "hard_boundary",
                    "message_indexes": [],
                    "reason": "首日个性化链路仅处理已真实开口客户",
                },
                "writer_context_message_indexes": [0],
                "selected_source_ids": {"step1": [], "step2": []},
                "required_assets": {
                    "step1": {"strategy": "none", "asset_id": "", "reason": "不触达"},
                    "step2": {"strategy": "none", "asset_id": "", "reason": "不触达"},
                },
                "payment_action": {"step": 0, "allowed": False, "reason": "不触达"},
            }
        )
    if _bool(response.get("eligible")):
        step1_scene = _string(response.get("step1_scene"))
        if step1_scene in FIRST_DAY_SCENES:
            response["current_scene"] = step1_scene
    _first_day_normalize_selected_source_ids(response, source_snapshot)
    payment_action = response.get("payment_action")
    if not isinstance(payment_action, dict):
        payment_action = {"step": 0, "allowed": False, "reason": "未选择预约金卡动作"}
        response["payment_action"] = payment_action
    payment_action.update(
        {
            "step": 0,
            "allowed": False,
            "reason": "首日主动唤醒仅允许文字引导转账或红包预约",
        }
    )
    available_assets = {
        _string(item.get("asset_id")): item
        for item in (source_snapshot or {}).get("asset_catalog") or []
        if isinstance(item, dict) and _string(item.get("asset_id"))
    }
    required_assets = response.get("required_assets")
    if isinstance(required_assets, dict):
        for key in ("step1", "step2"):
            required = required_assets.get(key)
            if not isinstance(required, dict):
                continue
            step_scene = _string(response.get(f"{key}_scene"))
            if step_scene in {"effect_proof", "activity_intro"} and (
                (_string(required.get("strategy")) or "none") == "none"
                or not _string(required.get("asset_id"))
            ):
                default_asset_id = _first_day_default_asset_id_for_sources(
                    available_assets,
                    [
                        _string(source_id)
                        for source_id in (response.get("selected_source_ids") or {}).get(key) or []
                        if _string(source_id)
                    ],
                )
                if default_asset_id:
                    required["strategy"] = "configured_image"
                    required["asset_id"] = default_asset_id
                    required["reason"] = "锁定场景含图片素材，代码补齐结构化素材意图"
            strategy = _string(required.get("strategy"))
            if strategy in OUTREACH_ASSET_STRATEGIES:
                continue
            asset = available_assets.get(_string(required.get("asset_id"))) or {}
            asset_type = _string(asset.get("type"))
            required["strategy"] = (
                "operation_video"
                if asset_type == "video"
                else "configured_image"
                if asset_type == "image"
                else "none"
            )
    warnings = _list_strings(response.get("normalization_warnings"))
    valid_evidence = []
    for item in evidence_items:
        index = item.get("message_index")
        if isinstance(index, int) and 0 <= index < message_count:
            valid_evidence.append(item)
        else:
            warnings.append(f"dropped_invalid_evidence_index:{index}")
    if len(valid_evidence) != len(raw_evidence):
        response["evidence"] = valid_evidence
    response["writer_context_message_indexes"] = [
        index for index in writer_indexes if 0 <= index < message_count
    ]
    if hard_boundary:
        hard_boundary["message_indexes"] = [
            index for index in hard_boundary_indexes if 0 <= index < message_count
        ]
    if precedence_decision:
        precedence_decision["message_indexes"] = [
            index for index in precedence_indexes if 0 <= index < message_count
        ]
    if warnings:
        response["normalization_warnings"] = list(dict.fromkeys(warnings))
    customer_mainline = response.get("customer_mainline")
    if not isinstance(customer_mainline, dict):
        summary = _string(customer_mainline) or _string(response.get("unresolved_customer_need"))
        customer_mainline = {"summary": summary}
        response["customer_mainline"] = customer_mainline
    fallback_mainline = (
        _string(customer_mainline.get("summary"))
        or _string(response.get("unresolved_customer_need"))
        or _string(response.get("step1_objective"))
        or "根据最近聊天继续当前销售主线"
    )
    for key, fallback in (
        ("latest_customer_main_need", fallback_mainline),
        ("silence_barrier", fallback_mainline),
        ("symptom_role", "症状信息仅作为素材选择辅助，不作为销售主线"),
        ("next_business_action", _string(response.get("step1_objective")) or fallback_mainline),
    ):
        if not _string(customer_mainline.get(key)):
            customer_mainline[key] = fallback
    if unopened_first_day:
        response.update(
            {
                "eligible": False,
                "suppress_reason": "first_day_customer_not_opened",
                "current_scene": "suppress",
                "scene_completion_matrix": {
                    scene: {
                        "status": "not_applicable",
                        "message_indexes": [],
                        "asset_ids": [],
                        "summary": "客户尚未真实开口，不进入首日个性化计划",
                    }
                    for scene in FIRST_DAY_COMPLETION_SCENES
                },
                "delivered_scenes": [],
                "customer_mainline": {
                    "latest_customer_main_need": "客户尚未真实开口",
                    "silence_barrier": "只有企微自动开场",
                    "symptom_role": "无",
                    "next_business_action": "继续原第三方 SOP 链路",
                },
                "step1_scene": "suppress",
                "step2_scene": "suppress",
                "step1_objective": "",
                "step2_objective": "",
                "forbidden_repetitions": [],
                "writer_context_message_indexes": [0],
                "selected_source_ids": {"step1": [], "step2": []},
                "required_assets": {
                    "step1": {"strategy": "none", "asset_id": "", "reason": "不触达"},
                    "step2": {"strategy": "none", "asset_id": "", "reason": "不触达"},
                },
                "payment_action": {"step": 0, "allowed": False, "reason": "不触达"},
                "hard_boundary": {
                    "active": False,
                    "type": "none",
                    "message_indexes": [],
                    "fact": "客户尚未发送真实消息",
                },
                "precedence_decision": {
                    "row_id": "hard_boundary",
                    "message_indexes": [],
                    "reason": "首日个性化链路仅处理已真实开口客户",
                },
                "message_index_base": 0,
                "evidence": [],
            }
        )
    return response


def _merge_first_day_scene_schema_repair(
    original: dict[str, Any],
    repaired: dict[str, Any],
) -> dict[str, Any]:
    output = dict(repaired) if isinstance(repaired, dict) else {}
    for key in (
        "eligible",
        "suppress_reason",
        "current_scene",
        "unresolved_customer_need",
        "step1_scene",
        "step2_scene",
        "step1_objective",
        "step2_objective",
        "payment_action",
        "confidence",
        "message_index_base",
    ):
        if output.get(key) is None:
            output[key] = original.get(key)
    for key in (
        "scene_completion_matrix",
        "customer_mainline",
        "writer_context_message_indexes",
        "selected_source_ids",
        "required_assets",
        "hard_boundary",
        "precedence_decision",
        "forbidden_repetitions",
        "delivered_scenes",
        "evidence",
    ):
        if output.get(key) in (None, {}, []):
            output[key] = original.get(key)
    original_boundary = original.get("hard_boundary") or {}
    if _bool(original_boundary.get("active")) and _string(original_boundary.get("type")) in FIRST_DAY_HARD_BOUNDARY_TYPES:
        output["eligible"] = False
        output["hard_boundary"] = original_boundary
        output["current_scene"] = _string(original.get("current_scene")) or "health_hold"
        output["step1_scene"] = "suppress"
        output["step2_scene"] = "suppress"
        output["step1_objective"] = ""
        output["step2_objective"] = ""
    return output


def _first_day_scene_lock_error(
    response: dict[str, Any],
    *,
    scene_analysis: dict[str, Any],
) -> str:
    structure_error = _first_day_outreach_plan_error(response)
    if structure_error:
        return structure_error
    steps = [step for step in response.get("steps") or [] if isinstance(step, dict)]
    expected_scenes = [
        _string(scene_analysis.get("step1_scene")),
        _string(scene_analysis.get("step2_scene")),
    ]
    actual_scenes = [_string(step.get("scene")) for step in steps]
    if actual_scenes != expected_scenes:
        return "first-day plan scenes must exactly match the scene analysis contract"
    payment_action = scene_analysis.get("payment_action") or {}
    expected_payment_step = _int(payment_action.get("step"), 0)
    actual_payment_steps = [
        index
        for index, step in enumerate(steps, start=1)
        if _bool(step.get("should_send_payment_collection"))
    ]
    if actual_payment_steps != ([expected_payment_step] if expected_payment_step else []):
        return "first-day plan payment step must exactly match the scene analysis contract"
    required_assets = scene_analysis.get("required_assets") or {}
    for index, step in enumerate(steps, start=1):
        required = required_assets.get(f"step{index}") or {}
        required_strategy = _string(required.get("strategy")) or "none"
        if _string(step.get("asset_strategy")) != required_strategy:
            return f"first-day plan step {index} asset strategy must match scene analysis"
        required_asset_id = _string(required.get("asset_id"))
        if required_asset_id and _string(step.get("asset_id")) != required_asset_id:
            return f"first-day plan step {index} asset id must match scene analysis"
    return ""


def _first_day_final_plan_error(
    response: dict[str, Any],
    *,
    scene_analysis: dict[str, Any],
    source_snapshot: dict[str, Any],
) -> str:
    error = _first_day_scene_lock_error(response, scene_analysis=scene_analysis)
    if error:
        return error
    plan_context = {"source_snapshot": source_snapshot}
    for index, step in enumerate(response.get("steps") or [], start=1):
        policy_error, evidence = _first_day_message_policy_error(
            _plan_step_texts(step),
            step_index=index,
            plan=plan_context,
            context={},
        )
        if policy_error:
            return f"{policy_error}: {evidence}"
    return ""


def _first_day_writer_payload(
    source_snapshot: dict[str, Any],
    scene_analysis: dict[str, Any],
    *,
    appointment_material_catalog: list[dict[str, Any]] | None = None,
    candidate_plan: dict[str, Any] | None = None,
    violations: list[Any] | None = None,
    repair_instructions: list[Any] | None = None,
    deterministic_error: str = "",
) -> dict[str, Any]:
    messages = source_snapshot.get("recent_messages") or []
    selected_indexes = {
        index
        for index in scene_analysis.get("writer_context_message_indexes") or []
        if isinstance(index, int) and 0 <= index < len(messages)
    }
    selected_messages = [
        {"message_index": index, **dict(message)}
        for index, message in enumerate(messages)
        if index in selected_indexes and isinstance(message, dict)
    ]
    selected_source_ids = {
        _string(source_id)
        for key in ("step1", "step2")
        for source_id in (scene_analysis.get("selected_source_ids") or {}).get(key) or []
        if _string(source_id)
    }
    selected_materials = [
        dict(item)
        for item in appointment_material_catalog or []
        if isinstance(item, dict)
        and _string(item.get("source_id")) in selected_source_ids
    ]
    selected_sop_packs = [
        dict(item)
        for item in source_snapshot.get("first_day_sop_sequence") or []
        if isinstance(item, dict)
        and _string(item.get("source_id")) in selected_source_ids
    ]
    required_asset_ids = {
        _string((scene_analysis.get("required_assets") or {}).get(key, {}).get("asset_id"))
        for key in ("step1", "step2")
        if _string((scene_analysis.get("required_assets") or {}).get(key, {}).get("asset_id"))
    }
    selected_assets = [
        dict(item)
        for item in source_snapshot.get("asset_catalog") or []
        if isinstance(item, dict) and _string(item.get("asset_id")) in required_asset_ids
    ]
    payload: dict[str, Any] = {
        "workflow_run_id": _string(source_snapshot.get("workflow_run_id")),
        "scene_contract": scene_analysis,
        "writer_context": {
            "recent_messages": selected_messages,
            "forbidden_repetitions": scene_analysis.get("forbidden_repetitions") or [],
            "selected_materials": selected_materials,
            "selected_sop_packs": selected_sop_packs,
            "selected_assets": selected_assets,
            "conversation_activity": source_snapshot.get("conversation_activity") or {},
            "activity_quote_fact": source_snapshot.get("activity_quote_fact") or {},
            "payment_collection_gate": source_snapshot.get("payment_collection_gate") or {},
        },
    }
    if candidate_plan is not None:
        payment_step = _int((scene_analysis.get("payment_action") or {}).get("step"), 0)
        payload.update(
            {
                "candidate_plan": candidate_plan,
                "violations": violations or [],
                "repair_instructions": repair_instructions or [],
                "deterministic_error": deterministic_error,
                "repair_mode": True,
                "immutable_contract_fields": {
                    "step1": {
                        "scene": _string(scene_analysis.get("step1_scene")),
                        "objective": _string(scene_analysis.get("step1_objective")),
                        "required_asset": (scene_analysis.get("required_assets") or {}).get("step1") or {},
                        "payment_allowed": payment_step == 1,
                    },
                    "step2": {
                        "scene": _string(scene_analysis.get("step2_scene")),
                        "objective": _string(scene_analysis.get("step2_objective")),
                        "required_asset": (scene_analysis.get("required_assets") or {}).get("step2") or {},
                        "payment_allowed": payment_step == 2,
                    },
                },
            }
        )
    return payload


def _normalize_first_day_repaired_plan(
    response: dict[str, Any],
    *,
    scene_analysis: dict[str, Any],
) -> dict[str, Any]:
    steps = [step for step in response.get("steps") or [] if isinstance(step, dict)]
    if len(steps) != 2:
        return response
    required_assets = scene_analysis.get("required_assets") or {}
    selected_source_ids = scene_analysis.get("selected_source_ids") or {}
    payment_step = 0
    for index, step in enumerate(steps, start=1):
        step["step"] = index
        step["scene"] = _string(scene_analysis.get(f"step{index}_scene"))
        reply_messages = [
            dict(message)
            for message in step.get("reply_messages") or []
            if isinstance(message, dict)
            and _string(message.get("type")) == "text"
            and isinstance(message.get("content"), dict)
            and _string(message["content"].get("text"))
        ]
        for message_index, message in enumerate(reply_messages, start=1):
            message["order"] = message_index
        step["reply_messages"] = reply_messages
        required_asset = required_assets.get(f"step{index}") or {}
        step["asset_strategy"] = _string(required_asset.get("strategy")) or "none"
        step["asset_id"] = _string(required_asset.get("asset_id"))
        step["content_sources"] = [
            _string(source_id)
            for source_id in selected_source_ids.get(f"step{index}") or []
            if _string(source_id)
        ]
        should_pay = payment_step == index
        step["should_send_payment_collection"] = should_pay
        step["payment_collection_basis"] = (
            "model_selected_after_quote" if should_pay else "none"
        )
        if should_pay:
            step["content_mode"] = "transaction"
        step["delay_minutes"] = 0 if index == 1 else min(
            20,
            max(15, _int(step.get("delay_minutes"), 15)),
        )
    return response


def _first_day_verifier_error(response: dict[str, Any]) -> str:
    if not isinstance(response, dict):
        return "first-day verifier response must be a json object"
    decision = _string(response.get("decision"))
    if decision not in {"pass", "repair", "replan", "block"}:
        return "first-day verifier decision must be pass, repair, replan, or block"
    violations = response.get("violations")
    if not isinstance(violations, list):
        return "first-day verifier violations must be a list"
    if any(
        not isinstance(item, dict)
        or not _string(item.get("code"))
        or not _string(item.get("field"))
        or not _string(item.get("evidence"))
        for item in violations
    ):
        return "first-day verifier violations contain an invalid item"
    repair_instructions = response.get("repair_instructions")
    if not isinstance(repair_instructions, list):
        return "first-day verifier repair_instructions must be a list"
    if any(
        not isinstance(item, dict)
        or not _string(item.get("field"))
        or not _string(item.get("instruction"))
        for item in repair_instructions
    ):
        return "first-day verifier repair_instructions contain an invalid item"
    replan_instructions = response.get("replan_instructions", [])
    if not isinstance(replan_instructions, list):
        return "first-day verifier replan_instructions must be a list"
    if any(
        not isinstance(item, dict)
        or not _string(item.get("field"))
        or not _string(item.get("instruction"))
        for item in replan_instructions
    ):
        return "first-day verifier replan_instructions contain an invalid item"
    if "verified_plan" in response or "steps" in response or "candidate_plan" in response:
        return "first-day verifier must not return customer plan content"
    block_category = _string(response.get("block_category")) or "none"
    if block_category not in {"none", "source_hard_boundary", "locked_scene_impossible"}:
        return "first-day verifier block_category is invalid"
    if decision == "block":
        if block_category == "none" or not violations or repair_instructions or replan_instructions:
            return "blocked verifier response requires a source block category and evidence"
        return ""
    if block_category != "none":
        return "pass, repair, or replan verifier response must use block_category=none"
    if decision == "pass" and (violations or repair_instructions or replan_instructions):
        return "passing verifier response must not include violations or repair instructions"
    if decision == "repair" and (not violations or not repair_instructions or replan_instructions):
        return "repair verifier response requires violations and repair instructions"
    if decision == "replan" and (not violations or repair_instructions or not replan_instructions):
        return "replan verifier response requires violations and replan_instructions"
    immutable_suffixes = (
        ".scene",
        ".delay_minutes",
        ".asset_strategy",
        ".asset_id",
        ".should_send_payment_collection",
        ".payment_collection_basis",
    )
    immutable_fields = [
        _string(item.get("field"))
        for item in [*violations, *repair_instructions]
        if isinstance(item, dict)
        and any(_string(item.get("field")).endswith(suffix) for suffix in immutable_suffixes)
    ]
    if immutable_fields and decision != "replan":
        return (
            "first-day verifier cannot repair immutable contract fields: "
            + ", ".join(dict.fromkeys(immutable_fields))
        )
    if decision == "replan" and not any(
        _string(item.get("field")).startswith("scene_contract.")
        for item in violations
        if isinstance(item, dict)
    ):
        return "replan verifier response must identify a scene_contract field"
    return ""


def _first_day_upgrade_scene_repeat_repair_to_replan(
    response: dict[str, Any],
    *,
    scene_analysis: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(response, dict) or _string(response.get("decision")) != "repair":
        return response
    repeated_step_fields: set[str] = set()
    for item in [*(response.get("violations") or []), *(response.get("repair_instructions") or [])]:
        if not isinstance(item, dict):
            continue
        code = _string(item.get("code")).lower()
        field = _string(item.get("field")).lower()
        text = f"{code} {_string(item.get('evidence'))} {_string(item.get('instruction'))}".lower()
        if "duplicate_scene_semantics" not in text and "location_slot_already_waiting" not in text:
            continue
        if "steps[0]" in field or "steps.0" in field:
            repeated_step_fields.add("step1")
        if "steps[1]" in field or "steps.1" in field:
            repeated_step_fields.add("step2")
    if not repeated_step_fields:
        return response

    replan_instructions: list[dict[str, str]] = []
    violations: list[dict[str, str]] = []
    for step_key in sorted(repeated_step_fields):
        scene_field = f"{step_key}_scene"
        if _string(scene_analysis.get(scene_field)) != "store_area_request":
            continue
        contract_field = "scene_contract.step1_scene" if step_key == "step1" else "scene_contract.step2_scene"
        violations.append(
            {
                "code": "store_area_request_duplicate_scene_semantics",
                "field": contract_field,
                "evidence": "审核节点已识别该门店位置任务与近期历史语义重复，不能通过换问法修复。",
            }
        )
        replan_instructions.append(
            {
                "field": contract_field,
                "instruction": "门店位置场景已完成或正在等待同一信息，选择其他尚未完成且不依赖继续询问位置的场景。",
            }
        )
    if not replan_instructions:
        return response
    upgraded = dict(response)
    upgraded["decision"] = "replan"
    upgraded["block_category"] = "none"
    upgraded["violations"] = violations
    upgraded["repair_instructions"] = []
    upgraded["replan_instructions"] = replan_instructions
    return upgraded


def _is_first_day_opened_silence_trigger(trigger_context: dict[str, Any] | None) -> bool:
    trigger = trigger_context if isinstance(trigger_context, dict) else {}
    return _string(trigger.get("trigger_type")) == FIRST_DAY_SILENCE_TRIGGER_TYPE


def _first_day_existing_run_retry_reason(
    existing_run: dict[str, Any],
    *,
    latest_customer_message_at: str,
    now: datetime | None = None,
) -> str:
    if not existing_run:
        return "new_run"
    status = _string(existing_run.get("status"))
    reason_code = _string(existing_run.get("reason_code"))
    if status == "failed":
        next_retry_at = _parse_iso(_string(existing_run.get("next_retry_at")))
        current = now or datetime.now(timezone.utc)
        if next_retry_at and next_retry_at.tzinfo is None:
            next_retry_at = next_retry_at.replace(tzinfo=timezone.utc)
        if not next_retry_at or next_retry_at > current:
            return ""
        return "failed_retry"
    if reason_code in FIRST_DAY_NON_RETRYABLE_RUN_REASONS:
        return ""
    if (
        status == "blocked"
        and reason_code in FIRST_DAY_RETRYABLE_SOFT_BLOCK_REASONS
        and _string(latest_customer_message_at)
    ):
        return f"soft_block_retry:{reason_code}"
    if status in {"running", "created"} and _string(existing_run.get("plan_id")):
        return ""
    if status in {"running", "created"}:
        reference = _parse_iso(_string(existing_run.get("updated_at"))) or _parse_iso(
            _string(existing_run.get("started_at"))
        )
        current = now or datetime.now(timezone.utc)
        if reference and reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        if reference and current - reference >= timedelta(minutes=FIRST_DAY_STALE_RUNNING_RETRY_MINUTES):
            return f"stale_{status}_retry"
    return ""




class FirstDayWorkflow:
    def __init__(
        self,
        *,
        repository: Any,
        model_client: Any,
        customer_context_service: Any,
        first_day_wechat_allowlist: str,
        planning: Any,
    ) -> None:
        self.repository = repository
        self.model_client = model_client
        self.customer_context_service = customer_context_service
        self.first_day_wechat_allowlist = first_day_wechat_allowlist
        self.planning = planning
        self._monitor_status: dict[str, Any] = {
            "last_scan_started_at": "",
            "last_scan_finished_at": "",
            "candidate_count": 0,
            "evaluated_count": 0,
            "created_count": 0,
            "rejected_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "skip_reasons": {},
            "last_error": "",
        }

    def _sync_first_day_run_for_task(
        self,
        *,
        plan: dict[str, Any],
        task: dict[str, Any],
        status: str,
        reason_code: str,
        final_decision: str,
        terminal: bool = False,
        error: Exception | str | None = None,
    ) -> None:
        snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
        workflow_run_id = _string(snapshot.get("workflow_run_id"))
        if not workflow_run_id:
            return
        current = self.repository.get_first_day_outreach_run(workflow_run_id, include_related=False)
        changes: dict[str, Any] = {
            "status": status,
            "reason_code": reason_code,
            "final_decision": final_decision,
        }
        if terminal:
            changes["finished_at"] = utc_now_iso()
            started_at = _parse_iso(_string(current.get("started_at")))
            if started_at:
                changes["duration_ms"] = max(
                    0,
                    round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
                )
        if error is not None:
            changes.update(
                error_node="task_send",
                error_type=type(error).__name__ if isinstance(error, Exception) else "TaskExecutionError",
                error_message=str(error)[:4000],
            )
        if reason_code in {"daily_limit", "before_send_check_failed"}:
            changes["retry_count"] = int(current.get("retry_count") or 0) + 1
        self.repository.update_first_day_outreach_run(workflow_run_id, **changes)

    async def evaluate_first_day_opened_silence_customers(
        self,
        *,
        limit: int = 5,
        silent_minutes: int = 3,
        auto_activate: bool = True,
    ) -> dict[str, Any]:
        started_at = utc_now_iso()
        stats: dict[str, Any] = {
            "mode": "first_day_opened_silence",
            "last_scan_started_at": started_at,
            "last_scan_finished_at": "",
            "candidate_count": 0,
            "evaluated_count": 0,
            "created_count": 0,
            "rejected_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "skip_reasons": {},
            "last_error": "",
            "results": [],
        }
        scan_limit = max(200, min(2000, max(1, int(limit)) * 200))
        candidates = await asyncio.to_thread(
            self.planning.list_candidates,
            limit=scan_limit,
            silent_minutes_min=0,
        )
        sop_candidate_loader = getattr(self.repository, "list_first_day_sop_contact_candidates", None)
        if callable(sop_candidate_loader):
            since = (
                datetime.now(timezone.utc) - timedelta(minutes=FIRST_DAY_WINDOW_MINUTES)
            ).isoformat()
            sop_candidates = await asyncio.to_thread(
                sop_candidate_loader,
                limit=scan_limit,
                since=since,
            )
            candidates.extend(sop_candidates)
            candidates = _dedupe_outreach_candidates(candidates)
        stats["candidate_count"] = len(candidates)
        effective_limit = max(1, int(limit))
        threshold_minutes = max(1, int(silent_minutes))

        def _first_day_priority(candidate: dict[str, Any]) -> tuple[int, int, float, str]:
            rough_reason = self._rough_first_day_silence_candidate_reason(
                candidate,
                silent_minutes=threshold_minutes,
            )
            if rough_reason:
                return (1, 0, 0.0, _string(candidate.get("customer_id")))
            reply_wait = max(0, _int(candidate.get("reply_wait_minutes"), 0))
            outbound_at = (
                _parse_iso(_string(candidate.get("latest_outbound_message_at")))
                or _parse_iso(_string(candidate.get("last_staff_message_at")))
                or _parse_iso(_string(candidate.get("last_ai_reply_at")))
                or _parse_iso(_string(candidate.get("updated_at")))
            )
            outbound_ts = outbound_at.timestamp() if outbound_at else 0.0
            return (
                0,
                max(0, reply_wait - threshold_minutes),
                -outbound_ts,
                _string(candidate.get("customer_id")),
            )

        candidates = sorted(candidates, key=_first_day_priority)
        evaluated_budget_used = 0
        for candidate in candidates:
            if evaluated_budget_used >= effective_limit:
                break
            rough_reason = self._rough_first_day_silence_candidate_reason(
                candidate,
                silent_minutes=threshold_minutes,
            )
            if rough_reason:
                result = {
                    "status": "skipped",
                    "customer_id": _string(candidate.get("customer_id")),
                    "reason": rough_reason,
                }
            else:
                try:
                    result = await self._evaluate_first_day_silence_candidate(
                        candidate,
                        silent_minutes=threshold_minutes,
                        auto_activate=auto_activate,
                    )
                except Exception as exc:
                    result = {
                        "status": "error",
                        "customer_id": _string(candidate.get("customer_id")),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            stats["results"].append(result)
            status = _string(result.get("status"))
            if status == "evaluated":
                stats["evaluated_count"] += 1
                if result.get("created"):
                    stats["created_count"] += 1
                else:
                    stats["rejected_count"] += 1
                evaluated_budget_used += 1
            elif status == "error":
                stats["error_count"] += 1
                stats["last_error"] = _string(result.get("error"))
                evaluated_budget_used += 1
            else:
                stats["skipped_count"] += 1
                reason = _string(result.get("reason")) or "unknown"
                skip_reasons = stats["skip_reasons"]
                skip_reasons[reason] = int(skip_reasons.get(reason) or 0) + 1
        stats["last_scan_finished_at"] = utc_now_iso()
        self._monitor_status = {key: value for key, value in stats.items() if key != "results"}
        return stats

    @staticmethod
    def _rough_first_day_silence_candidate_reason(candidate: dict[str, Any], *, silent_minutes: int) -> str:
        if _string(candidate.get("candidate_source")) == "sop_send_tasks":
            return ""
        if not _string(candidate.get("last_customer_message_at")):
            return "customer_never_spoke"
        if not bool(candidate.get("awaiting_customer_reply")):
            return "not_waiting_for_customer_reply"
        if _int(candidate.get("reply_wait_minutes"), 0) < silent_minutes:
            return "reply_wait_below_threshold"
        manual_takeover = _parse_iso(_string(candidate.get("last_manual_takeover_at")))
        remembered_customer = _parse_iso(_string(candidate.get("last_customer_message_at")))
        if manual_takeover and remembered_customer and manual_takeover >= remembered_customer:
            return "manual_takeover_active"
        return ""

    async def _evaluate_first_day_silence_candidate(
        self,
        candidate: dict[str, Any],
        *,
        silent_minutes: int,
        auto_activate: bool,
    ) -> dict[str, Any]:
        identity = {
            "customer_id": _string(candidate.get("customer_id")),
            "corp_id": _string(candidate.get("corp_id")),
            "user_id": _string(candidate.get("user_id")),
            "wechat": _string(candidate.get("wechat")),
            "external_userid": _string(candidate.get("external_userid")),
        }
        customer_id = identity["customer_id"]
        discovery_added_at = _string(candidate.get("sales_contact_started_at"))
        if not all((customer_id, identity["corp_id"], identity["wechat"], identity["external_userid"])):
            return {"status": "skipped", "customer_id": customer_id, "reason": "incomplete_sales_contact_identity"}
        if not _first_day_wechat_allowed(identity["wechat"], self.first_day_wechat_allowlist):
            return {
                "status": "skipped",
                "customer_id": customer_id,
                "wechat": identity["wechat"],
                "reason": "first_day_wechat_not_allowed",
            }
        candidate_customer_at = _string(candidate.get("last_customer_message_at"))
        candidate_staff_at = _string(
            candidate.get("latest_outbound_message_at") or candidate.get("last_staff_message_at")
        )
        candidate_fingerprint = _conversation_fingerprint(
            corp_id=identity["corp_id"],
            wechat=identity["wechat"],
            external_userid=identity["external_userid"],
            customer_id=customer_id,
            latest_customer_message_at=candidate_customer_at,
            latest_staff_message_at=candidate_staff_at,
        )
        lock = self.planning._plan_lock(identity)
        async with lock:
            active = await asyncio.to_thread(
                self.repository.get_active_outreach_plan_for_customer,
                customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
            )
            if active:
                return {"status": "skipped", "customer_id": customer_id, "reason": "nonterminal_plan_exists"}
            run_finder = getattr(self.repository, "find_first_day_outreach_run_by_fingerprint", None)
            existing_run = (
                await asyncio.to_thread(
                    run_finder,
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    conversation_fingerprint=candidate_fingerprint,
                )
                if callable(run_finder)
                else {}
            )
            retry_reason = _first_day_existing_run_retry_reason(
                existing_run,
                latest_customer_message_at=candidate_customer_at,
            )
            if (
                existing_run
                and not retry_reason
                and _string(candidate.get("candidate_source")) == "sop_send_tasks"
                and not candidate_customer_at
            ):
                retry_reason = "sop_candidate_requires_platform_refresh"
            if existing_run and not retry_reason:
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "conversation_fingerprint_already_logged",
                }
            if existing_run:
                workflow_run_id = _string(existing_run.get("workflow_run_id"))
                run_updater = getattr(self.repository, "update_first_day_outreach_run", None)
                if callable(run_updater):
                    await asyncio.to_thread(
                        run_updater,
                        workflow_run_id,
                        status="running",
                        reason_code="preflight_retry",
                        final_decision="retrying",
                        retry_count=int(existing_run.get("retry_count") or 0) + 1,
                        error_node="",
                        error_type="",
                        error_message="",
                        finished_at="",
                        next_retry_at="",
                        workflow={
                            **(
                                existing_run.get("workflow")
                                if isinstance(existing_run.get("workflow"), dict)
                                else {}
                            ),
                            "retry_reason": retry_reason,
                        },
                    )
            else:
                run_creator = getattr(self.repository, "create_first_day_outreach_run", None)
                if callable(run_creator):
                    run = await asyncio.to_thread(
                        run_creator,
                        **identity,
                        trigger_type=FIRST_DAY_SILENCE_TRIGGER_TYPE,
                        conversation_fingerprint=candidate_fingerprint,
                        input_snapshot={
                            "trigger_context": {
                                "source": "silence_monitor",
                                "trigger_type": FIRST_DAY_SILENCE_TRIGGER_TYPE,
                                "conversation_fingerprint": candidate_fingerprint,
                                "discovery_added_at": discovery_added_at,
                                "latest_customer_message_at": candidate_customer_at,
                                "latest_staff_message_at": candidate_staff_at,
                                "monitor_silent_minutes": silent_minutes,
                            }
                        },
                    )
                    workflow_run_id = _string(run.get("workflow_run_id"))
                else:
                    workflow_run_id = ""

            run_updater = getattr(self.repository, "update_first_day_outreach_run", None)

            async def _update_run(**changes: Any) -> None:
                if workflow_run_id and callable(run_updater):
                    await asyncio.to_thread(run_updater, workflow_run_id, **changes)

            try:
                refreshed = await self.planning.refresh_customer_conversation(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    user_id=identity["user_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    limit=50,
                )
            except Exception as exc:
                await _update_run(
                    status="failed",
                    reason_code="conversation_refresh_failed",
                    final_decision="retry_pending",
                    error_node="conversation_refresh",
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:4000],
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "error",
                    "customer_id": customer_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            customer_relation = (
                refreshed.get("customer_relation")
                if isinstance(refreshed.get("customer_relation"), dict)
                else {}
            )
            if not customer_relation.get("available"):
                self.planning._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    reason="customer_relation_unavailable",
                    relation=customer_relation,
                    trigger_context={"source": "silence_monitor", "trigger_type": FIRST_DAY_SILENCE_TRIGGER_TYPE},
                )
                await _update_run(
                    status="blocked",
                    reason_code="customer_relation_unavailable",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "customer_relation_unavailable"}
            if customer_relation_is_deleted(customer_relation):
                self.planning._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    reason="customer_deleted",
                    relation=customer_relation,
                    trigger_context={"source": "silence_monitor", "trigger_type": FIRST_DAY_SILENCE_TRIGGER_TYPE},
                )
                await _update_run(
                    status="blocked",
                    reason_code="customer_deleted",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "customer_deleted",
                    "customer_relation": customer_relation,
                }
            messages = refreshed.get("messages") or []
            first_added_at = _string(refreshed.get("first_added_at"))
            authoritative_added_at = _parse_iso(first_added_at)
            if not authoritative_added_at:
                await _update_run(
                    status="blocked",
                    reason_code="first_added_at_unavailable",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "first_added_at_unavailable",
                }
            if not _is_within_first_day(first_added_at):
                await _update_run(
                    status="blocked",
                    reason_code="not_first_day",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "not_first_day"}
            conversation_id = _string(refreshed.get("conversation_id"))
            if not conversation_id:
                await _update_run(
                    status="blocked",
                    reason_code="conversation_id_unavailable",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "conversation_id_unavailable",
                }
            local_now = datetime.now(timezone.utc).astimezone(OUTREACH_BEIJING_TIMEZONE)
            local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            local_day_end = local_day_start + timedelta(days=1)
            created_today = await asyncio.to_thread(
                self.repository.count_outreach_plans_for_trigger_between,
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                trigger_type=FIRST_DAY_SILENCE_TRIGGER_TYPE,
                started_at=local_day_start.astimezone(timezone.utc).isoformat(),
                ended_at=local_day_end.astimezone(timezone.utc).isoformat(),
            )
            if created_today >= FIRST_DAY_DAILY_PLAN_LIMIT:
                await _update_run(
                    status="blocked",
                    reason_code="first_day_daily_plan_limit_reached",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "first_day_daily_plan_limit_reached",
                    "created_today": created_today,
                    "daily_limit": FIRST_DAY_DAILY_PLAN_LIMIT,
                }
            real_customer_count = _real_customer_message_count(messages)
            latest_customer_text = _latest_real_customer_message_time(messages)
            latest_staff_text = self.planning._latest_message_time(messages, sender="staff")
            latest_customer = _parse_iso(latest_customer_text)
            latest_staff = _parse_iso(latest_staff_text)
            if real_customer_count <= 0 or not latest_customer:
                await _update_run(
                    status="blocked",
                    reason_code="customer_never_spoke",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "customer_never_spoke"}
            if not latest_staff or latest_staff <= latest_customer:
                await _update_run(
                    status="cancelled",
                    reason_code="customer_replied",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "not_waiting_for_customer_reply"}
            if self.planning._completed_cycle_blocks_auto_plan(
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                latest_customer_message_at=latest_customer_text,
            ):
                await _update_run(
                    status="blocked",
                    reason_code="outreach_cycle_completed_without_new_customer_reply",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "outreach_cycle_completed_without_new_customer_reply",
                }
            wait_minutes = max(
                0,
                int((datetime.now(timezone.utc) - latest_staff.astimezone(timezone.utc)).total_seconds() // 60),
            )
            if wait_minutes < silent_minutes:
                await _update_run(
                    status="cancelled",
                    reason_code="reply_wait_below_threshold",
                    final_decision="wait_for_silence",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "reply_wait_below_threshold"}
            conversation_fingerprint = _conversation_fingerprint(
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                customer_id=customer_id,
                latest_customer_message_at=latest_customer_text,
                latest_staff_message_at=latest_staff_text,
            )
            authoritative_existing_run = (
                await asyncio.to_thread(
                    run_finder,
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    conversation_fingerprint=conversation_fingerprint,
                )
                if callable(run_finder)
                else {}
            )
            if authoritative_existing_run and _string(
                authoritative_existing_run.get("workflow_run_id")
            ) != workflow_run_id:
                await _update_run(
                    status="blocked",
                    reason_code="authoritative_fingerprint_already_logged",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "conversation_fingerprint_already_logged",
                    "workflow_run_id": _string(
                        authoritative_existing_run.get("workflow_run_id")
                    ),
                }
            await _update_run(conversation_fingerprint=conversation_fingerprint)
            if await asyncio.to_thread(
                self.repository.has_outreach_evaluation_fingerprint,
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                conversation_fingerprint=conversation_fingerprint,
            ):
                await _update_run(
                    status="blocked",
                    reason_code="conversation_fingerprint_already_evaluated",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "conversation_fingerprint_already_evaluated",
                }
            local_context = await asyncio.to_thread(
                self.repository.recent_customer_context,
                customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
            )
            customer_context = await self._load_monitor_customer_context(
                identity=identity,
                memory=local_context.get("memory") or {},
            )
            order_gate = personalized_order_eligibility(customer_context)
            if not order_gate.get("available"):
                await _update_run(
                    status="failed",
                    reason_code="order_context_unavailable",
                    final_decision="retry_pending",
                    error_node="customer_context",
                    error_type="OrderContextUnavailable",
                    error_message=_string(order_gate.get("reason")),
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "order_context_unavailable"}
            if not order_gate.get("eligible"):
                order_reason = _string(order_gate.get("reason")) or "order_not_eligible"
                await _update_run(
                    status="blocked",
                    reason_code=order_reason,
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": order_reason,
                }
            source_context = {
                "memory": local_context.get("memory") or {},
                "recent_messages": messages[-50:],
                "customer_relation": customer_relation,
                "conversation_activity": {
                    "real_customer_message_count": real_customer_count,
                    "latest_customer_message_at": latest_customer_text,
                    "latest_staff_message_at": latest_staff_text,
                    "reply_wait_minutes": wait_minutes,
                    "customer_silence_minutes": max(
                        0,
                        int((datetime.now(timezone.utc) - latest_customer.astimezone(timezone.utc)).total_seconds() // 60),
                    ),
                    "awaiting_customer_reply": True,
                },
                "customer_context": customer_context,
                "conversation_id": conversation_id,
            }
            result = await self.planning.generate_plan(
                **identity,
                current_stage="first_day_opened_silence",
                business_goal="首日已开口客户在意向最高窗口沉默后，先轻触达承接最近聊天，再按状态推进效果、报价、预约金或异议处理",
                sop_plan_id=FIRST_DAY_SOP_PLAN_ID,
                source_context=source_context,
                trigger_context={
                    "source": "silence_monitor",
                    "trigger_type": FIRST_DAY_SILENCE_TRIGGER_TYPE,
                    "activation_policy": "auto_approved",
                    "conversation_fingerprint": conversation_fingerprint,
                    "conversation_id": conversation_id,
                    "first_added_at": first_added_at,
                    "latest_customer_message_at": latest_customer_text,
                    "latest_staff_message_at": latest_staff_text,
                    "reply_wait_minutes": wait_minutes,
                    "monitor_silent_minutes": silent_minutes,
                },
                workflow_run_id=workflow_run_id,
            )
            if not result.get("created"):
                return {
                    "status": "evaluated",
                    "customer_id": customer_id,
                    "created": False,
                    "reason": "model_rejected_plan",
                    "ai_result": result.get("ai_result") or {},
                }
            plan_id = _string((result.get("plan") or {}).get("id") or result.get("id"))
            if not plan_id:
                raise RuntimeError("first_day_silence_plan_missing_id")
            if auto_activate:
                activated = self.planning._auto_approve_plan(plan_id)
                result = {**result, **activated, "auto_approved": True}
            return {
                "status": "evaluated",
                "customer_id": customer_id,
                "created": True,
                "plan_id": plan_id,
                "result": result,
            }

    async def _load_monitor_customer_context(
        self,
        *,
        identity: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        if self.customer_context_service is None:
            return {}
        request_context = {
            "customer_id": identity.get("customer_id"),
            "corp_id": identity.get("corp_id"),
            "wechat": identity.get("wechat"),
            "external_userid": identity.get("external_userid"),
            "user_id": identity.get("user_id"),
        }
        return await asyncio.to_thread(
            self.customer_context_service.load,
            customer_id=_string(identity.get("customer_id")),
            memory=memory,
            request_context=request_context,
        )

    def monitor_status(self) -> dict[str, Any]:
        return dict(self._monitor_status)
