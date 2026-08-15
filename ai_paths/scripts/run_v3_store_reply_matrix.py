from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.graph.graph_builder import build_reply_graphs
from app.graph.nodes.action_nodes import _snapshot_store_values
from app.services.coze_client import CozeClient
from app.services.customer_scope import customer_scope_from_state
from app.services.memory_store import CustomerMemoryStore
from app.services.model_client import ModelClient
from app.services.model_led_objection_playbook_service import ModelLedObjectionPlaybookService
from app.services.runtime_budget import build_runtime_budget
from app.services.sop_execution_service import SopExecutionService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.storage import AppRepository, SQLiteStore
from app.services.trace_logger import TraceLogger


DEFAULT_FIXTURE = Path("workflow_tests/fixtures/v3_store_address_matrix_20260814.json")
DEFAULT_OUTPUT = Path(".tmp_runtime/v3_store_reply_matrix_20260814.json")


class _AllVisibleStoreKnowledge:
    """Expose the snapshot as a synthetic customer's read-only visible scope."""

    def __init__(self, stores: list[dict[str, Any]]) -> None:
        self._value = {
            "source": "simulation_all_visible_store_snapshot",
            "stores": deepcopy(stores),
            "appointment_extra_stores": [],
            "store_count": len(stores),
            "simulation_scope": True,
        }

    def load(self, **_: Any) -> dict[str, Any]:
        return deepcopy(self._value)

    def with_appointment_extra_stores(
        self,
        *,
        customer_store_knowledge: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        return deepcopy(customer_store_knowledge)


def _initial_state(
    *,
    settings: Settings,
    index: int,
    address: str,
) -> dict[str, Any]:
    customer_message = f"我在{address}，离我最近的门店在哪里？把具体地址发我。"
    state: dict[str, Any] = {
        "request_id": f"sim_store_reply_{index:03d}_{uuid4().hex[:8]}",
        "customer_id": f"sim_store_reply_{index:03d}",
        "corp_id": "sim_corp",
        "content": customer_message,
        "conversation_history": [f"用户: {customer_message}"],
        "conversation_turns": [],
        "file_image": None,
        "image_urls": [],
        "user_id": 0,
        "wechat": "sim_wechat",
        "external_userid": f"sim_external_{index:03d}",
        "request_context": {
            "source_protocol": "v3_store_reply_matrix",
            "workflow_id": "v3-store-reply-matrix",
            "msgtype": "text",
            "simulation_mode": True,
            "test_isolated": True,
            "memory_persist_allowed": False,
        },
        "test_isolated": True,
        "memory_persist_allowed": False,
        "runtime_budget": build_runtime_budget(settings),
        "trace": [],
        "errors": [],
        "warnings": [],
    }
    scope = customer_scope_from_state(state)
    state["sales_contact_key"] = scope.sales_contact_key
    state["global_customer_key"] = scope.global_customer_key
    state["customer_scope"] = scope.as_dict()
    return state


def _store_cards(messages: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "store_address":
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        store_id = str(content.get("store_id") or "").strip()
        if store_id:
            output.append(store_id)
    return output


def _tool_facts(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    workflow = tool_results.get("resolve_customer_store")
    lookup = tool_results.get("customer_store_lookup")
    distance = tool_results.get("distance_calculate")
    return (
        workflow if isinstance(workflow, dict) else {},
        lookup if isinstance(lookup, dict) else {},
        distance if isinstance(distance, dict) else {},
    )


def _model_timings(state: dict[str, Any]) -> dict[str, int]:
    timings: dict[str, int] = {}
    for item in state.get("trace") or []:
        if not isinstance(item, dict):
            continue
        node = str(item.get("node") or "").strip()
        if node:
            timings[node] = timings.get(node, 0) + int(item.get("duration_ms") or 0)
    return timings


async def _run_case(
    *,
    graph: Any,
    settings: Settings,
    index: int,
    address: str,
    visible_store_ids: set[str],
    store_names: dict[str, str],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        # The round deadline belongs to execution time. Creating it while the
        # case is waiting for a concurrency slot would expire queued cases.
        state = _initial_state(settings=settings, index=index, address=address)
        started = time.perf_counter()
        error = ""
        try:
            final_state = await graph.ainvoke(state)
        except Exception as exc:
            final_state = state
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    messages = [item for item in final_state.get("reply_messages") or [] if isinstance(item, dict)]
    cards = _store_cards(messages)
    workflow, lookup, distance = _tool_facts(final_state)
    fact_envelope = (
        final_state.get("fact_envelope")
        if isinstance(final_state.get("fact_envelope"), dict)
        else {}
    )
    structured_facts = (
        fact_envelope.get("structured_facts")
        if isinstance(fact_envelope.get("structured_facts"), dict)
        else {}
    )
    resolution_fact = (
        structured_facts.get("store_resolution_fact")
        if isinstance(structured_facts.get("store_resolution_fact"), dict)
        else {}
    )
    ranked = [item for item in distance.get("ranked_stores") or [] if isinstance(item, dict)]
    lookup_candidates = [item for item in lookup.get("candidate_stores") or [] if isinstance(item, dict)]
    candidates = ranked or lookup_candidates
    candidate_ids = [str(item.get("store_id") or "").strip() for item in candidates]
    violations: list[str] = []
    if error:
        violations.append("graph_exception")
    if not messages:
        violations.append("empty_reply")
    if any(
        message.get("type") == "text" and str(message.get("content") or "").strip() == "您稍等一下"
        for message in messages
    ):
        violations.append("neutral_fallback")
    if any(store_id not in visible_store_ids for store_id in cards):
        violations.append("invisible_store_card")
    if cards and any(store_id not in set(candidate_ids) for store_id in cards):
        violations.append("card_not_in_resolved_candidates")
    required_card_ids = [
        str(item or "").strip()
        for item in resolution_fact.get("delivery_store_ids") or []
        if str(item or "").strip()
    ]
    resolution_status = str(resolution_fact.get("status") or "")
    clarification_statuses = {
        "need_location",
        "need_location_confirmation",
        "ambiguous_location",
        "no_valid_candidate",
        "reuse_confirmed_store",
    }
    if resolution_status in {"send_single", "send_multiple"} and cards != required_card_ids:
        violations.append("required_store_delivery_mismatch")
    elif candidates and not cards and resolution_status not in clarification_statuses:
        violations.append("resolved_store_not_delivered")
    if resolution_status in clarification_statuses and cards:
        violations.append("store_card_not_allowed_for_resolution_status")
    if str(workflow.get("status") or "") in {"error", "store_scope_unavailable"}:
        violations.append("store_tool_failed")
    if not workflow:
        violations.append("store_tool_not_called")
    return {
        "case_id": f"address_{index:03d}",
        "input_address": address,
        "customer_message": state["content"],
        "elapsed_ms": elapsed_ms,
        "hard_pass": not violations,
        "violations": violations,
        "error": error,
        "tool_plan": deepcopy(final_state.get("tool_plan") or {}),
        "store_resolution": {
            "status": workflow.get("status"),
            "workflow_error": workflow.get("error"),
            "query": lookup.get("query") or distance.get("geocode_origin"),
            "lookup_error": lookup.get("error"),
            "destination": workflow.get("destination_resolution") or {},
            "geocode": lookup.get("geocode") or distance.get("origin_geocode") or {},
            "candidate_search_complete": lookup.get("candidate_search_complete"),
            "lookup_status": lookup.get("status"),
            "distance_status": distance.get("status"),
            "ranking_complete": distance.get("ranking_complete"),
            "candidate_store_count": len(candidates),
            "candidate_stores": candidates[:5],
            "final_fact": resolution_fact,
        },
        "reply_messages": messages,
        "store_cards": [
            {"store_id": store_id, "store_name": store_names.get(store_id, "")}
            for store_id in cards
        ],
        "reply_source": final_state.get("reply_source"),
        "reply_action": final_state.get("reply_action"),
        "content_gate": deepcopy(final_state.get("content_gate_result") or {}),
        "selected_content_ids": final_state.get("selected_content_ids") or [],
        "reply_sales_judgment": deepcopy(final_state.get("reply_sales_judgment") or {}),
        "used_fact_refs": final_state.get("used_fact_refs") or [],
        "errors": final_state.get("errors") or [],
        "warnings": final_state.get("warnings") or [],
        "node_timings_ms": _model_timings(final_state),
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[max(0, min(len(ordered) - 1, index))]


def _message_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in messages:
        msg_type = str(item.get("type") or "")
        content = item.get("content")
        if msg_type == "text":
            parts.append(str(content or ""))
        elif msg_type == "store_address" and isinstance(content, dict):
            parts.append(f"[门店卡:{content.get('store_id', '')}]")
        else:
            parts.append(f"[{msg_type}:{content}]")
    return " / ".join(parts)


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# V3 门店场景真实回复矩阵",
        "",
        "> 隔离测试：使用服务器模型/Coze 凭据和门店快照；未调用发送接口，未写客户状态。",
        "> 测试可见范围：服务器门店快照中的全部有效门店，仅用于验证地点解析和最近门店回复。",
        "",
        f"- 场景数：{summary['total']}",
        f"- 硬通过：{summary['hard_passed']}",
        f"- 硬失败：{summary['hard_failed']}",
        f"- P50/P90：{summary['p50_ms']}ms / {summary['p90_ms']}ms",
        f"- 门店快照数：{report['store_scope']['store_count']}",
        "",
        "| ID | 客户地址 | 解析地点 | 候选门店 | 最终客户可见回复 | 结果 |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["cases"]:
        resolution = item["store_resolution"]
        stores = "、".join(
            str(store.get("store_name") or store.get("store_id") or "")
            for store in resolution.get("candidate_stores") or []
        )
        destination = str(resolution.get("query") or "")
        reply = _message_text(item["reply_messages"])
        status = "PASS" if item["hard_pass"] else "FAIL:" + ",".join(item["violations"])
        cells = [item["case_id"], item["input_address"], destination, stores, reply, status]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", "<br>") for value in cells) + " |")
    return "\n".join(lines) + "\n"


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated real V3 store replies for the address matrix.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--case-ids", default="", help="Comma-separated case IDs.")
    parser.add_argument(
        "--with-content-gate",
        action="store_true",
        help="Run the real content Gate with an isolated SQLite state store.",
    )
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    addresses = [str(value).strip() for value in fixture.get("addresses") or [] if str(value).strip()]
    selected = {value.strip() for value in args.case_ids.split(",") if value.strip()}
    indexed = [
        (index, address)
        for index, address in enumerate(addresses, start=1)
        if not selected or f"address_{index:03d}" in selected
    ]
    stores = _snapshot_store_values()
    if not stores:
        raise RuntimeError("Store snapshot is unavailable; refusing to fabricate store facts.")
    visible_store_ids = {str(item.get("store_id") or item.get("id") or "").strip() for item in stores}
    store_names = {
        str(item.get("store_id") or item.get("id") or "").strip(): str(item.get("store_name") or "")
        for item in stores
    }

    settings = Settings().model_copy(
        update={
            "trace_log_dir": args.output.parent / "traces",
            "log_dir": args.output.parent / "traces",
            "memory_dir": args.output.parent / "memory",
            "db_path": args.output.parent / "simulation_state.db",
            "aics_storage_backend": "sqlite",
            "model_request_retry_attempts": 2,
            "model_round_timeout_seconds": 120.0,
            "model_strong_round_timeout_seconds": 120.0,
        }
    )
    model_client = ModelClient(settings)
    coze_client = CozeClient(settings)
    trace_logger = TraceLogger(settings)
    sop_execution_service = None
    if args.with_content_gate:
        sqlite_store = SQLiteStore(settings)
        sqlite_store.initialize()
        repository = AppRepository(sqlite_store)
        memory_store = CustomerMemoryStore(settings, repository)
        sop_execution_service = SopExecutionService(
            repository=repository,
            sop_reply_pack_service=SopReplyPackService(settings),
            model_client=model_client,
            memory_store=memory_store,
            customer_context_service=None,
            event_model_retry_attempts=settings.sop_event_model_retry_attempts,
            event_model_retry_delay_seconds=settings.sop_event_model_retry_delay_seconds,
            event_model_attempt_timeout_seconds=settings.sop_event_model_attempt_timeout_seconds,
            event_model_total_timeout_seconds=settings.sop_event_model_total_timeout_seconds,
            chat_gate_total_timeout_seconds=settings.sop_chat_gate_total_timeout_seconds,
            event_model_max_concurrency=max(1, int(args.concurrency)),
            model_led_objection_playbook_service=ModelLedObjectionPlaybookService(
                settings.v2_model_led_objection_playbook_path
            ),
        )
    graphs = build_reply_graphs(
        coze_client=coze_client,
        trace_logger=trace_logger,
        model_client=model_client,
        customer_store_knowledge_service=_AllVisibleStoreKnowledge(stores),
        sop_execution_service=sop_execution_service,
    )
    semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))
    try:
        cases = await asyncio.gather(
            *(
                _run_case(
                    graph=graphs.full_graph,
                    settings=settings,
                    index=index,
                    address=address,
                    visible_store_ids=visible_store_ids,
                    store_names=store_names,
                    semaphore=semaphore,
                )
                for index, address in indexed
            )
        )
    finally:
        await model_client.aclose()
        await coze_client.aclose()

    durations = [int(item["elapsed_ms"]) for item in cases]
    failures = [item for item in cases if not item["hard_pass"]]
    report = {
        "schema_version": "v3_store_reply_matrix_v1",
        "fixture": str(args.fixture),
        "isolation": {
            "simulation_mode": True,
            "customer_prefix": "sim_store_reply_",
            "external_send": False,
            "customer_state_write": False,
            "real_model": True,
            "real_coze_geocode": True,
            "content_gate": bool(args.with_content_gate),
        },
        "models": {
            "store_destination": settings.model_store_destination,
            "planner": settings.model_planner,
            "reply": settings.model_reply,
        },
        "store_scope": {
            "source": "server_store_snapshot_all_visible_for_simulation",
            "store_count": len(stores),
        },
        "summary": {
            "total": len(cases),
            "hard_passed": len(cases) - len(failures),
            "hard_failed": len(failures),
            "fallback_count": sum("neutral_fallback" in item["violations"] for item in cases),
            "store_tool_missing_count": sum("store_tool_not_called" in item["violations"] for item in cases),
            "mean_ms": int(statistics.mean(durations)) if durations else 0,
            "p50_ms": _percentile(durations, 0.50),
            "p90_ms": _percentile(durations, 0.90),
            "max_ms": max(durations, default=0),
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print(f"JSON: {args.output}", flush=True)
    print(f"Markdown: {markdown_path}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
