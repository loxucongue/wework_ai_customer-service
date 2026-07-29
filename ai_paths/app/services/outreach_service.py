from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.customer_context import CustomerContextService
from app.services.customer_relation import (
    customer_relation_is_deleted,
    normalize_customer_relation,
)
from app.services.customer_scope import build_customer_scope
from app.services.coze_client import CozeClient
from app.services.model_client import ModelClient
from app.services.outreach_asset_library_service import OutreachAssetLibraryService
from app.services.outreach_assets import (
    asset_reply_message,
    build_outreach_asset_catalog,
    recent_outreach_media,
    resolve_case_asset,
    resolve_configured_asset,
)
from app.services.outreach_prompts import (
    OUTREACH_MESSAGE_SYSTEM_PROMPT,
    OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT,
    OUTREACH_PLAN_SYSTEM_PROMPT,
    S10_OUTREACH_CONTEXT,
)
from app.services.outreach_system_client import OutreachSystemClient
from app.services.sop_platform_task_policy import personalized_order_eligibility
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
OUTREACH_FIRST_STEP_MAX_MINUTES = 12 * 60
OUTREACH_MIN_STEP_GAP_MINUTES = 6 * 60
OUTREACH_MAX_STEP_GAP_MINUTES = 72 * 60
OUTREACH_MAX_PLAN_MINUTES = 7 * 24 * 60
OUTREACH_DAILY_TASK_LIMIT = 2
OUTREACH_BEIJING_TIMEZONE = timezone(timedelta(hours=8))


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


def _is_second_beijing_day(contact_started_at: str, *, now: datetime | None = None) -> bool:
    started = _parse_iso(_string(contact_started_at))
    if not started:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(OUTREACH_BEIJING_TIMEZONE)
    return current.date() > started.astimezone(OUTREACH_BEIJING_TIMEZONE).date()


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
        pack_id = _string(event.get("pack_id") or event.get("sop_pack_id")).lower()
        category = _string(event.get("sop_category") or event.get("category")).lower()
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
    text: str,
    *,
    resolved_asset: dict[str, Any] | None = None,
    should_send_payment_collection: bool = False,
) -> list[dict[str, Any]]:
    output = [{"type": "text", "order": 1, "content": {"text": _string(text)}}]
    asset_message = asset_reply_message(resolved_asset or {}, order=2)
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
    if not isinstance(messages, list):
        return ""
    for item in messages:
        if not isinstance(item, dict) or _string(item.get("type")) != "text":
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = _string(content.get("text"))
        else:
            text = _string(content)
        if text:
            return text
    return ""


def _plan_step_text(step: dict[str, Any]) -> str:
    text = _first_reply_text(step.get("reply_messages"))
    if text:
        return text
    return _string(step.get("draft_text"))


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
    if not steps:
        return "plan must contain 1 to 3 steps"
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
    asset_strategies = [_string(step.get("asset_strategy")) or "none" for step in steps]
    if any(strategy not in OUTREACH_ASSET_STRATEGIES for strategy in asset_strategies):
        return "every step must use one allowed asset_strategy"
    for step in steps:
        messages = step.get("reply_messages")
        if not isinstance(messages, list) or len(messages) != 1:
            return "every step must contain exactly one reply_messages text item"
        if (
            not isinstance(messages[0], dict)
            or _string(messages[0].get("type")) != "text"
            or not _plan_step_text(step)
        ):
            return "plan step reply_messages must contain one non-empty text item"
        if _string(step.get("content_mode")) == "value_only" and _bool(
            step.get("should_send_payment_collection")
        ):
            return "value_only step cannot send payment_collection"
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
) -> str:
    if not _valid_activity_quote_evidence(activity_quote_fact):
        steps = [step for step in response.get("steps") or [] if isinstance(step, dict)]
        if any(_bool(step.get("should_send_payment_collection")) for step in steps):
            return "activity quote is incomplete; payment_collection must be disabled"
    return ""


class OutreachService:
    def __init__(
        self,
        *,
        repository: AppRepository,
        model_client: ModelClient,
        system_client: OutreachSystemClient,
        customer_context_service: CustomerContextService | None = None,
        outreach_asset_library_service: OutreachAssetLibraryService | None = None,
        coze_client: CozeClient | None = None,
        before_send_retry_seconds: int = 60,
    ) -> None:
        self.repository = repository
        self.model_client = model_client
        self.system_client = system_client
        self.customer_context_service = customer_context_service
        self.outreach_asset_library_service = outreach_asset_library_service
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
        return {
            "ok": True,
            "source": "local_cache",
            "warning": "平台历史聊天查询超时，已显示本地缓存记录",
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
    ) -> dict[str, Any]:
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
                    trigger_context=trigger_context or {},
                )
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
            return self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_relation_unavailable",
                relation=customer_relation,
                trigger_context=trigger_context or {},
            )
        if customer_relation_is_deleted(customer_relation):
            return self._relation_plan_skip(
                customer_id=customer_id,
                corp_id=corp_id,
                wechat=wechat,
                external_userid=external_userid,
                reason="customer_deleted",
                relation=customer_relation,
                trigger_context=trigger_context or {},
            )
        memory = context.get("memory") or {}
        recent_messages = context.get("recent_messages") or []
        goal = business_goal or "推动客户重新开口，并逐步推进到店或支付10元预约金"
        asset_catalog = self._outreach_asset_catalog()
        recent_media = recent_outreach_media(recent_messages, hours=72)
        activity_quote_fact = build_outreach_activity_quote_fact(recent_messages, memory)
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
            "customer_id": customer_id,
            "corp_id": corp_id,
            "user_id": user_id,
            "wechat": wechat,
            "external_userid": external_userid,
            "memory": memory,
            "recent_messages": recent_messages,
            "current_stage": current_stage,
            "business_goal": goal,
            "sop_plan_id": sop_plan_id,
            "offer_context": S10_OUTREACH_CONTEXT,
            "activity_quote_fact": activity_quote_fact,
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
        }
        model_messages = [
            {"role": "system", "content": OUTREACH_PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": dumps(source_snapshot)},
        ]
        response = await self.model_client.chat_json(
            model_messages,
            tier="strong",
            temperature=0.0,
        )
        structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
            response,
            activity_quote_fact=activity_quote_fact,
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
        structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
            response,
            activity_quote_fact=activity_quote_fact,
        )
        if structure_error:
            response = await self.model_client.chat_json(
                [
                    {"role": "system", "content": OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": dumps(
                            {
                                "source_snapshot": source_snapshot,
                                "candidate_plan": response,
                                "structure_error": structure_error,
                                "repair_instruction": (
                                    "修复结构错误并输出完整有效 json。只能使用合同允许的枚举，"
                                    "保留事实边界、递进策略和素材约束，不要解释。"
                                ),
                            }
                        ),
                    },
                ],
                tier="strong",
                temperature=0.0,
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
                },
            )
            return {"created": False, "ai_result": response}
        structure_error = _outreach_plan_structure_error(response) or _outreach_plan_context_error(
            response,
            activity_quote_fact=activity_quote_fact,
        )
        if structure_error:
            raise RuntimeError(f"outreach_plan_model_invalid_structure: {structure_error}")
        raw_steps = [step for step in response.get("steps") or [] if isinstance(step, dict)][:3]

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
        normalized_schedule = _normalize_outreach_schedule(now, raw_steps)
        for index, step in enumerate(raw_steps, start=1):
            schedule = normalized_schedule[index - 1]
            content_mode = _string(step.get("content_mode"))
            payment_collection_basis = _string(step.get("payment_collection_basis"))
            should_send_payment_collection = (
                _bool(step.get("should_send_payment_collection"))
                and index == len(raw_steps)
                and content_mode == "transaction"
                and payment_collection_basis == "model_selected_after_quote"
                and not payment_collection_added
                and _valid_activity_quote_evidence(activity_quote_fact)
            )
            payment_collection_added = payment_collection_added or should_send_payment_collection
            draft_text = _plan_step_text(step)
            if not draft_text:
                continue
            resolved_asset = resolved_assets[index - 1]
            task_metadata = {
                "content_mode": content_mode,
                "persuasion_angle": _string(step.get("persuasion_angle")),
                "new_value": _string(step.get("new_value")),
                "avoid_repeating": _list_strings(step.get("avoid_repeating")),
                "timing_reason": _string(step.get("timing_reason")),
                "urgency_level": _string(step.get("urgency_level")),
                "requested_delay_minutes": schedule["requested_delay_minutes"],
                "normalized_delay_minutes": schedule["normalized_delay_minutes"],
                "asset_strategy": _string(step.get("asset_strategy")) or "none",
                "asset_id": _string(step.get("asset_id")),
                "case_query": _string(step.get("case_query")),
                "cta": _string(step.get("cta")),
                "plan_arc": _string(response.get("plan_arc")),
            }
            reply_messages = _compose_outreach_messages(
                draft_text,
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
        return {
            "created": True,
            **self.repository.create_outreach_plan(
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
            ),
        }

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
        if self.outreach_asset_library_service is None:
            return []
        return build_outreach_asset_catalog(self.outreach_asset_library_service.load())

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
            "recent_messages": conversation_messages[-30:],
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
        scan_limit = max(50, min(500, max(1, int(limit)) * 20))
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
            event_summary="Personalized day-2 outreach plan auto-approved and queued",
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
                if sent_today_count >= OUTREACH_DAILY_TASK_LIMIT:
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
                    return {"ok": False, "status": "rescheduled", "error": message, "retryable": True}
            reply_messages = await self._generate_task_messages(task=task, plan=plan)
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
                outreach_status="waiting",
                outreach_plan_id=str(task["plan_id"]),
                last_outreach_at=sent_at,
            )
        self.repository.update_outreach_plan_status(str(task["plan_id"]), "waiting")
        self.repository.add_outreach_event(
            plan_id=str(task["plan_id"]),
            task_id=task_id,
            customer_id=str(task["customer_id"]),
            event_type="task_sent",
            event_summary="Outreach task sent",
            payload={"reply_messages": reply_messages, "send_result": send_result},
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
        payload = {
            "task": {
                "intent": task.get("intent"),
                "message_goal": task.get("message_goal"),
                "draft_text": _first_reply_text(task.get("reply_messages")),
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
        response = await self.model_client.chat_json(
            [
                {"role": "system", "content": OUTREACH_MESSAGE_SYSTEM_PROMPT},
                {"role": "user", "content": dumps(payload)},
            ],
            tier="balanced",
            temperature=0.0,
        )
        text = _first_reply_text(response.get("reply_messages"))
        if not text:
            raise RuntimeError("outreach_message_model_empty")
        return _compose_outreach_messages(
            text,
            resolved_asset=resolved_asset,
            should_send_payment_collection=bool(task.get("should_send_payment_collection")),
        )

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
        anchor = _parse_iso(_string((plan.get("source_snapshot") or {}).get("memory", {}).get("last_customer_message_at")))
        if not anchor:
            anchor = _parse_iso(_string(plan.get("created_at")))
        return bool(latest and anchor and latest > anchor)
