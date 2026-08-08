from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

from app.services.customer_context import CustomerContextService
from app.services.customer_relation import (
    customer_relation_is_deleted,
    normalize_customer_relation,
)
from app.services.customer_scope import build_customer_scope
from app.services.coze_client import CozeClient
from app.services.model_client import ModelClient
from app.services.precision_qa_playbook_service import PrecisionQaPlaybookService
from app.services.sop_reply_pack_service import SopReplyPackService
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
    "store_prompt": 10,
    "effect_case": 20,
    "activity_intro": 30,
    "price_quote": 30,
    "deposit_push": 40,
    "payment_followup": 50,
    "operation_video": 60,
    "final_close": 70,
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


def _task_content_sources(
    raw_sources: Any,
    *,
    should_send_payment_collection: bool,
    task_metadata: dict[str, Any],
    resolved_asset: dict[str, Any],
) -> list[Any]:
    sources = _list_strings(raw_sources) or ["s10_offer"]
    sources.extend(
        [
            {"should_send_payment_collection": bool(should_send_payment_collection)},
            {"outreach_task_metadata": task_metadata},
            {"resolved_asset": resolved_asset},
        ]
    )
    return sources


def _compose_outreach_messages(
    texts: str | list[str],
    *,
    resolved_asset: dict[str, Any] | None = None,
    should_send_payment_collection: bool = False,
) -> list[dict[str, Any]]:
    normalized_texts = [_string(item) for item in ([texts] if isinstance(texts, str) else texts)]
    output = [
        {"type": "text", "order": index, "content": {"text": text}}
        for index, text in enumerate(normalized_texts[:2], start=1)
        if text
    ]
    asset_message = asset_reply_message(resolved_asset or {}, order=len(output) + 1)
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
    items = task.get("content_source_metadata")
    if not isinstance(items, list):
        items = [item for item in task.get("content_sources", []) if isinstance(item, dict)]
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("resolved_asset"), dict):
            return dict(item["resolved_asset"])
    return {}


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
                before_step_index=index,
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
    if len(payment_steps) > 1:
        return "plan can contain at most one payment_collection step"
    if payment_steps:
        payment_step = payment_steps[0]
        if _string(payment_step.get("content_mode")) != "transaction":
            return "payment_collection step must use transaction content_mode"
        if _string(payment_step.get("payment_collection_basis")) != "model_selected_after_quote":
            return "payment_collection step must use model_selected_after_quote basis"
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
    for key in ("step1", "step2"):
        source_ids = selected_source_ids.get(key)
        if not isinstance(source_ids, list) or any(
            not _string(source_id) or _string(source_id) not in available_source_ids
            for source_id in source_ids
        ):
            return f"scene analysis selected_source_ids.{key} contains unavailable source"
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
    deposit_steps = [
        index
        for index, scene in enumerate((step1_scene, step2_scene), start=1)
        if scene == "deposit_close"
    ]
    if deposit_steps != ([payment_step] if payment_step else []):
        return "scene analysis deposit scene must exactly match payment_action.step"
    if payment_allowed:
        payment_gate = source_snapshot.get("payment_collection_gate") or {}
        expected_scene = step1_scene if payment_step == 1 else step2_scene
        if not _bool(payment_gate.get("eligible")) or expected_scene != "deposit_close":
            return "scene analysis payment action violates payment gate or scene lock"
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
    payment_action = response.get("payment_action")
    if not isinstance(payment_action, dict):
        payment_action = {"step": 0, "allowed": False, "reason": "未选择预约金卡动作"}
        response["payment_action"] = payment_action
    deposit_steps = [
        index
        for index, scene in enumerate(
            (_string(response.get("step1_scene")), _string(response.get("step2_scene"))),
            start=1,
        )
        if scene == "deposit_close"
    ]
    payment_gate = (source_snapshot or {}).get("payment_collection_gate") or {}
    if len(deposit_steps) == 1 and _bool(payment_gate.get("eligible")):
        payment_action["step"] = deposit_steps[0]
        payment_action["allowed"] = True
    elif not _bool(payment_action.get("allowed")):
        payment_action["step"] = 0
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
    payment_action = response.get("payment_action")
    if not isinstance(payment_action, dict):
        payment_action = {"step": 0, "allowed": False, "reason": "未选择预约金卡动作"}
        response["payment_action"] = payment_action
    if isinstance(payment_action, dict):
        deposit_steps = [
            index
            for index, scene in enumerate(
                (_string(response.get("step1_scene")), _string(response.get("step2_scene"))),
                start=1,
            )
            if scene == "deposit_close"
        ]
        payment_gate = (source_snapshot or {}).get("payment_collection_gate") or {}
        if len(deposit_steps) == 1 and _bool(payment_gate.get("eligible")):
            payment_action["step"] = deposit_steps[0]
            payment_action["allowed"] = True
        elif not _bool(payment_action.get("allowed")):
            payment_action["step"] = 0
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
    if available_source_ids and isinstance(response.get("selected_source_ids"), dict):
        for key in ("step1", "step2"):
            normalized_ids: list[str] = []
            for raw_id in response["selected_source_ids"].get(key) or []:
                source_id = _string(raw_id)
                if source_id in available_source_ids:
                    normalized_ids.append(source_id)
                    continue
                suffix_matches = [
                    candidate
                    for candidate in available_source_ids
                    if candidate.endswith(f":{source_id}")
                ]
                if len(suffix_matches) == 1:
                    normalized_ids.append(suffix_matches[0])
            response["selected_source_ids"][key] = list(dict.fromkeys(normalized_ids))
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
    payment_step = _int((scene_analysis.get("payment_action") or {}).get("step"), 0)
    for index, step in enumerate(steps, start=1):
        step["step"] = index
        step["scene"] = _string(scene_analysis.get(f"step{index}_scene"))
        required_asset = required_assets.get(f"step{index}") or {}
        step["asset_strategy"] = _string(required_asset.get("strategy")) or "none"
        step["asset_id"] = _string(required_asset.get("asset_id"))
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
    if decision not in {"pass", "repair", "block"}:
        return "first-day verifier decision must be pass, repair, or block"
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
    if "verified_plan" in response or "steps" in response or "candidate_plan" in response:
        return "first-day verifier must not return customer plan content"
    block_category = _string(response.get("block_category")) or "none"
    if block_category not in {"none", "source_hard_boundary", "locked_scene_impossible"}:
        return "first-day verifier block_category is invalid"
    if decision == "block":
        if block_category == "none" or not violations or repair_instructions:
            return "blocked verifier response requires a source block category and evidence"
        return ""
    if block_category != "none":
        return "pass or repair verifier response must use block_category=none"
    if decision == "pass" and (violations or repair_instructions):
        return "passing verifier response must not include violations or repair instructions"
    if decision == "repair" and (not violations or not repair_instructions):
        return "repair verifier response requires violations and repair instructions"
    return ""


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


class OutreachService:
    def __init__(
        self,
        *,
        repository: AppRepository,
        model_client: ModelClient,
        system_client: OutreachSystemClient,
        customer_context_service: CustomerContextService | None = None,
        precision_qa_playbook_service: PrecisionQaPlaybookService | None = None,
        sop_reply_pack_service: SopReplyPackService | None = None,
        coze_client: CozeClient | None = None,
        before_send_retry_seconds: int = 60,
    ) -> None:
        self.repository = repository
        self.model_client = model_client
        self.system_client = system_client
        self.customer_context_service = customer_context_service
        self.precision_qa_playbook_service = precision_qa_playbook_service
        self.sop_reply_pack_service = sop_reply_pack_service
        self.coze_client = coze_client
        self.before_send_retry_seconds = max(1, int(before_send_retry_seconds))
        self._plan_locks: dict[str, asyncio.Lock] = {}
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

    async def _run_first_day_model_node(
        self,
        *,
        node: str,
        prompt: str,
        prompt_version: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        attempts: list[dict[str, Any]] = []
        response: dict[str, Any] = {}
        for attempt in range(1, 4):
            attempt_started = time.perf_counter()
            try:
                response = await self.model_client.chat_json(
                    [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": dumps(payload)},
                    ],
                    tier="strong",
                    temperature=0.0,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 3),
                        "status": "completed",
                    }
                )
                break
            except Exception as exc:
                normalized_error = f"{type(exc).__name__}: {exc}"
                is_timeout = "timeout" in normalized_error.lower()
                attempts.append(
                    {
                        "attempt": attempt,
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 3),
                        "status": "timeout" if is_timeout else "failed",
                        "error": normalized_error[:800],
                    }
                )
                if attempt >= 3 or not is_timeout:
                    workflow_run_id = self._first_day_run_id_from_value(payload)
                    if workflow_run_id:
                        current = self.repository.get_first_day_outreach_run(
                            workflow_run_id,
                            include_related=False,
                        )
                        workflow = dict(current.get("workflow") or {})
                        workflow[node] = {"input": payload, "attempts": attempts}
                        self.repository.update_first_day_outreach_run(
                            workflow_run_id,
                            status="failed",
                            reason_code="model_node_failed",
                            final_decision="failed",
                            model_attempt_count=int(current.get("model_attempt_count") or 0) + len(attempts),
                            retry_count=int(current.get("retry_count") or 0) + max(0, len(attempts) - 1),
                            workflow_json=workflow,
                            error_node=node,
                            error_type=type(exc).__name__,
                            error_message=str(exc)[:4000],
                            duration_ms=round((time.perf_counter() - started) * 1000),
                            finished_at=utc_now_iso(),
                        )
                    raise
        trace = {
            "node": node,
            "prompt_version": prompt_version,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "model_usage": dict(getattr(self.model_client, "last_usage", None) or {}),
        }
        workflow_run_id = self._first_day_run_id_from_value(payload)
        if workflow_run_id:
            current = self.repository.get_first_day_outreach_run(
                workflow_run_id,
                include_related=False,
            )
            workflow = dict(current.get("workflow") or {})
            workflow[node] = {"input": payload, "output": response, "trace": trace}
            self.repository.update_first_day_outreach_run(
                workflow_run_id,
                workflow_json=workflow,
                model_attempt_count=int(current.get("model_attempt_count") or 0) + len(attempts),
                retry_count=int(current.get("retry_count") or 0) + max(0, len(attempts) - 1),
            )
        return response if isinstance(response, dict) else {}, trace

    @classmethod
    def _first_day_run_id_from_value(cls, value: Any) -> str:
        if isinstance(value, dict):
            direct = _string(value.get("workflow_run_id"))
            if direct:
                return direct
            for item in value.values():
                nested = cls._first_day_run_id_from_value(item)
                if nested:
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = cls._first_day_run_id_from_value(item)
                if nested:
                    return nested
        return ""

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

    def list_candidates(
        self,
        *,
        limit: int = 50,
        silent_minutes_min: int = 60,
        outreach_status: str = "",
        lifecycle_stage: str = "",
        no_plan_only: bool = False,
        keyword: str = "",
    ) -> list[dict[str, Any]]:
        candidates = self.repository.list_outreach_candidates(
            limit=limit,
            silent_minutes_min=silent_minutes_min,
            outreach_status=outreach_status,
            lifecycle_stage=lifecycle_stage,
            no_plan_only=no_plan_only,
            keyword=keyword,
        )
        if not keyword:
            return candidates
        return [item for item in candidates if self._candidate_matches_keyword(item, keyword)]

    def dashboard_stats(self) -> dict[str, Any]:
        return self.repository.outreach_dashboard_stats()

    def customer_detail(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
    ) -> dict[str, Any]:
        return self.repository.get_outreach_customer_detail(
            customer_id=customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )

    def list_sop_plans(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_outreach_sop_plans(limit=limit)

    def create_sop_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = _string(payload.get("name"))
        if not name:
            raise ValueError("name is required")
        return self.repository.create_outreach_sop_plan(
            name=name,
            description=_string(payload.get("description")),
            filters=self._normalize_sop_filters(payload.get("filters")),
            status=_string(payload.get("status")) or "draft",
        )

    def update_sop_plan(self, sop_plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        updated = self.repository.update_outreach_sop_plan(
            sop_plan_id,
            name=_string(payload.get("name")) if "name" in payload else None,
            description=_string(payload.get("description")) if "description" in payload else None,
            filters=self._normalize_sop_filters(payload.get("filters")) if "filters" in payload else None,
            status=_string(payload.get("status")) if "status" in payload else None,
        )
        if not updated:
            raise KeyError("sop_plan_not_found")
        return updated

    def delete_sop_plan(self, sop_plan_id: str) -> bool:
        return self.repository.delete_outreach_sop_plan(sop_plan_id)

    async def run_sop_plan(self, sop_plan_id: str, *, limit: int = 20, activate: bool = False) -> dict[str, Any]:
        sop_plan = self.repository.get_outreach_sop_plan(sop_plan_id)
        if not sop_plan:
            raise KeyError("sop_plan_not_found")
        filters = self._normalize_sop_filters(sop_plan.get("filters"))
        candidates = self._sop_candidates(filters, limit=limit)
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                result = await self.generate_plan(
                    customer_id=str(candidate.get("customer_id") or ""),
                    corp_id=str(candidate.get("corp_id") or ""),
                    user_id=str(candidate.get("user_id") or ""),
                    wechat=str(candidate.get("wechat") or ""),
                    external_userid=str(candidate.get("external_userid") or candidate.get("customer_id") or ""),
                    current_stage=str(candidate.get("lifecycle_stage") or ""),
                    business_goal=str(filters.get("business_goal") or "推进客户支付10元预约金并到店"),
                    sop_plan_id=sop_plan_id,
                )
                plan_id = str((result.get("plan") or {}).get("id") or result.get("id") or "")
                if activate and plan_id:
                    self.activate_plan(plan_id)
                results.append({"customer_id": candidate.get("customer_id"), "ok": True, "plan_id": plan_id})
            except Exception as exc:
                results.append(
                    {
                        "customer_id": candidate.get("customer_id"),
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        summary = {
            "candidate_count": len(candidates),
            "success_count": len([item for item in results if item.get("ok")]),
            "failed_count": len([item for item in results if not item.get("ok")]),
            "activate": activate,
            "limit": limit,
            "results": results,
        }
        updated = self.repository.update_outreach_sop_plan(
            sop_plan_id,
            last_run_summary=summary,
            touch_last_run=True,
        )
        return {"ok": True, "sop_plan": updated, "summary": summary}

    async def refresh_customer_conversation(
        self,
        *,
        customer_id: str,
        corp_id: str,
        user_id: str,
        wechat: str,
        external_userid: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        payload = await self.system_client.conversation(
            corp_id=corp_id,
            customer_id=customer_id,
            external_userid=external_userid or customer_id,
            user_id=user_id,
            wechat=wechat,
            limit=limit,
        )
        messages = self._conversation_messages(payload)
        customer_relation = normalize_customer_relation(payload)
        scope = build_customer_scope(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
        )
        latest_customer = self._latest_message_time(messages, sender="customer")
        latest_staff = self._latest_message_time(messages, sender="staff")
        if latest_customer and scope.persistence_allowed:
            self.repository.touch_customer_message_time(
                scope.sales_contact_key,
                field="last_customer_message_at",
                value=latest_customer,
            )
        if latest_staff and scope.persistence_allowed:
            self.repository.touch_customer_message_time(
                scope.sales_contact_key,
                field="last_staff_message_at",
                value=latest_staff,
            )
        self.repository.add_outreach_event(
            plan_id="",
            task_id="",
            customer_id=customer_id,
            event_type="conversation_refreshed",
            event_summary="Refreshed customer conversation from system API",
            payload={
                "latest_customer_message_at": latest_customer,
                "message_count": len(messages),
                "customer_relation": customer_relation,
            },
        )
        return {
            "raw": payload,
            "messages": messages,
            "latest_customer_message_at": latest_customer,
            "latest_staff_message_at": latest_staff,
            "customer_relation": customer_relation,
        }

    def cached_customer_conversation(
        self,
        customer_id: str,
        *,
        corp_id: str,
        wechat: str,
        external_userid: str = "",
        limit: int = 10,
        error: str = "",
    ) -> dict[str, Any]:
        context = self.repository.recent_customer_context(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        messages = self._local_context_messages(context.get("recent_messages") or [], limit=limit)
        if not messages:
            return {}
        error_code, warning = classify_conversation_refresh_error(error)
        return {
            "ok": True,
            "source": "local_cache",
            "warning": warning,
            "fallback_reason": error_code,
            "error": error,
            "raw": {},
            "messages": messages,
            "latest_customer_message_at": self._latest_message_time(messages, sender="customer"),
            "latest_staff_message_at": self._latest_message_time(messages, sender="staff"),
        }

    async def generate_plan(
        self,
        *,
        customer_id: str,
        corp_id: str = "",
        user_id: str = "",
        wechat: str = "",
        external_userid: str = "",
        current_stage: str = "",
        business_goal: str = "",
        sop_plan_id: str = "",
        source_context: dict[str, Any] | None = None,
        trigger_context: dict[str, Any] | None = None,
        workflow_run_id: str = "",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        first_day_trigger = _is_first_day_opened_silence_trigger(trigger_context)
        run_creator = getattr(self.repository, "create_first_day_outreach_run", None)
        if first_day_trigger and not workflow_run_id and callable(run_creator):
            run = run_creator(
                customer_id=customer_id,
                corp_id=corp_id,
                user_id=user_id,
                wechat=wechat,
                external_userid=external_userid,
                trigger_type=FIRST_DAY_SILENCE_TRIGGER_TYPE,
                input_snapshot={"trigger_context": trigger_context or {}},
            )
            workflow_run_id = _string(run.get("workflow_run_id"))
        try:
            return await self._generate_plan_impl(
                customer_id=customer_id,
                corp_id=corp_id,
                user_id=user_id,
                wechat=wechat,
                external_userid=external_userid,
                current_stage=current_stage,
                business_goal=business_goal,
                sop_plan_id=sop_plan_id,
                source_context=source_context,
                trigger_context=trigger_context,
                workflow_run_id=workflow_run_id,
            )
        except Exception as exc:
            if workflow_run_id:
                current = self.repository.get_first_day_outreach_run(
                    workflow_run_id,
                    include_related=False,
                )
                if _string(current.get("status")) not in {"blocked", "sent", "cancelled", "failed", "completed"}:
                    self.repository.update_first_day_outreach_run(
                        workflow_run_id,
                        status="failed",
                        reason_code="workflow_failed",
                        final_decision="failed",
                        error_node=_string(current.get("error_node")) or "plan_generation",
                        error_type=_string(current.get("error_type")) or type(exc).__name__,
                        error_message=_string(current.get("error_message")) or str(exc)[:4000],
                        finished_at=utc_now_iso(),
                    )
            raise
        finally:
            if workflow_run_id:
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )

    async def _generate_plan_impl(
        self,
        *,
        customer_id: str,
        corp_id: str = "",
        user_id: str = "",
        wechat: str = "",
        external_userid: str = "",
        current_stage: str = "",
        business_goal: str = "",
        sop_plan_id: str = "",
        source_context: dict[str, Any] | None = None,
        trigger_context: dict[str, Any] | None = None,
        workflow_run_id: str = "",
    ) -> dict[str, Any]:
        first_day_trigger = _is_first_day_opened_silence_trigger(trigger_context)
        context = dict(
            source_context
            or self.repository.recent_customer_context(
                customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
            )
        )
        customer_relation = (
            context.get("customer_relation")
            if isinstance(context.get("customer_relation"), dict)
            else {}
        )
        if not customer_relation.get("available"):
            try:
                refreshed = await self.refresh_customer_conversation(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    user_id=user_id,
                    wechat=wechat,
                    external_userid=external_userid,
                    limit=50,
                )
            except Exception as exc:
                result = self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    wechat=wechat,
                    external_userid=external_userid,
                    reason="customer_relation_check_failed",
                    relation={
                        "available": False,
                        "status": "unknown",
                        "is_deleted": False,
                        "deleted_at": "",
                        "updated_at": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    trigger_context=trigger_context or {},
                )
                if workflow_run_id:
                    self.repository.update_first_day_outreach_run(
                        workflow_run_id,
                        status="failed",
                        reason_code="customer_relation_check_failed",
                        final_decision="failed",
                        error_node="conversation_refresh",
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:4000],
                        finished_at=utc_now_iso(),
                    )
                return result
            customer_relation = (
                refreshed.get("customer_relation")
                if isinstance(refreshed.get("customer_relation"), dict)
                else {}
            )
            refreshed_messages = refreshed.get("messages")
            if isinstance(refreshed_messages, list):
                context["recent_messages"] = refreshed_messages[-50:]
        context["customer_relation"] = customer_relation
        if not customer_relation.get("available"):
            result = self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_relation_unavailable",
                relation=customer_relation,
                trigger_context=trigger_context or {},
            )
            if workflow_run_id:
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    status="blocked",
                    reason_code="customer_relation_unavailable",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
            return result
        if customer_relation_is_deleted(customer_relation):
            result = self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_deleted",
                relation=customer_relation,
                trigger_context=trigger_context or {},
            )
            if workflow_run_id:
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    status="blocked",
                    reason_code="customer_deleted",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
            return result
        memory = context.get("memory") or {}
        recent_messages = context.get("recent_messages") or []
        conversation_activity = _conversation_activity_from_context(
            existing=context.get("conversation_activity"),
            memory=memory,
            recent_messages=recent_messages,
        )
        reply_wait_minutes = _int(conversation_activity.get("reply_wait_minutes"), 0)
        customer_silence_minutes = _int(
            conversation_activity.get("customer_silence_minutes"),
            0,
        )
        goal = business_goal or "推动客户重新开口，并逐步推进到店或支付10元预约金"
        appointment_playbook = self._appointment_blocker_playbook()
        appointment_material_catalog = appointment_blocker_materials(appointment_playbook)
        first_day_sop_sequence = self._first_day_sop_sequence()
        asset_catalog = (
            build_appointment_blocker_asset_catalog(appointment_playbook)
            + self._first_day_sop_asset_catalog(first_day_sop_sequence)
        )
        recent_media = enrich_recent_outreach_media(
            recent_outreach_media(recent_messages, hours=72),
            asset_catalog,
        )
        activity_quote_fact = build_outreach_activity_quote_fact(recent_messages, memory)
        personalized_order_gate = personalized_order_eligibility(context.get("customer_context") or {})
        payment_collection_gate = personalized_payment_collection_eligibility(
            context.get("customer_context") or {},
            amount=10,
        )
        recent_sop_delivery = []
        recent_sop_delivery_loader = getattr(self.repository, "recent_sop_delivery", None)
        if callable(recent_sop_delivery_loader):
            recent_sop_delivery = recent_sop_delivery_loader(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                hours=72,
            )
        source_snapshot = {
            "workflow_run_id": workflow_run_id,
            "customer_id": customer_id,
            "corp_id": corp_id,
            "user_id": user_id,
            "wechat": wechat,
            "external_userid": external_userid,
            "customer_fact_snapshot": outreach_customer_fact_snapshot(memory),
            "recent_messages": recent_messages,
            "conversation_activity": conversation_activity,
            "current_stage": current_stage,
            "business_goal": goal,
            "sop_plan_id": sop_plan_id,
            "offer_context": S10_OUTREACH_CONTEXT,
            "activity_quote_fact": activity_quote_fact,
            "personalized_order_gate": personalized_order_gate,
            "payment_collection_gate": payment_collection_gate,
            "trigger_context": trigger_context or {},
            "customer_context": context.get("customer_context") or {},
            "customer_relation": customer_relation,
            "asset_catalog": [
                {
                    key: asset.get(key)
                    for key in (
                        "asset_id",
                        "type",
                        "name",
                        "annotation",
                        "use_cases",
                        "avoid_when",
                        "tags",
                    )
                }
                for asset in asset_catalog
            ],
            "recent_media_delivery": recent_media,
            "recent_sop_delivery": recent_sop_delivery,
            "first_day_sop_sequence": first_day_sop_sequence,
            "appointment_blocker_scene_index": build_appointment_blocker_scene_index(
                appointment_playbook
            ),
        }
        if workflow_run_id:
            self.repository.update_first_day_outreach_run(
                workflow_run_id,
                input_snapshot_json=source_snapshot,
            )
        unopened_first_day = first_day_trigger and _int(
            conversation_activity.get("real_customer_message_count"),
            -1,
        ) == 0
        if unopened_first_day:
            scene_analysis = _normalize_first_day_scene_analysis(
                {},
                message_count=len(source_snapshot.get("recent_messages") or []),
                source_snapshot=source_snapshot,
            )
            source_snapshot["first_day_workflow"] = {
                "scene_analysis": scene_analysis,
                "writer_result": {},
                "verifier_result": {},
                "traces": {},
                "routing_decision": "first_day_customer_not_opened",
            }
            response = {
                "should_create_plan": False,
                "stall_reason": "first_day_customer_not_opened",
                "plan_arc": "",
                "steps": [],
            }
        if first_day_trigger and not unopened_first_day:
            first_day_model_snapshot = dict(source_snapshot)
            scene_analysis, analyst_trace = await self._run_first_day_model_node(
                node="scene_analyst",
                prompt=FIRST_DAY_SCENE_ANALYST_PROMPT,
                prompt_version=FIRST_DAY_SCENE_ANALYST_PROMPT_VERSION,
                payload={"source_snapshot": first_day_model_snapshot},
            )
            scene_analysis = _normalize_first_day_scene_analysis(
                scene_analysis,
                message_count=len(first_day_model_snapshot.get("recent_messages") or []),
                source_snapshot=first_day_model_snapshot,
            )
            scene_error = _first_day_scene_analysis_error(
                scene_analysis,
                source_snapshot=first_day_model_snapshot,
            )
            if scene_error:
                invalid_scene_analysis = scene_analysis
                scene_analysis, repair_trace = await self._run_first_day_model_node(
                    node="scene_analyst_schema_repair",
                    prompt=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT,
                    prompt_version=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT_VERSION,
                    payload={
                        "source_snapshot": first_day_model_snapshot,
                        "invalid_scene_analysis": scene_analysis,
                        "schema_error": scene_error,
                        "instruction": "只修复 JSON 结构合同并返回完整场景分析，不得改变已有事实证据。",
                    },
                )
                scene_analysis = _merge_first_day_scene_schema_repair(
                    invalid_scene_analysis,
                    scene_analysis,
                )
                scene_analysis = _normalize_first_day_scene_analysis(
                    scene_analysis,
                    message_count=len(first_day_model_snapshot.get("recent_messages") or []),
                    source_snapshot=first_day_model_snapshot,
                )
                analyst_trace["schema_repair"] = repair_trace
                scene_error = _first_day_scene_analysis_error(
                    scene_analysis,
                    source_snapshot=first_day_model_snapshot,
                )
            if scene_error:
                invalid_scene_analysis = scene_analysis
                scene_analysis, second_repair_trace = await self._run_first_day_model_node(
                    node="scene_analyst_schema_repair_2",
                    prompt=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT,
                    prompt_version=FIRST_DAY_SCENE_SCHEMA_REPAIR_PROMPT_VERSION,
                    payload={
                        "source_snapshot": first_day_model_snapshot,
                        "invalid_scene_analysis": scene_analysis,
                        "schema_error": scene_error,
                        "instruction": "再次只修复剩余 JSON 结构错误，保留已有业务判断并返回完整对象。",
                    },
                )
                scene_analysis = _merge_first_day_scene_schema_repair(
                    invalid_scene_analysis,
                    scene_analysis,
                )
                scene_analysis = _normalize_first_day_scene_analysis(
                    scene_analysis,
                    message_count=len(first_day_model_snapshot.get("recent_messages") or []),
                    source_snapshot=first_day_model_snapshot,
                )
                analyst_trace["schema_repair_2"] = second_repair_trace
                scene_error = _first_day_scene_analysis_error(
                    scene_analysis,
                    source_snapshot=first_day_model_snapshot,
                )
            if scene_error:
                raise RuntimeError(f"first_day_scene_analysis_invalid: {scene_error}")

            source_snapshot["first_day_workflow"] = {
                "scene_analysis": scene_analysis,
                "writer_result": {},
                "verifier_result": {},
                "traces": {"scene_analyst": analyst_trace},
            }
            if not _bool(scene_analysis.get("eligible")):
                response = {
                    "should_create_plan": False,
                    "stall_reason": _string(scene_analysis.get("suppress_reason"))
                    or "first_day_scene_analyst_suppressed",
                    "plan_arc": "",
                    "steps": [],
                }
            else:
                writer_payload = _first_day_writer_payload(
                    first_day_model_snapshot,
                    scene_analysis,
                    appointment_material_catalog=appointment_material_catalog,
                )
                writer_result, writer_trace = await self._run_first_day_model_node(
                    node="plan_writer",
                    prompt=FIRST_DAY_PLAN_WRITER_PROMPT,
                    prompt_version=FIRST_DAY_PLAN_WRITER_PROMPT_VERSION,
                    payload=writer_payload,
                )
                source_snapshot["first_day_workflow"]["writer_result"] = writer_result
                source_snapshot["first_day_workflow"]["traces"]["plan_writer"] = writer_trace
                normalized_writer_result = _normalize_outreach_plan_response(dict(writer_result))
                writer_structure_error = _first_day_final_plan_error(
                    normalized_writer_result,
                    scene_analysis=scene_analysis,
                    source_snapshot=first_day_model_snapshot,
                )
                source_snapshot["first_day_workflow"]["writer_structure_error"] = writer_structure_error
                verifier_result, verifier_trace = await self._run_first_day_model_node(
                    node="contract_verifier",
                    prompt=FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
                    prompt_version=FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION,
                    payload={
                        "source_snapshot": first_day_model_snapshot,
                        "scene_contract": scene_analysis,
                        "candidate_plan": writer_result,
                        "candidate_structure_error": writer_structure_error,
                    },
                )
                verifier_error = _first_day_verifier_error(verifier_result)
                if verifier_error:
                    verifier_result, verifier_repair_trace = await self._run_first_day_model_node(
                        node="contract_verifier_schema_repair",
                        prompt=FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
                        prompt_version=FIRST_DAY_CONTRACT_VERIFIER_PROMPT_VERSION,
                        payload={
                            "source_snapshot": first_day_model_snapshot,
                            "scene_contract": scene_analysis,
                            "candidate_plan": writer_result,
                            "invalid_verifier_result": verifier_result,
                            "schema_error": verifier_error,
                            "instruction": "只修复审核结果 JSON 合同，不得输出或改写客户计划。",
                        },
                    )
                    verifier_trace["schema_repair"] = verifier_repair_trace
                    verifier_error = _first_day_verifier_error(verifier_result)
                if verifier_error:
                    raise RuntimeError(f"first_day_contract_verifier_invalid: {verifier_error}")
                source_snapshot["first_day_workflow"]["verifier_result"] = verifier_result
                source_snapshot["first_day_workflow"]["traces"]["contract_verifier"] = verifier_trace
                if _string(verifier_result.get("decision")) == "block":
                    violations = verifier_result.get("violations") or []
                    response = {
                        "should_create_plan": False,
                        "stall_reason": _string((violations[0] if violations else {}).get("code"))
                        or "first_day_contract_verifier_blocked",
                        "plan_arc": "",
                        "steps": [],
                    }
                else:
                    needs_repair = bool(writer_structure_error) or (
                        _string(verifier_result.get("decision")) == "repair"
                    )
                    if needs_repair:
                        violations = list(verifier_result.get("violations") or [])
                        repair_instructions = list(verifier_result.get("repair_instructions") or [])
                        if writer_structure_error and not repair_instructions:
                            violations.append(
                                {
                                    "code": "deterministic_contract_error",
                                    "field": "candidate_plan",
                                    "evidence": writer_structure_error,
                                }
                            )
                            repair_instructions.append(
                                {
                                    "field": "candidate_plan",
                                    "instruction": "修复确定性合同错误，严格保留两个锁定场景和业务目标。",
                                }
                            )
                        repaired_writer_result, repair_trace = await self._run_first_day_model_node(
                            node="plan_writer_repair",
                            prompt=FIRST_DAY_PLAN_WRITER_PROMPT,
                            prompt_version=FIRST_DAY_PLAN_WRITER_PROMPT_VERSION,
                            payload=_first_day_writer_payload(
                                first_day_model_snapshot,
                                scene_analysis,
                                appointment_material_catalog=appointment_material_catalog,
                                candidate_plan=writer_result,
                                violations=violations,
                                repair_instructions=repair_instructions,
                                deterministic_error=writer_structure_error,
                            ),
                        )
                        source_snapshot["first_day_workflow"]["writer_repair_result"] = repaired_writer_result
                        source_snapshot["first_day_workflow"]["traces"]["plan_writer_repair"] = repair_trace
                        response = _normalize_first_day_repaired_plan(
                            _normalize_outreach_plan_response(dict(repaired_writer_result)),
                            scene_analysis=scene_analysis,
                        )
                    else:
                        response = normalized_writer_result
                    final_error = _first_day_final_plan_error(
                        response,
                        scene_analysis=scene_analysis,
                        source_snapshot=first_day_model_snapshot,
                    )
                    source_snapshot["first_day_workflow"]["final_contract_error"] = final_error
                    if final_error:
                        response = {
                            "should_create_plan": False,
                            "stall_reason": "first_day_plan_repair_failed",
                            "plan_arc": "",
                            "steps": [],
                            "final_contract_error": final_error,
                        }
        elif not first_day_trigger:
            model_messages = [
                {"role": "system", "content": OUTREACH_PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": dumps(source_snapshot)},
            ]
            response = await self.model_client.chat_json(
                model_messages,
                tier="strong",
                temperature=0.0,
            )
            response = _normalize_outreach_plan_response(response)
            structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
                response,
                activity_quote_fact=activity_quote_fact,
                reply_wait_minutes=reply_wait_minutes,
                customer_silence_minutes=customer_silence_minutes,
            )
            if structure_error:
                response = await self.model_client.chat_json(
                    [
                        *model_messages,
                        {"role": "assistant", "content": dumps(response)},
                        {
                            "role": "user",
                            "content": (
                                "上一个 json 不符合结构合同。"
                                f"错误：{structure_error}。"
                                "请保留事实和销售判断，重新输出完整有效 json；不要解释。"
                            ),
                        },
                    ],
                    tier="strong",
                    temperature=0.0,
                )
                response = _normalize_outreach_plan_response(response)
            response = await self.model_client.chat_json(
                [
                    {"role": "system", "content": OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": dumps(
                            {
                                "source_snapshot": source_snapshot,
                                "candidate_plan": response,
                            }
                        ),
                    },
                ],
                tier="strong",
                temperature=0.0,
            )
            response = _normalize_outreach_plan_response(response)
            structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
                response,
                activity_quote_fact=activity_quote_fact,
                reply_wait_minutes=reply_wait_minutes,
                customer_silence_minutes=customer_silence_minutes,
            )
            for _repair_attempt in range(3):
                if not structure_error:
                    break
                response = await self.model_client.chat_json(
                    [
                        {"role": "system", "content": OUTREACH_PLAN_SCHEMA_REPAIR_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": dumps(
                                {
                                    "source_snapshot": source_snapshot,
                                    "candidate_plan": response,
                                    "structure_error": structure_error,
                                    "repair_instruction": (
                                        "严格按 structure_error 修复完整 json；保留现有业务语义和客户可见文字，"
                                        "不要重新判断是否创建计划，不要解释。"
                                    ),
                                }
                            ),
                        },
                    ],
                    tier="strong",
                    temperature=0.0,
                )
                response = _normalize_outreach_plan_response(response)
                structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
                    response,
                    activity_quote_fact=activity_quote_fact,
                    reply_wait_minutes=reply_wait_minutes,
                    customer_silence_minutes=customer_silence_minutes,
                )
        if not bool(response.get("should_create_plan", True)):
            self.repository.add_outreach_event(
                plan_id="",
                task_id="",
                customer_id=customer_id,
                event_type="plan_rejected",
                event_summary=str(response.get("stall_reason") or "AI decided not to create outreach plan"),
                payload={
                    "identity": {
                        "customer_id": customer_id,
                        "corp_id": corp_id,
                        "wechat": wechat,
                        "external_userid": external_userid,
                    },
                    "trigger_context": trigger_context or {},
                    "ai_result": response,
                    "first_day_workflow": source_snapshot.get("first_day_workflow") or {},
                    "workflow_run_id": workflow_run_id,
                },
            )
            if workflow_run_id:
                workflow = source_snapshot.get("first_day_workflow") or {}
                scene_analysis = workflow.get("scene_analysis") if isinstance(workflow, dict) else {}
                current_run = self.repository.get_first_day_outreach_run(
                    workflow_run_id,
                    include_related=False,
                )
                recorded_workflow = dict(current_run.get("workflow") or {})
                recorded_workflow["summary"] = workflow
                self.repository.update_first_day_outreach_run(
                    workflow_run_id,
                    status="blocked",
                    reason_code=str(response.get("stall_reason") or "plan_rejected"),
                    final_decision="no_plan",
                    first_scene=_string((scene_analysis or {}).get("step1_scene")),
                    second_scene=_string((scene_analysis or {}).get("step2_scene")),
                    workflow_json=recorded_workflow,
                    final_plan_json=response,
                    finished_at=utc_now_iso(),
                )
            return {"created": False, "ai_result": response}
        structure_error = (
            _first_day_outreach_plan_error(response)
            if first_day_trigger
            else _outreach_plan_structure_error(response) or _outreach_plan_context_error(
                response,
                activity_quote_fact=activity_quote_fact,
                reply_wait_minutes=reply_wait_minutes,
                customer_silence_minutes=customer_silence_minutes,
                allow_first_day_internal_activity_quote=first_day_trigger,
            )
        )
        if structure_error:
            raise RuntimeError(f"outreach_plan_model_invalid_structure: {structure_error}")
        raw_steps = [step for step in response.get("steps") or [] if isinstance(step, dict)][:2 if first_day_trigger else 3]

        resolved_assets = await asyncio.gather(
            *[
                self._resolve_outreach_asset(
                    step,
                    asset_catalog=asset_catalog,
                    recent_media=recent_media,
                )
                for step in raw_steps
            ]
        )
        used_asset_keys: set[str] = set()
        for asset_index, asset in enumerate(resolved_assets):
            key = _string(asset.get("document_id") or asset.get("url"))
            if key and key in used_asset_keys:
                resolved_assets[asset_index] = {}
                continue
            if key:
                used_asset_keys.add(key)

        now = utc_now_iso()
        tasks = []
        payment_collection_added = False
        normalized_schedule = (
            _normalize_first_day_outreach_schedule(now, raw_steps)
            if first_day_trigger
            else _normalize_outreach_schedule(now, raw_steps)
        )
        for index, step in enumerate(raw_steps, start=1):
            schedule = normalized_schedule[index - 1]
            content_mode = _string(step.get("content_mode"))
            payment_collection_basis = _string(step.get("payment_collection_basis"))
            activity_quote_ready = _valid_activity_quote_evidence(
                activity_quote_fact
            ) or (
                first_day_trigger
                and _first_day_internal_activity_quote_evidence(
                    raw_steps,
                    before_step_index=index - 1,
                )
            )
            should_send_payment_collection = (
                _bool(step.get("should_send_payment_collection"))
                and (first_day_trigger or index == len(raw_steps))
                and content_mode == "transaction"
                and payment_collection_basis == "model_selected_after_quote"
                and not payment_collection_added
                and activity_quote_ready
                and bool(payment_collection_gate.get("eligible"))
            )
            payment_collection_added = payment_collection_added or should_send_payment_collection
            draft_texts = _plan_step_texts(step)
            if not draft_texts:
                continue
            resolved_asset = resolved_assets[index - 1]
            task_metadata = {
                "scene": _string(step.get("scene")),
                "content_mode": content_mode,
                "persuasion_angle": _string(step.get("persuasion_angle")),
                "new_value": _string(step.get("new_value")),
                "avoid_repeating": _list_strings(step.get("avoid_repeating")),
                "timing_reason": _string(step.get("timing_reason")),
                "urgency_level": _string(step.get("urgency_level")),
                "no_reply_action": _string(step.get("no_reply_action")),
                "no_reply_strategy": _string(step.get("no_reply_strategy")),
                "requested_delay_minutes": schedule["requested_delay_minutes"],
                "normalized_delay_minutes": schedule["normalized_delay_minutes"],
                "asset_strategy": _string(step.get("asset_strategy")) or "none",
                "asset_id": _string(step.get("asset_id")),
                "case_query": _string(step.get("case_query")),
                "cta": _string(step.get("cta")),
                "plan_arc": _string(response.get("plan_arc")),
            }
            reply_messages = _compose_outreach_messages(
                draft_texts,
                resolved_asset=resolved_asset,
                should_send_payment_collection=should_send_payment_collection,
            )
            tasks.append(
                {
                    "step_index": int(step.get("step") or index),
                    "scheduled_at": schedule["scheduled_at"],
                    "intent": str(step.get("intent") or "outreach"),
                    "message_goal": str(step.get("message_goal") or ""),
                    "content_sources": _task_content_sources(
                        step.get("content_sources"),
                        should_send_payment_collection=should_send_payment_collection,
                        task_metadata=task_metadata,
                        resolved_asset=resolved_asset,
                    ),
                    "should_send_payment_collection": should_send_payment_collection,
                    "before_send_check": bool(step.get("before_send_check", True)),
                    "reply_messages": reply_messages,
                }
            )
        if not tasks:
            raise RuntimeError("outreach_plan_model_missing_reviewable_drafts")
        source_snapshot["ai_result"] = response
        created_plan = self.repository.create_outreach_plan(
                customer_id=customer_id,
                corp_id=corp_id,
                user_id=user_id,
                wechat=wechat,
                external_userid=external_userid,
                customer_stage=str(response.get("conversion_stage") or response.get("customer_stage") or ""),
                stall_reason=str(response.get("stall_reason") or ""),
                customer_psychology=str(response.get("customer_psychology") or ""),
                plan_goal=str(response.get("plan_goal") or ""),
                source_snapshot=source_snapshot,
                tasks=tasks[:3],
                sop_plan_id=sop_plan_id,
                workflow_run_id=workflow_run_id,
            )
        if workflow_run_id:
            plan = created_plan.get("plan") if isinstance(created_plan.get("plan"), dict) else {}
            created_tasks = created_plan.get("tasks") if isinstance(created_plan.get("tasks"), list) else []
            current_run = self.repository.get_first_day_outreach_run(
                workflow_run_id,
                include_related=False,
            )
            recorded_workflow = dict(current_run.get("workflow") or {})
            recorded_workflow["summary"] = source_snapshot.get("first_day_workflow") or {}
            updates: dict[str, Any] = {
                "plan_id": _string(plan.get("id")),
                "first_task_id": _string((created_tasks[0] if created_tasks else {}).get("id")),
                "second_task_id": _string((created_tasks[1] if len(created_tasks) > 1 else {}).get("id")),
                "first_scene": _string((raw_steps[0] if raw_steps else {}).get("scene")),
                "second_scene": _string((raw_steps[1] if len(raw_steps) > 1 else {}).get("scene")),
                "workflow_json": recorded_workflow,
                "final_plan_json": response,
            }
            if _string(current_run.get("status")) not in {
                "blocked", "sent", "cancelled", "failed", "completed"
            }:
                updates.update(
                    status="created",
                    reason_code="plan_created",
                    final_decision="send_pending",
                )
            self.repository.update_first_day_outreach_run(workflow_run_id, **updates)
        return {"created": True, **created_plan}

    def _relation_plan_skip(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        reason: str,
        relation: dict[str, Any],
        trigger_context: dict[str, Any],
    ) -> dict[str, Any]:
        active_loader = getattr(self.repository, "get_active_outreach_plan_for_customer", None)
        active = (
            active_loader(
                customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
            )
            if callable(active_loader)
            else {}
        )
        plan = active.get("plan") if isinstance(active.get("plan"), dict) else {}
        plan_id = _string(plan.get("id"))
        if plan_id and reason == "customer_deleted":
            skip_remaining = getattr(self.repository, "skip_remaining_outreach_tasks", None)
            if callable(skip_remaining):
                skip_remaining(plan_id, reason="customer_deleted")
            update_plan_status = getattr(self.repository, "update_outreach_plan_status", None)
            if callable(update_plan_status):
                update_plan_status(plan_id, "cancelled")
        event_type = (
            "plan_skipped_customer_deleted"
            if reason == "customer_deleted"
            else "plan_skipped_customer_relation_unavailable"
        )
        add_event = getattr(self.repository, "add_outreach_event", None)
        if callable(add_event):
            add_event(
                plan_id=plan_id,
                task_id="",
                customer_id=customer_id,
                event_type=event_type,
                event_summary=(
                    "Customer relation is deleted; personalized plan generation skipped"
                    if reason == "customer_deleted"
                    else "Customer relation could not be verified; personalized plan generation skipped"
                ),
                payload={
                    "identity": {
                        "customer_id": customer_id,
                        "corp_id": corp_id,
                        "wechat": wechat,
                        "external_userid": external_userid,
                    },
                    "reason": reason,
                    "customer_relation": relation,
                    "trigger_context": trigger_context,
                },
            )
        return {
            "created": False,
            "skipped": True,
            "reason": reason,
            "customer_relation": relation,
        }

    def _outreach_asset_catalog(self) -> list[dict[str, Any]]:
        return build_appointment_blocker_asset_catalog(self._appointment_blocker_playbook())

    def _first_day_sop_sequence(self) -> list[dict[str, Any]]:
        if self.sop_reply_pack_service is None:
            return []
        try:
            config = self.sop_reply_pack_service.load()
        except Exception:
            return []
        packs = config.get("packs") if isinstance(config.get("packs"), list) else []
        output: list[dict[str, Any]] = []
        for pack in packs:
            if not isinstance(pack, dict) or not _bool(pack.get("enabled")):
                continue
            raw_scopes = pack.get("scopes")
            scopes = [
                _string(item)
                for item in raw_scopes
                if _string(item)
            ] if isinstance(raw_scopes, list) else []
            scope = _string(pack.get("scope"))
            if "event_first_add" not in set(scopes + ([scope] if scope else [])):
                continue
            day_stage = _string(pack.get("day_stage"))
            if day_stage and not day_stage.startswith("day1"):
                continue
            messages = [
                dict(message)
                for message in pack.get("reply_messages") or []
                if isinstance(message, dict) and _string(message.get("type"))
            ]
            if not messages:
                continue
            category = _string(pack.get("sop_category"))
            mapped_scene = FIRST_DAY_SOP_SCENE_BY_CATEGORY.get(category, "")
            if not mapped_scene:
                continue
            pack_id = _string(pack.get("id"))
            media_asset_ids: list[str] = []
            compact_messages: list[dict[str, Any]] = []
            for order, message in enumerate(messages, start=1):
                message_type = _string(message.get("type"))
                content = message.get("content") if isinstance(message.get("content"), dict) else {}
                if message_type == "text":
                    compact_messages.append(
                        {
                            "type": "text",
                            "order": _int(message.get("order"), order),
                            "text": _string(content.get("text") if isinstance(content, dict) else ""),
                        }
                    )
                elif message_type in {"image", "video"}:
                    asset_id = f"sop-pack:{pack_id}:{_int(message.get('order'), order)}"
                    media_asset_ids.append(asset_id)
                    url = _string(content.get("url") if isinstance(content, dict) else "")
                    compact_messages.append(
                        {
                            "type": message_type,
                            "order": _int(message.get("order"), order),
                            "asset_id": asset_id,
                            "url": url,
                        }
                    )
                elif message_type == "payment_collection":
                    compact_messages.append(
                        {
                            "type": "payment_collection",
                            "order": _int(message.get("order"), order),
                            "amount": _int(content.get("amount") if isinstance(content, dict) else 10, 10),
                        }
                    )
            output.append(
                {
                    "source_id": f"sop-pack:{pack_id}",
                    "pack_id": pack_id,
                    "name": _string(pack.get("name")),
                    "sop_category": category,
                    "mapped_scene": mapped_scene,
                    "order": _int(
                        pack.get("order"),
                        FIRST_DAY_SOP_CATEGORY_ORDER.get(category, 999),
                    ),
                    "day_stage": day_stage,
                    "purpose": _string(pack.get("purpose")),
                    "reply_messages": compact_messages,
                    "asset_ids": media_asset_ids,
                }
            )
        output.sort(
            key=lambda item: (
                FIRST_DAY_SOP_CATEGORY_ORDER.get(_string(item.get("sop_category")), 999),
                _int(item.get("order"), 9999),
                _string(item.get("pack_id")),
            )
        )
        return output

    @staticmethod
    def _first_day_sop_asset_catalog(sop_sequence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for pack in sop_sequence:
            if not isinstance(pack, dict):
                continue
            pack_id = _string(pack.get("pack_id"))
            for message in pack.get("reply_messages") or []:
                if not isinstance(message, dict):
                    continue
                asset_id = _string(message.get("asset_id"))
                message_type = _string(message.get("type"))
                if not asset_id or message_type not in {"image", "video"}:
                    continue
                url = _string(message.get("url"))
                if not url:
                    continue
                assets.append(
                    {
                        "asset_id": asset_id,
                        "type": message_type,
                        "url": url,
                        "source": "first_day_sop_pack",
                        "name": pack_id,
                        "annotation": _string(pack.get("name")),
                        "use_cases": [_string(pack.get("purpose"))],
                        "avoid_when": ["近期已经发送相同 SOP 或相同素材"],
                        "tags": [_string(pack.get("sop_category")), pack_id],
                    }
                )
        return assets

    def _appointment_blocker_playbook(self) -> dict[str, Any]:
        if self.precision_qa_playbook_service is None:
            return {"version": 4, "items": []}
        try:
            return self.precision_qa_playbook_service.load()
        except Exception:
            return {"version": 4, "items": []}

    async def _resolve_outreach_asset(
        self,
        step: dict[str, Any],
        *,
        asset_catalog: list[dict[str, Any]],
        recent_media: dict[str, list[str]],
    ) -> dict[str, Any]:
        strategy = _string(step.get("asset_strategy")) or "none"
        if strategy not in OUTREACH_ASSET_STRATEGIES:
            return {}
        sent_urls = set(recent_media.get("urls") or [])
        sent_document_ids = set(recent_media.get("document_ids") or [])
        if strategy == "configured_image":
            return resolve_configured_asset(
                asset_catalog,
                _string(step.get("asset_id")),
                sent_urls=sent_urls,
                expected_type="image",
            )
        if strategy == "operation_video":
            return resolve_configured_asset(
                asset_catalog,
                _string(step.get("asset_id")),
                sent_urls=sent_urls,
                expected_type="video",
            )
        if strategy != "case_search":
            return {}

        query = _string(step.get("case_query"))
        if self.coze_client is not None and query:
            try:
                result = await asyncio.wait_for(
                    self.coze_client.search_kb("case_studies", query),
                    timeout=12.0,
                )
                case_asset = resolve_case_asset(
                    result,
                    sent_urls=sent_urls,
                    sent_document_ids=sent_document_ids,
                )
                if case_asset:
                    return case_asset
            except (TimeoutError, ValueError, RuntimeError):
                pass
        return resolve_configured_asset(
            asset_catalog,
            _string(step.get("fallback_asset_id")),
            sent_urls=sent_urls,
            expected_type="image",
        )

    async def ensure_platform_task_plan(
        self,
        *,
        identity: dict[str, Any],
        conversation_messages: list[dict[str, Any]],
        conversation_activity: dict[str, Any],
        customer_context: dict[str, Any],
        platform_task: dict[str, Any],
        customer_relation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one auto-approved day-2 personalized plan and reuse it on later platform triggers."""
        lock = self._plan_lock(identity)
        async with lock:
            return await self._ensure_platform_task_plan_locked(
                identity=identity,
                conversation_messages=conversation_messages,
                conversation_activity=conversation_activity,
                customer_context=customer_context,
                platform_task=platform_task,
                customer_relation=customer_relation or {},
            )

    async def _ensure_platform_task_plan_locked(
        self,
        *,
        identity: dict[str, Any],
        conversation_messages: list[dict[str, Any]],
        conversation_activity: dict[str, Any],
        customer_context: dict[str, Any],
        platform_task: dict[str, Any],
        customer_relation: dict[str, Any],
    ) -> dict[str, Any]:
        customer_id = _string(identity.get("customer_id"))
        corp_id = _string(identity.get("corp_id"))
        wechat = _string(identity.get("wechat"))
        external_userid = _string(identity.get("external_userid"))
        if not customer_relation.get("available"):
            try:
                refreshed = await self.refresh_customer_conversation(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    user_id=_string(identity.get("user_id")),
                    wechat=wechat,
                    external_userid=external_userid,
                    limit=50,
                )
                customer_relation = (
                    refreshed.get("customer_relation")
                    if isinstance(refreshed.get("customer_relation"), dict)
                    else {}
                )
            except Exception as exc:
                return self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=corp_id,
                    wechat=wechat,
                    external_userid=external_userid,
                    reason="customer_relation_check_failed",
                    relation={
                        "available": False,
                        "status": "unknown",
                        "is_deleted": False,
                        "deleted_at": "",
                        "updated_at": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    trigger_context={
                        "source": "sop_platform_task",
                        "platform_task": platform_task,
                    },
                )
        if not customer_relation.get("available"):
            return self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_relation_unavailable",
                relation=customer_relation,
                trigger_context={
                    "source": "sop_platform_task",
                    "platform_task": platform_task,
                },
            )
        if customer_relation_is_deleted(customer_relation):
            return self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_deleted",
                relation=customer_relation,
                trigger_context={
                    "source": "sop_platform_task",
                    "platform_task": platform_task,
                },
            )
        conversation_fingerprint = _conversation_fingerprint(
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            customer_id=customer_id,
            latest_customer_message_at=_string(conversation_activity.get("latest_customer_message_at")),
            latest_staff_message_at=_string(conversation_activity.get("latest_staff_message_at")),
        )
        active = self.repository.get_active_outreach_plan_for_customer(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        if active:
            plan = active.get("plan") if isinstance(active.get("plan"), dict) else {}
            latest_customer_at = _parse_iso(_string(conversation_activity.get("latest_customer_message_at")))
            plan_created_at = _parse_iso(_string(plan.get("created_at")))
            if latest_customer_at and plan_created_at and latest_customer_at > plan_created_at:
                self.repository.update_outreach_plan_status(_string(plan.get("id")), "cancelled")
                self.repository.add_outreach_event(
                    plan_id=_string(plan.get("id")),
                    task_id="",
                    customer_id=customer_id,
                    event_type="platform_task_plan_superseded_by_customer_reply",
                    event_summary="Customer replied after plan creation; regenerate from latest conversation",
                    payload={
                        "latest_customer_message_at": latest_customer_at.isoformat(),
                        "plan_created_at": plan_created_at.isoformat(),
                        "platform_task": platform_task,
                    },
                )
            else:
                trigger_context = (
                    plan.get("source_snapshot", {}).get("trigger_context")
                    if isinstance(plan.get("source_snapshot"), dict)
                    and isinstance(plan.get("source_snapshot", {}).get("trigger_context"), dict)
                    else {}
                )
                legacy_review_draft = (
                    _string(plan.get("status")) == "draft"
                    and _string(trigger_context.get("source")) == "sop_platform_task"
                    and _string(trigger_context.get("activation_policy")) != "auto_approved"
                )
                if legacy_review_draft:
                    self.repository.update_outreach_plan_status(_string(plan.get("id")), "cancelled")
                    self.repository.add_outreach_event(
                        plan_id=_string(plan.get("id")),
                        task_id="",
                        customer_id=customer_id,
                        event_type="legacy_review_plan_cancelled",
                        event_summary="Cancelled legacy review-required plan before creating auto-approved plan",
                        payload={"platform_task": platform_task},
                    )
                else:
                    if (
                        _string(plan.get("status")) == "draft"
                        and _string(trigger_context.get("activation_policy")) == "auto_approved"
                    ):
                        active = self._auto_approve_plan(_string(plan.get("id")))
                    self.repository.add_outreach_event(
                        plan_id=_string(plan.get("id")),
                        task_id="",
                        customer_id=customer_id,
                        event_type="platform_task_filtered_plan_reused",
                        event_summary="Filtered platform task and reused personalized outreach plan",
                        payload={"platform_task": platform_task, "conversation_activity": conversation_activity},
                    )
                    return {"created": False, "reused": True, **active}

        if self._completed_cycle_blocks_auto_plan(
            customer_id=customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            latest_customer_message_at=_string(conversation_activity.get("latest_customer_message_at")),
        ):
            return {
                "created": False,
                "reused": False,
                "skipped": True,
                "reason": "outreach_cycle_completed_without_new_customer_reply",
            }

        if self.repository.has_outreach_evaluation_fingerprint(
            customer_id=customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
            conversation_fingerprint=conversation_fingerprint,
        ):
            return {
                "created": False,
                "reused": False,
                "skipped": True,
                "reason": "conversation_fingerprint_already_evaluated",
            }

        local_context = self.repository.recent_customer_context(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        source_context = {
            "memory": local_context.get("memory") or {},
            "recent_messages": conversation_messages[-50:],
            "conversation_activity": conversation_activity,
            "customer_context": customer_context,
            "customer_relation": customer_relation,
        }
        result = await self.generate_plan(
            customer_id=customer_id,
            corp_id=corp_id,
            user_id=_string(identity.get("user_id")),
            wechat=wechat,
            external_userid=external_userid,
            current_stage="day2_personalized_spoken_unbooked",
            business_goal="从第二天起用不同心理角度递进唤醒客户，促使客户重新开口并推进到店或预约金",
            source_context=source_context,
            trigger_context={
                "source": "sop_platform_task",
                "platform_task_filtered": True,
                "platform_task": platform_task,
                "activation_policy": "auto_approved",
                "conversation_fingerprint": conversation_fingerprint,
            },
        )
        if not result.get("created"):
            return {"reused": False, **result}
        plan_id = _string((result.get("plan") or {}).get("id") or result.get("id"))
        if not plan_id:
            raise RuntimeError("personalized_outreach_plan_missing_id")
        activated = self._auto_approve_plan(plan_id)
        return {"reused": False, "auto_approved": True, **result, **activated}

    async def evaluate_silent_customers(
        self,
        *,
        limit: int = 5,
        silent_minutes: int = 10,
        auto_activate: bool = True,
    ) -> dict[str, Any]:
        started_at = utc_now_iso()
        stats: dict[str, Any] = {
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
        candidates = self.list_candidates(
            limit=scan_limit,
            silent_minutes_min=0,
        )
        stats["candidate_count"] = len(candidates)
        eligible_seen = 0
        for candidate in candidates:
            if eligible_seen >= max(1, int(limit)):
                break
            rough_reason = self._rough_silence_candidate_reason(
                candidate,
                silent_minutes=max(1, int(silent_minutes)),
            )
            if rough_reason:
                result = {
                    "status": "skipped",
                    "customer_id": _string(candidate.get("customer_id")),
                    "reason": rough_reason,
                }
            else:
                eligible_seen += 1
                try:
                    result = await self._evaluate_silent_candidate(
                        candidate,
                        silent_minutes=max(1, int(silent_minutes)),
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
            elif status == "error":
                stats["error_count"] += 1
                stats["last_error"] = _string(result.get("error"))
            else:
                stats["skipped_count"] += 1
                reason = _string(result.get("reason")) or "unknown"
                skip_reasons = stats["skip_reasons"]
                skip_reasons[reason] = int(skip_reasons.get(reason) or 0) + 1
        stats["last_scan_finished_at"] = utc_now_iso()
        self._monitor_status = {key: value for key, value in stats.items() if key != "results"}
        return stats

    @staticmethod
    def _rough_silence_candidate_reason(candidate: dict[str, Any], *, silent_minutes: int) -> str:
        if not _is_second_beijing_day(_string(candidate.get("sales_contact_started_at"))):
            return "not_proven_day2_plus"
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
        candidates = self.list_candidates(limit=scan_limit, silent_minutes_min=0)
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
        if not _is_within_first_day(_string(candidate.get("sales_contact_started_at"))):
            return "not_first_day"
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
        first_added_at = _string(candidate.get("sales_contact_started_at"))
        if not all((customer_id, identity["corp_id"], identity["wechat"], identity["external_userid"])):
            return {"status": "skipped", "customer_id": customer_id, "reason": "incomplete_sales_contact_identity"}
        if not _is_within_first_day(first_added_at):
            return {"status": "skipped", "customer_id": customer_id, "reason": "not_first_day"}
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
        lock = self._plan_lock(identity)
        async with lock:
            active = self.repository.get_active_outreach_plan_for_customer(
                customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
            )
            if active:
                return {"status": "skipped", "customer_id": customer_id, "reason": "nonterminal_plan_exists"}
            run_finder = getattr(self.repository, "find_first_day_outreach_run_by_fingerprint", None)
            existing_run = (
                run_finder(
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
                    run_updater(
                        workflow_run_id,
                        status="running",
                        reason_code="preflight_retry",
                        final_decision="retrying",
                        retry_count=int(existing_run.get("retry_count") or 0) + 1,
                        error_node="",
                        error_type="",
                        error_message="",
                        finished_at="",
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
                    run = run_creator(
                        **identity,
                        trigger_type=FIRST_DAY_SILENCE_TRIGGER_TYPE,
                        input_snapshot={
                            "trigger_context": {
                                "source": "silence_monitor",
                                "trigger_type": FIRST_DAY_SILENCE_TRIGGER_TYPE,
                                "conversation_fingerprint": candidate_fingerprint,
                                "first_added_at": first_added_at,
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

            def _update_run(**changes: Any) -> None:
                if workflow_run_id and callable(run_updater):
                    run_updater(workflow_run_id, **changes)

            local_now = datetime.now(timezone.utc).astimezone(OUTREACH_BEIJING_TIMEZONE)
            local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            local_day_end = local_day_start + timedelta(days=1)
            created_today = self.repository.count_outreach_plans_for_trigger_between(
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                trigger_type=FIRST_DAY_SILENCE_TRIGGER_TYPE,
                started_at=local_day_start.astimezone(timezone.utc).isoformat(),
                ended_at=local_day_end.astimezone(timezone.utc).isoformat(),
            )
            if created_today >= FIRST_DAY_DAILY_PLAN_LIMIT:
                _update_run(
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
            try:
                refreshed = await self.refresh_customer_conversation(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    user_id=identity["user_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    limit=50,
                )
            except Exception as exc:
                _update_run(
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
                self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    reason="customer_relation_unavailable",
                    relation=customer_relation,
                    trigger_context={"source": "silence_monitor", "trigger_type": FIRST_DAY_SILENCE_TRIGGER_TYPE},
                )
                _update_run(
                    status="blocked",
                    reason_code="customer_relation_unavailable",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "customer_relation_unavailable"}
            if customer_relation_is_deleted(customer_relation):
                self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    reason="customer_deleted",
                    relation=customer_relation,
                    trigger_context={"source": "silence_monitor", "trigger_type": FIRST_DAY_SILENCE_TRIGGER_TYPE},
                )
                _update_run(
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
            real_customer_count = _real_customer_message_count(messages)
            latest_customer_text = _latest_real_customer_message_time(messages)
            latest_staff_text = self._latest_message_time(messages, sender="staff")
            latest_customer = _parse_iso(latest_customer_text)
            latest_staff = _parse_iso(latest_staff_text)
            if real_customer_count <= 0 or not latest_customer:
                _update_run(
                    status="blocked",
                    reason_code="customer_never_spoke",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "customer_never_spoke"}
            if not latest_staff or latest_staff <= latest_customer:
                _update_run(
                    status="cancelled",
                    reason_code="customer_replied",
                    final_decision="no_plan",
                    finished_at=utc_now_iso(),
                )
                return {"status": "skipped", "customer_id": customer_id, "reason": "not_waiting_for_customer_reply"}
            if self._completed_cycle_blocks_auto_plan(
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                latest_customer_message_at=latest_customer_text,
            ):
                _update_run(
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
                _update_run(
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
            if self.repository.has_outreach_evaluation_fingerprint(
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                conversation_fingerprint=conversation_fingerprint,
            ):
                _update_run(
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
            local_context = self.repository.recent_customer_context(
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
                _update_run(
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
                _update_run(
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
            }
            result = await self.generate_plan(
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
                activated = self._auto_approve_plan(plan_id)
                result = {**result, **activated, "auto_approved": True}
            return {
                "status": "evaluated",
                "customer_id": customer_id,
                "created": True,
                "plan_id": plan_id,
                "result": result,
            }

    async def _evaluate_silent_candidate(
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
        if not all((customer_id, identity["corp_id"], identity["wechat"], identity["external_userid"])):
            return {"status": "skipped", "customer_id": customer_id, "reason": "incomplete_sales_contact_identity"}
        if not _is_second_beijing_day(_string(candidate.get("sales_contact_started_at"))):
            return {"status": "skipped", "customer_id": customer_id, "reason": "not_proven_day2_plus"}
        if not _string(candidate.get("last_customer_message_at")):
            return {"status": "skipped", "customer_id": customer_id, "reason": "customer_never_spoke"}
        manual_takeover = _parse_iso(_string(candidate.get("last_manual_takeover_at")))
        remembered_customer = _parse_iso(_string(candidate.get("last_customer_message_at")))
        if manual_takeover and remembered_customer and manual_takeover >= remembered_customer:
            return {"status": "skipped", "customer_id": customer_id, "reason": "manual_takeover_active"}

        lock = self._plan_lock(identity)
        async with lock:
            active = self.repository.get_active_outreach_plan_for_customer(
                customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
            )
            if active:
                return {"status": "skipped", "customer_id": customer_id, "reason": "nonterminal_plan_exists"}
            try:
                refreshed = await self.refresh_customer_conversation(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    user_id=identity["user_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    limit=50,
                )
            except Exception as exc:
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
                self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    reason="customer_relation_unavailable",
                    relation=customer_relation,
                    trigger_context={"source": "silence_monitor"},
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "customer_relation_unavailable",
                }
            if customer_relation_is_deleted(customer_relation):
                self._relation_plan_skip(
                    customer_id=customer_id,
                    corp_id=identity["corp_id"],
                    wechat=identity["wechat"],
                    external_userid=identity["external_userid"],
                    reason="customer_deleted",
                    relation=customer_relation,
                    trigger_context={"source": "silence_monitor"},
                )
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "customer_deleted",
                    "customer_relation": customer_relation,
                }
            latest_customer_text = _string(refreshed.get("latest_customer_message_at"))
            latest_staff_text = _string(refreshed.get("latest_staff_message_at"))
            latest_customer = _parse_iso(latest_customer_text)
            latest_staff = _parse_iso(latest_staff_text)
            if not latest_customer:
                return {"status": "skipped", "customer_id": customer_id, "reason": "customer_never_spoke"}
            if not latest_staff or latest_staff <= latest_customer:
                return {"status": "skipped", "customer_id": customer_id, "reason": "not_waiting_for_customer_reply"}
            if self._completed_cycle_blocks_auto_plan(
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                latest_customer_message_at=latest_customer_text,
            ):
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
                return {"status": "skipped", "customer_id": customer_id, "reason": "reply_wait_below_threshold"}
            conversation_fingerprint = _conversation_fingerprint(
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                customer_id=customer_id,
                latest_customer_message_at=latest_customer_text,
                latest_staff_message_at=latest_staff_text,
            )
            if self.repository.has_outreach_evaluation_fingerprint(
                customer_id=customer_id,
                corp_id=identity["corp_id"],
                wechat=identity["wechat"],
                external_userid=identity["external_userid"],
                conversation_fingerprint=conversation_fingerprint,
            ):
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": "conversation_fingerprint_already_evaluated",
                }

            local_context = self.repository.recent_customer_context(
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
                return {"status": "skipped", "customer_id": customer_id, "reason": "order_context_unavailable"}
            if not order_gate.get("eligible"):
                return {
                    "status": "skipped",
                    "customer_id": customer_id,
                    "reason": _string(order_gate.get("reason")) or "order_not_eligible",
                }

            source_context = {
                "memory": local_context.get("memory") or {},
                "recent_messages": (refreshed.get("messages") or [])[-50:],
                "customer_relation": customer_relation,
                "conversation_activity": {
                    "real_customer_message_count": len(
                        [
                            message
                            for message in refreshed.get("messages") or []
                            if _string(
                                message.get("direction")
                                or message.get("from")
                                or message.get("sender_type")
                            ).lower()
                            in {"customer", "user", "external"}
                        ]
                    ),
                    "latest_customer_message_at": latest_customer_text,
                    "latest_staff_message_at": latest_staff_text,
                    "reply_wait_minutes": wait_minutes,
                },
                "customer_context": customer_context,
            }
            result = await self.generate_plan(
                **identity,
                current_stage="day2_personalized_spoken_unbooked",
                business_goal="用个性化价值触达促使客户重新开口，再逐步推进到店或预约金",
                source_context=source_context,
                trigger_context={
                    "source": "silence_monitor",
                    "activation_policy": "auto_approved",
                    "conversation_fingerprint": conversation_fingerprint,
                    "latest_customer_message_at": latest_customer_text,
                    "latest_staff_message_at": latest_staff_text,
                    "reply_wait_minutes": wait_minutes,
                    "monitor_silent_minutes": silent_minutes,
                },
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
                raise RuntimeError("silence_monitor_plan_missing_id")
            if auto_activate:
                activated = self._auto_approve_plan(plan_id)
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

    def _completed_cycle_blocks_auto_plan(
        self,
        *,
        customer_id: str,
        corp_id: str,
        wechat: str,
        external_userid: str,
        latest_customer_message_at: str,
    ) -> bool:
        loader = getattr(self.repository, "get_latest_completed_outreach_plan_for_customer", None)
        if not callable(loader):
            return False
        completed_plan = loader(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        return _completed_cycle_blocks_automatic_replan(
            completed_plan if isinstance(completed_plan, dict) else {},
            latest_customer_message_at=latest_customer_message_at,
        )

    def _plan_lock(self, identity: dict[str, Any]) -> asyncio.Lock:
        scope = build_customer_scope(
            corp_id=identity.get("corp_id"),
            wechat=_string(identity.get("wechat")).lower(),
            external_userid=identity.get("external_userid"),
            customer_id=identity.get("customer_id"),
        )
        key = scope.sales_contact_key or "|".join(
            _string(identity.get(field)).lower()
            for field in ("corp_id", "wechat", "external_userid", "customer_id")
        )
        lock = self._plan_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._plan_locks[key] = lock
        return lock

    def _auto_approve_plan(self, plan_id: str) -> dict[str, Any]:
        customer_id = self._plan_customer_id(plan_id)
        self.repository.add_outreach_event(
            plan_id=plan_id,
            task_id="",
            customer_id=customer_id,
            event_type="plan_auto_approved",
            event_summary="Personalized outreach plan auto-approved and queued",
        )
        return self.repository.update_outreach_plan_status(plan_id, "active")

    def activate_plan(self, plan_id: str) -> dict[str, Any]:
        self.repository.add_outreach_event(
            plan_id=plan_id,
            task_id="",
            customer_id=self._plan_customer_id(plan_id),
            event_type="plan_activated",
            event_summary="Outreach plan activated",
        )
        return self.repository.update_outreach_plan_status(plan_id, "active")

    def pause_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.update_outreach_plan_status(plan_id, "paused")

    def resume_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.update_outreach_plan_status(plan_id, "active")

    def cancel_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.update_outreach_plan_status(plan_id, "cancelled")

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        return self.repository.get_outreach_plan(plan_id)

    def list_events(self, *, limit: int = 100, customer_id: str = "", plan_id: str = "") -> list[dict[str, Any]]:
        return self.repository.list_outreach_events(limit=limit, customer_id=customer_id, plan_id=plan_id)

    async def execute_due_tasks(self, *, limit: int = 20, auto_approved_only: bool = False) -> dict[str, Any]:
        tasks = self.repository.list_due_outreach_tasks(
            limit=limit,
            auto_approved_only=auto_approved_only,
        )
        results = []
        for task in tasks:
            results.append(await self.execute_task(task["id"]))
        return {"count": len(results), "results": results}

    async def execute_due_first_day_tasks(self, *, limit: int = 20) -> dict[str, Any]:
        tasks = self.repository.list_due_outreach_tasks(
            limit=limit,
            auto_approved_only=True,
        )
        results = []
        for task in tasks:
            plan_detail = self.repository.get_outreach_plan(str(task.get("plan_id") or ""))
            plan = plan_detail.get("plan") if isinstance(plan_detail.get("plan"), dict) else {}
            source_snapshot = (
                plan.get("source_snapshot")
                if isinstance(plan.get("source_snapshot"), dict)
                else {}
            )
            trigger_context = (
                source_snapshot.get("trigger_context")
                if isinstance(source_snapshot.get("trigger_context"), dict)
                else {}
            )
            if _string(trigger_context.get("trigger_type")) != FIRST_DAY_SILENCE_TRIGGER_TYPE:
                continue
            results.append(await self.execute_task(task["id"]))
        return {"count": len(results), "results": results}

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        task = self.repository.get_outreach_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}
        reply_messages = task.get("reply_messages") or []
        if not reply_messages:
            return {"ok": False, "status": "blocked", "error": "preview_required", "retryable": True}
        if not self.repository.claim_outreach_task(task_id):
            return {"ok": True, "status": "skipped", "reason": "task_already_claimed"}
        plan_detail = self.repository.get_outreach_plan(str(task["plan_id"]))
        plan = plan_detail.get("plan") or {}
        try:
            sent_today_loader = getattr(self.repository, "outreach_sent_today_count", None)
            if callable(sent_today_loader):
                sent_today_count = sent_today_loader(
                    customer_id=str(task["customer_id"]),
                    corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                    wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                    external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
                )
                source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
                trigger_context = (
                    source_snapshot.get("trigger_context")
                    if isinstance(source_snapshot.get("trigger_context"), dict)
                    else {}
                )
                is_first_day_plan = _string(trigger_context.get("trigger_type")) == FIRST_DAY_SILENCE_TRIGGER_TYPE
                daily_task_limit = FIRST_DAY_DAILY_TASK_LIMIT if is_first_day_plan else OUTREACH_DAILY_TASK_LIMIT
                if sent_today_count >= daily_task_limit:
                    if is_first_day_plan:
                        self.repository.update_outreach_task(
                            task_id,
                            status="skipped",
                            error_message="first_day_daily_task_limit_reached",
                        )
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="first_day_daily_task_limit_reached",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="plan_cancelled_first_day_daily_task_limit",
                            event_summary="First-day outreach plan cancelled because its daily task limit was reached",
                            payload={"sent_today": sent_today_count, "daily_task_limit": daily_task_limit},
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="first_day_daily_task_limit_reached",
                            final_decision="no_send",
                            terminal=True,
                        )
                        return {
                            "ok": True,
                            "status": "skipped",
                            "reason": "first_day_daily_task_limit_reached",
                        }
                    next_window = _next_outreach_day_start()
                    delay_seconds = max(
                        1,
                        int((next_window - datetime.now(timezone.utc)).total_seconds()),
                    )
                    self.repository.reschedule_outreach_task(
                        task_id,
                        delay_seconds=delay_seconds,
                        error_message="personalized_outreach_daily_limit",
                    )
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="task_deferred_daily_limit",
                        event_summary="Personalized outreach daily limit reached; task deferred",
                        payload={"sent_today": sent_today_count, "next_window": next_window.isoformat()},
                    )
                    self._sync_first_day_run_for_task(
                        plan=plan,
                        task=task,
                        status="created",
                        reason_code="daily_limit",
                        final_decision="retry_pending",
                    )
                    return {"ok": True, "status": "rescheduled", "reason": "daily_limit"}
            if task.get("before_send_check"):
                try:
                    refresh = await self.refresh_customer_conversation(
                        customer_id=str(task["customer_id"]),
                        corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                        user_id=str(task.get("user_id") or plan.get("user_id") or ""),
                        wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                        external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
                        limit=50,
                    )
                    customer_relation = (
                        refresh.get("customer_relation")
                        if isinstance(refresh.get("customer_relation"), dict)
                        else {}
                    )
                    if not customer_relation.get("available"):
                        raise RuntimeError("before_send_customer_relation_unavailable")
                    if customer_relation_is_deleted(customer_relation):
                        self.repository.update_outreach_task(task_id, status="skipped")
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="customer_deleted",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="task_skipped_customer_deleted",
                            event_summary="Customer deleted the sales contact before outreach execution",
                            payload={"customer_relation": customer_relation},
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="customer_deleted",
                            final_decision="cancelled",
                            terminal=True,
                        )
                        return {
                            "ok": True,
                            "status": "skipped",
                            "reason": "customer_deleted",
                            "customer_relation": customer_relation,
                        }
                    if self._customer_replied_after_plan(plan, refresh.get("latest_customer_message_at")):
                        self.repository.update_outreach_task(task_id, status="skipped")
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="customer_replied_after_plan_creation",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="task_skipped_customer_replied",
                            event_summary="Customer replied before outreach task execution",
                            payload=refresh,
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="customer_replied",
                            final_decision="second_task_cancelled",
                            terminal=True,
                        )
                        return {"ok": True, "status": "skipped", "reason": "customer_replied"}
                    order_gate = await self._refresh_order_eligibility(task=task, plan=plan)
                    if not order_gate.get("available"):
                        raise RuntimeError(
                            f"before_send_order_check_unavailable: {order_gate.get('reason') or 'unknown'}"
                        )
                    if not order_gate.get("eligible"):
                        self.repository.update_outreach_task(task_id, status="skipped")
                        self.repository.skip_remaining_outreach_tasks(
                            str(task["plan_id"]),
                            reason="customer_order_state_changed",
                            exclude_task_id=task_id,
                        )
                        self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                        self.repository.add_outreach_event(
                            plan_id=str(task["plan_id"]),
                            task_id=task_id,
                            customer_id=str(task["customer_id"]),
                            event_type="task_skipped_order_state_changed",
                            event_summary="Customer order state changed before outreach execution",
                            payload=order_gate,
                        )
                        self._sync_first_day_run_for_task(
                            plan=plan,
                            task=task,
                            status="cancelled",
                            reason_code="order_state_changed",
                            final_decision="cancelled",
                            terminal=True,
                        )
                        return {"ok": True, "status": "skipped", "reason": "order_state_changed"}
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    self.repository.reschedule_outreach_task(
                        task_id,
                        delay_seconds=self.before_send_retry_seconds,
                        error_message=message,
                    )
                    self.repository.add_outreach_event(
                        plan_id=str(task["plan_id"]),
                        task_id=task_id,
                        customer_id=str(task["customer_id"]),
                        event_type="before_send_check_failed",
                        event_summary="Conversation check failed before outreach send; send blocked",
                        payload={"error": message},
                    )
                    self._sync_first_day_run_for_task(
                        plan=plan,
                        task=task,
                        status="created",
                        reason_code="before_send_check_failed",
                        final_decision="retry_pending",
                        error=exc,
                    )
                    return {"ok": False, "status": "rescheduled", "error": message, "retryable": True}
            try:
                reply_messages = await self._generate_task_messages(task=task, plan=plan)
            except OutreachMessagePolicyError as exc:
                reason = _string(exc) or "first_day_message_policy_violation"
                self.repository.update_outreach_task(task_id, status="skipped", error_message=reason)
                self.repository.skip_remaining_outreach_tasks(
                    str(task["plan_id"]),
                    reason=reason,
                    exclude_task_id=task_id,
                )
                self.repository.update_outreach_plan_status(str(task["plan_id"]), "cancelled")
                self.repository.add_outreach_event(
                    plan_id=str(task["plan_id"]),
                    task_id=task_id,
                    customer_id=str(task["customer_id"]),
                    event_type="task_skipped_message_policy",
                    event_summary="First-day outreach message remained unsafe after rewrite",
                    payload={"reason": reason},
                )
                self._sync_first_day_run_for_task(
                    plan=plan,
                    task=task,
                    status="blocked",
                    reason_code=reason,
                    final_decision="blocked",
                    terminal=True,
                )
                return {"ok": True, "status": "skipped", "reason": reason}
            task = self.repository.update_outreach_task(
                task_id,
                status="sending",
                reply_messages=reply_messages,
            )
            send_result = await self.system_client.send(
                corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                customer_id=str(task["customer_id"]),
                external_userid=str(task.get("external_userid") or plan.get("external_userid") or task["customer_id"]),
                user_id=str(task.get("user_id") or plan.get("user_id") or ""),
                wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                reply_messages=reply_messages,
            )
        except Exception as exc:
            message = str(exc)
            self.repository.update_outreach_task(task_id, status="failed", error_message=message)
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="task_failed",
                event_summary=message[:240],
                payload={"error": message},
            )
            self._sync_first_day_run_for_task(
                plan=plan,
                task=task,
                status="failed",
                reason_code="task_failed",
                final_decision="failed",
                terminal=True,
                error=exc,
            )
            return {"ok": False, "status": "failed", "error": message}
        data = send_result.get("data") if isinstance(send_result.get("data"), dict) else {}
        sent_at = utc_now_iso()
        self.repository.update_outreach_task(
            task_id,
            status="sent",
            reply_messages=reply_messages,
            sent_at=sent_at,
            send_status=str(data.get("send_status") or send_result.get("msg") or "accepted"),
            system_msgid=str(data.get("system_msgid") or ""),
        )
        remaining_loader = getattr(self.repository, "outreach_plan_has_remaining_tasks", None)
        has_remaining_tasks = bool(remaining_loader(str(task["plan_id"]))) if callable(remaining_loader) else True
        next_plan_status = "waiting" if has_remaining_tasks else "completed"
        scope = build_customer_scope(
            corp_id=task.get("corp_id") or plan.get("corp_id"),
            wechat=task.get("wechat") or plan.get("wechat"),
            external_userid=task.get("external_userid") or plan.get("external_userid"),
            customer_id=task.get("customer_id"),
        )
        if scope.persistence_allowed:
            self.repository.touch_customer_message_time(scope.sales_contact_key, field="last_outreach_at", value=sent_at)
            self.repository.update_customer_outreach_state(
                scope.sales_contact_key,
                outreach_status=next_plan_status,
                outreach_plan_id=str(task["plan_id"]) if has_remaining_tasks else "",
                last_outreach_at=sent_at,
            )
        self.repository.update_outreach_plan_status(str(task["plan_id"]), next_plan_status)
        self.repository.add_outreach_event(
            plan_id=str(task["plan_id"]),
            task_id=task_id,
            customer_id=str(task["customer_id"]),
            event_type="task_sent",
            event_summary="Outreach task sent",
            payload={"reply_messages": reply_messages, "send_result": send_result},
        )
        self._sync_first_day_run_for_task(
            plan=plan,
            task=task,
            status="sent" if not has_remaining_tasks else "created",
            reason_code="plan_completed" if not has_remaining_tasks else "first_task_sent",
            final_decision="sent" if not has_remaining_tasks else "second_task_pending",
            terminal=not has_remaining_tasks,
        )
        if not has_remaining_tasks:
            self.repository.add_outreach_event(
                plan_id=str(task["plan_id"]),
                task_id=task_id,
                customer_id=str(task["customer_id"]),
                event_type="plan_cycle_completed",
                event_summary="Final outreach step sent; current personalized outreach cycle completed",
                payload={"sent_at": sent_at},
            )
        return {"ok": True, "status": "sent", "send_result": send_result}

    async def _refresh_order_eligibility(self, *, task: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
        trigger_context = (
            source_snapshot.get("trigger_context")
            if isinstance(source_snapshot.get("trigger_context"), dict)
            else {}
        )
        if trigger_context.get("activation_policy") != "auto_approved":
            return {"available": True, "eligible": True, "reason": "manual_plan_not_subject_to_auto_order_gate"}
        if self.customer_context_service is None:
            return {
                "available": False,
                "eligible": False,
                "reason": "customer_context_service_unavailable",
            }
        customer_id = str(task.get("customer_id") or plan.get("customer_id") or "")
        corp_id = str(task.get("corp_id") or plan.get("corp_id") or "")
        wechat = str(task.get("wechat") or plan.get("wechat") or "")
        external_userid = str(task.get("external_userid") or plan.get("external_userid") or "")
        user_id = str(task.get("user_id") or plan.get("user_id") or "")
        local_context = self.repository.recent_customer_context(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        request_context = {
            "customer_id": customer_id,
            "corp_id": corp_id,
            "wechat": wechat,
            "external_userid": external_userid,
            "user_id": user_id,
        }
        customer_context = await asyncio.to_thread(
            self.customer_context_service.load,
            customer_id=customer_id,
            memory=local_context.get("memory") or {},
            request_context=request_context,
        )
        if bool(task.get("should_send_payment_collection")):
            return personalized_payment_collection_eligibility(customer_context, amount=10)
        return personalized_order_eligibility(customer_context)

    async def preview_task(self, task_id: str) -> dict[str, Any]:
        task = self.repository.get_outreach_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}
        plan_detail = self.repository.get_outreach_plan(str(task["plan_id"]))
        plan = plan_detail.get("plan") or {}
        reply_messages = await self._generate_task_messages(task=task, plan=plan)
        task = self.repository.update_outreach_task(
            task_id,
            status=str(task.get("status") or "pending"),
            reply_messages=reply_messages,
        )
        self.repository.add_outreach_event(
            plan_id=str(task["plan_id"]),
            task_id=task_id,
            customer_id=str(task["customer_id"]),
            event_type="task_previewed",
            event_summary="Generated outreach task messages for review without sending",
            payload={"reply_messages": reply_messages},
        )
        return {"ok": True, "status": "previewed", "reply_messages": reply_messages, "task": task}

    async def _generate_task_messages(self, *, task: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
        context = self.repository.recent_customer_context(
            str(task["customer_id"]),
            corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
            wechat=str(task.get("wechat") or plan.get("wechat") or ""),
            external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
        )
        resolved_asset = _task_resolved_asset(task)
        task_metadata = _task_metadata(task)
        source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
        trigger_context = (
            source_snapshot.get("trigger_context")
            if isinstance(source_snapshot.get("trigger_context"), dict)
            else {}
        )
        first_day_opened_silence = (
            _string(trigger_context.get("trigger_type")) == FIRST_DAY_SILENCE_TRIGGER_TYPE
        )
        step_index = _int(task.get("step_index"), 0)
        payload = {
            "task": {
                "step_index": step_index,
                "first_day_opened_silence": first_day_opened_silence,
                "intent": task.get("intent"),
                "message_goal": task.get("message_goal"),
                "draft_text": _first_reply_text(task.get("reply_messages")),
                "draft_texts": _reply_texts(task.get("reply_messages")),
                "should_send_payment_collection": bool(task.get("should_send_payment_collection")),
            },
            "task_metadata": task_metadata,
            "resolved_asset": {
                key: resolved_asset.get(key)
                for key in (
                    "asset_id",
                    "type",
                    "source",
                    "name",
                    "annotation",
                    "use_cases",
                    "avoid_when",
                    "tags",
                    "description",
                )
                if resolved_asset.get(key)
            },
            "plan": {
                "customer_stage": plan.get("customer_stage"),
                "stall_reason": plan.get("stall_reason"),
                "customer_psychology": plan.get("customer_psychology"),
                "plan_goal": plan.get("plan_goal"),
            },
            "customer_context": context,
            "offer_context": S10_OUTREACH_CONTEXT,
        }
        model_messages = [
            {"role": "system", "content": OUTREACH_MESSAGE_SYSTEM_PROMPT},
            {"role": "user", "content": dumps(payload)},
        ]
        last_error = ""
        last_evidence = ""
        for attempt in range(2):
            response = await self.model_client.chat_json(
                model_messages,
                tier="balanced",
                temperature=0.0,
            )
            texts = _reply_texts(response.get("reply_messages"))
            if not texts:
                raise RuntimeError("outreach_message_model_empty")
            if not first_day_opened_silence:
                return _compose_outreach_messages(
                    texts,
                    resolved_asset=resolved_asset,
                    should_send_payment_collection=bool(task.get("should_send_payment_collection")),
                )
            last_error, last_evidence = _first_day_message_policy_error(
                texts,
                step_index=step_index,
                plan=plan,
                context=context,
            )
            if not last_error:
                return _compose_outreach_messages(
                    texts,
                    resolved_asset=resolved_asset,
                    should_send_payment_collection=bool(task.get("should_send_payment_collection")),
                )
            if attempt == 0:
                model_messages.extend(
                    [
                        {"role": "assistant", "content": dumps(response)},
                        {
                            "role": "user",
                            "content": dumps(
                                {
                                    "policy_error": last_error,
                                    "conflicting_text": last_evidence[:240],
                                    "repair_instruction": (
                                        "保持计划锁定的场景、事实、素材和CTA不变，完整重写客户可见文字。"
                                        "不得只换称呼或语序；使用中性称谓，不得出现任何性别化称呼或暗示。"
                                    ),
                                }
                            ),
                        },
                    ]
                )
        raise OutreachMessagePolicyError(last_error or "first_day_message_policy_violation")

    def _plan_customer_id(self, plan_id: str) -> str:
        detail = self.repository.get_outreach_plan(plan_id)
        return str(detail.get("plan", {}).get("customer_id") or "")

    def _sop_candidates(self, filters: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        query_limit = max(1, min(limit, 100))
        keyword = _string(filters.get("keyword") or filters.get("project_keyword") or filters.get("add_wechat_item"))
        return self.list_candidates(
            limit=query_limit,
            silent_minutes_min=_int(filters.get("silent_minutes_min"), 0),
            outreach_status=_string(filters.get("outreach_status")),
            lifecycle_stage=_string(filters.get("lifecycle_stage")),
            no_plan_only=_bool(filters.get("no_plan_only")),
            keyword=keyword,
        )

    @staticmethod
    def _normalize_sop_filters(value: Any) -> dict[str, Any]:
        filters = value if isinstance(value, dict) else {}
        return {
            "silent_minutes_min": max(0, _int(filters.get("silent_minutes_min"), 0)),
            "outreach_status": _string(filters.get("outreach_status")),
            "lifecycle_stage": _string(filters.get("lifecycle_stage")),
            "no_plan_only": _bool(filters.get("no_plan_only")),
            "keyword": _string(filters.get("keyword") or filters.get("project_keyword") or filters.get("add_wechat_item")),
            "business_goal": _string(filters.get("business_goal")),
            "limit": max(1, min(_int(filters.get("limit"), 20), 100)),
        }

    @staticmethod
    def _candidate_matches_keyword(candidate: dict[str, Any], keyword: str) -> bool:
        needle = keyword.strip().lower()
        if not needle:
            return True
        parts = [
            candidate.get("customer_id"),
            candidate.get("external_userid"),
            candidate.get("wechat"),
            candidate.get("platform_customer_name"),
            candidate.get("title"),
            candidate.get("last_customer_message"),
            candidate.get("latest_event_summary"),
            candidate.get("lifecycle_stage"),
        ]
        for field in ("portrait", "basic_info"):
            value = candidate.get(field)
            if isinstance(value, dict):
                parts.extend(str(item) for item in value.values())
        return needle in " ".join(_string(part).lower() for part in parts if part is not None)

    @staticmethod
    def _conversation_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        messages = data.get("messages") if isinstance(data, dict) else []
        return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []

    @staticmethod
    def _local_context_messages(recent_messages: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in recent_messages[-max(1, min(limit, 50)):]:
            role = _string(item.get("role"))
            created_at = _string(item.get("created_at"))
            if role == "user":
                content = _string(item.get("content"))
                if content:
                    output.append(
                        {
                            "direction": "customer",
                            "sender_type": "customer",
                            "sender_name": "客户",
                            "content": content,
                            "msgtype": "text",
                            "created_at": created_at,
                        }
                    )
                continue
            reply_messages = item.get("reply_messages") if isinstance(item.get("reply_messages"), list) else []
            if reply_messages:
                for reply in reply_messages:
                    if not isinstance(reply, dict):
                        continue
                    output.append(
                        {
                            "direction": "staff",
                            "sender_type": "staff",
                            "sender_name": "员工",
                            "content": reply.get("content"),
                            "msgtype": _string(reply.get("type")) or "text",
                            "created_at": created_at,
                        }
                    )
                continue
            content = _string(item.get("content"))
            if content:
                output.append(
                    {
                        "direction": "staff",
                        "sender_type": "staff",
                        "sender_name": "员工",
                        "content": content,
                        "msgtype": "text",
                        "created_at": created_at,
                    }
                )
        return output

    @staticmethod
    def _latest_message_time(messages: list[dict[str, Any]], *, sender: str) -> str:
        candidates = []
        for item in messages:
            direction = _string(item.get("direction") or item.get("from") or item.get("sender_type")).lower()
            if sender == "customer" and direction not in {"customer", "user", "external"}:
                continue
            if sender == "staff" and direction not in {"staff", "assistant", "service", "ai"}:
                continue
            value = _message_time_iso(item.get("msgtime") or item.get("created_at") or item.get("send_time"))
            if value:
                candidates.append(value)
        return max(candidates) if candidates else ""

    @staticmethod
    def _customer_replied_after_plan(plan: dict[str, Any], latest_customer_message_at: Any) -> bool:
        latest = _parse_iso(_string(latest_customer_message_at))
        source_snapshot = plan.get("source_snapshot") if isinstance(plan.get("source_snapshot"), dict) else {}
        fact_snapshot = (
            source_snapshot.get("customer_fact_snapshot")
            if isinstance(source_snapshot.get("customer_fact_snapshot"), dict)
            else source_snapshot.get("memory")
            if isinstance(source_snapshot.get("memory"), dict)
            else {}
        )
        anchor = _parse_iso(_string(fact_snapshot.get("last_customer_message_at")))
        if not anchor:
            anchor = _parse_iso(_string(plan.get("created_at")))
        return bool(latest and anchor and latest > anchor)
