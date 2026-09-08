from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.chat_runtime import _deferred_state_payload  # noqa: E402


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings().model_copy(
        update={
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "state.db",
        }
    )
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def _enqueue(repository: AppRepository) -> None:
    request = ChatRequest(
        content="敏感肌可以做吗",
        customer_id="customer-1",
        corp_id="corp-1",
        wechat="sl8003",
        external_userid="external-1",
    )
    repository.upsert_conversation(
        conversation_id="conversation-1",
        request=request,
        title="测试",
    )
    repository.start_run(
        request_id="request-1",
        conversation_id="conversation-1",
        customer_id="customer-1",
        input_snapshot={"content": request.content},
        interface_version="v3",
        http_request_ingress_id="ingress-1",
    )
    state = {
        "request_id": "request-1",
        "customer_id": "customer-1",
        "request_context": {"interface_version": "v3"},
        "reply_messages": [{"type": "text", "order": 1, "content": "可以先了解一下。"}],
        "reply_source": "main_model",
        "trace": [],
    }
    repository.save_v3_reply_core(
        conversation_id="conversation-1",
        final_state=state,
        reply_messages=state["reply_messages"],
        token_usage={},
        deferred_payload=state,
    )


def test_durable_finalization_is_claimed_once_and_removed_after_completion(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _enqueue(repository)

    claimed = repository.claim_v3_reply_finalizations(limit=5)

    assert len(claimed) == 1
    assert claimed[0]["final_state"]["request_id"] == "request-1"
    assert repository.claim_v3_reply_finalizations(limit=5) == []

    repository.finish_v3_reply_finalization(request_id="request-1")
    run = repository.get_run("request-1")["run"]
    assert run["output_snapshot"]["post_reply_finalization"]["status"] == "completed"
    assert run["output_snapshot"]["post_reply_finalization"]["duration_ms"] >= 0
    assert "post_reply_payload" not in run["output_snapshot"]
    assert repository.claim_v3_reply_finalizations(limit=5) == []


def test_deferred_payload_is_compressed_and_decoded_when_claimed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    request = ChatRequest(
        content="effect question",
        customer_id="customer-compressed",
        corp_id="corp-1",
        wechat="sl8003",
        external_userid="external-compressed",
    )
    repository.upsert_conversation(
        conversation_id="conversation-compressed",
        request=request,
        title="compressed payload",
    )
    state = {
        "request_id": "request-compressed",
        "customer_id": "customer-compressed",
        "trace": [{"node": "reply", "input_snapshot": {"prompt": "x" * 20000}}],
        "request_context": {"authorization": "Bearer secret-value"},
    }
    payload = _deferred_state_payload(state)
    assert payload["encoding"] == "zlib+base64+json"
    assert len(payload["data"]) < payload["uncompressed_bytes"]

    repository.save_v3_reply_core(
        conversation_id="conversation-compressed",
        final_state=state,
        reply_messages=[{"type": "text", "order": 1, "content": "ok"}],
        token_usage={},
        deferred_payload=payload,
    )
    claimed = repository.claim_v3_reply_finalizations(limit=5)
    assert claimed[0]["final_state"]["request_id"] == "request-compressed"
    assert claimed[0]["final_state"]["request_context"]["authorization"] == "[redacted]"


def test_failed_finalization_is_persisted_with_retry_delay(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _enqueue(repository)
    assert len(repository.claim_v3_reply_finalizations(limit=5)) == 1

    repository.finish_v3_reply_finalization(
        request_id="request-1",
        error="temporary database error",
    )

    run = repository.get_run("request-1")["run"]
    job = run["output_snapshot"]["post_reply_finalization"]
    assert job["status"] == "error"
    assert job["attempts"] == 1
    assert job["next_retry_at"]
    assert repository.claim_v3_reply_finalizations(limit=5) == []


def test_finalization_scan_is_bounded_to_recent_runs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _enqueue(repository)
    old_created_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE runs SET created_at=? WHERE request_id=?",
            (old_created_at, "request-1"),
        )

    assert repository.claim_v3_reply_finalizations(limit=5) == []


def test_async_full_snapshot_does_not_overwrite_http_duration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _enqueue(repository)
    job = repository.claim_v3_reply_finalizations(limit=1)[0]
    before = repository.get_run("request-1")["run"]["output_snapshot"]
    started_at = str(before["http_request_started_at"])
    finished_at = (datetime.fromisoformat(started_at) + timedelta(milliseconds=1234)).isoformat()
    assert repository.finalize_run_http_timing(
        request_id="request-1",
        ingress_id="ingress-1",
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=1234,
    )

    repository.save_run(
        conversation_id="conversation-1",
        final_state=job["final_state"],
        token_usage={},
    )
    run = repository.get_run("request-1")["run"]
    assert run["duration_ms"] == 1234
    assert run["output_snapshot"]["http_duration_ms"] == 1234
    assert run["output_snapshot"]["runtime_finished_at"] == finished_at


def test_reply_delivery_memory_events_are_idempotent_for_worker_retry(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    settings = Settings().model_copy(update={"memory_dir": tmp_path / "memory"})
    memory = CustomerMemoryStore(settings, repository=repository)

    for _ in range(2):
        memory.record_case_images_sent(
            "scope-1",
            document_ids=["case-1"],
            image_urls=["https://example.com/case-1.jpg"],
            request_id="request-1",
            interface_version="v3",
        )
        memory.record_store_fact(
            "scope-1",
            store={"store_id": "store-1", "store_name": "Xiamen Store"},
            event_type="store_address_sent",
            request_id="request-1",
            interface_version="v3",
        )

    events = memory.load("scope-1")["history_events"]
    assert [item["event_id"] for item in events].count("case_image_sent_request-1") == 1
    assert [item["event_id"] for item in events].count(
        "store_address_sent_store-1_request-1"
    ) == 1
