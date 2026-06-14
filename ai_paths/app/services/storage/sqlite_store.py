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
            seed_marker = "INSERT OR IGNORE INTO project_catalog"
            if seed_marker in schema:
                schema_prefix, seed_suffix = schema.split(seed_marker, 1)
                conn.executescript(schema_prefix)
                self._migrate_project_tables(conn)
                conn.executescript(seed_marker + seed_suffix)
            else:
                conn.executescript(schema)
                self._migrate_project_tables(conn)

    def _migrate_project_tables(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            "project_catalog",
            {
                "project_name": "TEXT NOT NULL DEFAULT ''",
                "duration": "TEXT NOT NULL DEFAULT ''",
                "original_price": "INTEGER NOT NULL DEFAULT 0",
                "enabled": "INTEGER NOT NULL DEFAULT 1",
            },
        )

    def _ensure_columns(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        columns: dict[str, str],
    ) -> None:
        existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}
        for column_name, ddl in columns.items():
            if column_name not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

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
