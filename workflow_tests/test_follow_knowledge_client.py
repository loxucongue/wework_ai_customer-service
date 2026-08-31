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


def test_custom_checkpoint_code_is_forwarded_but_unknown_action_is_rejected() -> None:
    client = FollowKnowledgeClient(_settings())

    result = asyncio.run(
        client.query_scripts(checkpoint_code="unknown", action_code="unknown")
    )

    assert result["status"] == "empty"
    assert result["reason"] == "unknown_action_code"


def test_custom_sequence_checkpoint_code_is_forwarded_to_tenant_api() -> None:
    client = FollowKnowledgeClient(_settings())

    async def request(path, payload):
        assert path == "event/trigger/follow-sequence"
        assert payload["checkpointCode"] == "tenant_custom_checkpoint"
        return httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "https://knowledge.example.test/event/trigger/follow-sequence",
            ),
            json={
                "code": 200,
                "message": "ok",
                "data": {"total": 0, "page": 1, "pageSize": 10, "list": []},
            },
        )

    client._request_with_retry = request  # type: ignore[method-assign]
    result = asyncio.run(client.query_sequences(checkpoint_code="tenant_custom_checkpoint"))

    assert result["status"] == "ok"
    assert result["query"]["checkpointCode"] == "tenant_custom_checkpoint"


def test_script_query_preserves_type_tag_and_all_paragraph_messages() -> None:
    client = FollowKnowledgeClient(_settings())

    async def request(path, payload):
        assert payload["checkpointTypeId"] == 9
        assert payload["checkpointTagId"] == 1
        assert payload["checkpointCode"] == ""
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
                            "id": 172,
                            "scriptCode": "D27",
                            "scriptName": "距离价值承接",
                            "checkpointCode": "distance",
                            "checkpointTypeId": 9,
                            "checkpointTypeName": "距离/便利",
                            "checkpointTagId": 1,
                            "checkpointTagName": "太远了不方便过来",
                            "actionCode": "empathy",
                            "contentType": "image_text",
                            "paragraphs": [
                                {
                                    "paragraphNo": 1,
                                    "messages": [
                                        {"msgType": "text", "contentText": "先承接距离。"},
                                        {
                                            "msgType": "image",
                                            "mediaUrl": "https://assets.example.test/a.png",
                                            "mediaUrlRaw": "tenant/a.png",
                                            "mediaTitle": "案例图A",
                                            "remark": "距离客户案例",
                                            "fileId": 88,
                                        },
                                    ],
                                },
                                {
                                    "paragraphNo": 2,
                                    "messages": [
                                        {"msgType": "text", "contentText": "再换效果价值。"},
                                        {
                                            "msgType": "video",
                                            "mediaUrl": "https://assets.example.test/b.mp4",
                                            "fileId": 89,
                                        },
                                    ],
                                },
                            ],
                            "status": 20,
                        }
                    ],
                },
            },
        )

    client._request_with_retry = request  # type: ignore[method-assign]
    result = asyncio.run(
        client.query_scripts(
            checkpoint_type_id=9,
            checkpoint_tag_id=1,
            checkpoint_code="distance",
            action_code="empathy",
        )
    )
    script = result["items"][0]
    assert script["authority_scope"] == "approved_sales_expression"
    assert script["hard_fact_authority"] is False
    assert script["source_ref"] == "follow_script:172"
    assert script["checkpoint_type"] == {"id": 9, "code": "distance", "name": "距离/便利"}
    assert script["checkpoint_tag"] == {"id": 1, "name": "太远了不方便过来"}
    assert [item["paragraph_no"] for item in script["paragraphs"]] == [1, 2]
    assert [item["source_ref"] for item in script["paragraphs"]] == [
        "follow_script:172:p1",
        "follow_script:172:p2",
    ]
    assert [message["type"] for item in script["paragraphs"] for message in item["messages"]] == [
        "text",
        "image",
        "text",
        "video",
    ]


def test_script_taxonomy_exposes_actual_action_availability_by_type_and_tag() -> None:
    client = FollowKnowledgeClient(_settings())

    async def query_all_scripts(**kwargs):
        del kwargs
        return {
            "status": "ok",
            "total": 3,
            "duration_ms": 4,
            "items": [
                {
                    "checkpoint_type": {"id": 10, "code": "price", "name": "价格/费用"},
                    "checkpoint_tag": {"id": 16, "name": "觉得价格贵"},
                    "action_code": "resolve",
                },
                {
                    "checkpoint_type": {"id": 10, "code": "price", "name": "价格/费用"},
                    "checkpoint_tag": {"id": 16, "name": "觉得价格贵"},
                    "action_code": "case",
                },
                {
                    "checkpoint_type": {"id": 10, "code": "price", "name": "价格/费用"},
                    "checkpoint_tag": {"id": 17, "name": "担心隐形消费"},
                    "action_code": "resolve",
                },
            ],
        }

    client.query_all_scripts = query_all_scripts  # type: ignore[method-assign]
    result = asyncio.run(client.query_script_taxonomy())

    price = result["types"][0]
    assert price["script_count"] == 3
    assert price["action_counts"] == {"case": 1, "resolve": 2}
    assert price["tags"] == [
        {"id": 16, "name": "觉得价格贵", "script_count": 2, "action_counts": {"case": 1, "resolve": 1}},
        {"id": 17, "name": "担心隐形消费", "script_count": 1, "action_counts": {"resolve": 1}},
    ]


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
