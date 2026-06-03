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
