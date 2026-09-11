"""Plan and safely synchronize the canonical material identity catalog.

SQLite keeps the legacy explicit-path workflow. MySQL is opt-in, defaults to a
read-only plan, and requires a frozen plan plus target confirmation for writes.
Artifacts contain hashes and identity aliases, never credentials or media URLs.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings  # noqa: E402
from app.services.material_identity import MAX_CATALOG_ITEMS, MAX_MEDIA_BYTES, prepare_catalog  # noqa: E402
from app.services.storage.material_repository import _decode  # noqa: E402
from app.services.storage.mysql_store import MySQLStore  # noqa: E402
from app.services.storage.repositories import AppRepository  # noqa: E402
from app.services.storage.sqlite_store import SQLiteStore  # noqa: E402

PLAN_VERSION = "material_identity_sync_plan_v2"
APPLY_CONFIRMATION = "APPLY MATERIAL IDENTITY PLAN"
ROLLBACK_CONFIRMATION = "ROLLBACK MATERIAL IDENTITY PLAN"
RETRYABLE_MARKERS = ("deadlock", "lock wait timeout", "try restarting transaction")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def _write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def build_plan(records: list[dict], existing: list[dict]) -> tuple[list[dict], list[dict]]:
    if not isinstance(records, list) or len(records) > MAX_CATALOG_ITEMS:
        raise ValueError("catalog_limit_exceeded")
    plan, failures, known = [], [], list(existing)
    for index, raw in enumerate(records):
        record = dict(raw)
        try:
            if record.get("path"):
                with Path(record["path"]).open("rb") as stream:
                    record["bytes"] = stream.read(MAX_MEDIA_BYTES + 1)
            rows = prepare_catalog([record], known)
            keys = {row["alias_key"] for row in rows}
            known = [row for row in known if row["alias_key"] not in keys] + rows
            plan = [row for row in plan if row["alias_key"] not in keys] + rows
        except TimeoutError:
            failures.append({"index": index, "reason": "download_timeout"})
        except OSError:
            failures.append({"index": index, "reason": "bytes_unavailable"})
        except (ValueError, ImportError) as exc:
            known_reasons = {
                "identity_unknown",
                "unsupported_format",
                "media_size_invalid",
                "identity_conflict_requires_override",
                "override_reason_required",
                "canonical_id_invalid",
                "canonical_type_conflict",
                "catalog_limit_exceeded",
            }
            failures.append({"index": index, "reason": str(exc) if str(exc) in known_reasons else "fingerprint_failed"})
    return plan, failures


def _catalog_checksum(rows: list[dict]) -> str:
    return _checksum(sorted(rows, key=lambda row: row["alias_key"]))


def _schema_head(repository: AppRepository) -> str:
    if repository.store.dialect == "sqlite":
        return "sqlite-schema.sql"
    with repository.store.connect() as conn:
        row = conn.execute("SELECT version_num FROM aics_schema_version LIMIT 1").fetchone()
    return str((row or {}).get("version_num") or "")


def _target(repository: AppRepository) -> str:
    if repository.store.dialect == "sqlite":
        return f"sqlite:{Path(repository.store.db_path).resolve()}"
    settings = repository.store.settings
    return f"mysql:{settings.aics_mysql_host}:{settings.aics_mysql_port}/{settings.aics_mysql_database}"


def _validate_snapshot_report(report: dict[str, Any], records: list[dict], source_checksum: str) -> None:
    if report.get("directory_checksum") != source_checksum:
        raise ValueError("snapshot_source_checksum_mismatch")
    verified = int(report.get("verified_references") or 0)
    pending = int(report.get("pending_references") or 0)
    total = int(report.get("media_references") or 0)
    if verified != len(records) or verified + pending != total:
        raise ValueError("snapshot_classification_count_mismatch")
    roles = report.get("roles") if isinstance(report.get("roles"), dict) else {}
    verified_roles = report.get("verified_roles") if isinstance(report.get("verified_roles"), dict) else {}
    if any(int(count or 0) > 0 and int(verified_roles.get(role) or 0) <= 0 for role, count in roles.items()):
        raise ValueError("snapshot_role_without_verified_material")


def make_frozen_plan(
    records: list[dict],
    existing: list[dict],
    repository: AppRepository,
    batch_size: int,
    source_checksum: str = "",
    claims: list[dict] | None = None,
    snapshot_report: dict[str, Any] | None = None,
) -> dict:
    if snapshot_report is not None:
        _validate_snapshot_report(snapshot_report, records, source_checksum)
    rows, failures = build_plan(records, existing)
    before = {row["alias_key"]: row for row in existing}
    batches = []
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset : offset + batch_size]
        batches.append(
            {
                "rows": batch,
                "before": [
                    before.get(row["alias_key"], {"alias_key": row["alias_key"], "absent": True}) for row in batch
                ],
            }
        )
    identity_by_alias = {row["alias_key"]: row for row in rows}
    normalized_claims: list[dict[str, Any]] = []
    seen_claims: set[tuple[str, str]] = set()
    for claim in claims or []:
        required = ("contact_key", "canonical_id", "request_id", "client_message_id", "alias_key")
        if any(not str(claim.get(field) or "").strip() for field in required):
            raise ValueError("historical_claim_fields_required")
        identity = identity_by_alias.get(str(claim["alias_key"]))
        if not identity or identity["canonical_id"] != str(claim["canonical_id"]):
            raise ValueError("historical_claim_identity_mismatch")
        key = (str(claim["contact_key"]), str(claim["canonical_id"]))
        if key in seen_claims:
            continue
        seen_claims.add(key)
        normalized_claims.append(
            {field: str(claim.get(field) or "") for field in (*required, "asset_role")}
            | {"status": "response_committed"}
        )
    claim_batches = [
        normalized_claims[offset : offset + batch_size] for offset in range(0, len(normalized_claims), batch_size)
    ]
    return {
        "version": PLAN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": _target(repository),
        "schema_head": _schema_head(repository),
        "manifest_checksum": _checksum(records),
        "source_checksum": source_checksum or _checksum(records),
        "claims_checksum": _checksum(normalized_claims),
        "snapshot_report_checksum": _checksum(snapshot_report or {}),
        "catalog_checksum": _catalog_checksum(existing),
        "batches": batches,
        "claim_batches": claim_batches,
        "summary": {
            "records": len(records),
            "aliases": len(rows),
            "batches": len(batches),
            "claims": len(normalized_claims),
            "claim_batches": len(claim_batches),
            "pending": int((snapshot_report or {}).get("pending_references") or 0),
            "failures": failures,
        },
    }


def _retry(operation, retries: int):
    for attempt in range(retries + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= retries or not any(marker in str(exc).lower() for marker in RETRYABLE_MARKERS):
                raise
            time.sleep(min(0.1 * (2**attempt), 2.0))


def apply_frozen_plan(repository: AppRepository, plan: dict, progress_path: Path, retries: int = 3) -> dict:
    if plan.get("version") != PLAN_VERSION or plan.get("target") != _target(repository):
        raise ValueError("plan_target_mismatch")
    if plan.get("schema_head") != _schema_head(repository):
        raise ValueError("schema_changed_since_plan")
    if (plan.get("summary") or {}).get("failures"):
        raise ValueError("plan_has_identity_failures")
    progress = {
        "version": PLAN_VERSION,
        "plan_checksum": _checksum(plan),
        "completed_batches": [],
        "completed_claim_batches": [],
        "undo": [],
    }
    if progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if progress.get("plan_checksum") != _checksum(plan):
            raise ValueError("progress_plan_mismatch")
    if not progress["completed_batches"]:
        current_rows = repository.material_catalog()
        current = {row["alias_key"]: row for row in current_rows}
        reconstructed = dict(current)
        inferred: list[int] = []
        saw_pending = False
        for index, batch in enumerate(plan.get("batches") or []):
            actual = [
                current.get(row["alias_key"], {"alias_key": row["alias_key"], "absent": True}) for row in batch["rows"]
            ]
            if actual == batch["rows"] and not saw_pending:
                inferred.append(index)
            elif actual == batch["before"]:
                saw_pending = True
            else:
                raise ValueError("catalog_changed_since_plan")
            for prior in batch["before"]:
                if prior.get("absent"):
                    reconstructed.pop(prior["alias_key"], None)
                else:
                    reconstructed[prior["alias_key"]] = prior
        if _catalog_checksum(list(reconstructed.values())) != plan.get("catalog_checksum"):
            raise ValueError("catalog_changed_since_plan")
        for index in inferred:
            batch = plan["batches"][index]
            progress["completed_batches"].append(index)
            progress["undo"].append({"before": batch["before"], "after": batch["rows"]})
        if inferred:
            _write_atomic(progress_path, progress)
    for index, batch in enumerate(plan.get("batches") or []):
        if index in set(progress["completed_batches"]):
            continue
        current = {row["alias_key"]: row for row in repository.material_catalog()}
        actual = [
            current.get(row["alias_key"], {"alias_key": row["alias_key"], "absent": True}) for row in batch["rows"]
        ]
        if actual == batch["rows"]:
            undo = {"before": batch["before"], "after": batch["rows"]}
        else:
            if actual != batch["before"]:
                raise ValueError("catalog_changed_since_plan")
            undo = _retry(
                lambda: repository.apply_material_catalog(batch["rows"], expected_before=batch["before"]), retries
            )
        progress["undo"].append(undo)
        progress["completed_batches"].append(index)
        _write_atomic(progress_path, progress)
    for index, claims in enumerate(plan.get("claim_batches") or []):
        if index in set(progress.get("completed_claim_batches") or []):
            continue

        def apply_claim_batch() -> None:
            with repository.store.connect() as conn:
                for claim in claims:
                    conn.execute(
                        """INSERT OR IGNORE INTO material_claims
                        (contact_key, canonical_id, request_id, client_message_id,
                         alias_key, asset_role, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'response_committed', ?)""",
                        (
                            claim["contact_key"],
                            claim["canonical_id"],
                            claim["request_id"],
                            claim["client_message_id"],
                            claim["alias_key"],
                            claim["asset_role"],
                            plan["generated_at"],
                        ),
                    )
                    row = conn.execute(
                        "SELECT canonical_id FROM material_claims WHERE contact_key=? AND canonical_id=?",
                        (claim["contact_key"], claim["canonical_id"]),
                    ).fetchone()
                    if not row:
                        raise ValueError("historical_claim_not_committed")

        _retry(apply_claim_batch, retries)
        progress.setdefault("completed_claim_batches", []).append(index)
        _write_atomic(progress_path, progress)
    return progress


def rollback_frozen_plan(repository: AppRepository, plan: dict, progress_path: Path, retries: int = 3) -> dict:
    if plan.get("target") != _target(repository) or plan.get("schema_head") != _schema_head(repository):
        raise ValueError("plan_target_or_schema_mismatch")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if progress.get("plan_checksum") != _checksum(plan):
        raise ValueError("progress_plan_mismatch")
    if progress.get("completed_claim_batches"):
        raise ValueError("catalog_rollback_has_delivery_claims")
    while progress["undo"]:
        _retry(lambda: repository.rollback_material_catalog(progress["undo"][-1]), retries)
        progress["undo"].pop()
        progress["completed_batches"].pop()
        _write_atomic(progress_path, progress)
    return progress


def _repository(args) -> tuple[AppRepository, bool]:
    if args.mysql_env_file:
        store = MySQLStore(
            Settings(
                _env_file=args.mysql_env_file,
                AICS_STORAGE_BACKEND="mysql",
                AI_PATHS_SERVICE_ROLE="control",
                AI_PATHS_BACKGROUND_WORKERS_ENABLED=False,
                SOP_PLATFORM_PULL_ENABLED=False,
            )
        )
        store.initialize()
        return AppRepository(store), True
    if not args.database:
        raise ValueError("--database or --mysql-env-file is required")
    store = SQLiteStore(Settings(_env_file=None, AI_PATHS_DB_PATH=args.database.resolve()))
    if args.apply or args.rollback_plan:
        store.initialize()
    return AppRepository(store), False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--mysql-env-file", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--source-checksum", help="Catalog checksum emitted by the read-only snapshot")
    parser.add_argument("--claims", type=Path, help="Ignored exact-evidence historical claim manifest")
    parser.add_argument("--snapshot-report", type=Path, help="Ignored verified/pending catalog classification")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback-plan", action="store_true")
    parser.add_argument("--confirm-target")
    parser.add_argument("--confirm-action")
    parser.add_argument("--undo", type=Path)
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if bool(args.database) == bool(args.mysql_env_file):
        parser.error("choose exactly one database target")
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size must be between 1 and 1000")
    if args.database and not args.apply and not args.rollback and not args.rollback_plan and not args.plan:
        if not args.manifest:
            parser.error("--manifest is required")
        existing = []
        if args.database.exists():
            with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_identities'"
                ).fetchone():
                    existing = [
                        _decode(row)
                        for row in conn.execute("SELECT * FROM material_identities LIMIT ?", (MAX_CATALOG_ITEMS + 1,))
                    ]
        rows, failures = build_plan(json.loads(args.manifest.read_text(encoding="utf-8")), existing)
        print(json.dumps({"status": "dry_run", "aliases": len(rows), "failures": failures}))
        return 0 if not failures else 2
    if args.database and (args.undo or args.rollback):
        if args.apply and not args.undo:
            parser.error("--apply requires --undo")
        if args.rollback and (args.apply or args.manifest):
            parser.error("--rollback is exclusive")
        store = SQLiteStore(Settings(_env_file=None, AI_PATHS_DB_PATH=args.database.resolve()))
        store.initialize()
        repository = AppRepository(store)
        if args.rollback:
            repository.rollback_material_catalog(json.loads(args.rollback.read_text(encoding="utf-8")))
            print(json.dumps({"status": "rolled_back"}))
            return 0
        records = json.loads(args.manifest.read_text(encoding="utf-8"))
        rows, failures = build_plan(records, repository.material_catalog())
        existing = {row["alias_key"]: row for row in repository.material_catalog()}
        undo = {
            "before": [existing.get(row["alias_key"], {"alias_key": row["alias_key"], "absent": True}) for row in rows],
            "after": rows,
        }
        _write_new(args.undo, undo)
        repository.apply_material_catalog(rows, expected_before=undo["before"])
        print(json.dumps({"status": "applied", "aliases": len(rows), "failures": failures}))
        return 0 if not failures else 2
    repository, mysql = _repository(args)
    try:
        if args.apply or args.rollback_plan:
            if not mysql or not args.plan or not args.progress or not args.manifest or not args.snapshot_report:
                parser.error("MySQL apply/rollback requires --manifest, --snapshot-report, --plan and --progress")
            expected = ROLLBACK_CONFIRMATION if args.rollback_plan else APPLY_CONFIRMATION
            if args.confirm_target != _target(repository) or args.confirm_action != expected:
                parser.error("target and action confirmation do not match")
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            if _checksum(json.loads(args.manifest.read_text(encoding="utf-8"))) != plan.get("manifest_checksum"):
                raise ValueError("manifest_changed_since_plan")
            if not args.source_checksum or args.source_checksum != plan.get("source_checksum"):
                raise ValueError("source_changed_since_plan")
            claims = json.loads(args.claims.read_text(encoding="utf-8")) if args.claims else []
            if _checksum(claims) != plan.get("claims_checksum"):
                raise ValueError("claims_changed_since_plan")
            snapshot_report = json.loads(args.snapshot_report.read_text(encoding="utf-8"))
            if _checksum(snapshot_report) != plan.get("snapshot_report_checksum"):
                raise ValueError("snapshot_report_changed_since_plan")
            _validate_snapshot_report(
                snapshot_report,
                json.loads(args.manifest.read_text(encoding="utf-8")),
                args.source_checksum,
            )
            result = (
                rollback_frozen_plan(repository, plan, args.progress, args.retries)
                if args.rollback_plan
                else apply_frozen_plan(repository, plan, args.progress, args.retries)
            )
            print(
                json.dumps(
                    {
                        "status": "rolled_back" if args.rollback_plan else "applied",
                        "completed_batches": len(result["completed_batches"]),
                    }
                )
            )
            return 0
        if not args.manifest:
            parser.error("--manifest is required for dry-run")
        if mysql and not args.source_checksum:
            parser.error("MySQL dry-run requires --source-checksum")
        if mysql and not args.snapshot_report:
            parser.error("MySQL dry-run requires --snapshot-report")
        records = json.loads(args.manifest.read_text(encoding="utf-8"))
        claims = json.loads(args.claims.read_text(encoding="utf-8")) if args.claims else []
        snapshot_report = json.loads(args.snapshot_report.read_text(encoding="utf-8")) if args.snapshot_report else None
        frozen = make_frozen_plan(
            records,
            repository.material_catalog(),
            repository,
            args.batch_size,
            source_checksum=args.source_checksum or "",
            claims=claims,
            snapshot_report=snapshot_report,
        )
        if args.plan:
            _write_new(args.plan, frozen)
        print(json.dumps({"status": "dry_run", "target": frozen["target"], **frozen["summary"]}))
        return 0 if not frozen["summary"]["failures"] else 2
    finally:
        repository.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
