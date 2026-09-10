"""Build/import an identity catalog from an explicit local manifest.

No environment credentials are loaded. This command only opens the explicitly
named local SQLite database; default mode is dry-run. Media files and undo data
belong under ignored artifacts, never in Git.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.services.material_identity import MAX_CATALOG_ITEMS, MAX_MEDIA_BYTES, prepare_catalog  # noqa: E402
from app.services.storage.material_repository import _decode  # noqa: E402
from app.services.storage.repositories import AppRepository  # noqa: E402
from app.services.storage.sqlite_store import SQLiteStore  # noqa: E402


def build_plan(records: list[dict], existing: list[dict]) -> tuple[list[dict], list[dict]]:
    if not isinstance(records, list) or len(records) > MAX_CATALOG_ITEMS:
        raise ValueError("catalog_limit_exceeded")
    plan, failures = [], []
    known = list(existing)
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
                "catalog_limit_exceeded",
            }
            failures.append({"index": index, "reason": str(exc) if str(exc) in known_reasons else "fingerprint_failed"})
    return plan, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--database", type=Path, required=True, help="Explicit local SQLite path, never a production DSN"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path, help="Required output undo file when applying")
    parser.add_argument("--rollback", type=Path, help="Previously saved undo file; refuses concurrent changes/claims")
    args = parser.parse_args()
    if args.apply and not args.undo:
        parser.error("--apply requires --undo")
    if args.rollback and (args.apply or args.manifest):
        parser.error("--rollback is exclusive with --apply/--manifest")
    if not args.rollback and not args.manifest:
        parser.error("--manifest is required")
    repository = None
    existing_catalog = []
    if args.apply or args.rollback:
        store = SQLiteStore(Settings(_env_file=None, AI_PATHS_DB_PATH=args.database.resolve()))
        store.initialize()
        repository = AppRepository(store)
        existing_catalog = repository.material_catalog()
    elif args.database.exists():
        # Dry-run never creates a DB, schema, WAL, or journal.
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_identities'").fetchone():
                existing_catalog = [
                    _decode(row)
                    for row in conn.execute("SELECT * FROM material_identities LIMIT ?", (MAX_CATALOG_ITEMS + 1,))
                ]
    if args.rollback:
        repository.rollback_material_catalog(json.loads(args.rollback.read_text(encoding="utf-8")))
        print(json.dumps({"status": "rolled_back"}))
        return 0
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    plan, failures = build_plan(records, existing_catalog)
    if args.apply:
        if args.undo.exists():
            parser.error("undo file already exists; choose a new path")
        # Write the intended undo before applying so interruption never loses it.
        existing = {row["alias_key"]: row for row in repository.material_catalog()}
        undo = {
            "before": [existing.get(row["alias_key"], {"alias_key": row["alias_key"], "absent": True}) for row in plan],
            "after": plan,
        }
        args.undo.parent.mkdir(parents=True, exist_ok=True)
        with args.undo.open("x", encoding="utf-8") as stream:
            json.dump(undo, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        repository.apply_material_catalog(plan, expected_before=undo["before"])
    print(json.dumps({"status": "applied" if args.apply else "dry_run", "aliases": len(plan), "failures": failures}))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
