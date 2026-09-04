from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import Settings


class SQLiteStore:
    dialect = "sqlite"

    def __init__(self, settings: Settings):
        self.db_path: Path = settings.db_path
        self.schema_path = Path(__file__).with_name("schema.sql")

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        with self.connect() as conn:
            # Existing SQLite tables must gain indexed columns before schema.sql
            # attempts to create the new indexes. New databases have no tables yet.
            self._ensure_v3_strategy_analytics_columns(conn)
            conn.executescript(schema)
            self._ensure_customer_memory_columns(conn)
            self._ensure_outreach_plan_columns(conn)
            self._ensure_first_day_outreach_run_columns(conn)
            self._ensure_sop_event_columns(conn)
            self._ensure_sop_send_task_columns(conn)
            self._ensure_v3_strategy_analytics_columns(conn)
            self._ensure_sales_contact_indexes(conn)

    @staticmethod
    def json_text(column: str, path: str) -> str:
        return f"json_extract({column}, '{path}')"

    @staticmethod
    def close() -> None:
        return None

    @staticmethod
    def _ensure_customer_memory_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(customer_memory)").fetchall()
        }
        columns = {
            "last_customer_message_at": "TEXT NOT NULL DEFAULT ''",
            "last_staff_message_at": "TEXT NOT NULL DEFAULT ''",
            "last_ai_reply_at": "TEXT NOT NULL DEFAULT ''",
            "last_manual_takeover_at": "TEXT NOT NULL DEFAULT ''",
            "last_outreach_at": "TEXT NOT NULL DEFAULT ''",
            "outreach_status": "TEXT NOT NULL DEFAULT 'none'",
            "outreach_plan_id": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE customer_memory ADD COLUMN {name} {definition}")

    @staticmethod
    def _ensure_outreach_plan_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(outreach_plans)").fetchall()
        }
        columns = {
            "sop_plan_id": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE outreach_plans ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outreach_plans_sop_plan_id ON outreach_plans(sop_plan_id, created_at)")

    @staticmethod
    def _ensure_first_day_outreach_run_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(first_day_outreach_runs)").fetchall()
        }
        columns = {
            "conversation_fingerprint": "TEXT DEFAULT NULL",
            "next_retry_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(
                    f"ALTER TABLE first_day_outreach_runs ADD COLUMN {name} {definition}"
                )
        populated_rows = conn.execute(
            """
            SELECT corp_id, wechat, external_userid, customer_id,
                   trigger_type, conversation_fingerprint
            FROM first_day_outreach_runs
            WHERE conversation_fingerprint IS NOT NULL
              AND conversation_fingerprint<>''
            """
        ).fetchall()
        claimed: set[tuple[str, str, str, str, str, str]] = {
            (
                str(row["corp_id"] or ""),
                str(row["wechat"] or "").lower(),
                str(row["external_userid"] or ""),
                str(row["customer_id"] or ""),
                str(row["trigger_type"] or ""),
                str(row["conversation_fingerprint"] or ""),
            )
            for row in populated_rows
        }
        rows = conn.execute(
            """
            SELECT workflow_run_id, corp_id, wechat, external_userid, customer_id,
                   trigger_type, input_snapshot_json
            FROM first_day_outreach_runs
            WHERE conversation_fingerprint IS NULL
            ORDER BY started_at DESC, workflow_run_id DESC
            """
        ).fetchall()
        for row in rows:
            try:
                snapshot = json.loads(str(row["input_snapshot_json"] or "{}"))
            except (TypeError, ValueError):
                continue
            trigger = snapshot.get("trigger_context") if isinstance(snapshot, dict) else {}
            fingerprint = str(
                trigger.get("conversation_fingerprint") if isinstance(trigger, dict) else ""
            ).strip()
            key = (
                str(row["corp_id"] or ""),
                str(row["wechat"] or "").lower(),
                str(row["external_userid"] or ""),
                str(row["customer_id"] or ""),
                str(row["trigger_type"] or ""),
                fingerprint,
            )
            if not fingerprint or key in claimed:
                continue
            conn.execute(
                "UPDATE first_day_outreach_runs SET conversation_fingerprint=? WHERE workflow_run_id=?",
                (fingerprint, row["workflow_run_id"]),
            )
            claimed.add(key)
        conn.execute("DROP INDEX IF EXISTS idx_first_day_runs_fingerprint")
        index_row = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type='index' AND name='idx_first_day_runs_contact_fingerprint'
            """
        ).fetchone()
        index_sql = str(index_row["sql"] or "").lower() if index_row else ""
        if index_sql and "lower(wechat)" not in index_sql.replace(" ", ""):
            conn.execute("DROP INDEX IF EXISTS idx_first_day_runs_contact_fingerprint")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_first_day_runs_contact_fingerprint
            ON first_day_outreach_runs(
                corp_id, lower(wechat), external_userid, customer_id,
                trigger_type, conversation_fingerprint
            )
            WHERE conversation_fingerprint IS NOT NULL AND conversation_fingerprint<>''
            """
        )

    @staticmethod
    def _ensure_sop_event_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(sop_events)").fetchall()
        }
        columns = {
            "id": "TEXT NOT NULL DEFAULT ''",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "next_retry_at": "TEXT NOT NULL DEFAULT ''",
            "last_retry_error": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE sop_events ADD COLUMN {name} {definition}")
        conn.execute("UPDATE sop_events SET id=event_id WHERE id=''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sop_events_id ON sop_events(id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sop_events_retry ON sop_events(status, next_retry_at)"
        )

    @staticmethod
    def _ensure_sop_send_task_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(sop_send_tasks)").fetchall()
        }
        columns = {
            "trigger_source": "TEXT NOT NULL DEFAULT ''",
            "sop_category": "TEXT NOT NULL DEFAULT ''",
            "send_once_key": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE sop_send_tasks ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sop_send_tasks_category ON sop_send_tasks(sop_category, status, created_at)")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sop_send_tasks_send_once_key
            ON sop_send_tasks(send_once_key)
            WHERE send_once_key<>'' AND status IN ('pending','sent')
            """
        )

    @staticmethod
    def _ensure_v3_strategy_analytics_columns(conn: sqlite3.Connection) -> None:
        table_columns = {
            "v3_strategy_usage_events": {
                "policy_version": "TEXT NOT NULL DEFAULT ''",
                "decision_status": "TEXT NOT NULL DEFAULT ''",
                "intent_confidence": "TEXT NOT NULL DEFAULT ''",
                "intent_secondary_json": "TEXT NOT NULL DEFAULT '[]'",
                "emotion_confidence": "TEXT NOT NULL DEFAULT ''",
                "emotion_pressure": "TEXT NOT NULL DEFAULT ''",
                "emotion_flow_action": "TEXT NOT NULL DEFAULT ''",
                "closing_action": "TEXT NOT NULL DEFAULT ''",
                "closing_node_key": "TEXT NOT NULL DEFAULT ''",
                "closing_trigger": "TEXT NOT NULL DEFAULT ''",
                "closing_customer_state": "TEXT NOT NULL DEFAULT ''",
                "closing_pressure": "TEXT NOT NULL DEFAULT ''",
                "closing_rule_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "closing_primary_rule_id": "TEXT NOT NULL DEFAULT ''",
                "closing_primary_rule_name": "TEXT NOT NULL DEFAULT ''",
                "closing_sequence_name": "TEXT NOT NULL DEFAULT ''",
                "closing_node_name": "TEXT NOT NULL DEFAULT ''",
                "closing_sequence_source_id": "TEXT NOT NULL DEFAULT ''",
                "closing_node_source_id": "TEXT NOT NULL DEFAULT ''",
                "closing_action_type_id": "INTEGER NOT NULL DEFAULT 0",
                "closing_action_type_name": "TEXT NOT NULL DEFAULT ''",
                "closing_script_type_id": "INTEGER NOT NULL DEFAULT 0",
                "closing_script_type_name": "TEXT NOT NULL DEFAULT ''",
                "closing_catalog_checksum": "TEXT NOT NULL DEFAULT ''",
                "closing_catalog_status": "TEXT NOT NULL DEFAULT ''",
                "closing_rule_match_status": "TEXT NOT NULL DEFAULT ''",
                "closing_constraint_status": "TEXT NOT NULL DEFAULT ''",
                "closing_constraint_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
                "cardpoint_category_key": "TEXT NOT NULL DEFAULT ''",
                "cardpoint_state": "TEXT NOT NULL DEFAULT ''",
                "decision_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
                "decision_evidence_refs_json": "TEXT NOT NULL DEFAULT '{}'",
                "retrieval_mode": "TEXT NOT NULL DEFAULT ''",
                "customer_turn_eligible": "INTEGER NOT NULL DEFAULT 1",
            },
            "v3_strategy_outcome_events": {
                "next_usage_event_id": "TEXT NOT NULL DEFAULT ''",
                "next_intent_code": "TEXT NOT NULL DEFAULT ''",
                "next_emotion_code": "TEXT NOT NULL DEFAULT ''",
                "emotion_transition": "TEXT NOT NULL DEFAULT ''",
                "attribution_anchor_source": "TEXT NOT NULL DEFAULT 'unknown'",
                "order_source": "TEXT NOT NULL DEFAULT ''",
                "order_query_status": "TEXT NOT NULL DEFAULT ''",
                "order_query_error": "TEXT NOT NULL DEFAULT ''",
                "order_last_refreshed_at": "TEXT NOT NULL DEFAULT ''",
                "order_state_after_14d": "TEXT NOT NULL DEFAULT ''",
                "order_state_after_30d": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in table_columns.items():
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if table_exists is None:
                continue
            existing = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v3_strategy_usage_events'"
        ).fetchone() is not None:
            conn.execute(
                """
                UPDATE v3_strategy_usage_events SET customer_turn_eligible=0
                WHERE reply_source IN (
                    'ignored_platform_auto_message', 'platform_recalled_message',
                    'platform_superseded', 'platform_filtered'
                )
                """
            )
        usage_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v3_strategy_usage_events'"
        ).fetchone()
        if usage_exists is None:
            return
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3_strategy_usage_intent "
            "ON v3_strategy_usage_events(intent_code, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3_strategy_usage_emotion "
            "ON v3_strategy_usage_events(emotion_before, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3_strategy_usage_closing "
            "ON v3_strategy_usage_events(closing_strategy_code, closing_action, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3_strategy_usage_closing_catalog "
            "ON v3_strategy_usage_events(closing_catalog_status, closing_rule_match_status, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3_strategy_usage_closing_rule "
            "ON v3_strategy_usage_events(closing_primary_rule_id, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3_strategy_usage_retrieval "
            "ON v3_strategy_usage_events(retrieval_mode, occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3_strategy_usage_decision "
            "ON v3_strategy_usage_events(decision_status, occurred_at)"
        )

    @staticmethod
    def _ensure_sales_contact_indexes(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversations_sales_contact
            ON conversations(corp_id, wechat, external_userid, customer_id, updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outreach_plans_sales_contact
            ON outreach_plans(corp_id, wechat, external_userid, customer_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sop_send_tasks_sales_contact
            ON sop_send_tasks(corp_id, wechat, external_userid, customer_id, status, created_at)
            """
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
