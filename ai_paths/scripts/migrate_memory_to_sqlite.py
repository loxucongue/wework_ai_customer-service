from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "ai_paths") not in sys.path:
    sys.path.insert(0, str(ROOT / "ai_paths"))

from app.config import get_settings  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402


def migrate(memory_dir: Path | None = None) -> int:
    settings = get_settings()
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    source = memory_dir or settings.memory_dir
    if not source.exists():
        return 0

    count = 0
    for path in source.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        customer_id = str(data.get("customer_id") or path.stem)
        repository.save_memory(customer_id, data)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate logs/memory JSON files into SQLite.")
    parser.add_argument("--memory-dir", default="", help="Optional source memory directory.")
    args = parser.parse_args()
    count = migrate(Path(args.memory_dir) if args.memory_dir else None)
    print(f"migrated={count}")


if __name__ == "__main__":
    main()
