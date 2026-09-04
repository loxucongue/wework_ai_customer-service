from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.services.message_delivery import MessageDeliveryService, delivery_response_metadata


logger = logging.getLogger(__name__)


class OutreachSystemClient:
    def __init__(self, settings: Settings, delivery_service: MessageDeliveryService | None = None):
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._delivery_service = delivery_service

    @property
    def available(self) -> bool:
        return bool(self.settings.outreach_system_token)

    @property
    def supports_conversation_id_send(self) -> bool:
        return bool(self.settings.outreach_system_send_conversation_id_enabled)

    def delivery_dispatch(self, idempotency_key: str) -> dict[str, Any]:
        if not self._delivery_service or not self._delivery_service.enabled:
            return {}
        return self._delivery_service.get_dispatch_by_idempotency_key(idempotency_key)

    async def conversation(
        self,
        *,
        corp_id: str,
        customer_id: str,
        external_userid: str,
        user_id: str,
        wechat: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/platform-agent/ai-outreach/conversation",
            params={
                "corp_id": corp_id,
                "customer_id": customer_id,
                "external_userid": external_userid,
                "user_id": user_id,
                "wechat": wechat,
                "limit": str(max(1, min(limit, 50))),
            },
        )

    async def conversation_status(
        self,
        *,
        corp_id: str,
        customer_id: str,
        external_userid: str,
        user_id: str,
        wechat: str,
        ai_profile_id: str = "",
        plan_id: str = "",
    ) -> dict[str, Any]:
        params = {
            "corp_id": corp_id,
            "customer_id": customer_id,
            "external_userid": external_userid,
            "user_id": user_id,
            "wechat": wechat,
        }
        if ai_profile_id:
            params["ai_profile_id"] = ai_profile_id
        if plan_id:
            params["plan_id"] = plan_id
        return await self._request(
            "GET",
            "/api/v1/platform-agent/ai-outreach/conversation/status",
            params=params,
        )

    async def send(
        self,
        *,
        corp_id: str,
        customer_id: str,
        external_userid: str,
        user_id: str,
        wechat: str,
        plan_id: str,
        task_id: str,
        reply_messages: list[dict[str, Any]],
        conversation_id: str = "",
        run_id: str | int | None = None,
        rule_id: str | int | None = None,
        rule_name: str | None = None,
        rule_task_id: str | int | None = None,
        trigger_event: str | None = None,
        sort_order: str | int | None = None,
        schedule_text: str | None = None,
        scheduled_at: str | int | None = None,
        source_channel: str = "proactive_message",
        source_kind: str = "proactive_message",
        source_request_id: str = "",
        source_task_id: str = "",
        source_context: dict[str, Any] | None = None,
        delivery_idempotency_key: str = "",
    ) -> dict[str, Any]:
        if self._delivery_service:
            self._delivery_service.assert_proactive_send_allowed(
                {
                    "corp_id": corp_id,
                    "customer_id": customer_id,
                    "external_userid": external_userid,
                    "wechat": wechat,
                }
            )
        body = {
            "corp_id": corp_id,
            "customer_id": customer_id,
            "external_userid": external_userid,
            "user_id": user_id,
            "wechat": wechat,
            "plan_id": plan_id,
            "task_id": task_id,
            "reply_messages": reply_messages,
        }
        if run_id is not None:
            body["runId"] = run_id
        if rule_id is not None:
            body["ruleId"] = rule_id
        if rule_name is not None:
            body["ruleName"] = rule_name
        if rule_task_id is not None:
            body["ruleTaskId"] = rule_task_id
        if trigger_event is not None:
            body["triggerEvent"] = trigger_event
        if sort_order is not None:
            body["sortOrder"] = sort_order
        if schedule_text is not None:
            body["scheduleText"] = schedule_text
        if scheduled_at is not None:
            body["scheduledAt"] = scheduled_at
        if self.settings.outreach_system_send_conversation_id_enabled and conversation_id:
            body["conversation_id"] = conversation_id
        dispatch_id = ""
        callback_required = False
        if self._delivery_service and self._delivery_service.enabled:
            phase_started = time.perf_counter()
            prepared = await asyncio.to_thread(
                self._delivery_service.prepare_dispatch,
                source_channel=source_channel,
                source_kind=source_kind,
                source_request_id=source_request_id or task_id,
                source_task_id=source_task_id or task_id,
                conversation_id=conversation_id,
                identity={
                    "corp_id": corp_id,
                    "customer_id": customer_id,
                    "external_userid": external_userid,
                    "user_id": user_id,
                    "wechat": wechat,
                },
                plan_id=plan_id,
                task_id=task_id,
                reply_messages=reply_messages,
                source_context=source_context or {},
                idempotency_key=delivery_idempotency_key,
            )
            self._log_managed_send_phase(task_id, "delivery_prepare", phase_started)
            dispatch = prepared.get("dispatch") if isinstance(prepared.get("dispatch"), dict) else {}
            dispatch_id = str(prepared.get("dispatch_id") or "")
            callback_required = bool(prepared.get("callback_required"))
            body["dispatch_id"] = dispatch_id
            body["reply_messages"] = prepared.get("reply_messages") or reply_messages
            if prepared.get("callback_url"):
                body["callback_url"] = prepared["callback_url"]
            existing_status = str(dispatch.get("status") or "")
            if not bool(dispatch.get("created")) and existing_status in {
                "platform_accepted",
                "submission_unknown",
                "sending",
                "send_succeeded",
                "send_failed",
                "partial_failed",
            }:
                if existing_status in {"send_failed", "partial_failed"}:
                    raise RuntimeError(
                        f"message_delivery_{existing_status}: dispatch_id={dispatch_id}"
                    )
                return {
                    "code": 0,
                    "msg": "duplicate_dispatch",
                    "data": {
                        "send_status": existing_status,
                        "delivery_status": existing_status,
                        "dispatch_id": dispatch_id,
                        "callback_required": callback_required,
                    },
                }
        try:
            phase_started = time.perf_counter()
            result = await self._request(
                "POST",
                "/api/v1/platform-agent/ai-outreach/send",
                json_body=body,
            )
            self._log_managed_send_phase(task_id, "outreach_send_http", phase_started)
        except Exception as exc:
            self._log_managed_send_phase(
                task_id,
                "outreach_send_http",
                phase_started,
                result=f"error:{type(exc).__name__}",
            )
            if self._delivery_service and dispatch_id:
                persist_started = time.perf_counter()
                await asyncio.to_thread(
                    self._delivery_service.record_submission,
                    dispatch_id,
                    status="submission_failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
                self._log_managed_send_phase(task_id, "delivery_submission_persist", persist_started)
            raise
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        upstream_status = str(data.get("send_status") or result.get("msg") or "")
        delivery_status = "submission_unknown" if upstream_status == "accepted_no_response" else "platform_accepted"
        if self._delivery_service and dispatch_id:
            metadata = delivery_response_metadata(result)
            persist_started = time.perf_counter()
            await asyncio.to_thread(
                self._delivery_service.record_submission,
                dispatch_id,
                status=delivery_status,
                platform_request_id=metadata["platform_request_id"],
                system_msgid=metadata["system_msgid"],
                error_code="read_timeout" if delivery_status == "submission_unknown" else "",
                error_message="platform send response timed out" if delivery_status == "submission_unknown" else "",
            )
            self._log_managed_send_phase(task_id, "delivery_submission_persist", persist_started)
            if not callback_required:
                finalize_started = time.perf_counter()
                await asyncio.to_thread(self._delivery_service.mark_finalized, dispatch_id)
                self._log_managed_send_phase(task_id, "delivery_finalize", finalize_started)
        result_data = dict(data)
        result_data.update(
            {
                "delivery_status": delivery_status,
                "dispatch_id": dispatch_id,
                "callback_required": callback_required,
            }
        )
        result["data"] = result_data
        return result

    @staticmethod
    def _log_managed_send_phase(
        task_id: str,
        phase: str,
        started: float,
        *,
        result: str = "ok",
    ) -> None:
        logger.warning(
            "managed_send_phase %s",
            json.dumps(
                {
                    "task_id": str(task_id or ""),
                    "phase": phase,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    "result": result,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("OUTREACH_SYSTEM_TOKEN is not configured")
        url = f"{self.settings.outreach_system_base_url.rstrip('/')}{path}"
        headers = {
            "X-Agent-Token": self.settings.outreach_system_token,
            "Content-Type": "application/json; charset=utf-8",
        }
        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["content"] = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        try:
            response = await self._http_client().request(method, url, **kwargs)
        except httpx.ReadTimeout:
            if method.upper() == "POST" and path.endswith("/ai-outreach/send"):
                return {"code": 0, "msg": "accepted_no_response", "data": {"send_status": "accepted_no_response"}}
            raise
        text = response.text
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": text}
        if response.status_code >= 400:
            raise RuntimeError(f"outreach_system_http_{response.status_code}: {text[:800]}")
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise RuntimeError(f"outreach_system_error: {payload}")
        return payload if isinstance(payload, dict) else {"data": payload}

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=int(self.settings.outreach_system_timeout_seconds))
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
