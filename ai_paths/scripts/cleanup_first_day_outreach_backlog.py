from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.services.storage import AppRepository, build_store


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit or cancel invalid and expired first-day outreach tasks."
    )
    parser.add_argument("--apply", action="store_true", help="Apply cleanup; default is dry-run.")
    parser.add_argument("--hours", type=int, default=24, help="Expiry age in hours.")
    args = parser.parse_args()

    store = build_store(get_settings())
    store.initialize()
    repository = AppRepository(store)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, args.hours))).isoformat()
    result = repository.cleanup_first_day_task_backlog(
        older_than=cutoff,
        dry_run=not args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
