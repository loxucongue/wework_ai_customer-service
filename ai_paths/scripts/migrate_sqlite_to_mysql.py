from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from alembic import command
from alembic.config import Config
import pymysql


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.services.storage.mysql_schema import EXPECTED_COLUMNS, EXPECTED_TABLES  # noqa: E402


INTEGRITY_CHUNK_ROWS = 1000
GENERATED_TARGET_COLUMNS = {"active_send_once_key"}
RELATIONSHIPS = (
    ("messages", "conversation_id", "conversations", "id"),
    ("runs", "conversation_id", "conversations", "id"),
    ("node_traces", "request_id", "runs", "request_id"),
    ("history_events", "customer_id", "customer_memory", "customer_id"),
    ("outreach_tasks", "plan_id", "outreach_plans", "id"),
    ("sop_send_tasks", "event_id", "sop_events", "event_id"),
    ("message_dispatch_items", "dispatch_id", "message_dispatches", "id"),
    ("message_delivery_events", "dispatch_id", "message_dispatches", "id"),
)

DELTA_TIMESTAMP_COLUMNS = {
    "conversations": "updated_at",
    "messages": "created_at",
    "runs": "created_at",
    "node_traces": "created_at",
    "customer_memory": "updated_at",
    "history_events": "created_at",
    "outreach_sop_plans": "updated_at",
    "outreach_plans": "updated_at",
    "outreach_tasks": "updated_at",
    "outreach_events": "created_at",
    "first_day_outreach_runs": "updated_at",
    "sop_events": "updated_at",
    "sop_send_tasks": "updated_at",
    "message_dispatches": "updated_at",
    "message_dispatch_items": "updated_at",
    "message_delivery_events": "received_at",
}


ACTIVE_TABLES = (
    "conversations",
    "messages",
    "runs",
    "node_traces",
    "customer_memory",
    "history_events",
    "outreach_sop_plans",
    "outreach_plans",
    "outreach_tasks",
    "outreach_events",
    "first_day_outreach_runs",
    "sop_events",
    "sop_send_tasks",
    "message_dispatches",
    "message_dispatch_items",
    "message_delivery_events",
)
LEGACY_TABLES = (
    "kb_sync_records",
    "pricing_question_rules",
    "pricing_rules",
    "project_catalog",
    "project_pricing_rules",
    "term_rewrite_rules",
)
JSON_COLUMNS = {
    "messages": ("reply_messages",),
    "runs": ("input_snapshot", "output_snapshot", "intents", "tags", "token_usage"),
    "node_traces": ("input_snapshot", "output_snapshot", "tool_calls"),
    "customer_memory": ("portrait", "basic_info"),
    "history_events": ("facts",),
    "outreach_sop_plans": ("filters_json", "last_run_summary_json"),
    "outreach_plans": ("source_snapshot",),
    "outreach_tasks": ("content_sources", "reply_messages_json"),
    "outreach_events": ("payload_json",),
    "first_day_outreach_runs": (
        "input_snapshot_json",
        "workflow_json",
        "final_plan_json",
    ),
    "sop_events": ("raw_payload_json",),
    "sop_send_tasks": ("reply_messages_json", "send_payload_json", "send_response_json"),
    "message_dispatches": ("reply_messages_json", "source_context_json"),
    "message_delivery_events": ("raw_payload_json",),
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate AI Paths SQLite data into isolated aics_* tables.")
    parser.add_argument(
        "mode",
        choices=("preflight", "rehearse", "resume", "apply", "delta", "verify"),
    )
    parser.add_argument("--sqlite-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "migration")
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--max-batch-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--confirm-service-stopped", action="store_true")
    parser.add_argument("--confirm-static-snapshot", action="store_true")
    parser.add_argument("--reset-existing-aics-data", action="store_true")
    parser.add_argument("--compressed-loader", action="store_true")
    parser.add_argument("--source-manifest-path", type=Path)
    parser.add_argument("--expected-sqlite-sha256", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--tables", default="")
    parser.add_argument("--skip-full-verify", action="store_true")
    return parser.parse_args()


def _mysql_connect(settings: Settings) -> pymysql.Connection:
    ssl: dict[str, Any] | None = None
    if settings.aics_mysql_ssl_ca:
        ssl = {"ca": settings.aics_mysql_ssl_ca}
    elif settings.aics_mysql_ssl_required:
        ssl = {"check_hostname": False}
    connection = pymysql.connect(
        host=settings.aics_mysql_host,
        port=settings.aics_mysql_port,
        user=settings.aics_mysql_user,
        password=settings.aics_mysql_password,
        database=settings.aics_mysql_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=settings.aics_mysql_connect_timeout_seconds,
        read_timeout=settings.aics_mysql_read_timeout_seconds,
        write_timeout=settings.aics_mysql_write_timeout_seconds,
        ssl=ssl,
        autocommit=False,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS database_name")
        if cursor.fetchone()["database_name"] != "wecom_cs":
            raise RuntimeError("Migration connected to an unexpected database")
        cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
        row = cursor.fetchone() or {}
        if settings.aics_mysql_ssl_required and not str(row.get("Value") or ""):
            raise RuntimeError("Migration refuses an unencrypted MySQL connection")
    return connection


def _sqlite_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _mysql_compressed_loader_connect(settings: Settings) -> Any:
    try:
        import mysql.connector  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "--compressed-loader requires mysql-connector-python"
        ) from exc
    kwargs: dict[str, Any] = {
        "host": settings.aics_mysql_host,
        "port": settings.aics_mysql_port,
        "user": settings.aics_mysql_user,
        "password": settings.aics_mysql_password,
        "database": settings.aics_mysql_database,
        "charset": "utf8mb4",
        "connection_timeout": settings.aics_mysql_connect_timeout_seconds,
        "read_timeout": settings.aics_mysql_read_timeout_seconds,
        "write_timeout": settings.aics_mysql_write_timeout_seconds,
        "compress": True,
        "autocommit": False,
    }
    if settings.aics_mysql_ssl_ca:
        kwargs["ssl_ca"] = settings.aics_mysql_ssl_ca
        kwargs["ssl_verify_cert"] = True
    elif not settings.aics_mysql_ssl_required:
        kwargs["ssl_disabled"] = True
    return mysql.connector.connect(**kwargs)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _migration_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    source_columns = [
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]
    target_columns = [
        name
        for name in EXPECTED_COLUMNS[f"aics_{table}"]
        if name not in GENERATED_TARGET_COLUMNS
    ]
    return [name for name in source_columns if name in target_columns]


def _primary_key_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = [
        row
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        if int(row["pk"] or 0)
    ]
    return [str(row["name"]) for row in sorted(columns, key=lambda row: int(row["pk"]))]


def _digest_rows(rows: Iterable[Any], columns: list[str]) -> dict[str, Any]:
    total = hashlib.sha256()
    chunk = hashlib.sha256()
    chunks: list[str] = []
    row_count = 0
    chunk_count = 0
    for row in rows:
        payload = json.dumps(
            [row[column] for column in columns],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        framed = len(payload).to_bytes(8, "big") + payload
        total.update(framed)
        chunk.update(framed)
        row_count += 1
        chunk_count += 1
        if chunk_count == INTEGRITY_CHUNK_ROWS:
            chunks.append(chunk.hexdigest())
            chunk = hashlib.sha256()
            chunk_count = 0
    if chunk_count:
        chunks.append(chunk.hexdigest())
    return {"rows": row_count, "sha256": total.hexdigest(), "chunks": chunks}


def _source_integrity(
    connection: sqlite3.Connection,
    table: str,
    columns: list[str],
    primary_key_columns: list[str],
    *,
    where: str,
    params: tuple[Any, ...],
) -> dict[str, Any]:
    selected = ",".join(f'"{column}"' for column in columns)
    order_columns = primary_key_columns or ["rowid"]
    ordered = ",".join(
        "rowid" if column == "rowid" else f'CAST("{column}" AS BLOB)'
        for column in order_columns
    )
    rows = connection.execute(
        f'SELECT {selected} FROM "{table}"{where} ORDER BY {ordered}',
        params,
    )
    return _digest_rows(rows, columns)


def _target_integrity(
    cursor: pymysql.cursors.Cursor,
    table: str,
    columns: list[str],
    primary_key_columns: list[str],
) -> dict[str, Any]:
    selected = ",".join(f"`{column}`" for column in columns)
    ordered = ",".join(f"BINARY(`{column}`)" for column in primary_key_columns)
    cursor.execute(f"SELECT {selected} FROM `aics_{table}` ORDER BY {ordered}")

    def rows() -> Iterable[Any]:
        while True:
            batch = cursor.fetchmany(500)
            if not batch:
                return
            yield from batch

    return _digest_rows(rows(), columns)


def _platform_fingerprint(connection: pymysql.Connection) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, COALESCE(TABLE_ROWS, 0) AS table_rows
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' AND TABLE_NAME NOT LIKE 'aics\\_%%'
            ORDER BY TABLE_NAME
            """,
            ("wecom_cs",),
        )
        tables = cursor.fetchall()
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COALESCE(COLUMN_DEFAULT, '') AS column_default,
                   EXTRA, ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME NOT LIKE 'aics\\_%%'
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            ("wecom_cs",),
        )
        columns = cursor.fetchall()
        cursor.execute(
            """
            SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, COALESCE(SUB_PART, 0) AS sub_part
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME NOT LIKE 'aics\\_%%'
            ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
            """,
            ("wecom_cs",),
        )
        indexes = cursor.fetchall()
    structural_tables = [
        {key: value for key, value in row.items() if key != "table_rows"}
        for row in tables
    ]
    canonical = json.dumps(
        {"tables": structural_tables, "columns": columns, "indexes": indexes},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return {
        "table_count": len(tables),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "tables": tables,
        "estimated_rows": {
            str(row["TABLE_NAME"]): int(row["table_rows"] or 0)
            for row in tables
        },
    }


def _source_manifest(connection: sqlite3.Connection, *, trace_days: int) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=trace_days)).isoformat()
    manifest: dict[str, Any] = {
        "trace_cutoff": cutoff,
        "tables": {},
        "json_errors": [],
        "relation_errors": [],
    }
    existing = {
        str(row["name"])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for table in ACTIVE_TABLES:
        if table not in existing:
            raise RuntimeError(f"Missing SQLite source table: {table}")
        where = " WHERE created_at>=?" if table == "node_traces" else ""
        params = (cutoff,) if where else ()
        count = connection.execute(f"SELECT COUNT(*) AS count FROM {table}{where}", params).fetchone()["count"]
        primary_key_columns = _primary_key_columns(connection, table)
        migration_columns = _migration_columns(connection, table)
        if not primary_key_columns:
            raise RuntimeError(f"Migration requires a primary key: {table}")
        distinct_count = int(count)
        if primary_key_columns:
            key = primary_key_columns[0]
            distinct_count = connection.execute(
                f"SELECT COUNT(DISTINCT {key}) AS count FROM {table}{where}",
                params,
            ).fetchone()["count"]
        manifest["tables"][table] = {
            "rows": int(count),
            "distinct_primary_keys": int(distinct_count),
            "primary_key": primary_key_columns,
            "columns": migration_columns,
        }
        for column in JSON_COLUMNS.get(table, ()):
            rows = connection.execute(
                f"SELECT rowid, {column} FROM {table}{where}",
                params,
            )
            for row in rows:
                try:
                    json.loads(str(row[column] or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    manifest["json_errors"].append(
                        {"table": table, "column": column, "rowid": int(row["rowid"])}
                    )
        integrity = _source_integrity(
            connection,
            table,
            migration_columns,
            primary_key_columns,
            where=where,
            params=params,
        )
        if integrity["rows"] != int(count):
            raise RuntimeError(f"Source changed while building manifest: {table}")
        manifest["tables"][table]["integrity"] = integrity
    for child, child_key, parent, parent_key in RELATIONSHIPS:
        row = connection.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM "{child}" c
            LEFT JOIN "{parent}" p ON p."{parent_key}"=c."{child_key}"
            WHERE c."{child_key}"<>'' AND p."{parent_key}" IS NULL
            """
        ).fetchone()
        count = int(row["count"] or 0)
        if count:
            manifest["relation_errors"].append(
                f"orphan:{child}.{child_key}:{parent}.{parent_key}:{count}"
            )
    return manifest


def _backup_sqlite(source: sqlite3.Connection, source_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = output_dir / f"ai_paths_{timestamp}.sqlite3"
    source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
    finally:
        target.close()
    return backup_path


def _archive_legacy(source: sqlite3.Connection, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"legacy_config_tables_{timestamp}.json.gz"
    existing = {
        str(row["name"])
        for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    payload = {}
    for table in LEGACY_TABLES:
        payload[table] = (
            [dict(row) for row in source.execute(f"SELECT * FROM {table}").fetchall()]
            if table in existing
            else []
        )
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def _run_alembic() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")


def _chunks(
    rows: Iterable[sqlite3.Row],
    size: int,
    *,
    columns: list[str],
    max_bytes: int,
) -> Iterable[list[sqlite3.Row]]:
    batch: list[sqlite3.Row] = []
    batch_bytes = 0
    for row in rows:
        row_bytes = sum(
            len(value) if isinstance(value, bytes) else len(str(value or "").encode("utf-8"))
            for value in (row[column] for column in columns)
        )
        if batch and (len(batch) >= size or batch_bytes + row_bytes > max_bytes):
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(row)
        batch_bytes += row_bytes
    if batch:
        yield batch


def _reset_aics_data(target: pymysql.Connection) -> None:
    with target.cursor() as cursor:
        for table in reversed(ACTIVE_TABLES):
            cursor.execute(f"TRUNCATE TABLE `aics_{table}`")
    target.commit()


def _migrate_table(
    source: sqlite3.Connection,
    target: Any,
    table: str,
    *,
    batch_size: int,
    max_batch_bytes: int,
    where: str = "",
    params: tuple[Any, ...] = (),
    upsert: bool = False,
    skip_primary_keys: set[tuple[Any, ...]] | None = None,
) -> int:
    columns = _migration_columns(source, table)
    source_rows = source.execute(f"SELECT * FROM {table}{where}", params)
    if skip_primary_keys:
        primary_key_columns = _primary_key_columns(source, table)
        source_rows = (
            row
            for row in source_rows
            if tuple(row[column] for column in primary_key_columns)
            not in skip_primary_keys
        )
    placeholders = ",".join("%s" for _ in columns)
    sql = (
        f"INSERT INTO `aics_{table}` ({','.join(f'`{column}`' for column in columns)}) "
        f"VALUES ({placeholders})"
    )
    if upsert:
        updates = ",".join(
            f"`{column}`=VALUES(`{column}`)" for column in columns
        )
        sql = f"{sql} ON DUPLICATE KEY UPDATE {updates}"
    migrated = 0
    with target.cursor() as cursor:
        # PyMySQL otherwise splits executemany statements at roughly 1 MiB,
        # which multiplies round trips when ECS and RDS are in different regions.
        if hasattr(cursor, "max_stmt_length"):
            cursor.max_stmt_length = max_batch_bytes + 1024 * 1024
        for batch in _chunks(
            source_rows,
            batch_size,
            columns=columns,
            max_bytes=max_batch_bytes,
        ):
            cursor.executemany(sql, [tuple(row[column] for column in columns) for row in batch])
            target.commit()
            migrated += len(batch)
    return migrated


def _target_primary_keys(
    target: pymysql.Connection,
    table: str,
    primary_key_columns: list[str],
) -> set[tuple[Any, ...]]:
    selected = ",".join(f"`{column}`" for column in primary_key_columns)
    with target.cursor() as cursor:
        cursor.execute(f"SELECT {selected} FROM `aics_{table}`")
        return {
            tuple(row[column] for column in primary_key_columns)
            for row in cursor.fetchall()
        }


def _verify(
    source_manifest: dict[str, Any],
    target: pymysql.Connection,
    platform_before: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {"tables": {}, "errors": []}
    with target.cursor() as cursor:
        for table in ACTIVE_TABLES:
            cursor.execute(f"SELECT COUNT(*) AS count FROM `aics_{table}`")
            target_count = int(cursor.fetchone()["count"])
            source_count = int(source_manifest["tables"][table]["rows"])
            source_table = source_manifest["tables"][table]
            target_integrity = _target_integrity(
                cursor,
                table,
                list(source_table["columns"]),
                list(source_table["primary_key"]),
            )
            source_integrity = source_table["integrity"]
            result["tables"][table] = {
                "source": source_count,
                "target": target_count,
                "source_sha256": source_integrity["sha256"],
                "target_sha256": target_integrity["sha256"],
            }
            if source_count != target_count:
                result["errors"].append(
                    f"row_count_mismatch:{table}:{source_count}:{target_count}"
                )
            if source_integrity["sha256"] != target_integrity["sha256"]:
                source_chunks = source_integrity["chunks"]
                target_chunks = target_integrity["chunks"]
                mismatched_chunks = [
                    index
                    for index in range(max(len(source_chunks), len(target_chunks)))
                    if index >= len(source_chunks)
                    or index >= len(target_chunks)
                    or source_chunks[index] != target_chunks[index]
                ]
                result["tables"][table]["mismatched_chunks"] = mismatched_chunks
                result["errors"].append(f"content_hash_mismatch:{table}")
        for child, child_key, parent, parent_key in RELATIONSHIPS:
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM `aics_{child}` c
                LEFT JOIN `aics_{parent}` p
                  ON p.`{parent_key}`=c.`{child_key}`
                WHERE c.`{child_key}`<>'' AND p.`{parent_key}` IS NULL
                """
            )
            count = int(cursor.fetchone()["count"])
            if count:
                result["errors"].append(
                    f"orphan:{child}.{child_key}:{parent}.{parent_key}:{count}"
                )
    platform_after = _platform_fingerprint(target)
    result["platform_before_sha256"] = platform_before["sha256"]
    result["platform_after_sha256"] = platform_after["sha256"]
    if platform_before["sha256"] != platform_after["sha256"]:
        result["errors"].append("platform_schema_or_index_fingerprint_changed")
    return result


def main() -> int:
    args = _args()
    settings = Settings()
    sqlite_path = args.sqlite_path or settings.db_path
    if not sqlite_path.exists():
        raise FileNotFoundError(sqlite_path)
    output_dir = args.output_dir.resolve()
    source = _sqlite_connect(sqlite_path)
    target = _mysql_connect(settings)
    try:
        platform_before = _platform_fingerprint(target)
        if args.source_manifest_path:
            if not args.expected_sqlite_sha256:
                raise RuntimeError(
                    "--source-manifest-path requires --expected-sqlite-sha256"
                )
            actual_sha256 = _sha256(sqlite_path)
            if actual_sha256 != args.expected_sqlite_sha256:
                raise RuntimeError("Cached source manifest snapshot checksum mismatch")
            cached = json.loads(args.source_manifest_path.read_text(encoding="utf-8"))
            source_manifest = cached.get("source", cached)
            if not isinstance(source_manifest.get("tables"), dict):
                raise RuntimeError("Cached source manifest is invalid")
        else:
            source_manifest = _source_manifest(
                source,
                trace_days=max(1, settings.aics_trace_retention_days),
            )
        report: dict[str, Any] = {
            "mode": args.mode,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sqlite_path": str(sqlite_path.resolve()),
            "source": source_manifest,
            "platform_before": platform_before,
        }
        if source_manifest["json_errors"]:
            raise RuntimeError(
                f"Invalid source JSON rows: {len(source_manifest['json_errors'])}"
            )
        if source_manifest["relation_errors"]:
            raise RuntimeError(
                f"Invalid source relationships: {len(source_manifest['relation_errors'])}"
            )
        if args.mode == "preflight":
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "preflight.json"
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(path)
            return 0
        if args.mode in {"rehearse", "resume", "apply", "delta"}:
            if args.mode in {"apply", "delta"} and not args.confirm_service_stopped:
                raise RuntimeError(
                    f"{args.mode} requires --confirm-service-stopped"
                )
            if args.mode == "rehearse" and not args.confirm_static_snapshot:
                raise RuntimeError("rehearse requires --confirm-static-snapshot")
            if args.mode == "resume" and not args.confirm_static_snapshot:
                raise RuntimeError("resume requires --confirm-static-snapshot")
            if args.mode in {"rehearse", "apply"} and not args.reset_existing_aics_data:
                raise RuntimeError("data load requires --reset-existing-aics-data")
            if args.mode == "delta" and not args.since:
                raise RuntimeError("delta requires --since")
            if args.mode in {"apply", "delta"}:
                backup = _backup_sqlite(source, sqlite_path, output_dir)
                report["backup"] = {"path": str(backup), "sha256": _sha256(backup)}
            else:
                report["snapshot"] = {
                    "path": str(sqlite_path.resolve()),
                    "sha256": _sha256(sqlite_path),
                }
            legacy = _archive_legacy(source, output_dir)
            report["legacy_archive"] = {"path": str(legacy), "sha256": _sha256(legacy)}
            _run_alembic()
            if args.mode in {"rehearse", "apply"}:
                _reset_aics_data(target)
            migrated = {}
            migration_durations = {}
            loader = (
                _mysql_compressed_loader_connect(settings)
                if args.compressed_loader
                else target
            )
            try:
                selected_tables = ACTIVE_TABLES
                if args.tables:
                    requested = [item.strip() for item in args.tables.split(",") if item.strip()]
                    unknown = sorted(set(requested) - set(ACTIVE_TABLES))
                    if unknown:
                        raise RuntimeError(f"Unknown migration tables: {', '.join(unknown)}")
                    selected_tables = tuple(
                        table for table in ACTIVE_TABLES if table in requested
                    )
                for table in selected_tables:
                    started = time.monotonic()
                    if args.mode == "delta":
                        timestamp_column = DELTA_TIMESTAMP_COLUMNS[table]
                        where = f" WHERE `{timestamp_column}`>=?"
                        params = (args.since,)
                    elif table == "node_traces":
                        where = " WHERE created_at>=?"
                        params = (source_manifest["trace_cutoff"],)
                    else:
                        where = ""
                        params = ()
                    skip_primary_keys = None
                    if args.mode == "resume":
                        primary_key_columns = source_manifest["tables"][table][
                            "primary_key"
                        ]
                        skip_primary_keys = _target_primary_keys(
                            target,
                            table,
                            primary_key_columns,
                        )
                    migrated[table] = _migrate_table(
                        source,
                        loader,
                        table,
                        batch_size=max(1, min(args.batch_size, 50_000)),
                        max_batch_bytes=max(1024 * 1024, args.max_batch_bytes),
                        where=where,
                        params=params,
                        upsert=args.mode == "delta",
                        skip_primary_keys=skip_primary_keys,
                    )
                    migration_durations[table] = round(time.monotonic() - started, 3)
                    print(
                        f"migrated {table}: {migrated[table]} rows "
                        f"in {migration_durations[table]}s",
                        flush=True,
                    )
            finally:
                if loader is not target:
                    loader.close()
            if args.mode == "delta":
                with target.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM `aics_node_traces` WHERE `created_at`<%s",
                        (source_manifest["trace_cutoff"],),
                    )
                    report["expired_node_traces_deleted"] = int(cursor.rowcount)
                target.commit()
            report["migrated"] = migrated
            report["migration_durations_seconds"] = migration_durations
        if args.skip_full_verify:
            report["verification"] = {"deferred": True, "errors": []}
        else:
            report["verification"] = _verify(source_manifest, target, platform_before)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{args.mode}.json"
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(path)
        return 1 if report["verification"]["errors"] else 0
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
