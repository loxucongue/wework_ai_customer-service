from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from app.services.storage.mysql_store import MySQLStore
from app.services.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)
_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)", flags=re.I)


class MirroredConnection:
    def __init__(self, primary: Any):
        self.primary = primary
        self.writes: list[tuple[str, list[tuple[Any, ...]], bool]] = []

    @property
    def total_changes(self) -> int:
        return int(self.primary.total_changes)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        result = self.primary.execute(sql, params)
        if _WRITE.match(sql):
            self.writes.append((sql, [tuple(params or ())], False))
        return result

    def executemany(self, sql: str, params: Sequence[Sequence[Any]]) -> Any:
        rows = [tuple(row) for row in params]
        result = self.primary.executemany(sql, rows)
        if _WRITE.match(sql):
            self.writes.append((sql, rows, True))
        return result


class MirroredStore:
    dialect = "mysql"

    def __init__(self, primary: MySQLStore, mirror: SQLiteStore):
        self.primary = primary
        self.mirror = mirror
        self.last_mirror_error = ""

    def initialize(self) -> None:
        self.primary.initialize()
        self.mirror.initialize()

    def json_text(self, column: str, path: str) -> str:
        return self.primary.json_text(column, path)

    @contextmanager
    def connect(self) -> Iterator[MirroredConnection]:
        primary_context = self.primary.connect()
        primary_connection = primary_context.__enter__()
        mirrored = MirroredConnection(primary_connection)
        try:
            yield mirrored
        except Exception as exc:
            primary_context.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            primary_context.__exit__(None, None, None)
            if mirrored.writes:
                try:
                    with self.mirror.connect() as mirror_connection:
                        for sql, rows, is_many in mirrored.writes:
                            if is_many:
                                mirror_connection.executemany(sql, rows)
                            else:
                                mirror_connection.execute(sql, rows[0])
                    self.last_mirror_error = ""
                except Exception as exc:
                    self.last_mirror_error = f"{type(exc).__name__}: {exc}"
                    logger.exception("SQLite mirror write failed after MySQL commit")

    def close(self) -> None:
        self.primary.close()
        self.mirror.close()

