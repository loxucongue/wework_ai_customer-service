from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.chat_runtime import ChatRuntime
from app.config import Settings
from app.schemas import ChatRequest
from app.services.outreach_system_client import OutreachSystemClient


class _Graph:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("human takeover must stop before graph execution")


class _TraceLogger:
    def write_run(self, state: dict[str, Any]) -> str:
        return f"logs/runs/{state.get('request_id')}.json"


class _Repository:
    def __init__(self) -> None:
        self.saved_states: list[dict[str, Any]] = []

    def upsert_conversation(self, **_kwargs: Any) -> None:
        return None

    def add_user_message(self, **_kwargs: Any) -> None:
        return None

    def save_run(self, *, final_state: dict[str, Any], **_kwargs: Any) -> None:
        self.saved_states.append(dict(final_state))


class _StatusClient:
    available = True

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[dict[str, Any]] = []

    async def conversation_status(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "takeover": {
                    "mode": self.mode,
                    "is_human": self.mode == "human",
                    "handoff_status": "human_pending" if self.mode == "human" else "",
                    "reason_code": "ai_turn_limit" if self.mode == "human" else "",
                }
            },
        }


class _FailingStatusClient:
    available = True

    async def conversation_status(self, **_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("status endpoint timed out")


class _CallbackService:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[dict[str, Any], bool]] = []

    def enqueue_customer_open(
        self,
        state: dict[str, Any],
        *,
        allow_empty_reply: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((dict(state), allow_empty_reply))
        return dict(self.result)


def _request() -> ChatRequest:
    return ChatRequest(
        content="我先考虑一下",
        customer_id="customer-1",
        corp_id="corp-1",
        external_userid="external-1",
        user_id=7294,
        wechat="DY258",
        request_context={
            "interface_version": "v3",
            "msgid": "customer-message-1",
            "msgtime": "1787400000000",
            "msgtype": "text",
        },
    )


def _runtime(status_client: _StatusClient, callback: _CallbackService) -> tuple[ChatRuntime, _Repository]:
    repository = _Repository()
    runtime = ChatRuntime(
        full_graph=_Graph(),
        trace_logger=_TraceLogger(),
        repository=repository,  # type: ignore[arg-type]
        outreach_system_client=status_client,  # type: ignore[arg-type]
        service_rule_data_service=callback,  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )
    return runtime, repository


def test_human_takeover_returns_empty_before_graph_and_attempts_opening_callback() -> None:
    status_client = _StatusClient("human")
    callback = _CallbackService({"status": "pending", "task_id": "101"})
    runtime, repository = _runtime(status_client, callback)

    response = asyncio.run(runtime.run_v3_takeover_guard(_request()))

    assert response is not None
    assert response.reply_messages == []
    assert response.meta["reply_source"] == "human_takeover_guard"
    assert response.meta["strategy_data_callback"] == {"status": "pending", "task_id": "101"}
    assert callback.calls[0][1] is True
    assert callback.calls[0][0]["reply_messages"] == []
    assert repository.saved_states[0]["takeover_guard"]["decision"] == "return_empty"


def test_ai_takeover_continues_existing_chain() -> None:
    status_client = _StatusClient("ai")
    callback = _CallbackService({"status": "pending"})
    runtime, repository = _runtime(status_client, callback)
    request = _request()

    response = asyncio.run(runtime.run_v3_takeover_guard(request))

    assert response is None
    assert request.request_context["takeover_guard"]["decision"] == "continue_ai"
    assert callback.calls == []
    assert repository.saved_states == []


def test_takeover_status_failure_returns_empty_before_graph() -> None:
    callback = _CallbackService({"status": "pending"})
    runtime, repository = _runtime(_FailingStatusClient(), callback)  # type: ignore[arg-type]
    request = _request()

    response = asyncio.run(runtime.run_v3_takeover_guard(request))

    assert response is not None
    assert response.reply_messages == []
    assert response.meta["reply_source"] == "takeover_status_fail_closed"
    assert request.request_context["takeover_guard"]["decision"] == "return_empty"
    assert request.request_context["takeover_guard"]["reason"] == "status_query_failed"
    assert callback.calls == []
    assert repository.saved_states[0]["takeover_guard"]["decision"] == "return_empty"


def test_missing_takeover_status_client_returns_empty_before_graph() -> None:
    callback = _CallbackService({"status": "pending"})
    repository = _Repository()
    runtime = ChatRuntime(
        full_graph=_Graph(),
        trace_logger=_TraceLogger(),
        repository=repository,  # type: ignore[arg-type]
        service_rule_data_service=callback,  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )

    response = asyncio.run(runtime.run_v3_takeover_guard(_request()))

    assert response is not None
    assert response.reply_messages == []
    assert response.meta["reply_source"] == "takeover_status_fail_closed"
    assert repository.saved_states[0]["takeover_guard"]["reason"] == "outreach_system_not_configured"


def test_conversation_status_client_uses_read_only_status_endpoint() -> None:
    settings = Settings(
        _env_file=None,
        OUTREACH_SYSTEM_BASE_URL="https://wecom.example.test",
        OUTREACH_SYSTEM_TOKEN="test-token",
    )
    client = OutreachSystemClient(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/platform-agent/ai-outreach/conversation/status"
        assert request.headers["X-Agent-Token"] == "test-token"
        assert request.url.params["corp_id"] == "corp-1"
        assert request.url.params["wechat"] == "DY258"
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {"takeover": {"mode": "ai"}}})

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(
        client.conversation_status(
            corp_id="corp-1",
            customer_id="customer-1",
            external_userid="external-1",
            user_id="7294",
            wechat="DY258",
            ai_profile_id="profile-1",
            plan_id="plan-1",
        )
    )
    asyncio.run(client.aclose())

    assert result["data"]["takeover"]["mode"] == "ai"
