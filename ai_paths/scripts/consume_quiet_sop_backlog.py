"""Consume one night's local SOP backlog without calling models or send APIs.

Pause the background worker before applying, then resume it after verification.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.storage import AppRepository, build_store
from app.services.storage.serialization import utc_now_iso


def consume_backlog(repository, *, local_date: str, apply: bool = False, backup: Path | None = None) -> dict:
    day = date.fromisoformat(local_date)
    zone = ZoneInfo("Asia/Shanghai")
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc).isoformat()
    end = datetime.combine(day, time(8), zone).astimezone(timezone.utc).isoformat()
    tasks = repository.list_quiet_hour_backlog_tasks(start_at=start, end_at=end, limit=None)
    contacts = {(t.get("corp_id"), t.get("wechat"), t.get("external_userid") or t.get("customer_id")) for t in tasks}
    if apply:
        if backup is None:
            raise ValueError("--backup is required for --apply")
        backup.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidentally replacing the original audit.
        with backup.open("x", encoding="utf-8") as handle:
            json.dump({"local_date": local_date, "tasks": tasks}, handle, ensure_ascii=False, indent=2)
        consumed_at = utc_now_iso()
        for task in tasks:
            payload = dict(task.get("send_payload") or {})
            marker = dict(payload.get("backlog_marker") or {})
            marker.update(pending=False, consumed_without_send=True, consumed_at=consumed_at,
                          reason="operator_consumed_no_morning_resend", local_date=local_date)
            payload["backlog_marker"] = marker
            repository.update_sop_send_task(
                task["id"], status=task["status"], send_payload=payload, error=str(task.get("error") or ""),
            )
    return {"local_date": local_date, "dry_run": not apply, "task_count": len(tasks),
            "contact_count": len(contacts), "send_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-date", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    store = build_store(get_settings())
    try:
        result = consume_backlog(AppRepository(store), local_date=args.local_date, apply=args.apply, backup=args.backup)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        store.close()


if __name__ == "__main__":
    main()
