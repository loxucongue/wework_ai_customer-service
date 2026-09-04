from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx

from app.services.sop_platform_client import SopPlatformClient


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
