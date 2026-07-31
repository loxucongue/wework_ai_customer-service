from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.platform_agent_client import PlatformAgentClient
from app.services.store_fact_integrity import assess_store_fact_integrity
from app.services.store_snapshot_service import (
    StoreSnapshotService,
    _store_option_is_recommendable,
    clean_text,
    geocode_region_conflicts,
    parse_region,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit every platform store and optionally replace the recommendation snapshot.",
    )
    parser.add_argument("--corp-id", required=True)
    parser.add_argument("--wechat", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write-snapshot", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    client = PlatformAgentClient(settings)
    service = StoreSnapshotService(settings, client, max_workers=max(1, min(args.workers, 20)))
    context = {
        "corp_id": args.corp_id,
        "wechat": args.wechat,
        "user_id": args.user_id,
    }

    option_rows = client.list_store_options(request_context=context)
    hydrated = service._hydrate_rows(option_rows, context)
    option_by_id = {
        str(row.get("id") or row.get("store_id") or ""): row
        for row in option_rows
        if isinstance(row, dict)
    }
    hydrated_by_id = {
        str(row.get("store_id") or ""): row
        for row in hydrated
        if isinstance(row, dict)
    }

    raw_results = _raw_geocode_results(
        service,
        hydrated,
        workers=max(1, min(args.workers, 20)),
    )
    records: list[dict[str, Any]] = []
    active_detail_errors: list[dict[str, str]] = []
    recommendable_stores: list[dict[str, Any]] = []

    for store_id, option in option_by_id.items():
        store = hydrated_by_id.get(store_id) or {
            "store_id": store_id,
            "store_name": clean_text(option.get("name")),
            "detail_source": "hydrate_missing",
        }
        recommendable = _store_option_is_recommendable(option)
        integrity = assess_store_fact_integrity(store, known_stores=hydrated)
        raw_result = raw_results.get(store_id) or {}
        raw_conflicts = geocode_region_conflicts(
            raw_result,
            address_region=parse_region(clean_text(store.get("store_address"))),
            parking_region=parse_region(clean_text(store.get("parking_address"))),
        ) if raw_result else []
        detail_source = clean_text(store.get("detail_source"))
        detail_error = detail_source != "store_info"
        if recommendable and detail_error:
            active_detail_errors.append(
                {
                    "store_id": store_id,
                    "store_name": clean_text(store.get("store_name")),
                    "detail_source": detail_source,
                }
            )
        if recommendable:
            recommendable_stores.append(store)
        records.append(
            {
                "store_id": store_id,
                "store_name": clean_text(store.get("store_name")),
                "recommendable_by_platform": recommendable,
                "platform_status": option.get("status"),
                "platform_shore_show": option.get("shore_show"),
                "platform_schedule_status": option.get("schedule_status"),
                "platform_plan_status": option.get("plan_status"),
                "platform_is_pause": option.get("is_pause"),
                "detail_source": detail_source,
                "platform_address": clean_text(store.get("store_address")),
                "parking_address": clean_text(store.get("parking_address")),
                "raw_geocode_result": raw_result,
                "raw_geocode_conflicts": raw_conflicts,
                "selected_geocode_query": clean_text(store.get("geocode_query")),
                "selected_geocode_result": {
                    "province": clean_text(store.get("province")),
                    "city": clean_text(store.get("city")),
                    "district": clean_text(store.get("district")),
                    "formatted_address": clean_text(store.get("geocode_formatted_address")),
                    "location": clean_text(store.get("location")),
                },
                "rejected_geocode_candidates": list(store.get("geocode_rejected_candidates") or []),
                "integrity": integrity,
            }
        )

    snapshot = service._build_snapshot(recommendable_stores)
    excluded_rows = [
        row for row in option_rows
        if not _store_option_is_recommendable(row)
    ]
    snapshot.update(
        {
            "platform_store_count": len(option_rows),
            "platform_recommendable_count": len(recommendable_stores),
            "excluded_platform_store_count": len(excluded_rows),
            "excluded_platform_stores": [
                {
                    "store_id": str(row.get("id") or row.get("store_id") or ""),
                    "store_name": clean_text(row.get("name")),
                    "status": row.get("status"),
                    "shore_show": row.get("shore_show"),
                }
                for row in excluded_rows
            ],
            "audit_report_path": str(args.output),
        }
    )

    summary = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "platform_store_count": len(option_rows),
        "platform_recommendable_count": len(recommendable_stores),
        "platform_excluded_count": len(excluded_rows),
        "active_detail_error_count": len(active_detail_errors),
        "active_detail_errors": active_detail_errors,
        "raw_geocode_empty_count": sum(not record["raw_geocode_result"] for record in records),
        "raw_geocode_conflict_count": sum(bool(record["raw_geocode_conflicts"]) for record in records),
        "selected_geocode_recovered_count": sum(
            bool(record["selected_geocode_result"]["location"])
            and record["selected_geocode_query"] != record["platform_address"]
            for record in records
        ),
        "selected_geocode_empty_count": sum(
            not record["selected_geocode_result"]["location"]
            for record in records
        ),
        "recommendable_snapshot_count": snapshot.get("store_count", 0),
        "recommendable_invalid_count": snapshot.get("invalid_store_count", 0),
        "recommendable_invalid_stores": snapshot.get("invalid_stores", []),
    }
    report = {"summary": summary, "stores": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.write_snapshot:
        if active_detail_errors:
            raise RuntimeError(
                f"refusing snapshot replacement: {len(active_detail_errors)} active store details failed",
            )
        service._write_snapshot(snapshot)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not active_detail_errors else 2


def _raw_geocode_results(
    service: StoreSnapshotService,
    stores: list[dict[str, Any]],
    *,
    workers: int,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for store in stores:
            store_id = clean_text(store.get("store_id"))
            address = clean_text(store.get("store_address"))
            if not store_id or not address:
                continue
            futures[executor.submit(service._geocode_store_address, address)] = store_id
        for future in as_completed(futures):
            store_id = futures[future]
            try:
                output[store_id] = future.result()
            except Exception:
                output[store_id] = {}
    return output


if __name__ == "__main__":
    raise SystemExit(main())
