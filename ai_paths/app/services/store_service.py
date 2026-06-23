from __future__ import annotations

from typing import Any

from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_platform_context import request_context_from_customer_context


class StoreService:
    """Platform-backed store appointment utilities."""

    def __init__(self, platform_client: PlatformAgentClient | None = None) -> None:
        self._platform_client = platform_client

    def available_time(self, *, store_id: str, date: str, customer_context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._platform_client or not self._platform_client.available:
            return {"source": "platform_agent_unavailable", "slots": {}, "error": "PLATFORM_AGENT_TOKEN is not configured"}
        try:
            data = self._platform_client.available_time(
                store_id=store_id,
                date=date,
                request_context=request_context_from_customer_context(customer_context or {}),
            )
            return {"source": "platform_agent.available_time", "date": date, "store_id": store_id, "slots": data}
        except Exception as exc:
            return {"source": "platform_agent.available_time", "date": date, "store_id": store_id, "slots": {}, "error": f"{type(exc).__name__}: {exc}"}
