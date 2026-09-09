from __future__ import annotations

import asyncio
import sys
import time
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.chat_runtime import ChatRuntime  # noqa: E402
from app.config import Settings  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402
from app.services.v3_reply_finalization_service import V3ReplyFinalizationService  # noqa: E402


class _FailGraph:
    async def ainvoke(self, _state: dict[str, object]) -> dict[str, object]:
        raise AssertionError("terminal request must not invoke the sales graph")


class _HumanStatus:
    available = True

    async def conversation_status(self, **_kwargs: object) -> dict[str, object]:
        return {"data": {"takeover": {"mode": "human", "is_human": True}}}


def _runtime(tmp_path: Path) -> tuple[ChatRuntime, AppRepository, SQLiteStore]:
    settings = Settings().model_copy(
        update={
            "service_role": "reply",
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "state.db",
            "memory_dir": tmp_path / "memory",
            "trace_log_dir": tmp_path / "trace",
            "background_workers_enabled": False,
        }
    )
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    runtime = ChatRuntime(
        full_graph=_FailGraph(),
        trace_logger=TraceLogger(settings),
        repository=repository,
        outreach_system_client=_HumanStatus(),  # type: ignore[arg-type]
        settings=settings,
    )
    return runtime, repository, store


def _request(*, msgid: str = "terminal-1", wechat: str = "SL8003") -> ChatRequest:
    return ChatRequest(
        content="客户已经回复",
        customer_id="customer-1",
        platform_customer_id="customer-1",
        corp_id="corp-1",
        user_id=88,
        wechat=wechat,
        external_userid="external-1",
        request_context={"interface_version": "v3", "msgid": msgid},
    )


def test_human_takeover_uses_one_reservation_and_one_terminal_checkout(tmp_path: Path) -> None:
    runtime, _repository, store = _runtime(tmp_path)
    original_connect = store.connect
    checkouts = 0

    @contextmanager
    def delayed_connect():
        nonlocal checkouts
        checkouts += 1
        time.sleep(0.1)
        with original_connect() as conn:
            yield conn

    store.connect = delayed_connect  # type: ignore[method-assign]
    started = time.perf_counter()
    response = asyncio.run(runtime.run_platform_reply(_request()))
    elapsed = time.perf_counter() - started

    assert response.reply_messages == []
    assert response.meta["reply_source"] == "human_takeover_guard"
    # The first transaction durably reserves generation before the remote
    # takeover check; the second atomically completes the terminal run.
    assert checkouts == 2
    assert elapsed < 0.45
    assert response.meta["persistence_metrics"]["terminal"]["connection_count"] == 1


def test_terminal_transaction_cancels_only_matching_sales_contact(tmp_path: Path) -> None:
    runtime, repository, _store = _runtime(tmp_path)
    matching = repository.create_outreach_plan(
        customer_id="customer-1",
        corp_id="corp-1",
        user_id="88",
        wechat="SL8003",
        external_userid="external-1",
        customer_stage="",
        stall_reason="",
        customer_psychology="",
        plan_goal="test",
        source_snapshot={},
        tasks=[{"intent": "follow", "message_goal": "test", "before_send_check": True}],
    )
    other_wechat = repository.create_outreach_plan(
        customer_id="customer-1",
        corp_id="corp-1",
        user_id="89",
        wechat="SL9000",
        external_userid="external-1",
        customer_stage="",
        stall_reason="",
        customer_psychology="",
        plan_goal="test",
        source_snapshot={},
        tasks=[{"intent": "follow", "message_goal": "test", "before_send_check": True}],
    )
    with repository.store.connect() as conn:
        conn.execute("UPDATE outreach_plans SET status='active'")

    response = asyncio.run(runtime.run_platform_reply(_request(msgid="cancel-scope")))

    assert response.meta["persistence_metrics"]["terminal"]["connection_count"] == 1
    assert repository.get_outreach_plan(matching["plan"]["id"])["plan"]["status"] == "cancelled"
    assert repository.get_outreach_plan(other_wechat["plan"]["id"])["plan"]["status"] == "active"
    run = repository.get_run(response.request_id)["run"]
    assert run["output_snapshot"]["post_reply_finalization"]["status"] == "pending"
    with repository.store.connect() as conn:
        usage = conn.execute(
            "SELECT decision_status FROM v3_strategy_usage_events WHERE request_id=?",
            (response.request_id,),
        ).fetchone()
    assert usage["decision_status"] == "system_guard"


def test_terminal_transaction_rolls_back_all_facts_on_usage_failure(tmp_path: Path) -> None:
    _runtime_instance, repository, _store = _runtime(tmp_path)
    request = _request(msgid="rollback")
    state = {
        "request_id": "rollback-request",
        "customer_id": "customer-1",
        "platform_customer_id": "customer-1",
        "corp_id": "corp-1",
        "wechat": "SL8003",
        "external_userid": "external-1",
        "sales_contact_key": "corp-1:sl8003:external-1",
        "request_context": {"interface_version": "v3", "memory_persist_allowed": True},
        "reply_messages": [],
        "reply_source": "human_takeover_guard",
        "decision_status": "system_guard",
        "trace": [],
    }
    original = repository._record_v3_strategy_usage_in_connection

    def fail_usage(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("forced usage failure")

    repository._record_v3_strategy_usage_in_connection = fail_usage  # type: ignore[method-assign]
    try:
        try:
            repository.save_v3_terminal_no_reply(
                default_conversation_id="conversation-rollback",
                resolve_existing=False,
                request=request,
                request_id="rollback-request",
                title="rollback",
                input_snapshot={},
                request_context=state["request_context"],
                final_state=state,
                reply_messages=[],
                token_usage={},
                deferred_payload=state,
            )
        except RuntimeError as exc:
            assert "forced usage failure" in str(exc)
        else:
            raise AssertionError("transaction should fail")
    finally:
        repository._record_v3_strategy_usage_in_connection = original  # type: ignore[method-assign]

    with repository.store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM runs WHERE request_id='rollback-request'"
        ).fetchone()["total"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM messages WHERE request_id='rollback-request'"
        ).fetchone()["total"] == 0


def test_terminal_finalizer_records_identity_and_allows_empty_callback(tmp_path: Path) -> None:
    runtime, repository, _store = _runtime(tmp_path)
    response = asyncio.run(runtime.run_platform_reply(_request(msgid="worker-terminal")))

    class _Callback:
        def __init__(self) -> None:
            self.allow_empty: list[bool] = []

        def enqueue_customer_open(
            self,
            _state: dict[str, object],
            *,
            allow_empty_reply: bool = False,
        ) -> dict[str, object]:
            self.allow_empty.append(allow_empty_reply)
            return {"status": "queued"}

    callback = _Callback()
    settings = Settings().model_copy(
        update={"trace_log_dir": tmp_path / "worker-trace", "memory_dir": tmp_path / "memory"}
    )
    service = V3ReplyFinalizationService(
        repository=repository,
        trace_logger=TraceLogger(settings),
        service_rule_data_service=callback,  # type: ignore[arg-type]
        outreach_service=None,
        memory_store=None,
    )

    result = service.process_batch()

    assert result == {"claimed": 1, "completed": 1, "failed": 0}
    assert callback.allow_empty == [True]
    run = repository.get_run(response.request_id)["run"]
    assert run["output_snapshot"]["post_reply_finalization"]["status"] == "completed"
    with repository.store.connect() as conn:
        identity = conn.execute(
            """
            SELECT verification_status FROM customer_identity_links
            WHERE corp_id='corp-1' AND LOWER(wechat)='sl8003' AND external_userid='external-1'
            """
        ).fetchone()
    assert identity["verification_status"] == "observed"
