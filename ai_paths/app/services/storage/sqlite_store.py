from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import Settings


class SQLiteStore:
    def __init__(self, settings: Settings):
        self.db_path: Path = settings.db_path
        self.schema_path = Path(__file__).with_name("schema.sql")

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(schema)
            self._ensure_customer_memory_columns(conn)
            self._ensure_outreach_plan_columns(conn)
            self._ensure_sop_send_task_columns(conn)

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
    def _ensure_sop_send_task_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(sop_send_tasks)").fetchall()
        }
        columns = {
            "trigger_source": "TEXT NOT NULL DEFAULT ''",
            "sop_category": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE sop_send_tasks ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sop_send_tasks_category ON sop_send_tasks(sop_category, status, created_at)")

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
