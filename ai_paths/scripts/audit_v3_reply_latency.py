"""Produce a customer-free V3 latency report from existing run telemetry.

The script is read-only. It parses persisted snapshots in memory but emits only
counts, time windows, durations, node names, and error/fallback totals.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings  # noqa: E402
from app.services.storage.mysql_store import MySQLStore  # noqa: E402
from app.services.storage.serialization import loads_dict  # noqa: E402


_SAFE_PREFIX = re.compile(r"^[A-Za-z0-9_]+$")
_EXCLUDED_SOURCES = {
    "ignored_platform_auto_message",
    "platform_protocol_filter",
    "platform_protocol_ignored",
}


def _percentile(values: Iterable[int], percentile: float) -> int:
    ordered = sorted(max(0, int(value or 0)) for value in values)
    if not ordered:
        return 0
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _duration_summary(values: Iterable[int]) -> dict[str, int]:
    clean = [max(0, int(value or 0)) for value in values]
    return {
        "count": len(clean),
        "p50_ms": _percentile(clean, 0.50),
        "p95_ms": _percentile(clean, 0.95),
        "max_ms": max(clean, default=0),
    }


def _natural_v3_run(row: dict[str, Any], output: dict[str, Any]) -> bool:
    return (
        int(row.get("duration_ms") or 0) > 0
        and str(output.get("interface_version") or "").strip().lower() == "v3"
        and bool(str(output.get("reply_source") or "").strip())
        and str(output.get("reply_source") or "").strip() not in _EXCLUDED_SOURCES
    )


def collect_latency_report(
    store: MySQLStore,
    *,
    limit: int,
    scan_limit: int,
    release_sha: str,
) -> dict[str, Any]:
    prefix = str(store.table_prefix or "")
    if not _SAFE_PREFIX.fullmatch(prefix):
        raise ValueError("unsafe AICS table prefix")
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT request_id, duration_ms, error, output_snapshot, created_at
            FROM runs
            WHERE duration_ms>0
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(limit, scan_limit),),
        ).fetchall()
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in rows:
        row = dict(raw)
        output = loads_dict(row.get("output_snapshot"))
        if _natural_v3_run(row, output):
            selected.append((row, output))
        if len(selected) >= limit:
            break

    request_ids = [str(row["request_id"]) for row, _output in selected]
    traces: list[dict[str, Any]] = []
    with store.connect() as conn:
        for offset in range(0, len(request_ids), 200):
            chunk = request_ids[offset : offset + 200]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            traces.extend(
                dict(item)
                for item in conn.execute(
                    f"""
                    SELECT request_id, node_name, duration_ms, error, output_snapshot
                    FROM node_traces
                    WHERE request_id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
            )

    ai_request_ids = {
        str(item.get("request_id") or "")
        for item in traces
        if str(item.get("node_name") or "") == "synthesize_reply"
    }
    phase_values: dict[str, list[int]] = defaultdict(list)
    persistence_values: dict[str, list[int]] = defaultdict(list)
    source_counts: Counter[str] = Counter()
    fallback_or_error = 0
    for row, output in selected:
        source = str(output.get("reply_source") or "").strip()
        source_counts[source] += 1
        fallback_or_error += int("fallback" in source or bool(str(row.get("error") or "").strip()))
        performance = output.get("performance") if isinstance(output.get("performance"), dict) else {}
        phases = performance.get("phases") if isinstance(performance.get("phases"), dict) else {}
        for name, value in phases.items():
            duration = value.get("duration_ms") if isinstance(value, dict) else value
            phase_values[str(name)].append(max(0, int(duration or 0)))
        reply_core = (
            performance.get("reply_core_persistence")
            if isinstance(performance.get("reply_core_persistence"), dict)
            else {}
        )
        for name, value in reply_core.items():
            persistence_values[str(name)].append(max(0, int(value or 0)))

    node_values: dict[str, list[int]] = defaultdict(list)
    background_values: dict[str, list[int]] = defaultdict(list)
    for trace in traces:
        node_name = str(trace.get("node_name") or "")
        node_values[node_name].append(max(0, int(trace.get("duration_ms") or 0)))
        if node_name != "layer_2_background_context":
            continue
        output = loads_dict(trace.get("output_snapshot"))
        for item in output.get("background_substeps") or []:
            if isinstance(item, dict) and str(item.get("name") or ""):
                background_values[str(item["name"])].append(
                    max(0, int(item.get("duration_ms") or 0))
                )

    ai_durations = [
        int(row.get("duration_ms") or 0)
        for row, _output in selected
        if str(row.get("request_id") or "") in ai_request_ids
    ]
    report = {
        "schema_version": "v3_reply_latency_audit_v1",
        "scope": "persisted_natural_v3_requests_customer_free",
        "release_sha": str(release_sha or ""),
        "sample_count": len(selected),
        "ai_graph_sample_count": len(ai_durations),
        "window": {
            "latest": str(selected[0][0].get("created_at") or "") if selected else "",
            "oldest": str(selected[-1][0].get("created_at") or "") if selected else "",
        },
        "http": _duration_summary(row["duration_ms"] for row, _output in selected),
        "ai_graph_http": _duration_summary(ai_durations),
        "fallback_or_error_count": fallback_or_error,
        "reply_source_counts": dict(sorted(source_counts.items())),
        "phases": {
            name: _duration_summary(values) for name, values in sorted(phase_values.items())
        },
        "nodes": {
            name: _duration_summary(values) for name, values in sorted(node_values.items())
        },
        "background_substeps": {
            name: _duration_summary(values)
            for name, values in sorted(background_values.items())
        },
        "reply_core_persistence": {
            name: _duration_summary(values)
            for name, values in sorted(persistence_values.items())
        },
        "privacy": {
            "customer_content_emitted": False,
            "customer_identity_emitted": False,
            "database_address_emitted": False,
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--scan-limit", type=int, default=1000)
    parser.add_argument("--minimum-sample", type=int, default=30)
    parser.add_argument("--release-sha", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    if settings.aics_storage_backend != "mysql":
        raise RuntimeError("latency audit requires the configured MySQL telemetry store")
    store = MySQLStore(settings)
    try:
        report = collect_latency_report(
            store,
            limit=max(1, min(args.limit, 500)),
            scan_limit=max(1, min(args.scan_limit, 5000)),
            release_sha=args.release_sha,
        )
    finally:
        store.close()
    if int(report["sample_count"]) < max(1, args.minimum_sample):
        raise RuntimeError(
            f"insufficient natural V3 samples: {report['sample_count']} < {args.minimum_sample}"
        )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
