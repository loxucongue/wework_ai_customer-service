from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.services.storage.repositories import AppRepository
from app.services.storage.sqlite_store import SQLiteStore


def test_outreach_dashboard_builds_funnel_trend_reasons_and_queue(tmp_path) -> None:
    store = SQLiteStore(Settings(AI_PATHS_DB_PATH=tmp_path / "outreach-bi.db", AICS_STORAGE_BACKEND="sqlite"))
    store.initialize()
    repository = AppRepository(store)
    start = "2026-09-06T00:00:00+00:00"
    end = "2026-09-07T00:00:00+00:00"
    scene = {"summary": {"scene_analysis": {"eligible": True, "customer_mainline": {"latest_customer_main_need": "想确认效果"}}}}
    snapshot = {
        "recent_messages": [
            {"role": "user", "content": "做一次真的能看到效果吗", "created_at": "2026-09-06T00:00:00+00:00"}
        ],
        "conversation_activity": {"reply_wait_minutes": 6},
    }
    with store.connect() as conn:
        for conversation_id, customer_id, external_userid, wechat, user_id, title in (
            ("c1", "customer-1", "ext-1", "sl8003", "staff-1", "客户甲"),
            ("c2", "customer-2", "ext-2", "sl8003", "staff-1", "客户乙"),
            ("c3", "customer-3", "ext-3", "sl8003", "staff-1", "客户丙"),
            ("c4", "customer-4", "", "sl9000", "staff-2", "客户丁"),
        ):
            conn.execute(
                "INSERT INTO conversations (id,customer_id,external_userid,corp_id,user_id,wechat,title,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (conversation_id, customer_id, external_userid, "corp", user_id, wechat, title, start, end),
            )
        conn.execute(
            "INSERT INTO messages (id,conversation_id,role,content,created_at) VALUES (?,?,?,?,?)",
            ("m1", "c1", "user", "看过案例后我想了解预约", "2026-09-06T00:20:00+00:00"),
        )
        for plan_id, customer_id, external_userid, wechat, created_at in (
            ("plan-1", "customer-1", "ext-1", "sl8003", "2026-09-06T00:01:00+00:00"),
            ("plan-2", "customer-4", "ext-4", "sl9000", "2026-09-06T00:31:00+00:00"),
        ):
            conn.execute(
                """INSERT INTO outreach_plans
                   (id,customer_id,corp_id,wechat,external_userid,status,plan_goal,source_snapshot,created_at,updated_at)
                   VALUES (?,?,?,?,?,'active','唤醒客户',?,?,?)""",
                (plan_id, customer_id, "corp", wechat, external_userid, "{}", created_at, created_at),
            )
        conn.execute(
            """INSERT INTO outreach_tasks
               (id,plan_id,customer_id,step_index,scheduled_at,status,intent,message_goal,content_sources,
                reply_messages_json,before_send_check,sent_at,send_status,system_msgid,created_at,updated_at)
               VALUES (?,?,?,?,?,'sent','','效果证明','[]','[]',1,?,'accepted','msg-1',?,?)""",
            ("task-1", "plan-1", "customer-1", 1, "2026-09-06T00:05:00+00:00", "2026-09-06T00:05:00+00:00", "2026-09-06T00:01:00+00:00", "2026-09-06T00:05:00+00:00"),
        )
        conn.execute(
            """INSERT INTO outreach_tasks
               (id,plan_id,customer_id,step_index,scheduled_at,status,intent,message_goal,content_sources,
                reply_messages_json,before_send_check,created_at,updated_at)
               VALUES (?,?,?,?,?,'pending','','活动价值','[]','[]',1,?,?)""",
            ("task-2", "plan-2", "customer-4", 1, "2026-09-06T01:00:00+00:00", "2026-09-06T00:31:00+00:00", "2026-09-06T00:31:00+00:00"),
        )
        conn.execute(
            """INSERT INTO outreach_events
               (id,plan_id,task_id,customer_id,event_type,event_summary,payload_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("event-1", "plan-1", "task-1", "customer-1", "task_skipped_order_state_changed", "订单状态变化", "{}", "2026-09-06T01:00:00+00:00"),
        )
        runs = [
            ("run-1", "plan-1", "customer-1", "ext-1", "sl8003", "created", "plan_created", "效果证明", "", snapshot, scene, "2026-09-06T00:00:00+00:00"),
            ("run-2", "", "customer-2", "ext-2", "sl8003", "blocked", "human_mode", "", "", {}, {}, "2026-09-06T00:10:00+00:00"),
            ("run-3", "", "customer-3", "ext-3", "sl8003", "failed", "workflow_failed", "", "", {}, {}, "2026-09-06T00:20:00+00:00"),
            ("run-4", "plan-2", "customer-4", "ext-4", "sl9000", "created", "plan_created", "活动价值", "", snapshot, scene, "2026-09-06T00:30:00+00:00"),
        ]
        for run in runs:
            conn.execute(
                """INSERT INTO first_day_outreach_runs
                   (workflow_run_id,plan_id,corp_id,wechat,customer_id,external_userid,status,reason_code,
                    first_scene,second_scene,input_snapshot_json,workflow_json,started_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run[0], run[1], "corp", run[4], run[2], run[3], run[5], run[6], run[7], run[8], json.dumps(run[9]), json.dumps(run[10]), run[11], run[11], run[11]),
            )

    result = repository.outreach_bi_dashboard(started_from=start, started_to=end)

    assert [item["count"] for item in result["funnel"]] == [4, 2, 2, 1, 1, 1]
    assert result["funnel"][0]["next_rate"] == 0.5
    assert result["outcomes"]["reopened_24h_rate"] == 1.0
    assert result["reason_breakdown"][:2] == [
        {"key": "workflow_failed", "label": "计划生成异常", "count": 1},
        {"key": "human_takeover", "label": "人工接待", "count": 1},
    ] or result["reason_breakdown"][:2] == [
        {"key": "human_takeover", "label": "人工接待", "count": 1},
        {"key": "workflow_failed", "label": "计划生成异常", "count": 1},
    ]
    assert result["queue"][0]["workflow_run_id"] == "run-4"
    assert result["queue"][0]["phase"] == "waiting_first_touch"
    assert result["queue"][0]["customer_name"] == "客户丁"
    assert result["queue"][0]["conversation_id"] == "c4"
    assert result["queue"][0]["user_id"] == "staff-2"
    assert result["queue"][0]["task_refs"] == [
        {
            "task_id": "task-2",
            "step_index": 1,
            "status": "pending",
            "system_msgid": "",
            "scheduled_at": "2026-09-06T01:00:00+00:00",
            "sent_at": "",
        }
    ]
    assert result["queue"][-1]["last_customer_message"] == "做一次真的能看到效果吗"
    assert any(item["sent"] == 1 and item["reopened"] == 1 for item in result["trend"])
    assert {item["wechat"] for item in result["wechat_breakdown"]} == {"sl8003", "sl9000"}

    scoped = repository.outreach_bi_dashboard(started_from=start, started_to=end, wechat="sl8003")
    assert scoped["funnel"][0]["count"] == 3
    assert {item["wechat"] for item in scoped["wechat_breakdown"]} == {"sl8003"}


def test_outreach_dashboard_customer_identity_does_not_cross_wechat(tmp_path) -> None:
    store = SQLiteStore(Settings(AI_PATHS_DB_PATH=tmp_path / "identity.db", AICS_STORAGE_BACKEND="sqlite"))
    store.initialize()
    repository = AppRepository(store)
    start = "2026-09-06T00:00:00+00:00"
    end = "2026-09-07T00:00:00+00:00"
    with store.connect() as conn:
        for conversation_id, wechat, user_id, title in (
            ("conversation-a", "sl8003", "staff-a", "账号A客户"),
            ("conversation-b", "sl9000", "staff-b", "账号B客户"),
        ):
            conn.execute(
                "INSERT INTO conversations (id,customer_id,external_userid,corp_id,user_id,wechat,title,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (conversation_id, "customer-shared", "external-shared", "corp", user_id, wechat, title, start, end),
            )
            conn.execute(
                """INSERT INTO first_day_outreach_runs
                   (workflow_run_id,corp_id,wechat,customer_id,external_userid,status,reason_code,
                    input_snapshot_json,workflow_json,started_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,'blocked','human_mode','{}','{}',?,?,?)""",
                (f"run-{wechat}", "corp", wechat, "customer-shared", "external-shared", start, start, start),
            )

    result = repository.outreach_bi_dashboard(started_from=start, started_to=end)
    by_wechat = {item["wechat"]: item for item in result["queue"]}

    assert by_wechat["sl8003"]["conversation_id"] == "conversation-a"
    assert by_wechat["sl8003"]["customer_name"] == "账号A客户"
    assert by_wechat["sl8003"]["user_id"] == "staff-a"
    assert by_wechat["sl9000"]["conversation_id"] == "conversation-b"
    assert by_wechat["sl9000"]["customer_name"] == "账号B客户"
    assert by_wechat["sl9000"]["user_id"] == "staff-b"


def test_outreach_dashboard_rejects_ranges_over_31_days(tmp_path) -> None:
    store = SQLiteStore(Settings(AI_PATHS_DB_PATH=tmp_path / "range.db", AICS_STORAGE_BACKEND="sqlite"))
    store.initialize()
    repository = AppRepository(store)

    with pytest.raises(ValueError, match="cannot exceed 31 days"):
        repository.outreach_bi_dashboard(
            started_from="2026-07-01T00:00:00+00:00",
            started_to="2026-09-01T00:00:00+00:00",
        )
