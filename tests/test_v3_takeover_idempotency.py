from __future__ import annotations

import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.chat_runtime import ChatRuntime  # noqa: E402
from app.config import Settings  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402


class _FailGraph:
    async def ainvoke(self, _state: dict[str, object]) -> dict[str, object]:
        raise AssertionError("status failure or human takeover must not invoke the reply graph")


class _StatusClient:
    available = True

    def __init__(self, *, result: dict[str, object] | None = None, error: Exception | None = None):
        self.result = result or {}
        self.error = error
        self.calls = 0

    async def conversation_status(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        await asyncio.sleep(0.02)
        if self.error is not None:
            raise self.error
        return self.result


def _runtime(
    tmp_path: Path,
    status_client: _StatusClient,
    *,
    recovery_enabled: bool = False,
) -> tuple[ChatRuntime, SQLiteStore]:
    settings = Settings().model_copy(
        update={
            "service_role": "reply",
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "state.db",
            "memory_dir": tmp_path / "memory",
            "trace_log_dir": tmp_path / "trace",
            "background_workers_enabled": False,
            "v3_reply_recovery_enabled": recovery_enabled,
        }
    )
    store = SQLiteStore(settings)
    store.initialize()
    runtime = ChatRuntime(
        full_graph=_FailGraph(),
        trace_logger=TraceLogger(settings),
        repository=AppRepository(store),
        outreach_system_client=status_client,  # type: ignore[arg-type]
        settings=settings,
    )
    return runtime, store


def _request(msgid: str = "same-platform-message") -> ChatRequest:
    return ChatRequest(
        content="多少钱？",
        customer_id="customer-1",
        corp_id="corp-1",
        user_id=88,
        wechat="SL8003",
        external_userid="external-1",
        request_context={"interface_version": "v3", "msgid": msgid},
    )


def test_duplicate_msgid_shares_one_failed_status_lookup_and_one_fallback_run(tmp_path: Path) -> None:
    client = _StatusClient(error=TimeoutError("status unavailable"))
    runtime, store = _runtime(tmp_path, client)

    async def invoke_twice():
        return await asyncio.gather(
            runtime.run_platform_reply(_request()),
            runtime.run_platform_reply(_request()),
        )

    first, second = asyncio.run(invoke_twice())

    assert client.calls == 1
    assert first.request_id == second.request_id
    assert [item.content for item in first.reply_messages] == ["您稍等一下"]
    assert first.meta["reply_source"] == "takeover_status_unavailable_fallback"
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"] == 1


def test_confirmed_human_takeover_still_returns_empty(tmp_path: Path) -> None:
    client = _StatusClient(
        result={"data": {"takeover": {"mode": "human", "is_human": True}}}
    )
    runtime, store = _runtime(tmp_path, client)

    response = asyncio.run(runtime.run_platform_reply(_request("human-message")))

    assert client.calls == 1
    assert response.reply_messages == []
    assert response.meta["reply_source"] == "human_takeover_guard"
    with store.connect() as conn:
        run = conn.execute(
            "SELECT generation_status, recovery_kind, recovery_next_at FROM runs WHERE request_id=?",
            (response.request_id,),
        ).fetchone()
    assert run["generation_status"] == "completed"
    assert run["recovery_kind"] == ""
    assert run["recovery_next_at"] == ""


def test_process_restart_replays_durable_result_before_remote_status_lookup(tmp_path: Path) -> None:
    first_client = _StatusClient(
        result={"data": {"takeover": {"mode": "human", "is_human": True}}}
    )
    first_runtime, store = _runtime(tmp_path, first_client)
    first = asyncio.run(first_runtime.run_platform_reply(_request("restart-replay")))

    second_client = _StatusClient(error=AssertionError("durable replay must skip status lookup"))
    settings = Settings().model_copy(
        update={
            "service_role": "reply",
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "state.db",
            "memory_dir": tmp_path / "memory-2",
            "trace_log_dir": tmp_path / "trace-2",
            "background_workers_enabled": False,
        }
    )
    restarted = ChatRuntime(
        full_graph=_FailGraph(),
        trace_logger=TraceLogger(settings),
        repository=AppRepository(store),
        outreach_system_client=second_client,  # type: ignore[arg-type]
        settings=settings,
    )

    replay = asyncio.run(restarted.run_platform_reply(_request("restart-replay")))

    assert second_client.calls == 0
    assert replay.request_id == first.request_id
    assert replay.response_id == first.response_id
    assert replay.replayed is True
    assert replay.reply_messages == []


def test_runtime_failure_persists_fallback_pending_for_worker_recovery(tmp_path: Path) -> None:
    client = _StatusClient(
        result={"data": {"takeover": {"mode": "ai", "is_human": False}}}
    )
    runtime, store = _runtime(tmp_path, client, recovery_enabled=True)

    response = asyncio.run(runtime.run_platform_reply(_request("recover-after-failure")))

    assert [item.content for item in response.reply_messages] == ["您稍等一下"]
    assert response.response_id
    assert all(item.client_message_id for item in response.reply_messages)
    assert response.meta["generation_status"] == "fallback_pending"
    with store.connect() as conn:
        run = conn.execute(
            "SELECT generation_status, recovery_kind, recovery_next_at, output_snapshot "
            "FROM runs WHERE request_id=?",
            (response.request_id,),
        ).fetchone()
    assert run["generation_status"] == "fallback_pending"
    assert run["recovery_kind"] == "runtime_exception"
    assert run["recovery_next_at"]
    assert "v3_recovery_payload" in run["output_snapshot"]
