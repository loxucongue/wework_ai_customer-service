from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pymysql


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _adoption_detail(output_snapshot: Any) -> tuple[int, int] | None:
    if isinstance(output_snapshot, str):
        try:
            output_snapshot = json.loads(output_snapshot)
        except json.JSONDecodeError:
            return None
    if not isinstance(output_snapshot, dict):
        return None
    observability = output_snapshot.get("observability_v3")
    if not isinstance(observability, dict):
        return None
    knowledge = observability.get("knowledge_match")
    if not isinstance(knowledge, dict):
        return None
    adopted = knowledge.get("adopted")
    if not isinstance(adopted, dict):
        return None
    sequence_adopted = 1 if str(adopted.get("sequence_id") or "").strip() else 0
    script_ids = adopted.get("script_ids")
    script_adopted = 1 if isinstance(script_ids, list) and any(str(item or "").strip() for item in script_ids) else 0
    return sequence_adopted, script_adopted


def _enabled(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill explicit V3 sequence/script adoption flags")
    parser.add_argument("--apply", action="store_true", help="persist verified observations")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    ssl_ca = os.environ.get("AICS_MYSQL_SSL_CA", "").strip()
    ssl_required = _enabled(os.environ.get("AICS_MYSQL_SSL_REQUIRED"), default=True)
    database = os.environ["AICS_MYSQL_DATABASE"]
    connect_args: dict[str, Any] = {
        "host": os.environ["AICS_MYSQL_HOST"],
        "port": int(os.environ.get("AICS_MYSQL_PORT", "3306")),
        "user": os.environ["AICS_MYSQL_USER"],
        "password": os.environ["AICS_MYSQL_PASSWORD"],
        "database": database,
        "charset": "utf8mb4",
        "connect_timeout": int(os.environ.get("AICS_MYSQL_CONNECT_TIMEOUT_SECONDS", "10")),
        "read_timeout": int(os.environ.get("AICS_MYSQL_READ_TIMEOUT_SECONDS", "15")),
        "write_timeout": int(os.environ.get("AICS_MYSQL_WRITE_TIMEOUT_SECONDS", "15")),
        "cursorclass": pymysql.cursors.DictCursor,
    }
    if ssl_ca:
        connect_args["ssl"] = {"ca": ssl_ca}
    elif ssl_required:
        connect_args["ssl"] = {"check_hostname": False}
    connection = pymysql.connect(
        **connect_args,
    )
    inspected = verified = sequence_adopted = script_adopted = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS database_name")
            current_database = str((cursor.fetchone() or {}).get("database_name") or "")
            if current_database != database or database != "wecom_cs":
                raise RuntimeError("refusing to backfill an unexpected database")
            if ssl_required:
                cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
                if not str((cursor.fetchone() or {}).get("Value") or ""):
                    raise RuntimeError("refusing an unencrypted MySQL connection")
            cursor.execute(
                """
                SELECT id, request_id FROM aics_v3_strategy_usage_events
                WHERE adoption_detail_observed=0
                  AND policy_version<>''
                  AND decision_status NOT IN ('not_enabled','system_guard','skipped')
                ORDER BY occurred_at DESC LIMIT %s
                """,
                (max(1, min(args.limit, 10000)),),
            )
            rows = cursor.fetchall()
            for row in rows:
                inspected += 1
                cursor.execute(
                    "SELECT output_snapshot FROM aics_runs WHERE request_id=%s",
                    (row["request_id"],),
                )
                run = cursor.fetchone()
                detail = _adoption_detail(run.get("output_snapshot") if run else None)
                if detail is None:
                    continue
                sequence_flag, script_flag = detail
                verified += 1
                sequence_adopted += sequence_flag
                script_adopted += script_flag
                if args.apply:
                    cursor.execute(
                        """
                        UPDATE aics_v3_strategy_usage_events
                        SET sequence_adopted=%s, script_adopted=%s,
                            adoption_detail_observed=1, updated_at=updated_at
                        WHERE id=%s AND adoption_detail_observed=0
                        """,
                        (sequence_flag, script_flag, row["id"]),
                    )
        if args.apply:
            connection.commit()
        else:
            connection.rollback()
    finally:
        connection.close()
    print(json.dumps({
        "mode": "apply" if args.apply else "dry_run",
        "inspected": inspected,
        "verified": verified,
        "unresolved": inspected - verified,
        "sequence_adopted": sequence_adopted,
        "script_adopted": script_adopted,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
