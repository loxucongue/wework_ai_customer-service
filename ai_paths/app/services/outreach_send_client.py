from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import asyncio
import httpx

from app.config import Settings
from app.services.customer_relation import normalize_customer_relation
from app.services.message_delivery import MessageDeliveryService, delivery_response_metadata


_REQUEST_RETRY_ATTEMPTS = 3
_REQUEST_RETRY_BACKOFF_SECONDS = 0.4
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class OutreachSendClient:
    """Client for the platform proactive message send endpoint."""

    def __init__(self, settings: Settings, delivery_service: MessageDeliveryService | None = None) -> None:
        self._base_url = str(settings.outreach_send_base_url or "").rstrip("/") + "/"
        self._token = settings.outreach_send_agent_token
        self._timeout = float(settings.outreach_send_timeout_seconds)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None
        self._delivery_service = delivery_service

    @property
    def available(self) -> bool:
        return bool(self._base_url.strip("/") and self._token)

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def send_reply_messages(
        self,
        *,
        request_id: str,
        request_context: dict[str, Any],
        fallback_customer_id: str,
        fallback_corp_id: str,
        fallback_user_id: int | str | None,
        fallback_wechat: str | None,
        fallback_external_userid: str | None,
        reply_messages: list[dict[str, Any]],
        source_channel: str = "ai_async_reply",
        source_kind: str = "ai_async_reply",
        source_request_id: str = "",
        source_task_id: str = "",
        conversation_id: str = "",
        source_context: dict[str, Any] | None = None,
        delivery_idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not self.available:
            return {"status": "skipped", "reason": "outreach_send_not_configured"}
        payload = self._payload(
            request_id=request_id,
            request_context=request_context,
            fallback_customer_id=fallback_customer_id,
            fallback_corp_id=fallback_corp_id,
            fallback_user_id=fallback_user_id,
            fallback_wechat=fallback_wechat,
            fallback_external_userid=fallback_external_userid,
            reply_messages=reply_messages,
        )
        missing = [
            key
            for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat")
            if not payload.get(key)
        ]
        if missing:
            return {"status": "skipped", "reason": "missing_required_fields", "missing": missing}
        if not reply_messages:
            return {"status": "skipped", "reason": "empty_reply_messages"}

        dispatch_id = ""
        callback_required = False
        if self._delivery_service:
            prepared = self._delivery_service.prepare_dispatch(
                source_channel=source_channel,
                source_kind=source_kind,
                source_request_id=source_request_id or request_id,
                source_task_id=source_task_id or request_id,
                conversation_id=conversation_id,
                identity={key: payload.get(key) for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat")},
                plan_id=str(payload.get("plan_id") or ""),
                task_id=str(payload.get("task_id") or ""),
                reply_messages=reply_messages,
                source_context=source_context or {},
                idempotency_key=delivery_idempotency_key,
            )
            dispatch = prepared.get("dispatch") if isinstance(prepared.get("dispatch"), dict) else {}
            dispatch_id = str(prepared.get("dispatch_id") or "")
            callback_required = bool(prepared.get("callback_required"))
            payload["dispatch_id"] = dispatch_id
            payload["reply_messages"] = prepared.get("reply_messages") or reply_messages
            if prepared.get("callback_url"):
                payload["callback_url"] = prepared["callback_url"]
            existing_status = str(dispatch.get("status") or "")
            if not bool(dispatch.get("created")) and existing_status in {
                "platform_accepted",
                "submission_unknown",
                "sending",
                "send_succeeded",
                "send_failed",
                "partial_failed",
            }:
                result_status = (
                    "sent"
                    if existing_status == "send_succeeded"
                    else "failed"
                    if existing_status in {"send_failed", "partial_failed"}
                    else "accepted"
                )
                return {
                    "status": result_status,
                    "delivery_status": existing_status,
                    "dispatch_id": dispatch_id,
                    "callback_required": callback_required,
                    "duplicate_dispatch": True,
                    "error": str(dispatch.get("error_message") or existing_status)
                    if result_status == "failed"
                    else "",
                    "payload_message_count": len(reply_messages),
                    "send_payload": payload,
                }

        try:
            response = await self._request_with_retry(
                "POST",
                "api/v1/platform-agent/ai-outreach/send",
                json=payload,
            )
        except httpx.ReadTimeout:
            if self._delivery_service and dispatch_id:
                self._delivery_service.record_submission(
                    dispatch_id,
                    status="submission_unknown",
                    error_code="read_timeout",
                    error_message="platform send response timed out",
                )
            return {
                "status": "accepted" if callback_required else "sent",
                "send_status": "accepted_no_response",
                "delivery_status": "submission_unknown",
                "dispatch_id": dispatch_id,
                "payload_message_count": len(reply_messages),
                "send_payload": payload,
            }
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            if self._delivery_service and dispatch_id:
                self._delivery_service.record_submission(
                    dispatch_id,
                    status="submission_failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            return {
                "status": "failed",
                "delivery_status": "submission_failed",
                "dispatch_id": dispatch_id,
                "error": f"{type(exc).__name__}: {exc}",
                "payload_message_count": len(reply_messages),
                "send_payload": payload,
            }
        data = _response_body(response)
        if response.status_code >= 400:
            if self._delivery_service and dispatch_id:
                self._delivery_service.record_submission(
                    dispatch_id,
                    status="submission_failed",
                    error_code=f"http_{response.status_code}",
                    error_message=response.text[:2000],
                )
            return {
                "status": "failed",
                "delivery_status": "submission_failed",
                "dispatch_id": dispatch_id,
                "error": f"http_status:{response.status_code}",
                "payload_message_count": len(reply_messages),
                "send_payload": payload,
                "response": {
                    "http_status": response.status_code,
                    "body": data,
                    "text": response.text[:2000],
                },
            }
        if self._delivery_service and dispatch_id:
            metadata = delivery_response_metadata(data)
            self._delivery_service.record_submission(
                dispatch_id,
                status="platform_accepted",
                platform_request_id=metadata["platform_request_id"],
                system_msgid=metadata["system_msgid"],
            )
        return {
            "status": "accepted" if callback_required else "sent",
            "delivery_status": "platform_accepted" if dispatch_id else "legacy_http_success",
            "dispatch_id": dispatch_id,
            "payload_message_count": len(reply_messages),
            "send_payload": payload,
            "response": data,
        }

    async def fetch_conversation(
        self,
        *,
        corp_id: str,
        customer_id: str,
        external_userid: str,
        user_id: str,
        wechat: str,
        limit: int = 30,
    ) -> dict[str, Any]:
        if not self.available:
            return {"status": "skipped", "reason": "outreach_send_not_configured"}
        params = {
            "corp_id": str(corp_id or "").strip(),
            "customer_id": str(customer_id or "").strip(),
            "external_userid": str(external_userid or "").strip(),
            "user_id": str(user_id or "").strip(),
            "wechat": str(wechat or "").strip(),
            "limit": str(max(1, min(int(limit or 30), 50))),
        }
        missing = [key for key in ("corp_id", "customer_id", "external_userid", "user_id", "wechat") if not params.get(key)]
        if missing:
            return {"status": "skipped", "reason": "missing_required_fields", "missing": missing, "request": params}

        try:
            response = await self._request_with_retry(
                "GET",
                "api/v1/platform-agent/ai-outreach/conversation",
                params=params,
            )
        except httpx.TimeoutException as exc:
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "request": params}
        except httpx.HTTPError as exc:
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "request": params}

        data = _response_body(response)
        if response.status_code >= 400:
            return {
                "status": "failed",
                "error": f"http_status:{response.status_code}",
                "request": params,
                "response": {
                    "http_status": response.status_code,
                    "body": data,
                    "text": response.text[:2000],
                },
            }
        messages = _conversation_messages(data)
        customer_relation = normalize_customer_relation(data)
        return {
            "status": "ok",
            "request": params,
            "message_count": len(messages),
            "messages": messages,
            "customer_relation": customer_relation,
            "response": data,
        }

    def _http_client(self) -> httpx.AsyncClient:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client.is_closed or self._client_loop_id != loop_id:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._client_loop_id = loop_id
        return self._client

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        client = self._http_client()
        url = urljoin(self._base_url, path)
        headers = {"X-Agent-Token": self._token}
        last_exc: Exception | None = None
        non_idempotent_send = method.upper() == "POST" and path.rstrip("/").endswith("/ai-outreach/send")
        for attempt in range(_REQUEST_RETRY_ATTEMPTS):
            try:
                response = await client.request(method, url, params=params, json=json, headers=headers)
                if (
                    response.status_code in _RETRYABLE_STATUS_CODES
                    and not non_idempotent_send
                    and attempt < (_REQUEST_RETRY_ATTEMPTS - 1)
                ):
                    await asyncio.sleep(_REQUEST_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                return response
            except httpx.ReadTimeout:
                raise
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError, httpx.PoolTimeout) as exc:
                last_exc = exc
                if attempt < (_REQUEST_RETRY_ATTEMPTS - 1):
                    await asyncio.sleep(_REQUEST_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Outreach {method} request failed without response")

    @staticmethod
    def _payload(
        *,
        request_id: str,
        request_context: dict[str, Any],
        fallback_customer_id: str,
        fallback_corp_id: str,
        fallback_user_id: int | str | None,
        fallback_wechat: str | None,
        fallback_external_userid: str | None,
        reply_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        external_userid = str(request_context.get("external_userid") or fallback_external_userid or "").strip()
        customer_id = str(request_context.get("customer_id") or fallback_customer_id or "").strip()
        payload = {
            "corp_id": str(request_context.get("corp_id") or fallback_corp_id or "").strip(),
            "customer_id": customer_id,
            "external_userid": external_userid,
            "user_id": str(request_context.get("user_id") or fallback_user_id or "").strip(),
            "wechat": str(request_context.get("wechat") or fallback_wechat or "").strip(),
            "plan_id": f"ai-paths-{request_id}",
            "task_id": f"ai-paths-final-reply-{request_id}",
            "reply_messages": reply_messages,
        }
        for key in (
            "customer_add_wechat_id",
            "account_id",
            "device_id",
            "assignee_id",
            "assignee_name",
            "wework_user_id",
            "enterprise_id",
            "platform_account_id",
            "platform_user_id",
        ):
            value = str(request_context.get(key) or "").strip()
            if value:
                payload[key] = value
        return payload


def _response_body(response: httpx.Response) -> dict[str, Any] | list[Any] | str:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def _conversation_messages(payload: dict[str, Any] | list[Any] | str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    messages = data.get("messages") if isinstance(data, dict) else []
    return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []
