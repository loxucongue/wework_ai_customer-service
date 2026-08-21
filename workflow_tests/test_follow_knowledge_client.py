from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from app.services.follow_knowledge_client import FollowKnowledgeClient


def _settings(**overrides):
    values = {
        "follow_knowledge_enabled": True,
        "follow_knowledge_base_url": "https://knowledge.example.test",
        "follow_knowledge_token": "test-token",
        "follow_knowledge_timeout_seconds": 4.0,
        "follow_knowledge_cache_ttl_seconds": 60.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_sequence_index_reads_every_page() -> None:
    client = FollowKnowledgeClient(_settings())
    calls: list[int] = []

    async def query_sequences(**kwargs):
        page = int(kwargs["page"])
        calls.append(page)
        count = 100 if page == 1 else 22
        return {
            "status": "ok",
            "total": 122,
            "page_size": 100,
            "items": [{"id": f"seq-{page}-{index}"} for index in range(count)],
            "cache_hit": False,
        }

    client.query_sequences = query_sequences  # type: ignore[method-assign]
    result = asyncio.run(client.query_all_sequences())

    assert calls == [1, 2]
    assert result["status"] == "ok"
    assert result["total"] == 122
    assert len(result["items"]) == 122


def test_script_query_normalizes_media_and_uses_cache() -> None:
    client = FollowKnowledgeClient(_settings())
    calls = 0

    async def request(path, payload):
        nonlocal calls
        calls += 1
        assert path == "event/trigger/follow-script"
        assert payload["checkpointCode"] == "distance"
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://knowledge.example.test/event/trigger/follow-script"),
            json={
                "code": 200,
                "message": "ok",
                "data": {
                    "total": 1,
                    "page": 1,
                    "pageSize": 10,
                    "list": [
                        {
                            "id": 1,
                            "scriptCode": "D01",
                            "scriptName": "距离案例",
                            "bodyText": "承接距离后用案例证明价值",
                            "checkpointCode": "distance",
                            "actionCode": "case",
                            "contentType": "image_text",
                            "media": {"fileId": 9, "url": "https://assets.example.test/case.png"},
                            "status": 20,
                        }
                    ],
                },
            },
        )

    client._request_with_retry = request  # type: ignore[method-assign]

    async def scenario():
        first = await client.query_scripts(checkpoint_code="distance", action_code="case")
        second = await client.query_scripts(checkpoint_code="distance", action_code="case")
        return first, second

    first, second = asyncio.run(scenario())

    assert calls == 1
    assert first["items"][0]["script_code"] == "D01"
    assert first["items"][0]["media"]["url"].endswith("case.png")
    assert second["cache_hit"] is True


def test_unknown_query_codes_return_empty_without_network() -> None:
    client = FollowKnowledgeClient(_settings())

    result = asyncio.run(
        client.query_scripts(checkpoint_code="unknown", action_code="unknown")
    )

    assert result["status"] == "empty"
    assert result["reason"] == "unknown_checkpoint_code"


def test_page_failure_stops_pagination_without_hiding_reason() -> None:
    client = FollowKnowledgeClient(_settings())

    async def query_sequences(**kwargs):
        page = int(kwargs["page"])
        if page == 1:
            return {
                "status": "ok",
                "total": 150,
                "page_size": 100,
                "items": [{"id": f"seq-{index}"} for index in range(100)],
            }
        return {"status": "error", "reason": "http_status:503", "items": []}

    client.query_sequences = query_sequences  # type: ignore[method-assign]
    result = asyncio.run(client.query_all_sequences())

    assert result["status"] == "error"
    assert result["reason"] == "http_status:503"
    assert len(result["items"]) == 100
