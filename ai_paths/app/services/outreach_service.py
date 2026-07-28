from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.customer_context import CustomerContextService
from app.services.customer_scope import build_customer_scope
from app.services.coze_client import CozeClient
from app.services.model_client import ModelClient
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
from app.services.sop_reply_pack_service import SopReplyPackService
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
OUTREACH_MIN_STEP_GAP_MINUTES = 24 * 60
OUTREACH_MAX_STEP_GAP_MINUTES = 72 * 60
OUTREACH_MAX_PLAN_MINUTES = 7 * 24 * 60


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


def _add_minutes(value: str, minutes: int) -> str:
    start = _parse_iso(value) or datetime.now(timezone.utc)
    return (start + timedelta(minutes=max(0, int(minutes)))).isoformat()


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
        return ""
    steps = [step for step in response.get("steps") or [] if isinstance(step, dict)][:3]
    if len(steps) < 2:
        return "plan must contain 2 or 3 steps"
    angles = [_string(step.get("persuasion_angle")) for step in steps]
    if any(angle not in OUTREACH_PERSUASION_ANGLES for angle in angles):
        return "every step must use one allowed persuasion_angle"
    if any(current == previous for previous, current in zip(angles, angles[1:])):
        return "adjacent steps must use different persuasion_angle values"
    if any(not _string(step.get("draft_text")) for step in steps):
        return "every step must contain draft_text"
    return ""


class OutreachService:
    def __init__(
        self,
        *,
        repository: AppRepository,
        model_client: ModelClient,
        system_client: OutreachSystemClient,
        customer_context_service: CustomerContextService | None = None,
        sop_reply_pack_service: SopReplyPackService | None = None,
        coze_client: CozeClient | None = None,
        before_send_retry_seconds: int = 60,
    ) -> None:
        self.repository = repository
        self.model_client = model_client
        self.system_client = system_client
        self.customer_context_service = customer_context_service
        self.sop_reply_pack_service = sop_reply_pack_service
        self.coze_client = coze_client
        self.before_send_retry_seconds = max(1, int(before_send_retry_seconds))

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
            payload={"latest_customer_message_at": latest_customer, "message_count": len(messages)},
        )
        return {
            "raw": payload,
            "messages": messages,
            "latest_customer_message_at": latest_customer,
            "latest_staff_message_at": latest_staff,
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
        context = source_context or self.repository.recent_customer_context(
            customer_id,
            corp_id=corp_id,
            wechat=wechat,
            external_userid=external_userid,
        )
        memory = context.get("memory") or {}
        recent_messages = context.get("recent_messages") or []
        goal = business_goal or "推动客户重新开口，并逐步推进到店或支付10元预约金"
        asset_catalog = self._outreach_asset_catalog()
        recent_media = recent_outreach_media(recent_messages, hours=72)
        activity_quote_fact = build_outreach_activity_quote_fact(recent_messages, memory)
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
            "asset_catalog": [
                {
                    key: asset.get(key)
                    for key in (
                        "asset_id",
                        "type",
                        "source_pack_id",
                        "source_pack_name",
                        "sop_category",
                        "purpose",
                    )
                }
                for asset in asset_catalog
            ],
            "recent_media_delivery": recent_media,
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
        structure_error = _outreach_plan_structure_error(response)
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
        structure_error = _outreach_plan_structure_error(response)
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
                payload=response,
            )
            return {"created": False, "ai_result": response}
        structure_error = _outreach_plan_structure_error(response)
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
        previous_delay = 0
        for index, step in enumerate(raw_steps, start=1):
            requested_delay = _int(step.get("delay_minutes"), previous_delay + OUTREACH_MIN_STEP_GAP_MINUTES)
            delay = min(
                max(requested_delay, previous_delay + OUTREACH_MIN_STEP_GAP_MINUTES),
                previous_delay + OUTREACH_MAX_STEP_GAP_MINUTES,
                OUTREACH_MAX_PLAN_MINUTES,
            )
            previous_delay = delay
            payment_collection_basis = _string(step.get("payment_collection_basis"))
            should_send_payment_collection = (
                _bool(step.get("should_send_payment_collection"))
                and index == len(raw_steps)
                and payment_collection_basis == "model_selected_after_quote"
                and not payment_collection_added
                and _valid_activity_quote_evidence(activity_quote_fact)
            )
            payment_collection_added = payment_collection_added or should_send_payment_collection
            draft_text = _string(step.get("draft_text"))
            if not draft_text:
                continue
            resolved_asset = resolved_assets[index - 1]
            task_metadata = {
                "persuasion_angle": _string(step.get("persuasion_angle")),
                "new_value": _string(step.get("new_value")),
                "avoid_repeating": _list_strings(step.get("avoid_repeating")),
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
                    "scheduled_at": _add_minutes(now, delay),
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

    def _outreach_asset_catalog(self) -> list[dict[str, Any]]:
        if self.sop_reply_pack_service is None:
            return []
        return build_outreach_asset_catalog(self.sop_reply_pack_service.load())

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
    ) -> dict[str, Any]:
        """Create one auto-approved day-2 personalized plan and reuse it on later platform triggers."""
        customer_id = _string(identity.get("customer_id"))
        corp_id = _string(identity.get("corp_id"))
        wechat = _string(identity.get("wechat"))
        external_userid = _string(identity.get("external_userid"))
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
            },
        )
        if not result.get("created"):
            return {"reused": False, **result}
        plan_id = _string((result.get("plan") or {}).get("id") or result.get("id"))
        if not plan_id:
            raise RuntimeError("personalized_outreach_plan_missing_id")
        activated = self._auto_approve_plan(plan_id)
        return {"reused": False, "auto_approved": True, **result, **activated}

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
            if task.get("before_send_check"):
                try:
                    refresh = await self.refresh_customer_conversation(
                        customer_id=str(task["customer_id"]),
                        corp_id=str(task.get("corp_id") or plan.get("corp_id") or ""),
                        user_id=str(task.get("user_id") or plan.get("user_id") or ""),
                        wechat=str(task.get("wechat") or plan.get("wechat") or ""),
                        external_userid=str(task.get("external_userid") or plan.get("external_userid") or ""),
                        limit=10,
                    )
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
                for key in ("asset_id", "type", "source", "source_pack_id", "sop_category", "description")
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
