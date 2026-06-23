from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import asyncio
import httpx

from app.config import Settings


class OutreachSendClient:
    """Client for the platform proactive message send endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = str(settings.outreach_send_base_url or "").rstrip("/") + "/"
        self._token = settings.outreach_send_agent_token
        self._timeout = float(settings.outreach_send_timeout_seconds)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None

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
        missing = [key for key in ("corp_id", "customer_id", "user_id", "wechat") if not payload.get(key)]
        if missing:
            return {"status": "skipped", "reason": "missing_required_fields", "missing": missing}
        if not reply_messages:
            return {"status": "skipped", "reason": "empty_reply_messages"}

        try:
            response = await self._http_client().post(
                urljoin(self._base_url, "api/v1/platform-agent/ai-outreach/send"),
                json=payload,
                headers={"X-Agent-Token": self._token},
            )
        except httpx.ReadTimeout:
            return {
                "status": "sent",
                "send_status": "accepted_no_response",
                "payload_message_count": len(reply_messages),
                "send_payload": payload,
            }
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}
        return {
            "status": "sent",
            "payload_message_count": len(reply_messages),
            "send_payload": payload,
            "response": data,
        }

    def _http_client(self) -> httpx.AsyncClient:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client.is_closed or self._client_loop_id != loop_id:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._client_loop_id = loop_id
        return self._client

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
        customer_id = external_userid or str(request_context.get("customer_id") or fallback_customer_id or "").strip()
        return {
            "corp_id": str(request_context.get("corp_id") or fallback_corp_id or "").strip(),
            "customer_id": customer_id,
            "external_userid": external_userid,
            "user_id": str(request_context.get("user_id") or fallback_user_id or "").strip(),
            "wechat": str(request_context.get("wechat") or fallback_wechat or "").strip(),
            "plan_id": f"ai-paths-{request_id}",
            "task_id": f"ai-paths-final-reply-{request_id}",
            "reply_messages": reply_messages,
        }
