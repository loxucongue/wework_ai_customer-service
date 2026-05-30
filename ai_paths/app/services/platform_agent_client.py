from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings


class PlatformAgentClient:
    """Typed wrapper around the current WeCom third-party platform APIs."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = str(settings.platform_agent_base_url).rstrip("/") + "/"
        self._token = settings.platform_agent_token
        self._request_from = settings.platform_agent_request_from
        self._timeout = float(settings.platform_agent_timeout_seconds)
        self._default_user_id = settings.platform_agent_default_user_id
        self._default_corp_id = settings.platform_agent_default_corp_id
        self._default_wechat = settings.platform_agent_default_wechat

    @property
    def available(self) -> bool:
        return bool(self._base_url and self._token)

    def get_customer_info(
        self,
        *,
        user_id: int | str | None,
        corp_id: str | None,
        wechat: str | None,
        external_userid: str | None,
    ) -> dict[str, Any]:
        if not external_userid:
            return {}
        data = self._get(
            "/platform_agent/customer/get_customer_info",
            {
                "user_id": user_id,
                "corp_id": corp_id,
                "wechat": wechat,
                "external_userid": external_userid,
            },
        )
        info = data.get("info") if isinstance(data, dict) else None
        return info if isinstance(info, dict) else {}

    def list_orders(
        self,
        *,
        customer_id: int | str,
        page: int = 1,
        limit: int = 10,
        request_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not customer_id:
            return []
        data = self._get(
            "/platform_agent/order/index",
            self._with_common_params({"customer_id": customer_id, "page": page, "limit": limit}, request_context),
        )
        if isinstance(data, dict):
            rows = data.get("list") or data.get("data") or []
        else:
            rows = data
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def list_stores(
        self,
        *,
        customer_id: int | str,
        customer_add_wechat_id: int | str,
        request_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not customer_id or not customer_add_wechat_id:
            return []
        data = self._get(
            "/platform_agent/store/index",
            self._with_common_params({"customer_id": customer_id, "customer_add_wechat_id": customer_add_wechat_id}, request_context),
        )
        rows = data.get("list") if isinstance(data, dict) else data
        if isinstance(rows, dict):
            flattened: list[dict[str, Any]] = []
            for group in rows.values():
                if isinstance(group, list):
                    flattened.extend(row for row in group if isinstance(row, dict))
            return flattened
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def list_store_options(self, *, request_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        data = self._get(
            "/platform_agent/option",
            self._with_common_params({"option": "store"}, request_context),
        )
        if isinstance(data, dict):
            rows = data.get("store") or data.get("list") or data.get("data") or []
        else:
            rows = data
        if isinstance(rows, dict):
            flattened: list[dict[str, Any]] = []
            for group in rows.values():
                if isinstance(group, list):
                    flattened.extend(row for row in group if isinstance(row, dict))
            return flattened
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def store_info(self, store_id: int | str, *, request_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not store_id:
            return {}
        data = self._get("/platform_agent/store/info", self._with_common_params({"id": store_id}, request_context))
        return data if isinstance(data, dict) else {}

    def available_time(self, *, store_id: int | str, date: str, request_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not store_id or not date:
            return {}
        return self._get(
            "/platform_agent/order/schedule/available_time",
            self._with_common_params({"store_id": store_id, "date": date}, request_context),
        )

    def _with_common_params(self, params: dict[str, Any], request_context: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(params)
        context = request_context or {}
        for key in ["user_id", "corp_id", "wechat"]:
            if context.get(key) not in (None, ""):
                merged[key] = context[key]
        if merged.get("user_id") in (None, "") and self._default_user_id not in (None, ""):
            merged["user_id"] = self._default_user_id
        if merged.get("corp_id") in (None, "") and self._default_corp_id:
            merged["corp_id"] = self._default_corp_id
        if merged.get("wechat") in (None, "") and self._default_wechat:
            merged["wechat"] = self._default_wechat
        return merged

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.available:
            raise RuntimeError("Platform agent token is not configured")
        clean_params = {key: value for key, value in params.items() if value not in (None, "")}
        response = httpx.get(
            urljoin(self._base_url, path.lstrip("/")),
            params=clean_params,
            headers=self._headers(),
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        code = payload.get("code")
        if code not in (0, 200, "0", "200", None):
            raise RuntimeError(str(payload.get("msg") or f"Platform agent error: {code}"))
        return payload.get("data", {})

    def _headers(self) -> dict[str, str]:
        return {
            "token": self._token,
            "Request-From": self._request_from,
        }


def unix_to_text(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
