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
from app.services.storage.mysql_schema import (
    EXPECTED_ALL_TABLES,
    EXPECTED_COLUMNS,
    EXPECTED_INDEXES,
    EXPECTED_UNIQUE_INDEXES,
)
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
            # SQL-mode-dependent backslash escaping is deliberately unsupported
            # in SQL text. Values belong in bound parameters.
            if char == "\\":
                raise RuntimeError("Ambiguous SQL literal escaping is forbidden")
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
        if char == "`":
            end = sql.find("`", index + 1)
            if end < 0 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", sql[index + 1 : end]):
                raise RuntimeError("Unverifiable quoted SQL identifier")
            output.append(sql[index : end + 1])
            index = end + 1
            continue
        if char == "#" or (char == "-" and next_char == "-" and (index + 2 == len(sql) or sql[index + 2].isspace())):
            newline = sql.find("\n", index + 2)
            if newline < 0:
                output.extend(" " * (len(sql) - index))
                break
            output.extend(" " * (newline - index))
            index = newline
            continue
        if char == "/" and next_char == "*":
            if sql[index + 2 : index + 3] in {"!", "+"}:
                raise RuntimeError("Executable SQL comments and hints are forbidden")
            end = sql.find("*/", index + 2)
            if end < 0:
                raise RuntimeError("Unterminated SQL comment")
            output.extend(" " * (end + 2 - index))
            index = end + 2
            continue
        output.append(char)
        index += 1
    if quote:
        raise RuntimeError("Unterminated SQL literal")
    return "".join(output)


def _runtime_sql_guard(sql: str, *, prefix: str) -> None:
    normalized = _sql_without_literals_or_comments(sql).strip()
    # Permit one optional terminator, never a second statement (including reads).
    normalized = normalized.removesuffix(";").rstrip()
    if not normalized or ";" in normalized:
        raise RuntimeError("Runtime SQL must contain exactly one statement")
    ddl_match = re.search(
        rf"\b({'|'.join(sorted(_DDL))})\b",
        normalized,
        flags=re.I,
    )
    if ddl_match:
        raise RuntimeError(f"Runtime DDL is forbidden: {ddl_match.group(1).upper()}")
    statement = re.match(r"[A-Za-z]+\b", normalized)
    kind = statement.group().upper() if statement else ""
    if kind in {"SELECT", "WITH", "SHOW", "EXPLAIN"}:
        # Locking SELECTs are reads. Only recognize the terminal lock clause;
        # never remove arbitrary UPDATE tokens or skip validation of the rest.
        read_sql = re.sub(
            r"\s+FOR\s+(?:UPDATE|SHARE)(?:\s+(?:NOWAIT|SKIP\s+LOCKED))?\s*$",
            "",
            normalized,
            flags=re.I,
        )
        if re.search(r"\b(?:INSERT|UPDATE|DELETE|REPLACE|INTO)\b|:=", read_sql, flags=re.I):
            raise RuntimeError("Runtime read contains an unverified write operation")
        return
    if kind not in _MUTATING:
        raise RuntimeError("Unverified runtime SQL statement")
    identifier = r"(?:`[A-Za-z_][A-Za-z0-9_]*`|[A-Za-z_][A-Za-z0-9_]*)"
    patterns = {
        "INSERT": rf"INSERT\s+(?:IGNORE\s+)?INTO\s+({identifier})(?=\s|\()",
        "REPLACE": rf"REPLACE\s+INTO\s+({identifier})(?=\s|\()",
        "UPDATE": rf"UPDATE\s+({identifier})\s+SET\b",
        "DELETE": rf"DELETE\s+FROM\s+({identifier})(?=\s|$)",
    }
    target = re.match(patterns[kind], normalized, flags=re.I)
    if not target:
        raise RuntimeError("Runtime write target could not be verified")
    table = target.group(1).strip("`")
    if not table.startswith(prefix):
        raise RuntimeError(f"Runtime write to non-AICS table is forbidden: {table}")
    remainder = normalized[target.end() :]
    if (
        kind == "DELETE"
        and remainder.strip()
        and not re.match(
            r"\s+(?:WHERE|ORDER\s+BY|LIMIT)\b",
            remainder,
            flags=re.I,
        )
    ):
        raise RuntimeError("Multi-target or qualified DELETE is forbidden")
    if kind in {"INSERT", "REPLACE"} and not re.match(
        r"\s*(?:\(|VALUES\b|VALUE\b|SET\b|SELECT\b)",
        remainder,
        flags=re.I,
    ):
        raise RuntimeError("Runtime write shape could not be verified")
    remainder = (
        re.sub(
            r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b",
            "ON DUPLICATE KEY SET",
            remainder,
            flags=re.I,
            count=1 if kind == "INSERT" else 0,
        )
        if kind == "INSERT"
        else remainder
    )
    if re.search(rf"\b({'|'.join(sorted(_MUTATING))})\b", remainder, flags=re.I):
        raise RuntimeError("Additional runtime write operation could not be verified")


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
        prepared = prepared.replace("__source_customer_member_relations__", "customer_member_relations")
        prepared = prepared.replace("__source_archive_messages__", "messages")
        prepared = _translate_mysql_upsert(prepared)
        prepared = _replace_qmark_placeholders(prepared)
        _runtime_sql_guard(prepared, prefix=self.table_prefix)
        return prepared

    @staticmethod
    def source_table(name: str) -> str:
        tables = {
            "customer_member_relations": "__source_customer_member_relations__",
            "archive_messages": "__source_archive_messages__",
        }
        if name not in tables:
            raise ValueError(f"Unsupported source table: {name}")
        return tables[name]

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
                       COLUMN_NAME AS column_name, SEQ_IN_INDEX AS seq_in_index,
                       NON_UNIQUE AS non_unique
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
        actual_unique_indexes: dict[str, set[str]] = {}
        for row in index_rows:
            table_name = str(row["table_name"])
            index_name = str(row["index_name"])
            actual_indexes.setdefault(table_name, {}).setdefault(
                index_name,
                [],
            ).append(str(row["column_name"]))
            if int(row.get("non_unique", 1) or 0) == 0:
                actual_unique_indexes.setdefault(table_name, set()).add(index_name)
        missing_indexes = []
        for table, indexes in EXPECTED_INDEXES.items():
            for name, columns in indexes.items():
                if tuple(actual_indexes.get(table, {}).get(name, ())) != columns:
                    missing_indexes.append(f"{table}.{name}")
                    continue
                expected_unique = name in EXPECTED_UNIQUE_INDEXES.get(table, set())
                actual_unique = name in actual_unique_indexes.get(table, set())
                if expected_unique != actual_unique:
                    missing_indexes.append(f"{table}.{name}:uniqueness")
        if missing_indexes:
            raise RuntimeError(f"AICS MySQL index fingerprint mismatch: {', '.join(missing_indexes)}")

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
