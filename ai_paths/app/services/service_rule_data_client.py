from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings


_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ServiceRuleDataClient:
    """Client for the tenant strategy-data callback endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._enabled = bool(settings.service_rule_data_enabled)
        self._base_url = str(settings.service_rule_data_base_url or "").rstrip("/") + "/"
        self._token = str(settings.service_rule_data_token or "").strip()
        self._timeout = max(1.0, float(settings.service_rule_data_timeout_seconds))
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self._enabled and self._base_url.strip("/") and self._token)

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("service_rule_data_not_configured")
        response = await self._request_with_retry(payload)
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("service_rule_data_invalid_json") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"service_rule_data_http_status:{response.status_code}")
        if not isinstance(body, dict) or int(body.get("code") or 0) != 200:
            message = str(body.get("message") or "unknown") if isinstance(body, dict) else "invalid_response"
            raise RuntimeError(f"service_rule_data_business_error:{message}")
        return body

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request_with_retry(self, payload: dict[str, Any]) -> httpx.Response:
        url = urljoin(self._base_url, "event/trigger/service-rule-data")
        headers = {"Content-Type": "application/json", "x-event-token": self._token}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._http_client().post(url, headers=headers, json=payload)
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                return response
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
            ) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("service_rule_data_request_failed")

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        return self._client
