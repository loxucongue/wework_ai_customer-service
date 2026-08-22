from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.services.message_delivery import MessageDeliveryService, delivery_response_metadata


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
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/v1/platform-agent/ai-outreach/conversation/status",
            params={
                "corp_id": corp_id,
                "customer_id": customer_id,
                "external_userid": external_userid,
                "user_id": user_id,
                "wechat": wechat,
            },
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
        source_channel: str = "proactive_message",
        source_kind: str = "proactive_message",
        source_request_id: str = "",
        source_task_id: str = "",
        source_context: dict[str, Any] | None = None,
        delivery_idempotency_key: str = "",
    ) -> dict[str, Any]:
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
        if self.settings.outreach_system_send_conversation_id_enabled and conversation_id:
            body["conversation_id"] = conversation_id
        dispatch_id = ""
        callback_required = False
        if self._delivery_service:
            prepared = self._delivery_service.prepare_dispatch(
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
            result = await self._request(
                "POST",
                "/api/v1/platform-agent/ai-outreach/send",
                json_body=body,
            )
        except Exception as exc:
            if self._delivery_service and dispatch_id:
                self._delivery_service.record_submission(
                    dispatch_id,
                    status="submission_failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            raise
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        upstream_status = str(data.get("send_status") or result.get("msg") or "")
        delivery_status = "submission_unknown" if upstream_status == "accepted_no_response" else "platform_accepted"
        if self._delivery_service and dispatch_id:
            metadata = delivery_response_metadata(result)
            self._delivery_service.record_submission(
                dispatch_id,
                status=delivery_status,
                platform_request_id=metadata["platform_request_id"],
                system_msgid=metadata["system_msgid"],
                error_code="read_timeout" if delivery_status == "submission_unknown" else "",
                error_message="platform send response timed out" if delivery_status == "submission_unknown" else "",
            )
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
