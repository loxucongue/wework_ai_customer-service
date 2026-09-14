from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.outreach_system_client import OutreachSystemClient


def _client() -> tuple[OutreachSystemClient, list[dict[str, Any]]]:
    settings = SimpleNamespace(
        outreach_system_token="token",
        outreach_system_send_conversation_id_enabled=False,
    )
    client = OutreachSystemClient(settings)
    requests: list[dict[str, Any]] = []

    async def request(_method: str, _path: str, **values: Any) -> dict[str, Any]:
        requests.append(values["json_body"])
        return {"code": 0, "msg": "accepted", "data": {"system_msgid": "msg-1"}}

    client._request = request  # type: ignore[method-assign]
    return client, requests


def _send(client: OutreachSystemClient, **values: Any) -> dict[str, Any]:
    return asyncio.run(
        client.send(
            corp_id="corp",
            customer_id="customer",
            external_userid="external",
            user_id="staff",
            wechat="staff",
            plan_id="plan",
            task_id="task",
            reply_messages=[{"type": "text", "content": "hello"}],
            **values,
        )
    )


def test_high_priority_is_forwarded_to_aggregate_platform() -> None:
    client, requests = _client()

    _send(client, priority="high")

    assert requests == [
        {
            "corp_id": "corp",
            "customer_id": "customer",
            "external_userid": "external",
            "user_id": "staff",
            "wechat": "staff",
            "plan_id": "plan",
            "task_id": "task",
            "reply_messages": [{"type": "text", "content": "hello"}],
            "priority": "high",
        }
    ]


def test_default_send_does_not_add_priority_field() -> None:
    client, requests = _client()

    _send(client)

    assert "priority" not in requests[0]


def test_request_audit_records_wire_field_names_without_credentials() -> None:
    client, requests = _client()
    audit: dict[str, Any] = {}
    _send(client, priority="high", sort_order=1, run_id=12, request_audit=audit)
    assert audit["body"] == requests[0]
    assert audit["body"]["sortOrder"] == 1
    assert audit["body"]["runId"] == 12
    assert "headers" not in audit
    assert "request_audit" not in requests[0]
    requests[0]["reply_messages"].clear()
    assert audit["body"]["reply_messages"]


def test_request_audit_survives_send_timeout() -> None:
    client, _requests = _client()
    audit: dict[str, Any] = {}

    async def timeout(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("synthetic timeout")

    client._request = timeout  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        _send(client, request_audit=audit)
    assert audit["method"] == "POST"
    assert audit["body"]["task_id"] == "task"
