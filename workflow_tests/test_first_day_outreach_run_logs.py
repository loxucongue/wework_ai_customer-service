from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.first_day_outreach_log import redact_first_day_log_value
from app.services.outreach_service import OutreachService
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


def _repository(tmp_path) -> AppRepository:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "runs.db"))
    store.initialize()
    return AppRepository(store)


def test_redaction_removes_credentials_and_signed_url_values() -> None:
    value = redact_first_day_log_value(
        {
            "Authorization": "Bearer secret",
            "access_token": "token-value",
            "X-Api-Key": "api-secret",
            "content": "手机号 13800138000",
            "image": (
                "https://example.oss-cn.test/a.png?OSSAccessKeyId=key"
                "&Expires=123&Signature=signed&width=800"
            ),
        }
    )

    assert value["Authorization"] == "[REDACTED]"
    assert value["access_token"] == "[REDACTED]"
    assert value["X-Api-Key"] == "[REDACTED]"
    assert value["content"] == "手机号 13800138000"
    assert "key" not in value["image"]
    assert "signed" not in value["image"]
    assert "width=800" in value["image"]


def test_first_day_run_list_detail_cursor_and_contact_boundary(tmp_path) -> None:
    repository = _repository(tmp_path)
    first = repository.create_first_day_outreach_run(
        customer_id="customer-a",
        corp_id="corp",
        user_id="user",
        wechat="staff-a",
        external_userid="external",
        trigger_type="first_day_opened_silence",
        input_snapshot={"recent_messages": [{"role": "customer", "content": "你好"}]},
    )
    second = repository.create_first_day_outreach_run(
        customer_id="customer-a",
        corp_id="corp",
        user_id="user",
        wechat="staff-b",
        external_userid="external",
        trigger_type="first_day_opened_silence",
    )
    repository.update_first_day_outreach_run(
        first["workflow_run_id"],
        status="blocked",
        reason_code="health_hold",
        final_decision="no_plan",
        first_scene="health_hold",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )

    page = repository.list_first_day_outreach_runs(limit=1, corp_id="corp", wechat="staff-a")
    assert [item["workflow_run_id"] for item in page["items"]] == [first["workflow_run_id"]]
    assert "input_snapshot" not in page["items"][0]
    assert repository.list_first_day_outreach_runs(wechat="staff-b")["items"][0]["workflow_run_id"] == second["workflow_run_id"]

    detail = repository.get_first_day_outreach_run(first["workflow_run_id"])
    assert detail["input_snapshot"]["recent_messages"][0]["content"] == "你好"
    assert detail["reason_code"] == "health_hold"

    repository.update_first_day_outreach_run(
        first["workflow_run_id"],
        error_message="request failed: https://example.test/a?token=secret-token",
    )
    sanitized = repository.list_first_day_outreach_runs(wechat="staff-a")["items"][0]
    assert "secret-token" not in sanitized["error_message"]


def test_retention_redacts_terminal_raw_data_but_keeps_active_runs(tmp_path) -> None:
    repository = _repository(tmp_path)
    terminal = repository.create_first_day_outreach_run(
        customer_id="terminal",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-terminal",
        trigger_type="first_day_opened_silence",
        input_snapshot={"recent_messages": [{"content": "raw"}]},
    )
    active = repository.create_first_day_outreach_run(
        customer_id="active",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-active",
        trigger_type="first_day_opened_silence",
        input_snapshot={"recent_messages": [{"content": "keep"}]},
    )
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with repository.store.connect() as conn:
        conn.execute(
            """
            UPDATE first_day_outreach_runs
            SET status='blocked', finished_at=?, updated_at=? WHERE workflow_run_id=?
            """,
            (old, old, terminal["workflow_run_id"]),
        )
        conn.execute(
            "UPDATE first_day_outreach_runs SET started_at=?, updated_at=? WHERE workflow_run_id=?",
            (old, old, active["workflow_run_id"]),
        )

    result = repository.prune_first_day_outreach_runs(raw_days=30, summary_days=90)

    assert result["raw_redacted_runs"] == 1
    assert repository.get_first_day_outreach_run(terminal["workflow_run_id"])["input_snapshot"] == {}
    assert repository.get_first_day_outreach_run(active["workflow_run_id"])["input_snapshot"]["recent_messages"][0]["content"] == "keep"


def test_first_day_unopened_outcome_is_logged_without_a_plan(tmp_path) -> None:
    repository = _repository(tmp_path)

    class NoModelCalls:
        async def chat_json(self, *args, **kwargs):
            raise AssertionError("unopened first-day routing must not call the personalized planner")

    service = OutreachService(
        repository=repository,
        model_client=NoModelCalls(),
        system_client=object(),
    )
    result = asyncio.run(
        service.generate_plan(
            customer_id="customer-unopened",
            corp_id="corp",
            user_id="user",
            wechat="staff",
            external_userid="external-unopened",
            source_context={
                "memory": {},
                "recent_messages": [
                    {"direction": "staff", "content": "自动欢迎语", "created_at": datetime.now(timezone.utc).isoformat()}
                ],
                "customer_context": {"orders": []},
                "customer_relation": {"available": True, "status": "active", "is_deleted": False},
            },
            trigger_context={"trigger_type": "first_day_opened_silence"},
        )
    )

    assert result["created"] is False
    page = repository.list_first_day_outreach_runs(customer_id="customer-unopened")
    assert len(page["items"]) == 1
    run = page["items"][0]
    assert run["plan_id"] == ""
    assert run["status"] == "blocked"
    assert run["reason_code"] == "first_day_customer_not_opened"


def test_plan_and_task_ids_are_linked_to_run_in_plan_creation_transaction(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_first_day_outreach_run(
        customer_id="customer-linked",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-linked",
        trigger_type="first_day_opened_silence",
    )

    created = repository.create_outreach_plan(
        customer_id="customer-linked",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-linked",
        customer_stage="opened",
        stall_reason="silent",
        customer_psychology="interested",
        plan_goal="follow up",
        source_snapshot={"workflow_run_id": run["workflow_run_id"]},
        tasks=[
            {"step_index": 1, "reply_messages": [{"type": "text", "content": "第一步"}]},
            {"step_index": 2, "reply_messages": [{"type": "text", "content": "第二步"}]},
        ],
        workflow_run_id=run["workflow_run_id"],
    )

    linked = repository.get_first_day_outreach_run(run["workflow_run_id"])
    assert linked["plan_id"] == created["plan"]["id"]
    assert linked["first_task_id"] == created["tasks"][0]["id"]
    assert linked["second_task_id"] == created["tasks"][1]["id"]
    assert linked["events"][0]["payload"]["workflow_run_id"] == run["workflow_run_id"]
