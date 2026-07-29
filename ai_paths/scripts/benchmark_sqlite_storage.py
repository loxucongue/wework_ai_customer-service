from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import Settings
from app.services.storage import AppRepository, SQLiteStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark isolated SQLite repository writes.")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[5, 10, 20])
    parser.add_argument("--writes-per-worker", type=int, default=20)
    parser.add_argument("--trace-count", type=int, default=6)
    parser.add_argument("--payload-bytes", type=int, default=2048)
    parser.add_argument("--database", type=Path)
    return parser.parse_args()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _write_once(
    repository: AppRepository,
    *,
    worker: int,
    index: int,
    trace_count: int,
    payload: str,
) -> float:
    request_id = f"sqlite-benchmark-{worker}-{index}-{uuid.uuid4().hex}"
    trace = [
        {
            "node": f"node_{trace_index}",
            "input_snapshot": {"payload": payload},
            "output_snapshot": {"ok": True, "index": trace_index},
            "tool_calls": [],
            "duration_ms": 1,
        }
        for trace_index in range(trace_count)
    ]
    state = {
        "request_id": request_id,
        "customer_id": f"isolated-{worker}",
        "content": "isolated storage benchmark",
        "conversation_history": [],
        "reply_messages": [
            {"type": "text", "order": 1, "content": {"text": "ok"}}
        ],
        "trace": trace,
    }
    started = time.perf_counter()
    repository.save_run(
        conversation_id="sqlite-benchmark",
        final_state=state,
        token_usage={},
    )
    return (time.perf_counter() - started) * 1000


def run_level(
    repository: AppRepository,
    *,
    concurrency: int,
    writes_per_worker: int,
    trace_count: int,
    payload: str,
) -> dict[str, object]:
    latencies: list[float] = []
    errors: list[str] = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                _write_once,
                repository,
                worker=worker,
                index=index,
                trace_count=trace_count,
                payload=payload,
            )
            for worker in range(concurrency)
            for index in range(writes_per_worker)
        ]
        for future in as_completed(futures):
            try:
                latencies.append(float(future.result()))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    duration_seconds = time.perf_counter() - started
    return {
        "concurrency": concurrency,
        "writes": concurrency * writes_per_worker,
        "duration_seconds": round(duration_seconds, 3),
        "writes_per_second": round(len(latencies) / duration_seconds, 2)
        if duration_seconds
        else 0.0,
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2) if latencies else 0.0,
            "p95": round(_percentile(latencies, 0.95), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "error_count": len(errors),
        "errors": errors[:20],
    }


def main() -> int:
    args = parse_args()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.database is None:
        temporary = tempfile.TemporaryDirectory(prefix="aics-sqlite-benchmark-")
        database = Path(temporary.name) / "benchmark.db"
    else:
        database = args.database.resolve()
        if database.exists():
            raise SystemExit(f"refusing to overwrite existing database: {database}")

    settings = Settings(AI_PATHS_DB_PATH=database)
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO conversations
                (id, customer_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "sqlite-benchmark",
                "isolated",
                "isolated storage benchmark",
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:00:00+00:00",
            ),
        )
    payload = "x" * max(1, args.payload_bytes)
    results = [
        run_level(
            repository,
            concurrency=max(1, concurrency),
            writes_per_worker=max(1, args.writes_per_worker),
            trace_count=max(1, args.trace_count),
            payload=payload,
        )
        for concurrency in args.concurrency
    ]
    with store.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    output = {
        "database": str(database),
        "database_size_bytes": database.stat().st_size if database.exists() else 0,
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if temporary is not None:
        temporary.cleanup()
    return 1 if any(item["error_count"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
