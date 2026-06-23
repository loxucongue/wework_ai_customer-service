from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings


class OutreachSystemClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self.settings.outreach_system_token)

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
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/platform-agent/ai-outreach/send",
            json_body={
                "corp_id": corp_id,
                "customer_id": customer_id,
                "external_userid": external_userid,
                "user_id": user_id,
                "wechat": wechat,
                "plan_id": plan_id,
                "task_id": task_id,
                "reply_messages": reply_messages,
            },
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
        response = await self._http_client().request(method, url, **kwargs)
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
