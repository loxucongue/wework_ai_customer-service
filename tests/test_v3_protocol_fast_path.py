from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.chat_runtime import ChatRuntime  # noqa: E402
from app.config import Settings  # noqa: E402
from app.routers.reply import create_reply_router  # noqa: E402
from app.schemas import ChatRequest, ChatResponse  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402


class _FailGraph:
    async def ainvoke(self, _state: dict[str, object]) -> dict[str, object]:
        raise AssertionError("protocol messages must not invoke the reply graph")


class _FailCoordinator:
    async def begin(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("protocol messages must not enter message coordination")


class _FailOutreach:
    def record_closing_sequence_shadow(self, _state: dict[str, object]) -> object:
        raise AssertionError("protocol messages must not write outreach strategy state")


def _settings(tmp_path: Path) -> Settings:
    return Settings().model_copy(
        update={
            "service_role": "reply",
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "state.db",
            "memory_dir": tmp_path / "memory",
            "trace_log_dir": tmp_path / "trace",
            "background_workers_enabled": False,
        }
    )


def _protocol_request(content: str, *, msgid: str = "msg-protocol-1") -> ChatRequest:
    return ChatRequest(
        content=content,
        customer_id="123456",
        corp_id="corp-protocol",
        user_id=88,
        wechat="SL8003",
        external_userid="external-protocol",
        request_context={
            "interface_version": "v3",
            "msgid": msgid,
            "http_request_started_at": "2026-09-08T00:00:00+00:00",
            "http_request_ingress_id": "ingress-protocol-1",
        },
    )


def test_protocol_message_persists_one_lightweight_idempotent_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteStore(settings)
    store.initialize()
    original_connect = store.connect
    connect_count = 0

    def counted_connect():
        nonlocal connect_count
        connect_count += 1
        return original_connect()

    store.connect = counted_connect  # type: ignore[method-assign]
    repository = AppRepository(store)
    runtime = ChatRuntime(
        full_graph=_FailGraph(),
        trace_logger=TraceLogger(settings),
        repository=repository,
        platform_reply_coordinator=_FailCoordinator(),  # type: ignore[arg-type]
        outreach_service=_FailOutreach(),  # type: ignore[arg-type]
        settings=settings,
    )
    request = _protocol_request("我已经添加了你，现在我们可以开始聊天了。")

    first = asyncio.run(runtime.run_platform_reply(request))
    second = asyncio.run(runtime.run_platform_reply(request))
    restarted_runtime = ChatRuntime(
        full_graph=_FailGraph(),
        trace_logger=TraceLogger(settings),
        repository=repository,
        platform_reply_coordinator=_FailCoordinator(),  # type: ignore[arg-type]
        outreach_service=_FailOutreach(),  # type: ignore[arg-type]
        settings=settings,
    )
    after_restart = asyncio.run(restarted_runtime.run_platform_reply(request))

    assert first.request_id == second.request_id == after_restart.request_id
    assert first.reply_messages == []
    assert first.meta["reply_source"] == "ignored_platform_auto_message"
    assert first.meta["model_usage"] == []
    # The first request and the after-restart idempotency check each need one
    # checkout; the in-process duplicate is served from the request cache.
    assert connect_count == 2
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM node_traces").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM conversations").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM v3_strategy_usage_events").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM outreach_plans").fetchone()["c"] == 0
    detail = repository.get_run(first.request_id)
    run = detail["run"]
    assert run["output_snapshot"]["reply_source"] == "ignored_platform_auto_message"
    assert detail["node_traces"][0]["node_name"] == "platform_protocol_filter"


def test_recalled_message_uses_same_lightweight_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    runtime = ChatRuntime(
        full_graph=_FailGraph(),
        trace_logger=TraceLogger(settings),
        repository=repository,
        platform_reply_coordinator=_FailCoordinator(),  # type: ignore[arg-type]
        settings=settings,
    )

    response = asyncio.run(
        runtime.run_platform_reply(_protocol_request("【消息已撤回】", msgid="msg-recalled-1"))
    )

    assert response.reply_messages == []
    assert response.meta["reply_source"] == "platform_recalled_message"
    assert response.meta["platform_protocol_event"]["reason"] == "customer_message_recalled"


class _RuntimeStub:
    def __init__(self) -> None:
        self.protocol_calls = 0
        self.takeover_calls = 0
        self.normal_calls = 0

    @staticmethod
    def is_platform_protocol_message(request: ChatRequest) -> bool:
        return ChatRuntime.is_platform_protocol_message(request)

    async def run_v3_takeover_guard(self, _request: ChatRequest) -> ChatResponse | None:
        self.takeover_calls += 1
        return None

    async def run_platform_reply(
        self,
        request: ChatRequest,
        background_tasks: object | None = None,
    ) -> ChatResponse:
        del background_tasks
        if self.is_platform_protocol_message(request):
            self.protocol_calls += 1
            return ChatResponse(
                request_id="protocol-request-id",
                reply_messages=[],
                meta={"reply_source": "ignored_platform_auto_message"},
            )
        await self.run_v3_takeover_guard(request)
        self.normal_calls += 1
        return ChatResponse(
            request_id="normal-request-id",
            reply_messages=[],
            meta={"reply_source": "test"},
        )

    async def run_platform_protocol_reply(
        self,
        request: ChatRequest,
        background_tasks: object | None = None,
    ) -> ChatResponse:
        del background_tasks
        assert self.is_platform_protocol_message(request)
        self.protocol_calls += 1
        return ChatResponse(
            request_id="protocol-request-id",
            reply_messages=[],
            meta={"reply_source": "ignored_platform_auto_message"},
        )


class _VoiceStub:
    def __init__(self) -> None:
        self.calls = 0

    async def prepare(self, request: ChatRequest, _client: object) -> ChatRequest:
        self.calls += 1
        return request


class _RepositoryStub:
    def __init__(self) -> None:
        self.responses: list[tuple[str, dict[str, object]]] = []

    def update_run_http_response(self, *, request_id: str, response_body: dict[str, object]) -> None:
        self.responses.append((request_id, response_body))


def _router_client() -> tuple[TestClient, _RuntimeStub, _VoiceStub]:
    settings = Settings().model_copy(update={"allow_missing_external_api_key": True})
    runtime = _RuntimeStub()
    voice = _VoiceStub()
    services = SimpleNamespace(
        chat_runtime=runtime,
        repository=_RepositoryStub(),
        platform_voice_batch_coordinator=voice,
        voice_transcription_client=object(),
    )
    app = FastAPI()
    app.include_router(create_reply_router(settings, services))  # type: ignore[arg-type]
    return TestClient(app), runtime, voice


def _payload(content: str) -> dict[str, object]:
    return {
        "content": content,
        "customer_id": "123456",
        "corp_id": "corp-protocol",
        "user_id": 88,
        "wechat": "SL8003",
        "external_userid": "external-protocol",
        "request_context": {"msgid": "msg-router-protocol"},
    }


def test_router_filters_protocol_before_takeover_and_voice() -> None:
    client, runtime, voice = _router_client()

    response = client.post(
        "/reply/workflow-compatible-v3",
        json=_payload("我已经添加了你，现在我们可以开始聊天了。"),
    )

    assert response.status_code == 200
    assert response.json()["data"]["reply_messages"] == []
    assert runtime.protocol_calls == 1
    assert runtime.takeover_calls == 0
    assert voice.calls == 0


def test_similar_customer_text_is_not_filtered() -> None:
    client, runtime, voice = _router_client()

    response = client.post(
        "/reply/workflow-compatible-v3",
        json=_payload("客户说我已经添加了你，现在我们可以开始聊天了，这是什么意思？"),
    )

    assert response.status_code == 200
    assert runtime.protocol_calls == 0
    assert runtime.takeover_calls == 1
    assert voice.calls == 1
    assert runtime.normal_calls == 1
