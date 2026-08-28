from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.outreach_system_client import OutreachSystemClient
from app.services.message_delivery import MessageDeliveryService
from app.services.storage import AppRepository, SQLiteStore


def test_conversation_status_uses_exact_identity_contract() -> None:
    client = OutreachSystemClient(
        SimpleNamespace(
            outreach_system_token="token",
            outreach_system_base_url="https://example.invalid",
            outreach_system_timeout_seconds=1,
            outreach_system_send_conversation_id_enabled=False,
        )
    )
    captured = {}

    async def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {"code": 0}

    client._request = fake_request
    asyncio.run(
        client.conversation_status(
            corp_id="corp",
            customer_id="123",
            external_userid="external",
            user_id="user",
            wechat="staff",
        )
    )

    assert captured["method"] == "GET"
    assert captured["path"] == "/api/v1/platform-agent/ai-outreach/conversation/status"
    assert captured["params"] == {
        "corp_id": "corp",
        "customer_id": "123",
        "external_userid": "external",
        "user_id": "user",
        "wechat": "staff",
    }


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
            run_id=0,
            rule_id=3,
            rule_name="加微后强触约策略A",
            rule_task_id=15,
            trigger_event="add_wecom",
            sort_order=2,
            schedule_text="5 minutes",
            scheduled_at="2026-08-28 19:31:17",
            reply_messages=[{"type": "text", "content": {"text": "test"}}],
        )
    )

    assert captured["json_body"]["conversation_id"] == "ww:staff:external"
    assert captured["json_body"]["customer_id"] == "123"
    assert captured["json_body"]["external_userid"] == "external"
    assert captured["json_body"]["task_id"] == "task"
    assert captured["json_body"]["runId"] == 0
    assert captured["json_body"]["ruleId"] == 3
    assert captured["json_body"]["ruleName"] == "加微后强触约策略A"
    assert captured["json_body"]["ruleTaskId"] == 15
    assert captured["json_body"]["triggerEvent"] == "add_wecom"
    assert captured["json_body"]["sortOrder"] == 2
    assert captured["json_body"]["scheduleText"] == "5 minutes"
    assert captured["json_body"]["scheduledAt"] == "2026-08-28 19:31:17"


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


def _callback_settings(*, required: bool) -> SimpleNamespace:
    return SimpleNamespace(
        outreach_system_token="token",
        outreach_system_base_url="https://example.invalid",
        outreach_system_timeout_seconds=1,
        outreach_system_send_conversation_id_enabled=True,
        message_delivery_callback_required=required,
        message_delivery_callback_public_url="https://ai.example/callbacks/v1/message-delivery",
        message_delivery_callback_token="callback-secret",
    )


def test_send_adds_delivery_contract_and_waits_for_required_callback(tmp_path) -> None:
    settings = _callback_settings(required=True)
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "required-callback.db"))
    store.initialize()
    repository = AppRepository(store)
    delivery = MessageDeliveryService(settings, repository)
    client = OutreachSystemClient(settings, delivery_service=delivery)
    captured = {}

    async def fake_request(_method, _path, **kwargs):
        captured.update(kwargs)
        return {"code": 0, "msg": "accepted", "data": {"platform_request_id": "upstream-1"}}

    client._request = fake_request
    result = asyncio.run(
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
            delivery_idempotency_key="delivery-contract-required",
        )
    )

    body = captured["json_body"]
    assert body["dispatch_id"]
    assert body["callback_url"] == settings.message_delivery_callback_public_url
    assert body["reply_messages"][0]["client_message_id"] == f"{body['dispatch_id']}:1"
    assert result["data"]["callback_required"] is True
    dispatch = repository.get_message_dispatch(body["dispatch_id"])
    assert dispatch["status"] == "platform_accepted"
    assert dispatch["finalized_at"] == ""


def test_shadow_callback_does_not_repeat_business_finalization(tmp_path) -> None:
    settings = _callback_settings(required=False)
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "shadow-callback.db"))
    store.initialize()
    repository = AppRepository(store)
    delivery = MessageDeliveryService(settings, repository)
    client = OutreachSystemClient(settings, delivery_service=delivery)

    async def fake_request(_method, _path, **_kwargs):
        return {"code": 0, "msg": "accepted", "data": {}}

    client._request = fake_request
    result = asyncio.run(
        client.send(
            corp_id="corp",
            customer_id="123",
            external_userid="external",
            user_id="user",
            wechat="staff",
            plan_id="plan",
            task_id="task",
            reply_messages=[{"type": "text", "content": {"text": "test"}}],
            delivery_idempotency_key="delivery-contract-shadow",
        )
    )

    dispatch = repository.get_message_dispatch(result["data"]["dispatch_id"])
    assert result["data"]["callback_required"] is False
    assert dispatch["finalized_at"]
