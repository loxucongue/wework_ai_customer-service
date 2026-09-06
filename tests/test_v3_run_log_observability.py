from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_paths"))

from app.config import Settings  # noqa: E402
from app.routers.operations_admin import create_operations_admin_router  # noqa: E402
from app.services.run_observability import (  # noqa: E402
    build_v3_run_observability,
    enrich_admin_observability_v3,
)
from app.services.run_observability_summary import sanitize_debug_payload  # noqa: E402
from app.services.storage.repositories import AppRepository  # noqa: E402
from app.services.storage.sqlite_store import SQLiteStore  # noqa: E402


def test_v3_admin_view_separates_router_from_reply_and_explains_adoption() -> None:
    view = {
        "summary": {},
        "nodes": [
            {
                "id": "node-router",
                "node_name": "semantic_evidence",
                "status": "success",
                "duration_ms": 120,
                "summary": ["识别到效果信任卡点"],
            },
            {
                "id": "node-reply",
                "node_name": "reply_decision",
                "status": "success",
                "duration_ms": 450,
                "summary": ["完成最终销售决策"],
            },
        ],
        "delivery": {"status": "direct_response_returned"},
    }
    detail = {
        "run": {
            "request_id": "run-1",
            "runtime_status": "completed",
            "created_at": "2026-09-06T08:00:00+00:00",
            "output_snapshot": {
                "decision_status": "valid",
                "realtime_intent": {"type": "fact_inquiry", "confidence": "high"},
                "emotion_decision": {"label": "curious", "pressure": "low"},
                "cardpoint_decision": {
                    "category_key": "effect_boundary",
                    "scenario_query": "客户询问一次能否做好",
                    "state": "active",
                },
                "closing_decision": {"action": "pause", "customer_state": "blocked"},
                "observability_v3": {
                    "checkpoint_decision": {
                        "classification_status": "matched",
                        "primary": {"code": "effect_trust", "name": "效果信任"},
                    },
                    "knowledge_match": {
                        "execution": {"script_lookup_invoked": True},
                        "matched_sequences": [{"sequence_id": "seq-1", "sequence_name": "效果证明"}],
                        "script_candidates": [{"script_id": "script-1", "script_name": "效果解释"}],
                        "adopted": {},
                    },
                },
            },
        },
        "strategy_usage_event": {},
    }

    result = enrich_admin_observability_v3(view, detail)

    assert result["decision_summary"]["intent"]["name"] == "咨询事实"
    assert result["checkpoint_summary"]["router"]["primary"]["code"] == "effect_trust"
    assert result["checkpoint_summary"]["final"]["category_key"] == "effect_boundary"
    assert result["knowledge_match"]["adoption_explanation"] == {
        "sequence": "reply_not_adopted",
        "script": "reply_not_adopted",
    }
    assert result["workflow_nodes"][2]["status"] == "success"
    assert result["workflow_nodes"][5]["status"] == "success"


def test_persisted_v3_summary_keeps_model_identity_without_prompt_content() -> None:
    stored = build_v3_run_observability(
        {
            "request_context": {"interface_version": "v3"},
            "trace": [
                {
                    "node": "reply_decision",
                    "tool_calls": [
                        {
                            "name": "reply_model",
                            "input": {
                                "model": "deepseek-chat",
                                "messages": [{"role": "user", "content": "客户原始内容"}],
                            },
                            "usage": {"model": "deepseek-chat", "total_tokens": 321, "attempts": 1},
                        }
                    ],
                }
            ],
        }
    )

    assert stored["model_usage"] == [
        {
            "node": "reply_decision",
            "name": "reply_model",
            "provider": "",
            "model": "deepseek-chat",
            "configured_model": "",
            "total_tokens": 321,
            "duration_ms": 0,
            "attempts": 1,
            "fallback_used": False,
        }
    ]
    assert "客户原始内容" not in json.dumps(stored["model_usage"], ensure_ascii=False)


def test_lightweight_run_detail_keeps_node_headers_and_loads_raw_node_on_demand(tmp_path: Path) -> None:
    settings = Settings(AI_PATHS_DB_PATH=tmp_path / "logs.db", AICS_STORAGE_BACKEND="sqlite")
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    now = "2026-09-06T08:00:00+00:00"
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id, external_userid, corp_id, wechat, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("conversation-1", "customer-1", "external-1", "corp-1", "sl8003", now, now),
        )
        for request_id in ("run-with-bi", "run-without-bi"):
            conn.execute(
                "INSERT INTO runs (request_id, conversation_id, customer_id, input_snapshot, output_snapshot, created_at) VALUES (?,?,?,?,?,?)",
                (
                    request_id,
                    "conversation-1",
                    "customer-1",
                    json.dumps({"content": "多少钱"}, ensure_ascii=False),
                    json.dumps({"realtime_intent": {"type": "fact_inquiry"}}, ensure_ascii=False),
                    now,
                ),
            )
        conn.execute(
            "INSERT INTO node_traces (id, request_id, node_name, input_snapshot, output_snapshot, tool_calls, duration_ms, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "node-1",
                "run-with-bi",
                "semantic_evidence",
                json.dumps({"authorization": "Bearer secret", "content": "多少钱"}, ensure_ascii=False),
                json.dumps({"checkpoint": "price"}, ensure_ascii=False),
                "[]",
                123,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO v3_strategy_usage_events (id, request_id, occurred_at, intent_code, emotion_before, checkpoint_code, sequence_candidate_count, sequence_id, script_id, decision_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("usage-1", "run-with-bi", now, "fact_inquiry", "curious", "price", 2, "seq-1", "script-1", "valid", now, now),
        )

    rows = repository.list_runs(limit=10)
    assert {row["request_id"] for row in rows} == {"run-with-bi", "run-without-bi"}
    with_bi = next(row for row in rows if row["request_id"] == "run-with-bi")
    without_bi = next(row for row in rows if row["request_id"] == "run-without-bi")
    assert with_bi["business_summary"]["wechat"] == "sl8003"
    assert with_bi["business_summary"]["sequence_matched"] is True
    assert without_bi["business_summary"]["usage_event_recorded"] is False
    assert [row["request_id"] for row in repository.list_runs(limit=10, sequence_adopted=True)] == ["run-with-bi"]

    lightweight = repository.get_run("run-with-bi", include_debug=False)
    assert lightweight["node_traces"][0]["id"] == "node-1"
    assert lightweight["node_traces"][0]["input_snapshot"] == {}
    raw = repository.get_run_node_trace(request_id="run-with-bi", node_id="node-1")
    assert raw["input_snapshot"]["content"] == "多少钱"
    assert repository.get_run_node_trace(request_id="run-without-bi", node_id="node-1") == {}


def test_node_debug_payload_masks_credentials_without_removing_business_input() -> None:
    sanitized = sanitize_debug_payload(
        {
            "input_snapshot": {"content": "查一下附近门店", "token": "secret-token"},
            "tool_calls": [{"input": {"Authorization": "Bearer abc", "city": "杭州"}}],
        }
    )

    assert sanitized["input_snapshot"]["content"] == "查一下附近门店"
    assert sanitized["input_snapshot"]["token"] == "[已隐藏]"
    assert sanitized["tool_calls"][0]["input"]["Authorization"] == "[已隐藏]"


def test_run_log_admin_endpoints_forward_filters_and_lazy_load_node() -> None:
    class Repository:
        def __init__(self) -> None:
            self.list_filters: dict[str, object] = {}
            self.include_debug: bool | None = None

        def list_runs(self, **filters: object) -> list[dict[str, object]]:
            self.list_filters = filters
            return [{"request_id": "run-1", "business_summary": {"intent_code": "fact_inquiry"}}]

        def get_run(self, request_id: str, *, include_debug: bool = True) -> dict[str, object]:
            self.include_debug = include_debug
            return {
                "run": {
                    "request_id": request_id,
                    "runtime_status": "completed",
                    "created_at": "2026-09-06T08:00:00+00:00",
                    "input_snapshot": {"content": "多少钱", "conversation_history": ["不应进入轻量响应"]},
                    "output_snapshot": {"realtime_intent": {"type": "fact_inquiry"}},
                },
                "node_traces": [
                    {
                        "id": "node-1",
                        "request_id": request_id,
                        "node_name": "semantic_evidence",
                        "input_snapshot": {},
                        "output_snapshot": {},
                        "tool_calls": [],
                        "duration_ms": 10,
                        "created_at": "2026-09-06T08:00:00+00:00",
                    }
                ],
                "strategy_usage_event": {},
            }

        def get_run_node_trace(self, *, request_id: str, node_id: str) -> dict[str, object]:
            return {
                "id": node_id,
                "request_id": request_id,
                "node_name": "semantic_evidence",
                "input_snapshot": {"content": "多少钱", "token": "secret"},
                "output_snapshot": {"checkpoint": "price"},
                "tool_calls": [],
                "duration_ms": 10,
                "created_at": "2026-09-06T08:00:00+00:00",
            }

        def list_message_dispatches_for_request(self, _request_id: str) -> list[dict[str, object]]:
            return []

    repository = Repository()
    services = SimpleNamespace(
        repository=repository,
        trace_logger=SimpleNamespace(read_run=lambda _request_id: {}),
    )
    app = FastAPI()
    app.include_router(create_operations_admin_router(Settings(_env_file=None), services))  # type: ignore[arg-type]
    client = TestClient(app)

    list_response = client.get("/admin/runs", params={"sequence_adopted": "true", "node_failed": "false"})
    assert list_response.status_code == 200
    assert repository.list_filters["sequence_adopted"] is True
    assert repository.list_filters["node_failed"] is False

    detail_response = client.get("/admin/runs/run-1", params={"include_debug": "false"})
    assert detail_response.status_code == 200
    assert repository.include_debug is False
    assert detail_response.json()["observability_view"]["contract_version"] == "run_observability_v2"
    assert "raw_log" not in detail_response.json()
    assert "conversation_history" not in detail_response.json()["run"]["input_snapshot"]

    node_response = client.get("/admin/runs/run-1/nodes/node-1")
    assert node_response.status_code == 200
    assert node_response.json()["trace"]["input_snapshot"]["token"] == "[已隐藏]"
