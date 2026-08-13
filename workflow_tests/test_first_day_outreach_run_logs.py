from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.customer_scope import build_customer_scope
from app.services.first_day_outreach_log import redact_first_day_log_value
from app.services.outreach_service import OutreachService
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


def _repository(tmp_path) -> AppRepository:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "runs.db"))
    store.initialize()
    return AppRepository(store)


def test_sqlite_initialize_upgrades_legacy_first_day_run_table(tmp_path) -> None:
    db_path = tmp_path / "legacy-runs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE first_day_outreach_runs (
                workflow_run_id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL DEFAULT '',
                first_task_id TEXT NOT NULL DEFAULT '',
                second_task_id TEXT NOT NULL DEFAULT '',
                corp_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                wechat TEXT NOT NULL DEFAULT '',
                external_userid TEXT NOT NULL DEFAULT '',
                customer_id TEXT NOT NULL DEFAULT '',
                trigger_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                reason_code TEXT NOT NULL DEFAULT '',
                final_decision TEXT NOT NULL DEFAULT '',
                first_scene TEXT NOT NULL DEFAULT '',
                second_scene TEXT NOT NULL DEFAULT '',
                model_attempt_count INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                input_snapshot_json TEXT NOT NULL DEFAULT '{}',
                workflow_json TEXT NOT NULL DEFAULT '{}',
                final_plan_json TEXT NOT NULL DEFAULT '{}',
                error_node TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                raw_redacted_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )

    store = SQLiteStore(SimpleNamespace(db_path=db_path))
    store.initialize()

    with store.connect() as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(first_day_outreach_runs)").fetchall()
        }
        indexes = {
            str(row["name"])
            for row in conn.execute("PRAGMA index_list(first_day_outreach_runs)").fetchall()
        }
    assert "conversation_fingerprint" in columns
    assert "next_retry_at" in columns
    assert "idx_first_day_runs_contact_fingerprint" in indexes


def _insert_conversation(
    repository: AppRepository,
    *,
    customer_id: str,
    corp_id: str,
    user_id: str,
    wechat: str,
    external_userid: str,
    updated_at: str,
) -> None:
    with repository.store.connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations
                (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                str(uuid4()),
                customer_id,
                external_userid,
                corp_id,
                user_id,
                wechat,
                updated_at,
                updated_at,
            ),
        )


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


def test_first_day_run_fingerprint_is_unique_and_reused(tmp_path) -> None:
    repository = _repository(tmp_path)
    identity = {
        "customer_id": "customer-dedupe",
        "corp_id": "corp",
        "user_id": "user",
        "wechat": "staff",
        "external_userid": "external-dedupe",
        "trigger_type": "first_day_opened_silence",
        "conversation_fingerprint": "fingerprint-1",
    }

    first = repository.create_first_day_outreach_run(**identity)
    second = repository.create_first_day_outreach_run(**identity)

    assert second["workflow_run_id"] == first["workflow_run_id"]
    assert len(repository.list_first_day_outreach_runs(customer_id="customer-dedupe")["items"]) == 1


def test_first_day_run_fingerprint_is_scoped_by_sales_contact(tmp_path) -> None:
    repository = _repository(tmp_path)
    common = {
        "customer_id": "customer-dedupe",
        "corp_id": "corp",
        "user_id": "user",
        "external_userid": "external-dedupe",
        "trigger_type": "first_day_opened_silence",
        "conversation_fingerprint": "fingerprint-1",
    }

    first = repository.create_first_day_outreach_run(**common, wechat="staff-a")
    second = repository.create_first_day_outreach_run(**common, wechat="staff-b")

    assert second["workflow_run_id"] != first["workflow_run_id"]


def test_due_first_day_tasks_require_all_prior_steps_sent(tmp_path) -> None:
    repository = _repository(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    plan = repository.create_outreach_plan(
        customer_id="customer-prior",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-prior",
        customer_stage="opened",
        stall_reason="silent",
        customer_psychology="interested",
        plan_goal="follow up",
        source_snapshot={
            "trigger_context": {
                "trigger_type": "first_day_opened_silence",
                "activation_policy": "auto_approved",
            }
        },
        tasks=[
            {"step_index": 1, "scheduled_at": now, "reply_messages": [{"type": "text", "content": "first"}]},
            {"step_index": 2, "scheduled_at": now, "reply_messages": [{"type": "text", "content": "second"}]},
        ],
        sop_plan_id="first_day_opened_silence",
    )
    repository.update_outreach_plan_status(plan["plan"]["id"], "active")
    repository.update_outreach_task(plan["tasks"][0]["id"], status="failed")

    assert repository.list_due_first_day_tasks(limit=10, now=now) == []
    assert repository.list_due_outreach_tasks(limit=10, now=now) == []


def test_first_day_backlog_cleanup_cancels_failed_dependency_and_expired_plan(tmp_path) -> None:
    repository = _repository(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    plan = repository.create_outreach_plan(
        customer_id="customer-cleanup",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-cleanup",
        customer_stage="opened",
        stall_reason="silent",
        customer_psychology="interested",
        plan_goal="follow up",
        source_snapshot={"trigger_context": {"trigger_type": "first_day_opened_silence"}},
        tasks=[
            {"step_index": 1, "scheduled_at": old, "reply_messages": [{"type": "text", "content": "first"}]},
            {"step_index": 2, "scheduled_at": old, "reply_messages": [{"type": "text", "content": "second"}]},
        ],
        sop_plan_id="first_day_opened_silence",
    )
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE outreach_plans SET created_at=?, status='active' WHERE id=?",
            (old, plan["plan"]["id"]),
        )
    repository.update_outreach_task(plan["tasks"][0]["id"], status="failed")

    result = repository.cleanup_first_day_task_backlog(
        older_than=(datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
        dry_run=False,
    )

    assert result["affected_plan_count"] == 1
    detail = repository.get_outreach_plan(plan["plan"]["id"])
    assert detail["plan"]["status"] == "cancelled"
    assert [task["status"] for task in detail["tasks"]] == ["failed", "skipped"]
    assert any(event["event_type"] == "first_day_backlog_cleanup" for event in detail["events"])


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


def test_list_candidates_prioritizes_waiting_reply_and_longer_reply_wait(tmp_path) -> None:
    repository = _repository(tmp_path)
    now = datetime.now(timezone.utc)
    common = {
        "corp_id": "corp",
        "user_id": "user",
        "wechat": "staff",
    }
    old_customer = "customer-old-wait"
    new_customer = "customer-newer-update"
    _insert_conversation(
        repository,
        customer_id=old_customer,
        external_userid="external-old",
        updated_at=(now - timedelta(hours=6)).isoformat(),
        **common,
    )
    _insert_conversation(
        repository,
        customer_id=new_customer,
        external_userid="external-new",
        updated_at=(now - timedelta(minutes=5)).isoformat(),
        **common,
    )

    old_scope = build_customer_scope(customer_id=old_customer, external_userid="external-old", **common)
    new_scope = build_customer_scope(customer_id=new_customer, external_userid="external-new", **common)
    repository.touch_customer_message_time(
        old_scope.sales_contact_key,
        field="last_customer_message_at",
        value=(now - timedelta(hours=7)).isoformat(),
    )
    repository.touch_customer_message_time(
        old_scope.sales_contact_key,
        field="last_staff_message_at",
        value=(now - timedelta(hours=5)).isoformat(),
    )
    repository.touch_customer_message_time(
        new_scope.sales_contact_key,
        field="last_customer_message_at",
        value=(now - timedelta(hours=3)).isoformat(),
    )
    repository.touch_customer_message_time(
        new_scope.sales_contact_key,
        field="last_staff_message_at",
        value=(now - timedelta(minutes=30)).isoformat(),
    )

    candidates = repository.list_outreach_candidates(limit=10, silent_minutes_min=0)

    assert [item["customer_id"] for item in candidates[:2]] == [old_customer, new_customer]
    assert candidates[0]["awaiting_customer_reply"] is True
    assert int(candidates[0]["reply_wait_minutes"]) > int(candidates[1]["reply_wait_minutes"])


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


def test_legacy_first_day_plans_are_backfilled_once_and_active_plan_is_linked(tmp_path) -> None:
    repository = _repository(tmp_path)
    created = repository.create_outreach_plan(
        customer_id="customer-legacy",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-legacy",
        customer_stage="opened",
        stall_reason="silent",
        customer_psychology="interested",
        plan_goal="follow up",
        source_snapshot={
            "trigger_context": {"trigger_type": "first_day_opened_silence"},
            "ai_result": {
                "steps": [
                    {"scene": "effect_proof"},
                    {"scene": "activity_intro"},
                ]
            },
        },
        tasks=[
            {"step_index": 1, "reply_messages": [{"type": "text", "content": "第一步"}]},
            {"step_index": 2, "reply_messages": [{"type": "text", "content": "第二步"}]},
        ],
        sop_plan_id="first_day_opened_silence",
    )

    first = repository.backfill_first_day_outreach_runs()
    second = repository.backfill_first_day_outreach_runs()

    assert first == {"scanned_plans": 1, "created_runs": 1, "linked_active_plans": 1}
    assert second == {"scanned_plans": 1, "created_runs": 0, "linked_active_plans": 0}
    page = repository.list_first_day_outreach_runs(customer_id="customer-legacy")
    assert len(page["items"]) == 1
    run = page["items"][0]
    assert run["plan_id"] == created["plan"]["id"]
    assert run["first_scene"] == "effect_proof"
    assert run["second_scene"] == "activity_intro"
    linked_plan = repository.get_outreach_plan(created["plan"]["id"])["plan"]
    assert linked_plan["source_snapshot"]["workflow_run_id"] == run["workflow_run_id"]


def test_dashboard_outcome_stats_accept_string_delay_metadata(tmp_path) -> None:
    repository = _repository(tmp_path)
    created = repository.create_outreach_plan(
        customer_id="customer-dashboard",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-dashboard",
        customer_stage="opened",
        stall_reason="silent",
        customer_psychology="interested",
        plan_goal="follow up",
        source_snapshot={"trigger_context": {"activation_policy": "auto_approved"}},
        tasks=[
            {
                "step_index": 1,
                "reply_messages": [{"type": "text", "content": "第一步"}],
                "content_sources": [
                    {
                        "outreach_task_metadata": {
                            "normalized_delay_minutes": "15",
                            "persuasion_angle": "effect",
                        }
                    }
                ],
            }
        ],
    )
    now = datetime.now(timezone.utc).isoformat()
    with repository.store.connect() as conn:
        conn.execute(
            "UPDATE outreach_tasks SET status='sent', sent_at=?, updated_at=? WHERE id=?",
            (now, now, created["tasks"][0]["id"]),
        )

    stats = repository.outreach_dashboard_stats(now=now)

    assert stats["outcomes"]["sent_tasks"] == 1


def test_first_day_monitor_logs_and_reuses_conversation_refresh_failure(tmp_path) -> None:
    repository = _repository(tmp_path)
    now = datetime.now(timezone.utc)
    customer_at = (now - timedelta(minutes=10)).isoformat()
    staff_at = (now - timedelta(minutes=4)).isoformat()
    candidate = {
        "customer_id": "customer-refresh-failure",
        "corp_id": "corp",
        "user_id": "user",
        "wechat": "staff",
        "external_userid": "external-refresh-failure",
        "sales_contact_started_at": (now - timedelta(hours=1)).isoformat(),
        "last_customer_message_at": customer_at,
        "last_staff_message_at": staff_at,
        "latest_outbound_message_at": staff_at,
        "awaiting_customer_reply": True,
        "reply_wait_minutes": 4,
    }

    class RefreshFailureService(OutreachService):
        async def refresh_customer_conversation(self, **_kwargs):
            raise RuntimeError("outreach_system_http_403: scope mismatch")

    service = RefreshFailureService(
        repository=repository,
        model_client=object(),
        system_client=object(),
    )

    first = asyncio.run(
        service._evaluate_first_day_silence_candidate(
            candidate,
            silent_minutes=3,
            auto_activate=True,
        )
    )
    second = asyncio.run(
        service._evaluate_first_day_silence_candidate(
            candidate,
            silent_minutes=3,
            auto_activate=True,
        )
    )

    assert first["status"] == "error"
    assert second["status"] == "skipped"
    assert second["reason"] == "conversation_fingerprint_already_logged"
    page = repository.list_first_day_outreach_runs(customer_id=candidate["customer_id"])
    assert len(page["items"]) == 1
    run = page["items"][0]
    assert run["status"] == "failed"
    assert run["reason_code"] == "conversation_refresh_failed"
    assert run["error_node"] == "conversation_refresh"
    assert run["retry_count"] == 0


def test_first_day_monitor_does_not_replan_completed_cycle_without_customer_reply(tmp_path) -> None:
    repository = _repository(tmp_path)
    now = datetime.now(timezone.utc)
    customer_at = (now - timedelta(minutes=10)).isoformat()
    staff_at = (now - timedelta(minutes=4)).isoformat()
    completed = repository.create_outreach_plan(
        customer_id="customer-completed-cycle",
        corp_id="corp",
        user_id="user",
        wechat="staff",
        external_userid="external-completed-cycle",
        customer_stage="opened",
        stall_reason="silent",
        customer_psychology="interested",
        plan_goal="follow up",
        source_snapshot={"trigger_context": {"trigger_type": "first_day_opened_silence"}},
        tasks=[
            {"step_index": 1, "reply_messages": [{"type": "text", "content": "first"}]},
            {"step_index": 2, "reply_messages": [{"type": "text", "content": "second"}]},
        ],
        sop_plan_id="first_day_opened_silence",
    )
    repository.update_outreach_plan_status(completed["plan"]["id"], "completed")

    class CompletedCycleService(OutreachService):
        async def refresh_customer_conversation(self, **_kwargs):
            return {
                "messages": [
                    {"direction": "customer", "content": "hello", "created_at": customer_at},
                    {"direction": "staff", "content": "reply", "created_at": staff_at},
                ],
                "first_added_at": (now - timedelta(hours=1)).isoformat(),
                "conversation_id": "ww:staff:external-completed-cycle",
                "customer_relation": {"available": True, "status": "active", "is_deleted": False},
            }

    service = CompletedCycleService(
        repository=repository,
        model_client=object(),
        system_client=object(),
    )
    result = asyncio.run(
        service._evaluate_first_day_silence_candidate(
            {
                "customer_id": "customer-completed-cycle",
                "corp_id": "corp",
                "user_id": "user",
                "wechat": "staff",
                "external_userid": "external-completed-cycle",
                "sales_contact_started_at": (now - timedelta(hours=1)).isoformat(),
                "last_customer_message_at": customer_at,
                "last_staff_message_at": staff_at,
                "latest_outbound_message_at": staff_at,
                "awaiting_customer_reply": True,
                "reply_wait_minutes": 4,
            },
            silent_minutes=3,
            auto_activate=True,
        )
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "outreach_cycle_completed_without_new_customer_reply"
    runs = repository.list_first_day_outreach_runs(customer_id="customer-completed-cycle")["items"]
    assert len(runs) == 1
    assert runs[0]["status"] == "blocked"
    assert runs[0]["reason_code"] == "outreach_cycle_completed_without_new_customer_reply"


def test_first_day_daily_task_limit_cancels_whole_plan_without_rescheduling(tmp_path) -> None:
    repository = _repository(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    identity = {
        "customer_id": "customer-daily-task-limit",
        "corp_id": "corp",
        "user_id": "user",
        "wechat": "staff",
        "external_userid": "external-daily-task-limit",
    }
    for cycle in range(2):
        historical = repository.create_outreach_plan(
            **identity,
            customer_stage="opened",
            stall_reason="silent",
            customer_psychology="interested",
            plan_goal="follow up",
            source_snapshot={"trigger_context": {"trigger_type": "first_day_opened_silence"}},
            tasks=[
                {"step_index": 1, "reply_messages": [{"type": "text", "content": f"first-{cycle}"}]},
                {"step_index": 2, "reply_messages": [{"type": "text", "content": f"second-{cycle}"}]},
            ],
            sop_plan_id="first_day_opened_silence",
        )
        with repository.store.connect() as conn:
            conn.execute(
                "UPDATE outreach_tasks SET status='sent', sent_at=?, updated_at=? WHERE plan_id=?",
                (now, now, historical["plan"]["id"]),
            )
        repository.update_outreach_plan_status(historical["plan"]["id"], "completed")

    run = repository.create_first_day_outreach_run(
        **identity,
        trigger_type="first_day_opened_silence",
    )
    pending = repository.create_outreach_plan(
        **identity,
        customer_stage="opened",
        stall_reason="silent",
        customer_psychology="interested",
        plan_goal="follow up",
        source_snapshot={
            "workflow_run_id": run["workflow_run_id"],
            "trigger_context": {
                "trigger_type": "first_day_opened_silence",
                "activation_policy": "auto_approved",
            },
        },
        tasks=[
            {"step_index": 1, "scheduled_at": now, "reply_messages": [{"type": "text", "content": "first"}]},
            {"step_index": 2, "scheduled_at": now, "reply_messages": [{"type": "text", "content": "second"}]},
        ],
        sop_plan_id="first_day_opened_silence",
        workflow_run_id=run["workflow_run_id"],
    )
    repository.update_outreach_plan_status(pending["plan"]["id"], "active")
    service = OutreachService(repository=repository, model_client=object(), system_client=object())

    result = asyncio.run(service.execute_task(pending["tasks"][0]["id"]))

    assert result == {
        "ok": True,
        "status": "skipped",
        "reason": "first_day_daily_task_limit_reached",
    }
    detail = repository.get_outreach_plan(pending["plan"]["id"])
    assert detail["plan"]["status"] == "cancelled"
    assert [task["status"] for task in detail["tasks"]] == ["skipped", "skipped"]
    assert all(task["error_message"] == "first_day_daily_task_limit_reached" for task in detail["tasks"])
    updated_run = repository.get_first_day_outreach_run(run["workflow_run_id"])
    assert updated_run["status"] == "cancelled"
    assert updated_run["reason_code"] == "first_day_daily_task_limit_reached"
