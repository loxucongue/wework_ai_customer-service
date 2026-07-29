from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
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
    "sop_events",
    "sop_send_tasks",
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
    "sop_events": ("raw_payload_json",),
    "sop_send_tasks": ("reply_messages_json", "send_payload_json", "send_response_json"),
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate AI Paths SQLite data into isolated aics_* tables.")
    parser.add_argument("mode", choices=("preflight", "apply", "verify"))
    parser.add_argument("--sqlite-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "migration")
    parser.add_argument("--batch-size", type=int, default=300)
    parser.add_argument("--confirm-service-stopped", action="store_true")
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    manifest: dict[str, Any] = {"trace_cutoff": cutoff, "tables": {}, "json_errors": []}
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
        primary_key_columns = [
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            if int(row["pk"] or 0)
        ]
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
        }
        for column in JSON_COLUMNS.get(table, ()):
            rows = connection.execute(
                f"SELECT rowid, {column} FROM {table}{where}",
                params,
            ).fetchall()
            for row in rows:
                try:
                    json.loads(str(row[column] or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    manifest["json_errors"].append(
                        {"table": table, "column": column, "rowid": int(row["rowid"])}
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


def _chunks(rows: Iterable[sqlite3.Row], size: int) -> Iterable[list[sqlite3.Row]]:
    batch: list[sqlite3.Row] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _migrate_table(
    source: sqlite3.Connection,
    target: pymysql.Connection,
    table: str,
    *,
    batch_size: int,
    trace_cutoff: str,
) -> int:
    source_columns = [str(row["name"]) for row in source.execute(f"PRAGMA table_info({table})").fetchall()]
    target_columns = [name for name in EXPECTED_COLUMNS[f"aics_{table}"] if name != "active_send_once_key"]
    columns = [name for name in source_columns if name in target_columns]
    where = " WHERE created_at>=?" if table == "node_traces" else ""
    source_params = (trace_cutoff,) if where else ()
    source_rows = source.execute(f"SELECT * FROM {table}{where}", source_params)
    placeholders = ",".join("%s" for _ in columns)
    updates = ",".join(f"`{column}`=VALUES(`{column}`)" for column in columns)
    sql = (
        f"INSERT INTO `aics_{table}` ({','.join(f'`{column}`' for column in columns)}) "
        f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"
    )
    migrated = 0
    with target.cursor() as cursor:
        for batch in _chunks(source_rows, batch_size):
            cursor.executemany(sql, [tuple(row[column] for column in columns) for row in batch])
            target.commit()
            migrated += len(batch)
    return migrated


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
            result["tables"][table] = {"source": source_count, "target": target_count}
            if source_count != target_count:
                result["errors"].append(
                    f"row_count_mismatch:{table}:{source_count}:{target_count}"
                )
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM aics_outreach_tasks t
            LEFT JOIN aics_outreach_plans p ON p.id=t.plan_id
            WHERE p.id IS NULL
            """
        )
        if int(cursor.fetchone()["count"]):
            result["errors"].append("orphan_outreach_tasks")
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM aics_sop_send_tasks t
            LEFT JOIN aics_sop_events e ON e.event_id=t.event_id
            WHERE e.event_id IS NULL
            """
        )
        if int(cursor.fetchone()["count"]):
            result["errors"].append("orphan_sop_send_tasks")
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
        if args.mode == "preflight":
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "preflight.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(path)
            return 0
        if args.mode == "apply":
            if not args.confirm_service_stopped:
                raise RuntimeError("apply requires --confirm-service-stopped")
            backup = _backup_sqlite(source, sqlite_path, output_dir)
            legacy = _archive_legacy(source, output_dir)
            report["backup"] = {"path": str(backup), "sha256": _sha256(backup)}
            report["legacy_archive"] = {"path": str(legacy), "sha256": _sha256(legacy)}
            _run_alembic()
            migrated = {}
            for table in ACTIVE_TABLES:
                migrated[table] = _migrate_table(
                    source,
                    target,
                    table,
                    batch_size=max(200, min(args.batch_size, 500)),
                    trace_cutoff=source_manifest["trace_cutoff"],
                )
            report["migrated"] = migrated
        report["verification"] = _verify(source_manifest, target, platform_before)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{args.mode}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)
        return 1 if report["verification"]["errors"] else 0
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
