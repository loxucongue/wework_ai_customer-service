from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services.storage.mirrored_store import MirroredStore
from app.services.storage.mysql_schema import (
    EXPECTED_ALL_TABLES,
    EXPECTED_COLUMNS,
    EXPECTED_TABLES,
    VERSION_TABLE,
)
from app.services.storage.mysql_store import MySQLStore, _runtime_sql_guard
from app.services.storage.repositories import AppRepository
from app.services.storage.store_base import LOGICAL_TABLES, map_logical_tables
from app.services.storage.sqlite_store import SQLiteStore


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        aics_table_prefix="aics_",
        aics_mysql_database="wecom_cs",
        aics_mysql_host="127.0.0.1",
        aics_mysql_port=3306,
        aics_mysql_user="test",
        aics_mysql_password="test",
        aics_mysql_ssl_required=False,
        aics_mysql_ssl_ca=None,
        aics_mysql_connect_timeout_seconds=1,
        aics_mysql_read_timeout_seconds=1,
        aics_mysql_write_timeout_seconds=1,
        aics_mysql_pool_size=1,
        aics_mysql_max_overflow=0,
    )


def test_mysql_schema_contains_only_expected_aics_tables() -> None:
    assert len(EXPECTED_TABLES) == 17
    assert len(EXPECTED_ALL_TABLES) == 18
    assert VERSION_TABLE == "aics_schema_version"
    assert all(table.startswith("aics_") for table in EXPECTED_ALL_TABLES)
    assert {table.removeprefix("aics_") for table in EXPECTED_TABLES} == set(LOGICAL_TABLES)
    assert "active_send_once_key" in EXPECTED_COLUMNS["aics_sop_send_tasks"]


def test_migration_contract_covers_every_runtime_table() -> None:
    spec = importlib.util.spec_from_file_location(
        "aics_migration_contract",
        Path("ai_paths/scripts/migrate_sqlite_to_mysql.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert set(module.ACTIVE_TABLES) == set(LOGICAL_TABLES)
    assert module.JSON_COLUMNS["first_day_outreach_runs"] == (
        "input_snapshot_json",
        "workflow_json",
        "final_plan_json",
    )
    assert module.JSON_COLUMNS["strategy_data_outbox"] == (
        "payload_json",
        "response_json",
    )


def test_compressed_loader_is_probed_before_schema_upgrade_and_reset() -> None:
    source = Path("ai_paths/scripts/migrate_sqlite_to_mysql.py").read_text(
        encoding="utf-8"
    )

    probe = source.index("loader_probe = _mysql_compressed_loader_connect(settings)")
    upgrade = source.index("_run_alembic()", probe)
    reset = source.index("_reset_aics_data(target)", probe)

    assert probe < upgrade < reset


def test_migration_integrity_digest_is_row_framed_and_chunked() -> None:
    spec = importlib.util.spec_from_file_location(
        "aics_migration_digest",
        Path("ai_paths/scripts/migrate_sqlite_to_mysql.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rows = [{"id": "a", "value": "bc"}, {"id": "ab", "value": "c"}]
    first = module._digest_rows(rows, ["id", "value"])
    second = module._digest_rows(list(rows), ["id", "value"])
    reversed_rows = module._digest_rows(list(reversed(rows)), ["id", "value"])

    assert first == second
    assert first["rows"] == 2
    assert len(first["chunks"]) == 1
    assert first["sha256"] != reversed_rows["sha256"]


def test_migration_batches_are_bounded_by_rows_and_bytes() -> None:
    spec = importlib.util.spec_from_file_location(
        "aics_migration_batches",
        Path("ai_paths/scripts/migrate_sqlite_to_mysql.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rows = [
        {"id": "1", "payload": "a" * 8},
        {"id": "2", "payload": "b" * 8},
        {"id": "3", "payload": "c" * 8},
    ]
    by_bytes = list(
        module._chunks(rows, 100, columns=["id", "payload"], max_bytes=10)
    )
    by_rows = list(
        module._chunks(rows, 2, columns=["id", "payload"], max_bytes=1000)
    )

    assert [len(batch) for batch in by_bytes] == [1, 1, 1]
    assert [len(batch) for batch in by_rows] == [2, 1]


def test_migration_delta_timestamp_columns_cover_every_active_table() -> None:
    spec = importlib.util.spec_from_file_location(
        "aics_migration_delta_contract",
        Path("ai_paths/scripts/migrate_sqlite_to_mysql.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert set(module.DELTA_TIMESTAMP_COLUMNS) == set(module.ACTIVE_TABLES)
    for table, column in module.DELTA_TIMESTAMP_COLUMNS.items():
        assert column in EXPECTED_COLUMNS[f"aics_{table}"]


def test_table_mapper_does_not_rewrite_string_literals() -> None:
    sql = (
        "SELECT * FROM conversations "
        "WHERE source='conversations' AND note=\"messages\" "
        "AND id IN (SELECT conversation_id FROM messages)"
    )
    mapped = map_logical_tables(sql, prefix="aics_")
    assert "FROM aics_conversations" in mapped
    assert "FROM aics_messages" in mapped
    assert "'conversations'" in mapped
    assert '"messages"' in mapped


def test_mysql_store_translates_placeholders_and_sqlite_upserts() -> None:
    store = MySQLStore(_settings())
    try:
        sql = store.prepare_sql(
            """
            INSERT INTO customer_memory (customer_id, portrait)
            VALUES (?, '?')
            ON CONFLICT(customer_id) DO UPDATE SET portrait=excluded.portrait
            """
        )
        assert "INSERT INTO aics_customer_memory" in sql
        assert "VALUES (%s, '?')" in sql
        assert "ON DUPLICATE KEY UPDATE portrait=VALUES(portrait)" in sql

        ignored = store.prepare_sql(
            "INSERT OR IGNORE INTO sop_events (event_id) VALUES (?)"
        )
        assert ignored == "INSERT IGNORE INTO aics_sop_events (event_id) VALUES (%s)"

        replaced = store.prepare_sql(
            "INSERT OR REPLACE INTO runs (request_id) VALUES (?)"
        )
        assert replaced == "REPLACE INTO aics_runs (request_id) VALUES (%s)"

        like_query = store.prepare_sql(
            "SELECT event_id FROM sop_events WHERE event_type LIKE '%加微%' AND status=?"
        )
        assert "LIKE '%%加微%%'" in like_query
        assert like_query.endswith("status=%s")
    finally:
        store.close()


def test_runtime_guard_rejects_ddl_and_platform_writes() -> None:
    with pytest.raises(RuntimeError, match="Runtime DDL"):
        _runtime_sql_guard("ALTER TABLE aics_runs ADD COLUMN bad INT", prefix="aics_")
    with pytest.raises(RuntimeError, match="non-AICS"):
        _runtime_sql_guard("UPDATE conversations SET title='x'", prefix="aics_")
    with pytest.raises(RuntimeError, match="non-AICS"):
        _runtime_sql_guard("DELETE FROM platform_customers", prefix="aics_")
    with pytest.raises(RuntimeError, match="non-AICS"):
        _runtime_sql_guard(
            "WITH chosen AS (SELECT 1) DELETE FROM platform_customers",
            prefix="aics_",
        )
    with pytest.raises(RuntimeError, match="Runtime DDL"):
        _runtime_sql_guard(
            "/* migration must not run here */ ALTER TABLE aics_runs ADD COLUMN bad INT",
            prefix="aics_",
        )
    _runtime_sql_guard(
        "UPDATE aics_conversations SET title='do not DELETE FROM platform_customers'",
        prefix="aics_",
    )
    _runtime_sql_guard("UPDATE aics_conversations SET title='x'", prefix="aics_")


class _Cursor:
    rowcount = 1

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


class _Connection:
    def __init__(self, calls: list[str], label: str):
        self.calls = calls
        self.label = label
        self.total_changes = 0

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        self.calls.append(f"{self.label}:execute:{sql.strip().split()[0].upper()}")
        return _Cursor()

    def executemany(self, sql: str, params: Any) -> _Cursor:
        self.calls.append(f"{self.label}:executemany")
        return _Cursor()


class _Store:
    dialect = "test"

    def __init__(self, calls: list[str], label: str):
        self.calls = calls
        self.label = label

    def initialize(self) -> None:
        self.calls.append(f"{self.label}:initialize")

    @contextmanager
    def connect(self):
        self.calls.append(f"{self.label}:begin")
        connection = _Connection(self.calls, self.label)
        try:
            yield connection
        except Exception:
            self.calls.append(f"{self.label}:rollback")
            raise
        else:
            self.calls.append(f"{self.label}:commit")

    def close(self) -> None:
        self.calls.append(f"{self.label}:close")


def test_mirror_replays_only_writes_after_primary_commit() -> None:
    calls: list[str] = []
    store = MirroredStore(_Store(calls, "primary"), _Store(calls, "mirror"))  # type: ignore[arg-type]
    with store.connect() as connection:
        connection.execute("SELECT * FROM conversations")
        connection.execute("UPDATE conversations SET title=? WHERE id=?", ("x", "1"))

    assert calls == [
        "primary:begin",
        "primary:execute:SELECT",
        "primary:execute:UPDATE",
        "primary:commit",
        "mirror:begin",
        "mirror:execute:UPDATE",
        "mirror:commit",
    ]


def test_mysql_configuration_rejects_wrong_prefix_and_database() -> None:
    wrong_prefix = _settings()
    wrong_prefix.aics_table_prefix = "ai_"
    with pytest.raises(ValueError, match="AICS_TABLE_PREFIX"):
        MySQLStore(wrong_prefix)

    wrong_database = _settings()
    wrong_database.aics_mysql_database = "other"
    with pytest.raises(ValueError, match="AICS_MYSQL_DATABASE"):
        MySQLStore(wrong_database)


def test_migration_source_manifest_uses_14_day_trace_window(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    store = SQLiteStore(SimpleNamespace(db_path=database))
    store.initialize()
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO conversations
                (id, customer_id, external_userid, corp_id, user_id, wechat, title, created_at, updated_at)
            VALUES ('c1', 'u1', 'e1', 'corp', 'staff', 'wx', '', '2026-07-29T00:00:00+00:00', '2026-07-29T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO runs
                (request_id, conversation_id, customer_id, created_at)
            VALUES ('r1', 'c1', 'u1', '2026-07-29T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO node_traces
                (id, request_id, node_name, created_at)
            VALUES ('recent', 'r1', 'planner', '2999-01-01T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO first_day_outreach_runs
                (workflow_run_id, input_snapshot_json, workflow_json,
                 final_plan_json, started_at, created_at, updated_at)
            VALUES ('first-day-1', '{}', '{}', '{}',
                    '2026-07-29T00:00:00+00:00',
                    '2026-07-29T00:00:00+00:00',
                    '2026-07-29T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO node_traces
                (id, request_id, node_name, created_at)
            VALUES ('old', 'r1', 'planner', '2000-01-01T00:00:00+00:00')
            """
        )

    spec = importlib.util.spec_from_file_location(
        "aics_migration",
        Path("ai_paths/scripts/migrate_sqlite_to_mysql.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        manifest = module._source_manifest(connection, trace_days=14)
    finally:
        connection.close()
    assert manifest["tables"]["runs"]["rows"] == 1
    assert manifest["tables"]["node_traces"]["rows"] == 1
    assert manifest["tables"]["first_day_outreach_runs"]["rows"] == 1
    assert manifest["tables"]["first_day_outreach_runs"]["integrity"]["rows"] == 1
    assert manifest["tables"]["first_day_outreach_runs"]["integrity"]["sha256"]
    assert manifest["json_errors"] == []
    assert manifest["relation_errors"] == []


@pytest.mark.skipif(
    os.getenv("AICS_TEST_MYSQL_ENABLED") != "true",
    reason="set AICS_TEST_MYSQL_ENABLED=true for the isolated MySQL contract",
)
def test_isolated_mysql_repository_and_mirror_contract(tmp_path: Path) -> None:
    host = os.getenv("AICS_TEST_MYSQL_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        pytest.fail("The opt-in storage contract only accepts an isolated loopback MySQL")
    settings = SimpleNamespace(
        aics_table_prefix="aics_",
        aics_mysql_database="wecom_cs",
        aics_mysql_host=host,
        aics_mysql_port=int(os.getenv("AICS_TEST_MYSQL_PORT", "3306")),
        aics_mysql_user=os.environ["AICS_TEST_MYSQL_USER"],
        aics_mysql_password=os.environ["AICS_TEST_MYSQL_PASSWORD"],
        aics_mysql_ssl_required=True,
        aics_mysql_ssl_ca=os.getenv("AICS_TEST_MYSQL_SSL_CA", ""),
        aics_mysql_connect_timeout_seconds=5,
        aics_mysql_read_timeout_seconds=5,
        aics_mysql_write_timeout_seconds=5,
        aics_mysql_pool_size=1,
        aics_mysql_max_overflow=0,
    )
    primary = MySQLStore(settings)
    mirror = SQLiteStore(SimpleNamespace(db_path=tmp_path / "mirror.db"))
    store = MirroredStore(primary, mirror)
    suffix = uuid4().hex
    customer_id = f"mysql-contract-{suffix}"
    old_run_id = f"old-{suffix}"
    new_run_id = f"new-{suffix}"
    try:
        store.initialize()
        repository = AppRepository(store)
        repository.save_memory(
            customer_id,
            {
                "portrait": {"contract_marker": suffix},
                "basic_info": {"source": "isolated_mysql"},
            },
        )
        assert repository.load_memory(customer_id)["portrait"]["contract_marker"] == suffix
        with mirror.connect() as connection:
            mirrored = connection.execute(
                "SELECT portrait FROM customer_memory WHERE customer_id=?",
                (customer_id,),
            ).fetchone()
        assert mirrored is not None

        with primary.connect() as connection:
            connection.execute(
                "INSERT INTO runs (request_id, customer_id, created_at) VALUES (?, ?, ?)",
                (old_run_id, customer_id, "2000-01-01T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO runs (request_id, customer_id, created_at) VALUES (?, ?, ?)",
                (new_run_id, customer_id, "2999-01-01T00:00:00+00:00"),
            )
            connection.execute(
                """
                INSERT INTO node_traces (id, request_id, node_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (old_run_id, old_run_id, "planner", "2000-01-01T00:00:00+00:00"),
            )
            connection.execute(
                """
                INSERT INTO node_traces (id, request_id, node_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (new_run_id, new_run_id, "planner", "2999-01-01T00:00:00+00:00"),
            )
        pruned = AppRepository(primary).prune_runtime_history(trace_days=14, run_days=90)
        assert pruned["node_traces"] >= 1
        assert pruned["runs"] >= 1
        with primary.connect() as connection:
            assert connection.execute(
                "SELECT request_id FROM runs WHERE request_id=?",
                (old_run_id,),
            ).fetchone() is None
            assert connection.execute(
                "SELECT request_id FROM runs WHERE request_id=?",
                (new_run_id,),
            ).fetchone()
    finally:
        with primary.connect() as connection:
            connection.execute(
                "DELETE FROM node_traces WHERE request_id IN (?, ?)",
                (old_run_id, new_run_id),
            )
            connection.execute(
                "DELETE FROM runs WHERE request_id IN (?, ?)",
                (old_run_id, new_run_id),
            )
            connection.execute(
                "DELETE FROM history_events WHERE customer_id=?",
                (customer_id,),
            )
            connection.execute(
                "DELETE FROM customer_memory WHERE customer_id=?",
                (customer_id,),
            )
        store.close()
