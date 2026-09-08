from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import Settings


class DingTalkRobotClient:
    """Minimal signed DingTalk custom-robot client.

    Credentials are retained only in memory and are intentionally excluded from
    logs and model representations by the Settings fields that provide them.
    """

    def __init__(self, settings: Settings) -> None:
        self._enabled = bool(settings.sop_failure_alert_enabled)
        self._webhook_url = str(settings.sop_failure_alert_webhook_url or "").strip()
        self._signing_secret = str(settings.sop_failure_alert_signing_secret or "").strip()
        self._timeout = max(1.0, float(settings.sop_failure_alert_timeout_seconds or 5.0))
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self._enabled and self._webhook_url and self._signing_secret)

    async def send_markdown(self, *, title: str, text: str) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("sop_failure_alert_not_configured")
        timestamp = int(time.time() * 1000)
        try:
            response = await self._http_client().post(
                self._signed_url(timestamp),
                json={
                    "msgtype": "markdown",
                    "markdown": {
                        "title": str(title or "第三方 SOP 失败预警")[:64],
                        "text": str(text or "")[:18000],
                    },
                },
            )
        except httpx.RequestError as exc:
            # httpx exception strings can include the full request URL. The
            # webhook access token and signature must never enter logs or DB.
            raise RuntimeError(f"dingtalk_robot_network_error:{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"dingtalk_robot_http_status:{response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("dingtalk_robot_invalid_json") from exc
        if not isinstance(body, dict):
            raise RuntimeError("dingtalk_robot_invalid_response")
        try:
            error_code = int(body.get("errcode") or 0)
        except (TypeError, ValueError):
            error_code = -1
        if error_code != 0:
            error_message = str(body.get("errmsg") or "unknown")[:200]
            raise RuntimeError(f"dingtalk_robot_business_error:{error_code}:{error_message}")
        return body

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _signed_url(self, timestamp: int) -> str:
        string_to_sign = f"{timestamp}\n{self._signing_secret}".encode("utf-8")
        digest = hmac.new(
            self._signing_secret.encode("utf-8"),
            string_to_sign,
            digestmod=hashlib.sha256,
        ).digest()
        signature = base64.b64encode(digest).decode("utf-8")
        parts = urlsplit(self._webhook_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update({"timestamp": str(timestamp), "sign": signature})
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        return self._client
