from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.services.store_fact_integrity import assess_store_fact_integrity


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit store snapshot fact integrity without modifying data.")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    stores_by_id = payload.get("stores_by_id") if isinstance(payload, dict) else {}
    stores = [dict(item) for item in stores_by_id.values() if isinstance(item, dict)]
    invalid: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for store in stores:
        result = assess_store_fact_integrity(store, known_stores=stores)
        item = {
            "store_id": result["store_id"],
            "store_name": result["store_name"],
            "region": result["region"],
            "violations": result["violations"],
            "warnings": result["warnings"],
            "evidence": result["evidence"],
        }
        if result["status"] == "invalid":
            invalid.append(item)
        elif result["warnings"]:
            warnings.append(item)

    report = {
        "snapshot": str(args.snapshot),
        "snapshot_store_count": len(stores),
        "invalid_store_count": len(invalid),
        "warning_store_count": len(warnings),
        "invalid_stores": invalid,
        "warning_stores": warnings,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
