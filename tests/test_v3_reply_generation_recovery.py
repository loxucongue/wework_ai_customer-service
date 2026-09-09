from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.schemas import ChatRequest, ChatResponse, ReplyMessage  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.storage.mysql_schema import (  # noqa: E402
    EXPECTED_COLUMNS,
    EXPECTED_INDEXES,
    EXPECTED_UNIQUE_INDEXES,
)
from app.services.v3_reply_recovery import (  # noqa: E402
    GENERATION_STATUS_FALLBACK_PENDING,
    GENERATION_STATUS_MANUAL_REVIEW,
    GENERATION_STATUS_RECOVERED,
    v3_generation_key,
    v3_response_id,
)
from app.services.workflow_compat import workflow_response_from_chat  # noqa: E402


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings().model_copy(
        update={"aics_storage_backend": "sqlite", "db_path": tmp_path / "generation.db"}
    )
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def _request(msgid: str = "platform-message-1") -> ChatRequest:
    return ChatRequest(
        content="多少钱？",
        customer_id="customer-1",
        platform_customer_id="customer-1",
        corp_id="corp-1",
        conversation_history=["客户: 想了解活动"],
        file_image="https://example.test/a.jpg?token=secret-value",
        user_id=88,
        wechat="SL8003",
        external_userid="external-1",
        customer_add_wechat_id="add-1",
        confirmed_store_id="store-1",
        confirmed_store_name="门店一",
        appointment_id="appointment-1",
        appointment_time="2026-09-10 10:00",
        request_context={"interface_version": "v3", "msgid": msgid, "api_token": "secret"},
    )


def _reserve(
    repository: AppRepository,
    request: ChatRequest,
    *,
    request_id: str,
    started_at: str = "2026-09-09T00:00:00+00:00",
) -> dict[str, object]:
    generation_key = v3_generation_key(
        corp_id=request.corp_id,
        wechat=str(request.wechat or ""),
        external_userid=str(request.external_userid or ""),
        msgid=str(request.request_context.get("msgid") or ""),
    )
    return repository.prepare_v3_request(
        default_conversation_id=f"conversation-{request_id}",
        resolve_existing=True,
        request=request,
        request_id=request_id,
        title="test",
        input_snapshot=request.model_dump(),
        interface_version="v3",
        started_at=started_at,
        http_request_ingress_id=f"ingress-{request_id}",
        generation_key=generation_key,
        response_id=v3_response_id(generation_key),
    )


def test_generation_reservation_is_stable_and_does_not_duplicate_customer_message(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()

    first = _reserve(repository, request, request_id="request-1")
    same_request = _reserve(repository, request, request_id="request-1")
    platform_retry = _reserve(repository, request, request_id="request-2")

    assert first["replayed"] is False
    assert first["continue_existing"] is False
    assert same_request["replayed"] is False
    assert same_request["continue_existing"] is True
    assert platform_retry["replayed"] is True
    assert platform_retry["request_id"] == "request-1"
    assert platform_retry["response_id"] == first["response_id"]
    with repository.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS total FROM runs").fetchone()["total"] == 1
        assert conn.execute("SELECT COUNT(*) AS total FROM messages WHERE role='user'").fetchone()["total"] == 1


def test_completed_generation_replays_exact_http_result_with_stable_message_ids(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request()
    reserved = _reserve(repository, request, request_id="request-complete")
    response_id = str(reserved["response_id"])
    messages = [{"type": "text", "order": 1, "content": "活动价是268元哦～"}]
    response_snapshot = {
        "request_id": "request-complete",
        "response_id": response_id,
        "replayed": False,
        "reply_messages": messages,
        "scene": "price",
        "intent": "ask_price",
        "subflow": "mainline",
        "trace_url": None,
        "meta": {"reply_source": "v3_reply", "api_token": "must-not-persist"},
    }
    repository.save_v3_reply_core(
        conversation_id=str(reserved["conversation_id"]),
        final_state={
            "request_id": "request-complete",
            "customer_id": "customer-1",
            "request_context": {"interface_version": "v3"},
            "reply_source": "v3_reply",
        },
        reply_messages=messages,
        token_usage={},
        deferred_payload={},
        response_snapshot=response_snapshot,
    )
    native = ChatResponse(
        request_id="request-complete",
        response_id=response_id,
        reply_messages=[ReplyMessage(**messages[0])],
        scene="price",
        intent="ask_price",
        subflow="mainline",
    )
    http_body = workflow_response_from_chat(native)
    repository.finalize_run_http_timing(
        request_id="request-complete",
        ingress_id="ingress-request-complete",
        started_at="2026-09-09T00:00:00+00:00",
        finished_at="2026-09-09T00:00:01+00:00",
        duration_ms=1000,
        response_body=http_body,
    )

    replay = repository.get_v3_generation_result(generation_key=str(reserved["generation_key"]))

    assert replay["ready"] is True
    assert replay["response"]["response_id"] == response_id
    assert replay["response"]["replayed"] is True
    assert replay["response"]["meta"]["replayed"] is True
    assert replay["response"]["reply_messages"][0]["client_message_id"]
    assert replay["http_response"]["data"]["replayed"] is True
    assert replay["http_response"]["data"]["reply_messages"] == http_body["data"]["reply_messages"]
    assert "must-not-persist" not in str(replay)
    run = repository.get_run("request-complete")["run"]
    assert "v3_recovery_payload" not in run["output_snapshot"]


def test_stale_generation_and_fallback_recovery_are_claimed_and_completed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request("stale-message")
    reserved = _reserve(
        repository,
        request,
        request_id="request-stale",
        started_at="2026-09-09T00:00:00+00:00",
    )
    generation_key = str(reserved["generation_key"])

    stale = repository.recover_stale_v3_generation(
        generation_key=generation_key,
        stale_before="2026-09-09T00:00:10+00:00",
        next_retry_at="2026-09-09T00:00:15+00:00",
    )
    assert stale["recovered_stale"] is True
    assert stale["generation_status"] == GENERATION_STATUS_FALLBACK_PENDING

    claimed = repository.claim_v3_fallback_recoveries(
        now="2026-09-09T00:00:20+00:00",
        lease_seconds=60,
        max_attempts=2,
    )
    assert len(claimed) == 1
    assert claimed[0]["recovery_attempts"] == 1
    assert claimed[0]["request"]["confirmed_store_id"] == "store-1"
    assert claimed[0]["request"]["appointment_id"] == "appointment-1"
    assert claimed[0]["request"]["request_context"]["api_token"] == "[REDACTED]"
    assert "secret-value" not in str(claimed[0]["request"]["file_image"])

    completed = repository.complete_v3_fallback_recovery(
        request_id="request-stale",
        reply_messages=[{"type": "text", "order": 1, "content": "补答成功"}],
        dispatch_id="dispatch-1",
        response_snapshot={"request_id": "request-stale", "meta": {"reply_source": "recovery"}},
    )
    assert completed["status"] == GENERATION_STATUS_RECOVERED
    replay = repository.get_v3_generation_result(generation_key=generation_key)
    assert replay["generation_status"] == GENERATION_STATUS_RECOVERED
    assert replay["recovery_dispatch_id"] == "dispatch-1"
    assert replay["response"]["reply_messages"] == []
    assert replay["recovery_reply_messages"][0]["content"] == "补答成功"
    with repository.store.connect() as conn:
        raw = conn.execute(
            "SELECT output_snapshot FROM runs WHERE request_id='request-stale'"
        ).fetchone()["output_snapshot"]
    assert "v3_recovery_payload" not in raw


def test_recovery_delivery_does_not_replace_primary_platform_response(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request("primary-fallback-stays-primary")
    reserved = _reserve(repository, request, request_id="request-primary-fallback")
    generation_key = str(reserved["generation_key"])
    primary_messages = [{"type": "text", "order": 1, "content": "您稍等一下"}]

    repository.save_v3_reply_core(
        conversation_id=str(reserved["conversation_id"]),
        final_state={
            "request_id": "request-primary-fallback",
            "generation_status": GENERATION_STATUS_FALLBACK_PENDING,
            "recovery_kind": "reply_timeout",
            "recovery_next_at": "2026-09-09T00:00:15+00:00",
            "reply_source": "deterministic_runtime_exception_fallback",
            "request_context": {"interface_version": "v3"},
        },
        reply_messages=primary_messages,
        token_usage={},
        deferred_payload={},
    )
    repository.claim_v3_fallback_recoveries(
        now="2026-09-09T00:00:20+00:00",
        max_attempts=2,
    )
    repository.complete_v3_fallback_recovery(
        request_id="request-primary-fallback",
        reply_messages=[{"type": "text", "order": 1, "content": "这是恢复后的正式答复"}],
        dispatch_id="dispatch-recovery",
        response_snapshot={
            "request_id": "request-primary-fallback",
            "meta": {"reply_source": "recovery"},
        },
    )

    replay = repository.get_v3_generation_result(generation_key=generation_key)
    assert replay["generation_status"] == GENERATION_STATUS_RECOVERED
    assert replay["response"]["reply_messages"][0]["content"] == "您稍等一下"
    assert replay["recovery_reply_messages"][0]["content"] == "这是恢复后的正式答复"
    assert (
        replay["response"]["reply_messages"][0]["client_message_id"]
        == primary_messages[0]["client_message_id"]
    )
    assert (
        replay["response"]["reply_messages"][0]["client_message_id"]
        != replay["recovery_reply_messages"][0]["client_message_id"]
    )


def test_same_reserved_request_can_finish_terminal_fallback_without_duplicate_ingress(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request("terminal-fallback")
    reserved = _reserve(repository, request, request_id="request-terminal")
    next_retry_at = "2026-09-09T00:00:15+00:00"
    state = {
        "request_id": "request-terminal",
        "customer_id": "customer-1",
        "corp_id": "corp-1",
        "wechat": "SL8003",
        "external_userid": "external-1",
        "generation_key": reserved["generation_key"],
        "response_id": reserved["response_id"],
        "generation_status": GENERATION_STATUS_FALLBACK_PENDING,
        "recovery_kind": "model_timeout",
        "recovery_next_at": next_retry_at,
        "reply_source": "deterministic_runtime_exception_fallback",
        "request_context": {"interface_version": "v3", "memory_persist_allowed": False},
        "errors": [{"node": "reply", "error": "timeout"}],
    }
    messages = [{"type": "text", "order": 1, "content": "您稍等一下"}]

    persisted = repository.save_v3_terminal_no_reply(
        default_conversation_id=str(reserved["conversation_id"]),
        resolve_existing=True,
        request=request,
        request_id="request-terminal",
        title="test",
        input_snapshot=request.model_dump(),
        request_context=state["request_context"],
        final_state=state,
        reply_messages=messages,
        token_usage={},
        deferred_payload={},
    )

    assert persisted["replayed"] is False
    assert persisted["continue_existing"] is True
    replay = repository.get_v3_generation_result(generation_key=str(reserved["generation_key"]))
    assert replay["generation_status"] == GENERATION_STATUS_FALLBACK_PENDING
    assert replay["recovery_next_at"] == next_retry_at
    assert replay["response"]["reply_messages"][0]["client_message_id"]
    with repository.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS total FROM messages WHERE role='user'").fetchone()["total"] == 1
        output = conn.execute(
            "SELECT output_snapshot FROM runs WHERE request_id='request-terminal'"
        ).fetchone()["output_snapshot"]
    assert "v3_recovery_payload" in output


def test_reply_core_keeps_recovery_payload_when_fallback_is_pending(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = _request("core-fallback")
    reserved = _reserve(repository, request, request_id="request-core-fallback")
    messages = [{"type": "text", "order": 1, "content": "您稍等一下"}]

    result = repository.save_v3_reply_core(
        conversation_id=str(reserved["conversation_id"]),
        final_state={
            "request_id": "request-core-fallback",
            "customer_id": "customer-1",
            "generation_status": GENERATION_STATUS_FALLBACK_PENDING,
            "recovery_kind": "reply_timeout",
            "recovery_next_at": "2026-09-09T00:00:15+00:00",
            "reply_source": "deterministic_empty_reply_fallback",
            "request_context": {"interface_version": "v3"},
        },
        reply_messages=messages,
        token_usage={},
        deferred_payload={},
    )

    assert result["generation_status"] == GENERATION_STATUS_FALLBACK_PENDING
    claimed = repository.claim_v3_fallback_recoveries(
        now="2026-09-09T00:00:20+00:00",
        max_attempts=2,
    )
    assert len(claimed) == 1
    assert claimed[0]["request"]["content"] == "多少钱？"

    exhausted = repository.retry_v3_fallback_recovery(
        request_id="request-core-fallback",
        next_retry_at="2026-09-09T00:00:45+00:00",
        error="second failure",
        max_attempts=1,
    )
    assert exhausted["status"] == GENERATION_STATUS_MANUAL_REVIEW
    run = repository.get_v3_generation_result(generation_key=str(reserved["generation_key"]))
    assert run["generation_status"] == GENERATION_STATUS_MANUAL_REVIEW
    assert run["recovery_kind"] == "manual_review"


def test_generation_schema_is_declared_for_mysql_and_sqlite_upgrade(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    expected = {
        "generation_key",
        "response_id",
        "generation_status",
        "recovery_kind",
        "recovery_attempts",
        "recovery_next_at",
        "recovery_dispatch_id",
        "recovery_error",
    }
    assert expected <= set(EXPECTED_COLUMNS["aics_runs"])
    assert EXPECTED_INDEXES["aics_runs"]["uq_aics_runs_generation_key"] == ("generation_key",)
    assert EXPECTED_INDEXES["aics_runs"]["uq_aics_runs_response_id"] == ("response_id",)
    assert "uq_aics_runs_generation_key" in EXPECTED_UNIQUE_INDEXES["aics_runs"]
    assert "uq_aics_runs_response_id" in EXPECTED_UNIQUE_INDEXES["aics_runs"]
    assert "idx_aics_runs_generation_recovery" not in EXPECTED_UNIQUE_INDEXES["aics_runs"]
    with repository.store.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(runs)").fetchall()}
    assert expected <= columns
    assert {"uq_runs_generation_key", "uq_runs_response_id", "idx_runs_generation_recovery"} <= indexes


def test_recovery_new_customer_message_check_is_isolated_by_receiving_wechat(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    original = _request("original")
    _reserve(
        repository,
        original,
        request_id="request-original",
        started_at="2026-09-09T00:00:00+00:00",
    )
    other_wechat = _request("other-wechat").model_copy(update={"wechat": "SL9000"})
    _reserve(
        repository,
        other_wechat,
        request_id="request-other-wechat",
        started_at="2026-09-09T00:00:05+00:00",
    )

    assert repository.has_newer_customer_message_for_v3_recovery("request-original") is False

    same_wechat = _request("same-wechat-new")
    _reserve(
        repository,
        same_wechat,
        request_id="request-same-wechat-new",
        started_at="2026-09-09T00:00:10+00:00",
    )
    newer = repository.newer_customer_message_for_v3_recovery("request-original")
    assert newer == {
        "has_newer": True,
        "latest_at": "2026-09-09T00:00:10+00:00",
        "reason": "",
    }
