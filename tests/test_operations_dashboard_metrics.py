from __future__ import annotations

import json

from app.config import Settings
from app.services.storage.repositories import AppRepository
from app.services.storage.mysql_store import MySQLStore
from app.services.storage.sqlite_store import SQLiteStore


def test_operations_dashboard_uses_authoritative_business_facts(tmp_path) -> None:
    store = SQLiteStore(Settings(AI_PATHS_DB_PATH=tmp_path / "bi.db", AICS_STORAGE_BACKEND="sqlite"))
    store.initialize()
    repository = AppRepository(store)
    start = "2026-09-05T16:00:00+00:00"
    end = "2026-09-06T15:59:59+00:00"
    now = "2026-09-06T01:00:00+00:00"
    with store.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE bi_customer_member_relations (
              enterprise_id TEXT, wework_user_id TEXT, external_userid TEXT,
              conversation_id TEXT, member_friend_added_at TEXT
            );
            CREATE TABLE bi_archive_messages (
              conversation_id TEXT, wework_user_id TEXT, external_userid TEXT,
              content TEXT, message_origin TEXT, msg_type TEXT, direction TEXT, timestamp TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO bi_customer_member_relations VALUES (?,?,?,?,?)",
            [
                ("corp", "SL1580", "ext-1", "archive-1", "2026-09-06 08:00:00"),
                ("corp", "SL1580", "ext-2", "archive-2", "2026-09-06 09:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO bi_archive_messages VALUES (?,?,?,?,?,?,?,?)",
            [
                ("archive-1", "SL1580", "ext-1", "我已经添加了你，现在我们可以开始聊天了。", "archive_history", "text", "incoming", "2026-09-06 08:00:01"),
                ("archive-1", "SL1580", "ext-1", "你好", "archive_history", "text", "incoming", "2026-09-06 08:03:00"),
                ("archive-2", "SL1580", "ext-2", "加微前旧消息", "archive_history", "text", "incoming", "2026-09-06 08:59:00"),
            ],
        )
        conn.execute(
            "INSERT INTO conversations (id,customer_id,external_userid,corp_id,wechat,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            ("conversation", "customer", "ext-1", "corp", "SL1580", now, now),
        )
        for request_id, isolated in (("prod", False), ("test", True)):
            conn.execute(
                "INSERT INTO runs (request_id,conversation_id,customer_id,input_snapshot,duration_ms,error,created_at) VALUES (?,?,?,?,?,?,?)",
                (request_id, "conversation", "customer", json.dumps({"request_context": {"test_isolated": isolated}}), 100, "", now),
            )
        for event_id in ("sent-event", "shadow-event", "no-send-event"):
            conn.execute(
                "INSERT INTO sop_events (event_id,event_type,status,received_at,updated_at) VALUES (?,?,?,?,?)",
                (event_id, "platform_sop_task", "platform_completed", now, now),
            )
        accepted = {"data": {"send_status": "accepted", "system_msgids": ["msg-1"]}}
        conn.execute(
            "INSERT INTO sop_send_tasks (id,event_id,idempotency_key,status,send_payload_json,send_response_json,created_at,updated_at,sent_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("sent", "sent-event", "sent", "sent", "{}", json.dumps(accepted), now, now, now),
        )
        conn.execute(
            "INSERT INTO sop_send_tasks (id,event_id,idempotency_key,status,send_payload_json,created_at,updated_at,sent_at) VALUES (?,?,?,?,?,?,?,?)",
            ("shadow", "shadow-event", "shadow", "shadow_send", "{}", now, now, now),
        )
        conn.execute(
            "INSERT INTO sop_send_tasks (id,event_id,idempotency_key,status,send_payload_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            ("no-send", "no-send-event", "no-send", "completed_without_send", json.dumps({"reason_code": "human_takeover"}), now, now),
        )
        for run_id, status, retry_count, duration in (("first-1", "failed", 9, 500), ("first-2", "blocked", 1, 0)):
            conn.execute(
                """INSERT INTO first_day_outreach_runs
                   (workflow_run_id,plan_id,corp_id,wechat,status,reason_code,retry_count,duration_ms,started_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, "same-plan", "corp", "SL1580", status, status, retry_count, duration, now, now, now),
            )

    result = repository.operations_dashboard(started_from=start, started_to=end)

    assert result["contacts"] == {"new_contacts": 2, "opened_contacts": 1}
    assert result["ai_reply"]["calls"] == 1
    assert result["platform_sop"]["sent"] == 1
    assert result["platform_sop"]["no_send"] == 1
    assert result["platform_sop"]["reason_breakdown"] == [{"key": "human_takeover", "count": 1}]
    assert result["first_day_outreach"]["plans_created"] == 1
    assert result["first_day_outreach"]["failed"] == 1
    assert result["first_day_outreach"]["retry_count"] == 2
    assert result["first_day_outreach"]["retry_attempts"] == 10
    assert result["first_day_outreach"]["avg_ms"] == 500


def test_mysql_source_tables_are_read_without_aics_prefix() -> None:
    store = object.__new__(MySQLStore)
    store.table_prefix = "aics_"

    sql = store.prepare_sql(
        f"SELECT * FROM {store.source_table('customer_member_relations')} r "
        f"JOIN {store.source_table('archive_messages')} m ON m.conversation_id=r.conversation_id"
    )

    assert "FROM customer_member_relations" in sql
    assert "JOIN messages" in sql
    assert "aics_customer_member_relations" not in sql
    assert "aics_messages" not in sql
