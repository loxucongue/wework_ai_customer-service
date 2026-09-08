from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.services.storage.repositories import AppRepository  # noqa: E402
from app.services.storage.sqlite_store import SQLiteStore  # noqa: E402


def _repository(tmp_path) -> tuple[SQLiteStore, AppRepository]:
    store = SQLiteStore(
        Settings(AI_PATHS_DB_PATH=tmp_path / "outreach-history.db", AICS_STORAGE_BACKEND="sqlite")
    )
    store.initialize()
    return store, AppRepository(store)


def _insert_sent_task(
    store: SQLiteStore,
    *,
    plan_id: str,
    task_id: str,
    wechat: str,
    external_userid: str,
    selected_script_id: str,
) -> None:
    now = "2026-09-08T08:00:00+00:00"
    metadata = {
        "outreach_task_metadata": {
            "plan_mode": "follow_sequence",
            "follow_sequence": {"id": "sequence-1", "checksum": "checksum-1"},
            "follow_sequence_node": {"id": "node-1"},
        }
    }
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO outreach_plans
                (id,sop_plan_id,customer_id,corp_id,user_id,wechat,external_userid,status,
                 plan_goal,source_snapshot,created_at,updated_at)
            VALUES (?,'first_day_opened_silence','customer-1','corp-1','operator-1',?,?,
                    'completed','follow up','{}',?,?)
            """,
            (plan_id, wechat, external_userid, now, now),
        )
        conn.execute(
            """
            INSERT INTO outreach_tasks
                (id,plan_id,customer_id,step_index,scheduled_at,status,intent,message_goal,
                 content_sources,reply_messages_json,before_send_check,sent_at,send_status,
                 system_msgid,error_message,created_at,updated_at)
            VALUES (?,?,'customer-1',1,?,'sent','follow','new value',?,?,1,?,
                    'accepted',?,'',?,?)
            """,
            (
                task_id,
                plan_id,
                now,
                json.dumps([metadata], ensure_ascii=False),
                json.dumps(
                    [{"type": "text", "content": {"text": f"message for {wechat}"}}],
                    ensure_ascii=False,
                ),
                now,
                f"msg-{task_id}",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO outreach_events
                (id,plan_id,task_id,customer_id,event_type,event_summary,payload_json,created_at)
            VALUES (?,?,?,?, 'task_follow_script_selected','selected',?,?)
            """,
            (
                f"event-{task_id}",
                plan_id,
                task_id,
                "customer-1",
                json.dumps(
                    {
                        "selected_script_id": selected_script_id,
                        "value_dimension": "case_proof",
                        "new_information": "new case",
                        "conversion_action": "none",
                    }
                ),
                now,
            ),
        )


def test_recent_outreach_delivery_keeps_wechat_identity_and_selection_progress(tmp_path) -> None:
    store, repository = _repository(tmp_path)
    _insert_sent_task(
        store,
        plan_id="plan-a",
        task_id="task-a",
        wechat="SL8003",
        external_userid="external-1",
        selected_script_id="script-a",
    )
    _insert_sent_task(
        store,
        plan_id="plan-b",
        task_id="task-b",
        wechat="DY258",
        external_userid="external-1",
        selected_script_id="script-b",
    )

    history = repository.recent_outreach_delivery(
        customer_id="customer-1",
        corp_id="corp-1",
        wechat="SL8003",
        external_userid="external-1",
    )

    assert len(history) == 1
    assert history[0]["task_id"] == "task-a"
    assert history[0]["selected_script_id"] == "script-a"
    assert history[0]["follow_sequence_node_id"] == "node-1"
    assert history[0]["value_dimension"] == "case_proof"
