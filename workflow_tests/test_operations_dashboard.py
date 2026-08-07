from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.storage import AppRepository, SQLiteStore


def test_operations_dashboard_aggregates_authoritative_runtime_tables(tmp_path) -> None:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "operations.db"))
    store.initialize()
    repository = AppRepository(store)
    now = datetime.now(UTC)
    at = now.isoformat()
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO conversations
            (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
            VALUES ('conversation-a', 'customer-a', '', 'corp-a', '', 'staff-a', '', ?, ?)""",
            (at, at),
        )
        conn.execute(
            """INSERT INTO runs
            (request_id, conversation_id, customer_id, input_snapshot, output_snapshot, intents, tags,
             duration_ms, token_usage, error, created_at)
            VALUES (?, 'conversation-a', 'customer-a', ?, '{}', '[]', '[]', 1200, '{}', '', ?)""",
            ("run-ok", json.dumps({"corp_id": "corp-a", "wechat": "staff-a"}), at),
        )
        conn.execute(
            """INSERT INTO runs
            (request_id, conversation_id, customer_id, input_snapshot, output_snapshot, intents, tags,
             duration_ms, token_usage, error, created_at)
            VALUES (?, 'conversation-a', 'customer-a', ?, '{}', '[]', '[]', 5000, '{}', 'model timeout', ?)""",
            ("run-timeout", json.dumps({"corp_id": "corp-a", "wechat": "staff-a"}), at),
        )
        conn.execute(
            """INSERT INTO node_traces
            (id, request_id, node_name, input_snapshot, output_snapshot, tool_calls, duration_ms, error, created_at)
            VALUES ('trace-1', 'run-ok', 'reply', '{}', '{}', '[]', 900, '', ?)""",
            (at,),
        )
        conn.execute(
            """INSERT INTO sop_events
            (id, event_id, event_type, source, request_reply, upstream_created_at, raw_payload_json,
             status, error, retry_count, next_retry_at, last_retry_error, received_at, updated_at)
            VALUES ('event-log', 'platform:1', 'platform_sop_task', 'platform', 0, '', '{}',
                    'platform_completed', '', 1, '', '', ?, ?)""",
            (at, at),
        )
        conn.execute(
            """INSERT INTO sop_send_tasks
            (id, event_id, idempotency_key, send_once_key, customer_id, external_userid, corp_id, user_id,
             wechat, sop_pack_id, sop_pack_name, sop_category, trigger_source, reply_messages_json, status,
             send_payload_json, send_response_json, error, created_at, updated_at, sent_at)
            VALUES ('sop-task', 'platform:1', '', '', 'customer-a', '', 'corp-a', '', 'staff-a', '', '', '',
                    'platform', '[]', 'sent', '{}', '{}', '', ?, ?, ?)""",
            (at, at, at),
        )
        conn.execute(
            """INSERT INTO outreach_plans
            (id, sop_plan_id, customer_id, corp_id, user_id, wechat, external_userid, status,
             customer_stage, stall_reason, customer_psychology, plan_goal, source_snapshot,
             created_at, updated_at, paused_at, cancelled_at, completed_at)
            VALUES ('plan-1', '', 'customer-a', 'corp-a', '', 'staff-a', '', 'active', '', '', '', '', '{}',
                    ?, ?, '', '', '')""",
            (at, at),
        )
        conn.execute(
            """INSERT INTO first_day_outreach_runs
            (workflow_run_id, plan_id, corp_id, wechat, customer_id, trigger_type, status, reason_code,
             final_decision, model_attempt_count, retry_count, duration_ms, started_at, finished_at,
             created_at, updated_at)
            VALUES ('workflow-1', 'plan-1', 'corp-a', 'staff-a', 'customer-a', 'first_day_opened_silence',
                    'created', 'plan_created', 'send', 3, 1, 3000, ?, ?, ?, ?)""",
            (at, at, at, at),
        )
        conn.execute(
            """INSERT INTO outreach_tasks
            (id, plan_id, customer_id, step_index, scheduled_at, status, intent, message_goal,
             content_sources, reply_messages_json, before_send_check, sent_at, send_status,
             system_msgid, error_message, created_at, updated_at)
            VALUES ('outreach-task-1', 'plan-1', 'customer-a', 1, ?, 'sent', '', '', '[]', '[]', 1,
                    ?, 'sent', '', '', ?, ?)""",
            (at, at, at, at),
        )

    result = repository.operations_dashboard(
        started_from=(now - timedelta(hours=1)).isoformat(),
        started_to=(now + timedelta(minutes=1)).isoformat(),
        corp_id="corp-a",
        wechat="staff-a",
    )

    assert result["ai_reply"]["calls"] == 2
    assert result["ai_reply"]["timeout"] == 1
    assert result["ai_reply"]["p90_ms"] == 5000
    assert result["platform_sop"]["sent"] == 1
    assert result["platform_sop"]["retry_count"] == 1
    assert result["first_day_outreach"]["plans_created"] == 1
    assert result["first_day_outreach"]["first_sent"] == 1
    assert result["first_day_outreach"]["model_attempts"] == 3


def test_operations_dashboard_rejects_ranges_over_90_days(tmp_path) -> None:
    store = SQLiteStore(SimpleNamespace(db_path=tmp_path / "operations.db"))
    store.initialize()
    repository = AppRepository(store)
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="90 days"):
        repository.operations_dashboard(
            started_from=(now - timedelta(days=91)).isoformat(),
            started_to=now.isoformat(),
        )
