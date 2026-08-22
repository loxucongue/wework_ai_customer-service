from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from app.services.storage import AppRepository, SQLiteStore


def _repository(tmp_path: Path) -> AppRepository:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "live-runs.db"))
    store.initialize()
    return AppRepository(store)


def _insert_conversation(repository: AppRepository, conversation_id: str) -> None:
    with repository.store.connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations
                (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, "sim_customer", "sim_external", "sim_corp", "sim_user", "sim_wechat", "test", "2026-08-13T00:00:00+00:00", "2026-08-13T00:00:00+00:00"),
        )


def test_run_is_visible_while_running_and_completion_preserves_start_time(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _insert_conversation(repository, "conversation-live")
    repository.start_run(
        request_id="request-live-v2",
        conversation_id="conversation-live",
        customer_id="sim_customer",
        interface_version="v2",
        input_snapshot={
            "content": "测试运行中任务",
            "request_context": {"interface_version": "v2"},
        },
    )

    running = repository.get_run("request-live-v2")["run"]
    assert running["runtime_status"] == "running"
    assert running["runtime_phase"] == "request_received"
    assert running["interface_version"] == "v2"
    assert running["started_at"] == running["created_at"]
    assert running["finished_at"] == ""

    repository.update_run_progress(request_id="request-live-v2", phase="reply")
    time.sleep(0.01)
    repository.save_run(
        conversation_id="conversation-live",
        final_state={
            "request_id": "request-live-v2",
            "customer_id": "sim_customer",
            "content": "测试运行中任务",
            "request_context": {"interface_version": "v2"},
            "reply_messages": [{"type": "text", "content": "测试完成"}],
            "trace": [],
            "errors": [],
        },
        token_usage={},
    )

    completed = repository.get_run("request-live-v2")["run"]
    assert completed["runtime_status"] == "completed"
    assert completed["runtime_phase"] == "completed"
    assert completed["started_at"] == running["started_at"]
    assert completed["finished_at"]
    assert completed["duration_ms"] >= 1


def test_legacy_runs_decode_as_completed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _insert_conversation(repository, "conversation-legacy")
    repository.save_run(
        conversation_id="conversation-legacy",
        final_state={
            "request_id": "request-legacy",
            "customer_id": "sim_customer",
            "content": "legacy",
            "request_context": {},
            "reply_messages": [{"type": "text", "content": "ok"}],
            "trace": [],
            "errors": [],
        },
        token_usage={},
    )

    run = repository.get_run("request-legacy")["run"]
    assert run["runtime_status"] == "completed"
    assert run["started_at"]
    assert run["finished_at"]


def test_v3_runs_preserve_sidecar_interface_metadata(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _insert_conversation(repository, "conversation-v3")
    repository.start_run(
        request_id="request-live-v3",
        conversation_id="conversation-v3",
        customer_id="sim_customer",
        interface_version="v3",
        input_snapshot={
            "content": "测试 v3",
            "request_context": {
                "interface_version": "v3",
                "reply_chain_mode": "model_led_sales_brain_v3",
                "v3_sidecar": True,
            },
        },
    )

    running = repository.get_run("request-live-v3")["run"]
    assert running["interface_version"] == "v3"

    repository.save_run(
        conversation_id="conversation-v3",
        final_state={
            "request_id": "request-live-v3",
            "customer_id": "sim_customer",
            "content": "测试 v3",
            "request_context": {
                "interface_version": "v3",
                "reply_chain_mode": "model_led_sales_brain_v3",
                "v3_sidecar": True,
            },
            "reply_messages": [{"type": "text", "content": "v3 ok"}],
            "trace": [],
            "errors": [],
        },
        token_usage={},
    )

    completed = repository.get_run("request-live-v3")["run"]
    output = completed["output_snapshot"]
    assert completed["interface_version"] == "v3"
    assert output["reply_chain_mode"] == "model_led_sales_brain_v3"
    assert output["v3_sidecar"] is True


def test_v3_run_detail_refreshes_strategy_callback_outbox_status(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _insert_conversation(repository, "conversation-v3-callback")
    callback = repository.enqueue_strategy_data_callback(
        idempotency_key="customer_open:100:message-1",
        record_kind="customer_open",
        task_id="100",
        sales_contact_key="sales_contact:v2:sim_corp:sim_wechat:sim_external",
        customer_id="sim_customer",
        interface_version="v3",
        payload={"recordKind": "customer_open", "taskId": 100},
    )
    repository.save_run(
        conversation_id="conversation-v3-callback",
        final_state={
            "request_id": "request-v3-callback",
            "customer_id": "sim_customer",
            "content": "test",
            "request_context": {"interface_version": "v3"},
            "reply_messages": [{"type": "text", "content": "ok"}],
            "strategy_data_callback": {
                "status": "pending",
                "outbox_id": callback["id"],
                "task_id": "100",
            },
            "trace": [],
            "errors": [],
        },
        token_usage={},
    )

    pending = repository.get_run("request-v3-callback")["run"]["output_snapshot"]
    assert pending["strategy_data_callback"]["status"] == "pending"

    repository.complete_strategy_data_callback(
        callback["id"],
        response={"code": 200, "message": "操作成功"},
    )
    sent = repository.get_run("request-v3-callback")["run"]["output_snapshot"]
    assert sent["strategy_data_callback"]["status"] == "sent"
    assert sent["strategy_data_callback"]["sent_at"]
    assert sent["strategy_data_callback"]["response_code"] == 200
    assert sent["strategy_data_callback"]["request_payload"] == {
        "recordKind": "customer_open",
        "taskId": 100,
    }
    assert sent["strategy_data_callback"]["response"]["code"] == 200
    assert sent["strategy_data_callback"]["created_at"]
    assert sent["strategy_data_callback"]["response_message"] == "操作成功"
