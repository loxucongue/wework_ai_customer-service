from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


START = "2026-09-06T00:00:00+00:00"
END = "2026-09-07T00:00:00+00:00"


def _repository(tmp_path) -> tuple[SQLiteStore, AppRepository]:
    store = SQLiteStore(
        Settings(AI_PATHS_DB_PATH=tmp_path / "outreach-customer-log.db", AICS_STORAGE_BACKEND="sqlite")
    )
    store.initialize()
    return store, AppRepository(store)


def _insert_plan(
    store: SQLiteStore,
    *,
    plan_id: str,
    sop_plan_id: str,
    customer_id: str = "customer-a",
    external_userid: str = "external-a",
    wechat: str = "SL8003",
    user_id: str = "",
    status: str = "active",
    created_at: str = "2026-09-06T01:00:00+00:00",
    source_snapshot: dict | None = None,
) -> None:
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO outreach_plans
                (id,sop_plan_id,customer_id,corp_id,user_id,wechat,external_userid,status,plan_goal,
                 source_snapshot,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,'re-engage',?,?,?)
            """,
            (
                plan_id,
                sop_plan_id,
                customer_id,
                "corp-a",
                user_id,
                wechat,
                external_userid,
                status,
                json.dumps(source_snapshot or {}),
                created_at,
                created_at,
            ),
        )


def _insert_task(
    store: SQLiteStore,
    *,
    task_id: str,
    plan_id: str,
    status: str,
    scheduled_at: str,
    system_msgid: str = "",
) -> None:
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO outreach_tasks
                (id,plan_id,customer_id,step_index,scheduled_at,status,message_goal,content_sources,
                 reply_messages_json,before_send_check,sent_at,send_status,system_msgid,error_message,
                 created_at,updated_at)
            VALUES (?,?,?,1,?,?, 'ask_for_visit','[]','[]',1,?,?,?, '',?,?)
            """,
            (
                task_id,
                plan_id,
                "customer-a",
                scheduled_at,
                status,
                scheduled_at if status == "sent" else "",
                "accepted" if status == "sent" else "",
                system_msgid,
                scheduled_at,
                scheduled_at,
            ),
        )


def test_customer_logs_group_automatic_plans_and_keep_wechat_scope(tmp_path) -> None:
    store, repository = _repository(tmp_path)
    _insert_plan(
        store,
        plan_id="first-day",
        sop_plan_id="first_day_opened_silence",
        user_id="operator-1",
        created_at="2026-09-06T01:00:00+00:00",
        source_snapshot={
            "conversation_id": "conversation-1",
            "customer_add_wechat_id": "relation-1",
        },
    )
    _insert_task(
        store,
        task_id="sent-verified",
        plan_id="first-day",
        status="sent",
        scheduled_at="2026-09-06T01:30:00+00:00",
        system_msgid="platform-message-1",
    )
    _insert_plan(
        store,
        plan_id="follow-up",
        sop_plan_id="followup_strategy:visit",
        created_at="2026-09-06T02:00:00+00:00",
    )
    _insert_task(
        store,
        task_id="next-task",
        plan_id="follow-up",
        status="pending",
        scheduled_at="2026-09-06T05:00:00+00:00",
    )
    _insert_plan(
        store,
        plan_id="auto-approved",
        sop_plan_id="",
        created_at="2026-09-06T03:00:00+00:00",
        source_snapshot={
            "api_key": "must-not-leak",
            "trigger_context": {"activation_policy": "auto_approved"},
        },
    )
    _insert_task(
        store,
        task_id="unverified-sent",
        plan_id="auto-approved",
        status="sent",
        scheduled_at="2026-09-06T03:30:00+00:00",
    )
    _insert_plan(
        store,
        plan_id="manual-plan",
        sop_plan_id="manual_follow_up",
        created_at="2026-09-06T03:45:00+00:00",
    )
    _insert_task(
        store,
        task_id="manual-task",
        plan_id="manual-plan",
        status="sent",
        scheduled_at="2026-09-06T04:00:00+00:00",
        system_msgid="manual-platform-message",
    )
    _insert_plan(
        store,
        plan_id="other-wechat",
        sop_plan_id="closing_sequence:deposit",
        wechat="DY258",
        created_at="2026-09-06T04:30:00+00:00",
    )
    _insert_task(
        store,
        task_id="consumed-on-other-wechat",
        plan_id="other-wechat",
        status="shadowed",
        scheduled_at="2026-09-06T04:45:00+00:00",
    )
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO first_day_outreach_runs
                (workflow_run_id,plan_id,corp_id,wechat,customer_id,external_userid,trigger_type,
                 status,reason_code,input_snapshot_json,workflow_json,started_at,created_at,updated_at)
            VALUES ('run-no-plan','','corp-a','SL8003','customer-a','external-a',
                    'first_day_opened_silence','blocked','human_takeover','{}','{}',?,?,?)
            """,
            ("2026-09-06T04:50:00+00:00",) * 3,
        )
        conn.execute(
            """
            INSERT INTO outreach_events
                (id,plan_id,task_id,customer_id,event_type,event_summary,payload_json,created_at)
            VALUES ('duplicate-event','','','customer-a','plan_rejected','duplicate run event',?,?)
            """,
            (
                json.dumps(
                    {
                        "workflow_run_id": "run-no-plan",
                        "identity": {
                            "corp_id": "corp-a",
                            "wechat": "SL8003",
                            "customer_id": "customer-a",
                            "external_userid": "external-a",
                        },
                        "trigger_context": {"trigger_type": "first_day_opened_silence"},
                    }
                ),
                "2026-09-06T04:51:00+00:00",
            ),
        )

    result = repository.list_outreach_customer_logs(started_from=START, started_to=END)

    assert result["metrics"] == {
        "customer_count": 2,
        "identity_incomplete_count": 0,
        "plan_count": 4,
        "no_plan_count": 1,
        "task_count": 4,
        "sent_count": 1,
        "sent_without_message_id_count": 1,
        "consumed_count": 1,
        "pending_count": 1,
        "processing_count": 0,
        "failed_count": 0,
    }
    sl8003_item = next(item for item in result["items"] if item["identity"]["wechat"] == "SL8003")
    assert sl8003_item["plan_count"] == 3
    assert sl8003_item["no_plan_count"] == 1
    assert sl8003_item["task_summary"]["sent"] == 1
    assert sl8003_item["task_summary"]["sent_without_message_id"] == 1
    assert sl8003_item["next_task"]["task_id"] == "next-task"
    assert all(item["latest_record"]["plan_id"] != "manual-plan" for item in result["items"])

    history = repository.get_outreach_customer_log(
        sl8003_item["contact_key"],
        started_from=START,
        started_to=END,
    )
    assert len(history["history"]) == 4
    assert {record["source_type"] for record in history["history"]} == {
        "first_day",
        "followup_strategy",
        "auto_approved",
    }
    assert all(record.get("plan_id") != "manual-plan" for record in history["history"])

    verified_detail = repository.get_outreach_customer_log_plan(sl8003_item["contact_key"], "first-day")
    unverified_detail = repository.get_outreach_customer_log_plan(sl8003_item["contact_key"], "auto-approved")
    assert verified_detail["tasks"][0]["actual_send"] is True
    assert verified_detail["identity"] == {
        "corp_id": "corp-a",
        "wechat": "SL8003",
        "external_userid": "external-a",
        "customer_id": "customer-a",
        "user_id": "operator-1",
        "customer_add_wechat_id": "relation-1",
        "conversation_id": "conversation-1",
    }
    assert unverified_detail["tasks"][0]["actual_send"] is False
    assert unverified_detail["technical"]["source_snapshot"]["api_key"] == "[REDACTED]"
    assert repository.get_outreach_customer_log_plan(sl8003_item["contact_key"], "other-wechat") == {}

    by_external_userid = repository.list_outreach_customer_logs(
        started_from=START,
        started_to=END,
        identity_query="external-a",
    )
    assert [item["identity"]["wechat"] for item in by_external_userid["items"]] == ["SL8003", "DY258"]


def test_customer_logs_keep_incomplete_records_separate_and_validate_range(tmp_path) -> None:
    store, repository = _repository(tmp_path)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO first_day_outreach_runs
                (workflow_run_id,plan_id,corp_id,wechat,customer_id,external_userid,trigger_type,
                 status,reason_code,input_snapshot_json,workflow_json,started_at,created_at,updated_at)
            VALUES ('incomplete-run','','corp-a','','customer-incomplete','',
                    'first_day_opened_silence','blocked','missing_identity','{}','{}',?,?,?)
            """,
            ("2026-09-06T01:00:00+00:00",) * 3,
        )

    result = repository.list_outreach_customer_logs(
        started_from=START,
        started_to=END,
        identity_state="incomplete",
    )

    assert result["metrics"]["customer_count"] == 0
    assert result["metrics"]["identity_incomplete_count"] == 1
    assert result["items"][0]["identity_state"] == "incomplete"
    assert result["items"][0]["contact_key"] == "run:incomplete-run"

    with pytest.raises(ValueError, match="cannot exceed 90 days"):
        repository.list_outreach_customer_logs(
            started_from="2026-06-01T00:00:00+00:00",
            started_to=END,
        )
    with pytest.raises(ValueError, match="started_from must be an ISO-8601 datetime"):
        repository.list_outreach_customer_logs(started_from="not-a-date", started_to=END)


def test_customer_log_detail_never_treats_customer_id_as_external_userid(tmp_path) -> None:
    store, repository = _repository(tmp_path)
    _insert_plan(
        store,
        plan_id="external-scope",
        sop_plan_id="first_day_opened_silence",
        customer_id="platform-customer",
        external_userid="wm-contact",
        created_at="2026-09-06T01:00:00+00:00",
    )
    _insert_plan(
        store,
        plan_id="legacy-customer-only",
        sop_plan_id="first_day_opened_silence",
        customer_id="wm-contact",
        external_userid="",
        created_at="2026-09-06T02:00:00+00:00",
    )

    result = repository.list_outreach_customer_logs(started_from=START, started_to=END)
    external_item = next(
        item for item in result["items"] if item["identity"].get("external_userid") == "wm-contact"
    )
    detail = repository.get_outreach_customer_log(
        external_item["contact_key"],
        started_from=START,
        started_to=END,
    )

    assert [record["plan_id"] for record in detail["history"]] == ["external-scope"]
    assert repository.get_outreach_customer_log_plan(
        external_item["contact_key"],
        "legacy-customer-only",
    ) == {}


def test_customer_log_list_aggregates_repeated_no_plan_events_without_losing_count(tmp_path) -> None:
    store, repository = _repository(tmp_path)
    payload = json.dumps(
        {
            "identity": {
                "corp_id": "corp-a",
                "wechat": "SL8003",
                "customer_id": "customer-a",
                "external_userid": "external-a",
            },
            "trigger_context": {"activation_policy": "auto_approved"},
            "reason": "no_eligible_source",
        }
    )
    with store.connect() as conn:
        for event_id, created_at in (
            ("rejected-1", "2026-09-06T01:00:00+00:00"),
            ("rejected-2", "2026-09-06T02:00:00+00:00"),
        ):
            conn.execute(
                """
                INSERT INTO outreach_events
                    (id,plan_id,task_id,customer_id,event_type,event_summary,payload_json,created_at)
                VALUES (?,'','','customer-a','plan_rejected','not planned',?,?)
                """,
                (event_id, payload, created_at),
            )
        second_customer_payload = json.loads(payload)
        second_customer_payload["identity"]["customer_id"] = "customer-b"
        conn.execute(
            """
            INSERT INTO outreach_events
                (id,plan_id,task_id,customer_id,event_type,event_summary,payload_json,created_at)
            VALUES ('rejected-3','','','customer-b','plan_rejected','not planned',?,?)
            """,
            (
                json.dumps(second_customer_payload),
                "2026-09-06T03:00:00+00:00",
            ),
        )

    result = repository.list_outreach_customer_logs(started_from=START, started_to=END)

    assert result["metrics"]["no_plan_count"] == 3
    assert len(result["items"]) == 1
    assert result["items"][0]["no_plan_count"] == 3

    detail = repository.get_outreach_customer_log(
        result["items"][0]["contact_key"],
        started_from=START,
        started_to=END,
    )
    assert [record["record_id"] for record in detail["history"]] == [
        "event:rejected-3",
        "event:rejected-2",
        "event:rejected-1",
    ]
