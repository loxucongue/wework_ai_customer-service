from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.schemas import ConversationModeChangedEvent


class ConversationModeWritebackUnavailable(RuntimeError):
    pass


class ConversationModeWritebackTimeout(RuntimeError):
    pass


class ConversationModeWritebackRejected(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class ConversationModeRelayService:
    """Forwards platform-owned mode transitions without storing local state."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    @property
    def writeback_url(self) -> str:
        return str(self.settings.conversation_mode_writeback_url or "").strip()

    @property
    def writeback_token(self) -> str:
        return str(
            self.settings.conversation_mode_writeback_token
            or self.settings.outreach_system_token
            or ""
        ).strip()

    @property
    def available(self) -> bool:
        return bool(self.writeback_url and self.writeback_token)

    async def forward(self, event: ConversationModeChangedEvent) -> dict[str, Any]:
        if not self.available:
            raise ConversationModeWritebackUnavailable(
                "Conversation mode strategy writeback is not configured"
            )
        body = event.model_dump(mode="json")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Agent-Token": self.writeback_token,
            "X-Idempotency-Key": event.event_id,
        }
        try:
            response = await self._http_client().post(
                self.writeback_url,
                headers=headers,
                content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            )
        except httpx.TimeoutException as exc:
            raise ConversationModeWritebackTimeout(
                "Conversation mode strategy writeback timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ConversationModeWritebackRejected(
                f"Conversation mode strategy writeback failed: {type(exc).__name__}: {exc}"
            ) from exc

        payload = _response_payload(response)
        if response.status_code >= 400:
            raise ConversationModeWritebackRejected(
                f"Conversation mode strategy writeback returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise ConversationModeWritebackRejected(
                f"Conversation mode strategy writeback rejected event: {payload.get('msg') or payload.get('code')}"
            )
        return {
            "event_id": event.event_id,
            "http_status": response.status_code,
            "response": payload,
        }

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=float(self.settings.conversation_mode_writeback_timeout_seconds)
            )
        return self._client


def _response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:2000]}
