from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.outreach_system_client import OutreachSystemClient


def test_send_uses_distinct_external_userid_and_conversation_id_when_enabled() -> None:
    client = OutreachSystemClient(
        SimpleNamespace(
            outreach_system_token="token",
            outreach_system_base_url="https://example.invalid",
            outreach_system_timeout_seconds=1,
            outreach_system_send_conversation_id_enabled=True,
        )
    )
    captured = {}

    async def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {"code": 0}

    client._request = fake_request
    asyncio.run(
        client.send(
            corp_id="corp",
            customer_id="123",
            external_userid="external",
            user_id="user",
            wechat="staff",
            plan_id="plan",
            task_id="task",
            conversation_id="ww:staff:external",
            reply_messages=[{"type": "text", "content": {"text": "test"}}],
        )
    )

    assert captured["json_body"]["conversation_id"] == "ww:staff:external"
    assert captured["json_body"]["customer_id"] == "123"
    assert captured["json_body"]["external_userid"] == "external"


def test_send_omits_conversation_id_until_downstream_contract_is_enabled() -> None:
    client = OutreachSystemClient(
        SimpleNamespace(
            outreach_system_token="token",
            outreach_system_base_url="https://example.invalid",
            outreach_system_timeout_seconds=1,
            outreach_system_send_conversation_id_enabled=False,
        )
    )
    captured = {}

    async def fake_request(_method, _path, **kwargs):
        captured.update(kwargs)
        return {"code": 0}

    client._request = fake_request
    asyncio.run(
        client.send(
            corp_id="corp",
            customer_id="123",
            external_userid="external",
            user_id="user",
            wechat="staff",
            plan_id="plan",
            task_id="task",
            conversation_id="ww:staff:external",
            reply_messages=[{"type": "text", "content": {"text": "test"}}],
        )
    )

    assert "conversation_id" not in captured["json_body"]
    assert captured["json_body"]["customer_id"] == "123"
    assert captured["json_body"]["external_userid"] == "external"
