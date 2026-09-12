from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from scripts.audit_v3_reply_latency import collect_latency_report  # noqa: E402


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, runs, traces):
        self.runs = runs
        self.traces = traces

    def execute(self, sql, _params):
        return _Result(self.traces if "node_traces" in sql else self.runs)


class _Store:
    table_prefix = "aics_"

    def __init__(self, runs, traces):
        self.connection = _Connection(runs, traces)

    @contextmanager
    def connect(self):
        yield self.connection


def test_latency_audit_emits_only_aggregates() -> None:
    runs = []
    traces = []
    for index, duration in enumerate((1000, 2000, 3000), start=1):
        request_id = f"request-{index}"
        runs.append(
            {
                "request_id": request_id,
                "duration_ms": duration,
                "error": "",
                "created_at": f"2026-09-12T00:00:0{index}+00:00",
                "output_snapshot": json.dumps(
                    {
                        "interface_version": "v3",
                        "reply_source": "main_model",
                        "reply_messages": [{"content": f"secret-{index}"}],
                        "performance": {
                            "phases": {"full_graph": {"duration_ms": duration - 100}},
                            "reply_core_persistence": {
                                "duration_ms": 50,
                                "connection_acquire_ms": 20,
                                "connection_count": 1,
                                "statement_count": 6,
                            },
                        },
                    }
                ),
            }
        )
        traces.extend(
            [
                {
                    "request_id": request_id,
                    "node_name": "synthesize_reply",
                    "duration_ms": 500,
                    "error": "",
                    "output_snapshot": "{}",
                },
                {
                    "request_id": request_id,
                    "node_name": "layer_2_background_context",
                    "duration_ms": 200,
                    "error": "",
                    "output_snapshot": json.dumps(
                        {
                            "background_substeps": [
                                {"name": "customer_read_snapshot", "duration_ms": 80}
                            ],
                            "customer_profile": {"name": f"private-{index}"},
                        }
                    ),
                },
            ]
        )

    report = collect_latency_report(
        _Store(runs, traces),  # type: ignore[arg-type]
        limit=3,
        scan_limit=10,
        release_sha="a" * 40,
    )
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["sample_count"] == 3
    assert report["ai_graph_sample_count"] == 3
    assert report["http"]["p50_ms"] == 2000
    assert report["http"]["p95_ms"] == 3000
    assert report["background_substeps"]["customer_read_snapshot"]["p50_ms"] == 80
    assert report["reply_core_persistence"]["connection_acquire_ms"]["p50_ms"] == 20
    assert "secret" not in rendered
    assert "private" not in rendered
    assert report["privacy"]["customer_content_emitted"] is False


def test_latency_audit_excludes_protocol_and_non_v3_runs() -> None:
    runs = [
        {
            "request_id": "protocol",
            "duration_ms": 10,
            "error": "",
            "created_at": "2026-09-12T00:00:00+00:00",
            "output_snapshot": json.dumps(
                {"interface_version": "v3", "reply_source": "ignored_platform_auto_message"}
            ),
        },
        {
            "request_id": "legacy",
            "duration_ms": 20,
            "error": "",
            "created_at": "2026-09-12T00:00:00+00:00",
            "output_snapshot": json.dumps(
                {"interface_version": "v2", "reply_source": "main_model"}
            ),
        },
    ]

    report = collect_latency_report(
        _Store(runs, []),  # type: ignore[arg-type]
        limit=10,
        scan_limit=10,
        release_sha="",
    )

    assert report["sample_count"] == 0
