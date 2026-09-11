from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.services.sop_platform_client import SopPlatformClient
from app.services.outreach_system_client import OutreachSystemClient


def test_http_timing_log_has_safe_identifiers_without_request_content(caplog) -> None:
    secret_text = "private customer message"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "data": {"status": 70}})

    settings = SimpleNamespace(
        sop_platform_token="secret-token",
        sop_platform_base_url="https://platform.example",
        sop_platform_timeout_seconds=5,
    )
    client = SopPlatformClient(settings)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def exercise() -> None:
        try:
            await client._request(
                "POST",
                "/event/trigger/consume",
                json_body={"taskId": "60623", "remark": secret_text},
            )
        finally:
            await client.aclose()

    with caplog.at_level(logging.INFO, logger="app.services.sop_platform_client"):
        asyncio.run(exercise())

    message = next(record.message for record in caplog.records if record.message.startswith("sop_platform_http "))
    payload = json.loads(message.removeprefix("sop_platform_http "))
    assert payload["path"] == "/event/trigger/consume"
    assert payload["task_id"] == "60623"
    assert payload["http_status"] == 200
    assert secret_text not in message
    assert "secret-token" not in message


def test_managed_send_phase_log_contains_only_timing_metadata(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="app.services.outreach_system_client"):
        OutreachSystemClient._log_managed_send_phase(
            "platform-sop-send-60623",
            "delivery_prepare",
            0.0,
        )

    message = next(record.message for record in caplog.records if record.message.startswith("managed_send_phase "))
    payload = json.loads(message.removeprefix("managed_send_phase "))
    assert payload["task_id"] == "platform-sop-send-60623"
    assert payload["phase"] == "delivery_prepare"
    assert payload["elapsed_ms"] >= 0
    assert set(payload) == {"task_id", "phase", "elapsed_ms", "result"}


def test_consume_can_complete_task_and_exact_message_group_together() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"code": 200, "data": {"status": 30}})

    settings = SimpleNamespace(
        sop_platform_token="secret-token",
        sop_platform_base_url="https://platform.example",
        sop_platform_timeout_seconds=5,
    )
    client = SopPlatformClient(settings)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def exercise() -> None:
        try:
            await client.consume(
                task_id=101,
                status=30,
                messages=[{"msgId": "7", "status": 30, "remark": ""}],
            )
        finally:
            await client.aclose()

    asyncio.run(exercise())

    assert captured == {
        "taskId": 101,
        "status": 30,
        "remark": "",
        "messages": [{"msgId": 7, "status": 30, "remark": ""}],
    }


def test_sop_messages_falls_back_to_first_unconsumed_list_item() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "list": [
                        {"id": 6, "status": 30, "message_content": [{"type": "text", "content": "old"}]},
                        {"id": 7, "status": 10, "message_content": [{"type": "text", "content": "next"}]},
                    ]
                },
            },
        )

    settings = SimpleNamespace(
        sop_platform_token="secret-token",
        sop_platform_base_url="https://platform.example",
        sop_platform_timeout_seconds=5,
        sop_platform_batch_size=100,
    )
    client = SopPlatformClient(settings)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def exercise() -> dict[str, object]:
        try:
            return await client.sop_messages(event_log_id=9)
        finally:
            await client.aclose()

    page = asyncio.run(exercise())

    assert page["next_item"]["id"] == 7


def test_consume_task_70_omits_message_results() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"code": 200, "data": {"status": 70}})

    settings = SimpleNamespace(
        sop_platform_token="secret-token",
        sop_platform_base_url="https://platform.example",
        sop_platform_timeout_seconds=5,
    )
    client = SopPlatformClient(settings)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def exercise() -> None:
        try:
            await client.consume(task_id=101, status=70, remark="human_takeover")
        finally:
            await client.aclose()

    asyncio.run(exercise())

    assert captured == {"taskId": 101, "status": 70, "remark": "human_takeover"}
    assert "messages" not in captured


@pytest.mark.parametrize(
    ("status", "messages", "expected"),
    [
        (30, None, "exactly one explicit msgId"),
        (30, [{"msgId": 7, "status": 40}], "message status 30"),
        (70, [{"msgId": 7, "status": 70}], "must not consume message content"),
    ],
)
def test_consume_rejects_implicit_or_non_sent_message_consumption(
    status: int,
    messages: list[dict[str, object]] | None,
    expected: str,
) -> None:
    settings = SimpleNamespace(
        sop_platform_token="secret-token",
        sop_platform_base_url="https://platform.example",
        sop_platform_timeout_seconds=5,
    )
    client = SopPlatformClient(settings)

    async def exercise() -> None:
        try:
            with pytest.raises(ValueError, match=expected):
                await client.consume(task_id=101, status=status, messages=messages)
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_pending_connect_timeout_retries_once_with_fresh_client_and_recovers(caplog) -> None:
    calls: list[str] = []

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        calls.append("shared")
        raise httpx.ConnectTimeout("connect timeout", request=request)

    def recovered_handler(_request: httpx.Request) -> httpx.Response:
        calls.append("fresh")
        return httpx.Response(200, json={"code": 200, "data": {"list": [], "total": 0}})

    settings = SimpleNamespace(
        sop_platform_token="secret-token",
        sop_platform_base_url="https://platform.example",
        sop_platform_timeout_seconds=5,
        sop_platform_batch_size=100,
    )
    client = SopPlatformClient(settings)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    retry_client = httpx.AsyncClient(transport=httpx.MockTransport(recovered_handler))
    client._new_http_client = lambda: retry_client  # type: ignore[method-assign]

    async def exercise() -> dict[str, object]:
        try:
            return await client.pending(limit=10)
        finally:
            await client.aclose()

    with caplog.at_level(logging.INFO, logger="app.services.sop_platform_client"):
        page = asyncio.run(exercise())

    assert page["total"] == 0
    assert calls == ["shared", "fresh"]
    assert retry_client.is_closed is True
    assert client.read_retry_status() == {
        "transient_timeout": 1,
        "timeout_recovered": 1,
        "timeout_exhausted": 0,
    }
    assert any('"result": "transient_timeout"' in record.message for record in caplog.records)
    assert any('"result": "timeout_recovered"' in record.message for record in caplog.records)


def test_sop_messages_connect_timeout_stops_after_one_fresh_connection_retry() -> None:
    calls: list[str] = []

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        calls.append("timeout")
        raise httpx.ConnectTimeout("connect timeout", request=request)

    settings = SimpleNamespace(
        sop_platform_token="secret-token",
        sop_platform_base_url="https://platform.example",
        sop_platform_timeout_seconds=5,
        sop_platform_batch_size=100,
    )
    client = SopPlatformClient(settings)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    retry_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    client._new_http_client = lambda: retry_client  # type: ignore[method-assign]

    async def exercise() -> None:
        try:
            with pytest.raises(httpx.ConnectTimeout):
                await client.sop_messages(event_log_id=9)
        finally:
            await client.aclose()

    asyncio.run(exercise())

    assert calls == ["timeout", "timeout"]
    assert client.read_retry_status() == {
        "transient_timeout": 1,
        "timeout_recovered": 0,
        "timeout_exhausted": 1,
    }
