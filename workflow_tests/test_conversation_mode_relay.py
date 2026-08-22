from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.schemas import ConversationModeChangedEvent
from app.services.conversation_mode_relay import (
    ConversationModeRelayService,
    ConversationModeWritebackRejected,
    ConversationModeWritebackTimeout,
    ConversationModeWritebackUnavailable,
)


def _settings(**overrides):
    values = {
        "conversation_mode_writeback_url": "https://platform.example/strategy/events",
        "conversation_mode_writeback_token": "strategy-token",
        "conversation_mode_writeback_timeout_seconds": 1.0,
        "outreach_system_token": "fallback-token",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _event(**overrides) -> ConversationModeChangedEvent:
    values = {
        "event_id": "mode-event-1",
        "event_type": "conversation_mode_changed",
        "occurred_at": "2026-08-22T16:30:00+08:00",
        "sequence": 7,
        "corp_id": "corp-1",
        "wechat": "DY258",
        "external_userid": "external-1",
        "customer_id": "customer-1",
        "conversation_id": "ww:dy258:external-1",
        "from_mode": "ai",
        "to_mode": "human",
        "reason_code": "manual_takeover",
        "operator_id": "staff-1",
        "operator_name": "中文客服",
    }
    values.update(overrides)
    return ConversationModeChangedEvent(**values)


def test_event_rejects_non_transition() -> None:
    with pytest.raises(ValidationError, match="must be different"):
        _event(from_mode="ai", to_mode="ai")


def test_forward_preserves_event_and_uses_idempotency_header() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"code": 0, "msg": "ok"}, request=request)

    service = ConversationModeRelayService(_settings())
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    event = _event()
    result = asyncio.run(service.forward(event))
    asyncio.run(service.aclose())

    assert result == {
        "event_id": "mode-event-1",
        "http_status": 200,
        "response": {"code": 0, "msg": "ok"},
    }
    assert captured["headers"]["x-agent-token"] == "strategy-token"
    assert captured["headers"]["x-idempotency-key"] == "mode-event-1"
    assert captured["body"] == event.model_dump(mode="json")
    assert captured["body"]["operator_name"] == "中文客服"


def test_forward_uses_existing_outreach_token_as_compatibility_fallback() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Agent-Token"] == "fallback-token"
        return httpx.Response(200, json={"code": 0}, request=request)

    service = ConversationModeRelayService(
        _settings(conversation_mode_writeback_token="")
    )
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(service.forward(_event()))
    asyncio.run(service.aclose())


def test_forward_requires_writeback_configuration() -> None:
    service = ConversationModeRelayService(
        _settings(
            conversation_mode_writeback_url="",
            conversation_mode_writeback_token="",
            outreach_system_token="",
        )
    )
    with pytest.raises(ConversationModeWritebackUnavailable):
        asyncio.run(service.forward(_event()))


def test_forward_maps_timeout_for_caller_retry() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    service = ConversationModeRelayService(_settings())
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ConversationModeWritebackTimeout):
        asyncio.run(service.forward(_event()))
    asyncio.run(service.aclose())


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (500, {"code": 500, "msg": "failed"}),
        (200, {"code": 1201, "msg": "event rejected"}),
    ],
)
def test_forward_rejects_http_and_application_errors(status_code, payload) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)

    service = ConversationModeRelayService(_settings())
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ConversationModeWritebackRejected):
        asyncio.run(service.forward(_event()))
    asyncio.run(service.aclose())
