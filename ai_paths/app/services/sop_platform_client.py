from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import Settings


class SopPlatformClient:
    """Client for the third-party SOP task queue.

    The upstream state contract is intentionally limited to 10 (pending),
    20 (processing), and 30 (completed).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self.settings.sop_platform_token)

    async def pending(self) -> list[dict[str, Any]]:
        if not self.available:
            raise RuntimeError("SOP_PLATFORM_TOKEN is not configured")
        now = datetime.now(timezone.utc)
        start = now - timedelta(seconds=max(1, self.settings.sop_platform_lookback_seconds))
        end = now + timedelta(seconds=max(0, self.settings.sop_platform_window_seconds))
        payload = {
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "corp_id": "",
            "wechat": "",
            "limit": max(1, min(int(self.settings.sop_platform_batch_size), 500)),
        }
        response = await self._request("POST", "/event/trigger/pending", json_body=payload)
        data = response.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("list", "items", "records", "tasks"):
                items = data.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        return []

    async def consume(self, *, task_id: str | int, status: int) -> dict[str, Any]:
        if status not in {20, 30}:
            raise ValueError("platform SOP status must be 20 or 30")
        return await self._request(
            "POST",
            "/event/trigger/consume",
            json_body={"taskId": task_id, "status": status},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("SOP_PLATFORM_TOKEN is not configured")
        url = f"{self.settings.sop_platform_base_url.rstrip('/')}{path}"
        headers = {
            "x-event-token": self.settings.sop_platform_token,
            "Content-Type": "application/json; charset=utf-8",
        }
        kwargs: dict[str, Any] = {"headers": headers}
        if json_body is not None:
            kwargs["content"] = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        response = await self._http_client().request(method, url, **kwargs)
        text = response.text
        if response.status_code >= 400:
            raise RuntimeError(f"sop_platform_http_{response.status_code}: {text[:800]}")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"sop_platform_invalid_json_response: {text[:800]}") from None
        if not isinstance(payload, dict):
            raise RuntimeError("sop_platform_invalid_response")
        code = payload.get("code")
        if code not in (None, 0, "0", 200, "200"):
            raise RuntimeError(f"sop_platform_error: {payload}")
        return payload

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            timeout = max(1.0, float(self.settings.sop_platform_timeout_seconds))
            self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
