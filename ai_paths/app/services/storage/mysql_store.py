from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Iterator, Sequence
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
import pymysql

from app.config import Settings
from app.services.storage.mysql_schema import EXPECTED_ALL_TABLES, EXPECTED_COLUMNS, EXPECTED_INDEXES
from app.services.storage.store_base import map_logical_tables


logger = logging.getLogger(__name__)
_MUTATING = {"INSERT", "UPDATE", "DELETE", "REPLACE"}
_DDL = {"ALTER", "CREATE", "DROP", "RENAME", "TRUNCATE"}


def _replace_qmark_placeholders(sql: str) -> str:
    output: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            output.append("%%" if char == "%" else char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"', "`"}:
            quote = char
            output.append(char)
        elif char == "?":
            output.append("%s")
        elif char == "%":
            output.append("%%")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _translate_mysql_upsert(sql: str) -> str:
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT IGNORE INTO", sql, flags=re.I)
    translated = re.sub(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", "REPLACE INTO", translated, flags=re.I)
    do_nothing = re.search(
        r"\s+ON\s+CONFLICT\s*\(\s*([^)]+)\s*\)\s+DO\s+NOTHING\s*$",
        translated,
        flags=re.I | re.S,
    )
    if do_nothing:
        key = do_nothing.group(1).split(",")[0].strip()
        translated = translated[: do_nothing.start()] + f" ON DUPLICATE KEY UPDATE {key}={key}"
    else:
        conflict = re.search(
            r"\s+ON\s+CONFLICT\s*\(\s*[^)]+\s*\)\s+DO\s+UPDATE\s+SET\s+(.+)$",
            translated,
            flags=re.I | re.S,
        )
        if conflict:
            assignments = re.sub(
                r"\bexcluded\.([A-Za-z_][A-Za-z0-9_]*)",
                lambda match: f"VALUES({match.group(1)})",
                conflict.group(1),
                flags=re.I,
            )
            translated = translated[: conflict.start()] + " ON DUPLICATE KEY UPDATE " + assignments
    return translated


def _sql_without_literals_or_comments(sql: str) -> str:
    output: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if quote:
            if char == quote:
                if next_char == quote:
                    output.extend((" ", " "))
                    index += 2
                    continue
                quote = None
            output.append(" ")
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            output.append(" ")
            index += 1
            continue
        if char == "-" and next_char == "-":
            newline = sql.find("\n", index + 2)
            if newline < 0:
                output.extend(" " * (len(sql) - index))
                break
            output.extend(" " * (newline - index))
            index = newline
            continue
        if char == "/" and next_char == "*":
            end = sql.find("*/", index + 2)
            if end < 0:
                output.extend(" " * (len(sql) - index))
                break
            output.extend(" " * (end + 2 - index))
            index = end + 2
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _runtime_sql_guard(sql: str, *, prefix: str) -> None:
    normalized = _sql_without_literals_or_comments(sql)
    ddl_match = re.search(
        rf"\b({'|'.join(sorted(_DDL))})\b",
        normalized,
        flags=re.I,
    )
    if ddl_match:
        raise RuntimeError(f"Runtime DDL is forbidden: {ddl_match.group(1).upper()}")
    if not re.search(
        rf"\b({'|'.join(sorted(_MUTATING))})\b",
        normalized,
        flags=re.I,
    ):
        return
    target_sql = re.sub(
        r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b",
        "ON DUPLICATE KEY SET",
        normalized,
        flags=re.I,
    )
    table_matches = re.findall(
        r"\b(?:INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+`?([A-Za-z0-9_]+)`?",
        target_sql,
        flags=re.I,
    )
    if not table_matches:
        raise RuntimeError("Runtime write target could not be verified")
    invalid = [table for table in table_matches if not table.startswith(prefix)]
    if invalid:
        table = invalid[0]
        raise RuntimeError(f"Runtime write to non-AICS table is forbidden: {table}")


class MySQLCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount or 0)

    def fetchone(self) -> dict[str, Any] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._cursor.fetchall())


class MySQLConnection:
    def __init__(self, raw_connection: Any, store: "MySQLStore"):
        self._raw_connection = raw_connection
        self._store = store
        self.total_changes = 0

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> MySQLCursor:
        prepared = self._store.prepare_sql(sql)
        cursor = self._raw_connection.cursor(pymysql.cursors.DictCursor)
        cursor.execute(prepared, tuple(params or ()))
        wrapped = MySQLCursor(cursor)
        if re.match(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)", prepared, flags=re.I):
            self.total_changes += max(0, wrapped.rowcount)
        return wrapped

    def executemany(self, sql: str, params: Sequence[Sequence[Any]]) -> MySQLCursor:
        prepared = self._store.prepare_sql(sql)
        cursor = self._raw_connection.cursor(pymysql.cursors.DictCursor)
        cursor.executemany(prepared, [tuple(row) for row in params])
        wrapped = MySQLCursor(cursor)
        self.total_changes += max(0, wrapped.rowcount)
        return wrapped

    def commit(self) -> None:
        self._raw_connection.commit()

    def rollback(self) -> None:
        self._raw_connection.rollback()

    def close(self) -> None:
        self._raw_connection.close()


class MySQLStore:
    dialect = "mysql"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.table_prefix = settings.aics_table_prefix
        self._validate_configuration()
        query = f"charset=utf8mb4&connect_timeout={settings.aics_mysql_connect_timeout_seconds}"
        url = (
            f"mysql+pymysql://{quote_plus(settings.aics_mysql_user)}:"
            f"{quote_plus(settings.aics_mysql_password)}@"
            f"{settings.aics_mysql_host}:{settings.aics_mysql_port}/"
            f"{settings.aics_mysql_database}?{query}"
        )
        connect_args: dict[str, Any] = {
            "read_timeout": settings.aics_mysql_read_timeout_seconds,
            "write_timeout": settings.aics_mysql_write_timeout_seconds,
        }
        if settings.aics_mysql_ssl_ca:
            connect_args["ssl"] = {"ca": settings.aics_mysql_ssl_ca}
        elif settings.aics_mysql_ssl_required:
            connect_args["ssl"] = {"check_hostname": False}
        self.engine: Engine = create_engine(
            url,
            pool_size=settings.aics_mysql_pool_size,
            max_overflow=settings.aics_mysql_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=connect_args,
        )

    def _validate_configuration(self) -> None:
        if self.table_prefix != "aics_":
            raise ValueError("AICS_TABLE_PREFIX must be exactly 'aics_'")
        if self.settings.aics_mysql_database != "wecom_cs":
            raise ValueError("AICS_MYSQL_DATABASE must be 'wecom_cs'")
        required = {
            "AICS_MYSQL_HOST": self.settings.aics_mysql_host,
            "AICS_MYSQL_USER": self.settings.aics_mysql_user,
            "AICS_MYSQL_PASSWORD": self.settings.aics_mysql_password,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing MySQL settings: {', '.join(missing)}")

    def prepare_sql(self, sql: str) -> str:
        prepared = map_logical_tables(sql, prefix=self.table_prefix)
        prepared = _translate_mysql_upsert(prepared)
        prepared = _replace_qmark_placeholders(prepared)
        _runtime_sql_guard(prepared, prefix=self.table_prefix)
        return prepared

    def json_text(self, column: str, path: str) -> str:
        return f"JSON_UNQUOTE(JSON_EXTRACT({column}, '{path}'))"

    def initialize(self) -> None:
        with self.connect() as conn:
            ssl_row = conn.execute("SHOW STATUS LIKE 'Ssl_cipher'").fetchone() or {}
            ssl_cipher = str(ssl_row.get("Value") or "")
            if self.settings.aics_mysql_ssl_required and not ssl_cipher:
                raise RuntimeError("AICS MySQL connection is not encrypted; SSL is required")
            database_row = conn.execute("SELECT DATABASE() AS database_name").fetchone() or {}
            if database_row.get("database_name") != self.settings.aics_mysql_database:
                raise RuntimeError("Connected to an unexpected MySQL database")
            rows = conn.execute(
                """
                SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=? AND TABLE_NAME LIKE ?
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                (self.settings.aics_mysql_database, f"{self.table_prefix}%"),
            ).fetchall()
            index_rows = conn.execute(
                """
                SELECT TABLE_NAME AS table_name, INDEX_NAME AS index_name,
                       COLUMN_NAME AS column_name, SEQ_IN_INDEX AS seq_in_index
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=? AND TABLE_NAME LIKE ?
                ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
                """,
                (self.settings.aics_mysql_database, f"{self.table_prefix}%"),
            ).fetchall()
        actual: dict[str, list[str]] = {}
        for row in rows:
            actual.setdefault(str(row["table_name"]), []).append(str(row["column_name"]))
        missing_tables = sorted(set(EXPECTED_ALL_TABLES) - set(actual))
        if missing_tables:
            raise RuntimeError(f"Missing AICS MySQL tables: {', '.join(missing_tables)}")
        mismatches = []
        for table, expected in EXPECTED_COLUMNS.items():
            if not set(expected).issubset(set(actual.get(table, ()))):
                mismatches.append(table)
        if mismatches:
            raise RuntimeError(f"AICS MySQL schema fingerprint mismatch: {', '.join(mismatches)}")
        actual_indexes: dict[str, dict[str, list[str]]] = {}
        for row in index_rows:
            actual_indexes.setdefault(str(row["table_name"]), {}).setdefault(
                str(row["index_name"]),
                [],
            ).append(str(row["column_name"]))
        missing_indexes = []
        for table, indexes in EXPECTED_INDEXES.items():
            for name, columns in indexes.items():
                if tuple(actual_indexes.get(table, {}).get(name, ())) != columns:
                    missing_indexes.append(f"{table}.{name}")
        if missing_indexes:
            raise RuntimeError(
                f"AICS MySQL index fingerprint mismatch: {', '.join(missing_indexes)}"
            )

    @contextmanager
    def connect(self) -> Iterator[MySQLConnection]:
        raw_connection = self.engine.raw_connection()
        connection = MySQLConnection(raw_connection, self)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        self.engine.dispose()
