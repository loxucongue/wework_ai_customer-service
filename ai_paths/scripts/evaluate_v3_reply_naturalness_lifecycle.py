from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AI_PATHS_ROOT = ROOT / "ai_paths"
if str(AI_PATHS_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_PATHS_ROOT))

from app.chat_runtime import ChatRuntime, _chat_response_from_generation  # noqa: E402
from app.config import Settings  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.services.memory_store import CustomerMemoryStore  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.services.trace_logger import TraceLogger  # noqa: E402
from app.services.v3_reply_finalization_service import V3ReplyFinalizationService  # noqa: E402
from app.services.v3_reply_recovery import v3_generation_key  # noqa: E402
from app.services.workflow_compat import workflow_response_from_chat  # noqa: E402
from scripts.v3_reply_naturalness_cases import CASES  # noqa: E402


class _AiStatusClient:
    available = True

    async def conversation_status(self, **_kwargs: object) -> dict[str, object]:
        return {"data": {"takeover": {"mode": "ai", "is_human": False}}}


class _ReplayGraph:
    """Replay already-generated DeepSeek replies through the real persistence tail."""

    def __init__(self, replies: dict[str, dict[str, Any]]) -> None:
        self._replies = replies
        self.duration_ms: dict[str, int] = {}

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        case_id = str(state.get("external_userid") or "").removeprefix("eval-")
        reply = self._replies[case_id]
        messages = []
        for order, raw in enumerate(reply.get("reply_messages") or [], start=1):
            message = dict(raw)
            message["order"] = order
            messages.append(message)
        state["reply_messages"] = messages
        state["reply_source"] = "main_model"
        state["decision_status"] = "ok"
        state["sales_judgment"] = dict(reply.get("sales_judgment") or {})
        state["policy_decision"] = dict(reply.get("policy_decision") or {})
        state["reply_knowledge_use"] = dict(reply.get("knowledge_use") or {})
        state["selected_content_ids"] = list(reply.get("selected_content_ids") or [])
        state.setdefault("trace", []).append(
            {
                "node": "l2_deepseek_reply_replay",
                "case_id": case_id,
                "source": "ignored_l2_artifact",
            }
        )
        self.duration_ms[case_id] = int((time.perf_counter() - started) * 1000)
        return state


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", default="candidate_prompt_candidate_order")
    return parser.parse_args()


def _load_l3_rows(path: Path, *, variant: str) -> list[dict[str, Any]]:
    wanted = {str(case["id"]) for case in CASES if bool(case.get("l3"))}
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            case_id = str(row.get("case_id") or "")
            if row.get("variant") == variant and int(row.get("rep") or 0) == 0 and case_id in wanted:
                rows[case_id] = row
    missing = sorted(wanted - rows.keys())
    if missing:
        raise RuntimeError("missing L3 source cases: " + ",".join(missing))
    return [rows[str(case["id"])] for case in CASES if str(case["id"]) in wanted]


def _without_replay_markers(body: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(body, ensure_ascii=False))
    normalized.pop("replayed", None)
    data = normalized.get("data") if isinstance(normalized.get("data"), dict) else {}
    data.pop("replayed", None)
    return normalized


async def _run(args: argparse.Namespace) -> int:
    rows = _load_l3_rows(args.input, variant=args.variant)
    args.output.mkdir(parents=True, exist_ok=True)
    ephemeral = args.output / ".ephemeral"
    if ephemeral.exists():
        shutil.rmtree(ephemeral)
    ephemeral.mkdir(parents=True)
    settings = Settings().model_copy(
        update={
            "service_role": "reply",
            "aics_storage_backend": "sqlite",
            "db_path": ephemeral / "state.db",
            "memory_dir": ephemeral / "memory",
            "trace_log_dir": ephemeral / "trace",
            "background_workers_enabled": False,
        }
    )
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    memory = CustomerMemoryStore(settings, repository)
    replies = {str(row["case_id"]): dict(row.get("reply") or {}) for row in rows}
    graph = _ReplayGraph(replies)
    runtime = ChatRuntime(
        full_graph=graph,
        commit_graph=None,
        trace_logger=TraceLogger(settings),
        repository=repository,
        memory_store=memory,
        outreach_system_client=_AiStatusClient(),  # type: ignore[arg-type]
        settings=settings,
    )
    results: list[dict[str, Any]] = []
    request_ids: set[str] = set()
    for row in rows:
        case_id = str(row["case_id"])
        msgid = f"naturalness-l3-{case_id}"
        request = ChatRequest(
            content=next(str(case["current"]) for case in CASES if case["id"] == case_id),
            customer_id=f"customer-{case_id}",
            corp_id="synthetic-corp",
            wechat="synthetic-wechat",
            external_userid=f"eval-{case_id}",
            request_context={
                "interface_version": "v3",
                "source_protocol": "synthetic_naturalness_lifecycle_replay",
                "msgid": msgid,
            },
        )
        response = await runtime.run_platform_reply(request)
        public_body = workflow_response_from_chat(response)
        generation_key = v3_generation_key(
            corp_id=request.corp_id,
            wechat=str(request.wechat or ""),
            external_userid=str(request.external_userid or ""),
            msgid=msgid,
        )
        durable = repository.get_v3_generation_result(generation_key=generation_key)
        durable_body = workflow_response_from_chat(_chat_response_from_generation(durable))
        repository.update_run_http_response(request_id=response.request_id, response_body=public_body)
        request_ids.add(str(response.request_id))
        results.append(
            {
                "case_id": case_id,
                "source_model": str(row.get("model") or ""),
                "source_fallback_index": int((row.get("usage") or {}).get("fallback_index") or 0),
                "reply_message_count": len((public_body.get("data") or {}).get("reply_messages") or []),
                "durable_replay_equal": _without_replay_markers(durable_body)
                == _without_replay_markers(public_body),
                "runtime_status": str(
                    ((repository.get_run(response.request_id).get("run") or {}).get("output_snapshot") or {}).get(
                        "runtime_status"
                    )
                    or ""
                ),
            }
        )
    finalizer = V3ReplyFinalizationService(
        repository=repository,
        trace_logger=TraceLogger(settings),
        service_rule_data_service=None,
        outreach_service=None,
        batch_size=len(rows) + 5,
    )
    finalization = finalizer.process_batch()
    completed_after_finalize = 0
    for request_id in request_ids:
        snapshot = (repository.get_run(request_id).get("run") or {}).get("output_snapshot") or {}
        if (snapshot.get("post_reply_finalization") or {}).get("status") == "completed":
            completed_after_finalize += 1
    with store.connect() as conn:
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("runs", "messages", "v3_strategy_usage_events", "message_dispatches", "strategy_data_outbox")
        }
    metrics = {
        "mode": "l2_deepseek_output_ephemeral_lifecycle_replay",
        "case_count": len(rows),
        "lifecycle_passed": sum(
            bool(result["durable_replay_equal"])
            and result["runtime_status"] == "completed"
            and result["reply_message_count"] > 0
            for result in results
        ),
        "unique_request_ids": len(request_ids),
        "durable_replay_equal": sum(bool(result["durable_replay_equal"]) for result in results),
        "source_models": sorted({str(result["source_model"]) for result in results}),
        "source_fallback_indexes": sorted({int(result["source_fallback_index"]) for result in results}),
        "new_external_model_calls": 0,
        "router_or_tool_calls": 0,
        "finalization": finalization,
        "finalization_completed": completed_after_finalize,
        "ephemeral_counts": counts,
        "scope_note": "Replays real L2 DeepSeek outputs through ChatRuntime persistence/response/finalization only; it does not re-run Router or tools.",
        "cases": results,
    }
    (args.output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    store.close()
    shutil.rmtree(ephemeral)
    return 0 if metrics["lifecycle_passed"] == len(rows) and completed_after_finalize == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parse_args())))
