"""Run the real V3 graph with real DeepSeek and synthetic external fact adapters.

No generated reply is replayed into the graph. External platform/content adapters
are synthetic; routing, fact-action execution, admission and persistence are real.
This is an isolated integration probe, not validation of production integrations.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from app.routers.reply import create_reply_router
from app.services.v3_request_timing import V3RequestTimingMiddleware, _BACKGROUND_FINALIZERS
from app.services.v3_reply_finalization_service import V3ReplyFinalizationService
from app.services.trace_logger import TraceLogger

from app.config import Settings
from app.services.ai_sales_policy_service import AiSalesPolicyService
from app.services.customer_context import CustomerContextService
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.model_client import ModelClient
from app.services.material_identity import prepare_catalog
from app.services.sales_strategy_service import SalesStrategyService
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.store_service import StoreService
from app.services.v3_semantic_router_service import V3SemanticRouterService
from scripts.evaluate_v3_full_chain_deepseek import build_case_runtime, ephemeral_counts
from scripts.evaluate_v3_reply_naturalness import _baseline_prompt
from scripts.v3_reply_naturalness_cases import CASES


class SyntheticPlatform:
    available = True

    def __init__(self, case: dict[str, Any]):
        self.case = case
        self.calls: list[str] = []

    def get_customer_info(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("get_customer_info")
        return {"id": "900001", "customer_add_wechat_id": "900002"}

    def list_orders(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append("list_orders")
        if self.case.get("authoritative_paid"):
            return [
                {
                    "id": "900003",
                    "store_id": "900004",
                    "prepay_required": 10,
                    "prepay_paid": 10,
                    "prepay_status": "paid",
                    "status": "pending",
                }
            ]
        return []

    def list_stores(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append("list_stores")
        return [{"id": "900004", "name": "杭州青禾护理中心"}]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"Synthetic platform operation unavailable: {name}")


class SyntheticStores:
    def load(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "synthetic_platform",
            "stores": [
                {
                    "store_id": "900004",
                    "store_name": "杭州青禾护理中心",
                    "province": "浙江省",
                    "city": "杭州市",
                    "district": "西湖区",
                    "store_address": "杭州市西湖区青禾路18号",
                    "business_hours": "10:00-20:00",
                    "is_open": True,
                    "parking_name": "青禾停车场",
                    "parking_address": "青禾路18号",
                    "latitude": 30.27,
                    "longitude": 120.13,
                    "source": "synthetic_platform",
                }
            ],
            "appointment_extra_stores": [],
        }

    def with_appointment_extra_stores(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs["customer_store_knowledge"]


class SyntheticCoze:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def run_workflow(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("External workflow unavailable in synthetic fixture")

    async def search_kb(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("External KB unavailable in synthetic fixture")


class SyntheticStatus:
    available = True

    async def conversation_status(self, **kwargs: Any) -> dict[str, Any]:
        return {"data": {"takeover": {"mode": "ai", "is_human": False}}}


class AuditedModel(ModelClient):
    async def chat_json(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return await super().chat_json(*args, **kwargs)
        finally:
            self.calls.append(
                {
                    "tier": kwargs.get("tier"),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "usage": copy.deepcopy(self.last_usage),
                }
            )


class AuditedSemantic(DeepSeekSemanticClient):
    async def chat_json(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return await super().chat_json(*args, **kwargs)
        finally:
            self.calls.append(
                {"duration_ms": int((time.perf_counter() - started) * 1000), "usage": copy.deepcopy(self.last_usage)}
            )


async def run_http_lifecycle(runtime: dict[str, Any], sample: dict[str, Any]) -> tuple[dict, dict, dict]:
    """Exercise the production route/middleware with temporary persistence only."""

    class TextOnlyCoordinator:
        async def prepare(self, request: Any, client: Any) -> Any:
            assert not request.file_image
            return request

    app = FastAPI()
    app.add_middleware(V3RequestTimingMiddleware, repository=runtime["repository"])
    isolated_settings = runtime["settings"].model_copy(
        update={
            "ai_paths_api_key": "synthetic-evaluation-token",
            "ai_external_api_key": "",
        }
    )
    app.include_router(
        create_reply_router(
            isolated_settings,
            SimpleNamespace(
                chat_runtime=runtime["runtime"],
                platform_voice_batch_coordinator=TextOnlyCoordinator(),
                voice_transcription_client=None,
            ),
        )
    )
    payload = {**sample, "platform_customer_id": "900001", "platform_user_id": "900005"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://isolated",
        headers={"Authorization": "Bearer synthetic-evaluation-token"},
    ) as client:
        http_started = time.perf_counter()
        first = await client.post("/reply/workflow-compatible-v3", json=payload)
        first_http_ms = int((time.perf_counter() - http_started) * 1000)
        first.raise_for_status()
        body = first.json()
        replay = await client.post("/reply/workflow-compatible-v3", json=payload)
        replay.raise_for_status()
        replay_body = replay.json()
    pending = list(_BACKGROUND_FINALIZERS)
    if pending:
        await asyncio.gather(*pending)
    finalizer = V3ReplyFinalizationService(
        repository=runtime["repository"],
        trace_logger=TraceLogger(runtime["settings"]),
        service_rule_data_service=None,
        outreach_service=None,
        batch_size=5,
    )
    finalization_started = time.perf_counter()
    finalizer.process_batch()
    finalization_ms = int((time.perf_counter() - finalization_started) * 1000)
    snapshot = (runtime["repository"].get_run(body["execute_id"]).get("run") or {}).get("output_snapshot") or {}
    snapshot["evaluation_timings"] = {"first_asgi_http_ms": first_http_ms, "finalization_ms": finalization_ms}
    return body, replay_body, snapshot


async def run(args: argparse.Namespace) -> None:
    args.output = args.output.resolve()
    # Credentials are read only for DeepSeek. No production SDK is constructed.
    settings = Settings(_env_file=args.env_file)
    settings = settings.model_copy(
        update={
            "service_role": "reply",
            "background_workers_enabled": False,
            "model_provider": "relay",
            "model_relay_api_key": settings.deepseek_api_key,
            "model_relay_base_url": settings.deepseek_api_base_url,
            "model_reply": "deepseek-chat",
            "model_fast": "deepseek-chat",
            "model_planner": "deepseek-chat",
            "model_balanced": "deepseek-chat",
            "model_strong": "deepseek-chat",
            "model_store_destination": "deepseek-chat",
            "model_reply_fallbacks": "",
            "model_planner_fallbacks": "",
            "model_balanced_fallbacks": "",
            "model_strong_fallbacks": "",
            "model_fast_fallbacks": "",
            "model_store_destination_fallbacks": "",
            "model_emergency_fallbacks": "",
            "model_secondary_provider": "",
            "model_secondary": "",
            "model_hedge_max_parallel": 1,
            "deepseek_semantic_model": "deepseek-chat",
            "deepseek_semantic_max_tokens": 1800,
            "deepseek_semantic_timeout_seconds": 45,
            "model_timeout_seconds": 60,
            "model_round_timeout_seconds": 150,
            "model_reply_total_timeout_seconds": 60,
            "geocode_workflow_id": "",
            "distance_workflow_id": "",
            "aics_storage_backend": "sqlite",
            "ai_sales_policy_enabled": True,
            "sop_platform_pull_enabled": False,
            "service_rule_data_enabled": False,
        }
    )
    import app.prompts.reply_synthesizer as reply_prompts
    import app.graph.nodes.action_nodes as fact_actions

    # Replace only the external snapshot fact source; selection/validation code runs unchanged.
    fact_actions._snapshot_store_values = lambda: copy.deepcopy(SyntheticStores().load()["stores"])
    if args.baseline_ref:
        reply_prompts.PARALLEL_REPLY_SYSTEM_PROMPT = _baseline_prompt(args.baseline_ref)
    cases = [copy.deepcopy(case) for case in CASES if case["l3"]]
    if args.case_ids:
        cases = [case for case in cases if case["id"] in args.case_ids.split(",")]
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []

    async def one(case: dict[str, Any]) -> None:
        started = time.perf_counter()
        model = AuditedModel(settings)
        model.calls = []
        semantic = AuditedSemantic(settings, None)
        semantic.calls = []
        platform = SyntheticPlatform(case)
        shared = {
            "model_client": model,
            "semantic_client": semantic,
            "coze_client": SyntheticCoze(settings),
            "platform_client": platform,
            "customer_context": CustomerContextService(platform),
            "store_knowledge": SyntheticStores(),
            "store_service": StoreService(platform),
            "semantic_router": V3SemanticRouterService(semantic_client=semantic, knowledge_client=None),
            "sales_strategy": SalesStrategyService(settings),
            "policy": AiSalesPolicyService(settings),
            "outreach_system_client": SyntheticStatus(),
        }

        def substitute(text: str) -> str:
            return (
                text.replace("云州海棠示例店", "杭州青禾护理中心").replace("云州市", "杭州市").replace("云州", "杭州")
            )

        sample = {
            "content": substitute(case["current"]),
            "customer_id": "900001",
            "corp_id": "synthetic-corp",
            "wechat": "synthetic-wechat",
            "external_userid": "synthetic-" + case["id"],
            "customer_add_wechat_id": "900002",
            "conversation_history": [
                ("客户：" if item["role"] == "customer" else "客服：") + substitute(item["content"])
                for item in case["history"]
            ],
            "request_context": {"msgid": "full-graph-" + case["id"]},
        }
        # Explicit fixture state, not customer-intent logic: these cases say a
        # store card was already delivered. Text alone is not a delivery event.
        if case["id"] in {"short-03", "soft-06", "action-05", "action-09", "action-10", "safety-05"}:
            sample.update(confirmed_store_id="900004", confirmed_store_name="杭州青禾护理中心")
            sample["prior_deliveries"] = [
                {
                    "request_id": "synthetic-prior-" + case["id"],
                    "occurred_at": "2026-09-10T08:00:00+08:00",
                    "reply_messages": [{"type": "store_address", "content": {"store_id": "900004"}}],
                }
            ]
        runtime = build_case_runtime(settings=settings, shared=shared, sample=sample, case_dir=args.output / case["id"])
        if args.register_synthetic_media:
            packs = SopReplyPackService(runtime["settings"]).load().get("packs") or []
            records = []
            for pack in packs:
                for message in pack.get("reply_messages") or []:
                    if message.get("type") not in {"image", "video"}:
                        continue
                    content = message.get("content") if isinstance(message.get("content"), dict) else {}
                    url = str(content.get("url") or content.get("image_url") or content.get("video_url") or "")
                    if url:
                        records.append(
                            {
                                "type": message["type"],
                                "url": url,
                                "file_id": len(records) + 1,
                                "file_namespace": "follow_knowledge",
                                "source": "synthetic_l3_fixture",
                            }
                        )
            runtime["repository"].apply_material_catalog(prepare_catalog(records))
        row: dict[str, Any] = {"case_id": case["id"], "external_fact_mode": "synthetic"}
        try:
            body, replay_body, snapshot = await run_http_lifecycle(runtime, sample)
            request_id = body["execute_id"]
            final = runtime["graph"].final_by_request.get(request_id, {})
            (args.output / case["id"] / "full_state.json").write_text(
                json.dumps(final, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            row.update(
                request_id=request_id,
                response=body,
                reply_source=final.get("reply_source"),
                decision_status=final.get("decision_status"),
                errors=final.get("errors"),
                reply_error=final.get("reply_error"),
                node_timings=[
                    {"node": entry.get("node"), "duration_ms": entry.get("duration_ms")}
                    for entry in final.get("trace", [])
                ],
                model_names=sorted(set(_model_names(final))),
                counts=ephemeral_counts(runtime["store"]),
            )
            row["replay_same_request_id"] = replay_body["execute_id"] == request_id
            row["replay_same_messages"] = replay_body["data"]["reply_messages"] == body["data"]["reply_messages"]
            row["http_transport_verified"] = True
            row["finalization_verified"] = (snapshot.get("post_reply_finalization") or {}).get("status") == "completed"
            row["http_and_finalization_timings"] = snapshot["evaluation_timings"]
            row["response_types"] = [item.get("type") for item in (body.get("data") or {}).get("reply_messages", [])]
            row["required_structures_present"] = all(
                kind in row["response_types"] for kind in case["required_message_types"]
            )
        except Exception as exc:
            row["exception"] = f"{type(exc).__name__}: {exc}"
        finally:
            row["http_lifecycle_ms"] = int((time.perf_counter() - started) * 1000)
            row["platform_calls"] = platform.calls
            row["reply_and_tool_model_calls"] = model.calls
            row["router_and_retrieval_model_calls"] = semantic.calls
            rows.append(row)
            (args.output / "results.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            runtime["store"].close()
            await model.aclose()
            await semantic.aclose()
        print(
            json.dumps(
                {
                    "case": case["id"],
                    "source": row.get("reply_source"),
                    "exception": row.get("exception"),
                    "structures": row.get("required_structures_present"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    semaphore = asyncio.Semaphore(args.concurrency)

    async def guarded(case: dict[str, Any]) -> None:
        async with semaphore:
            await one(case)

    await asyncio.gather(*(guarded(case) for case in cases))


def summarize(output: Path) -> dict[str, Any]:
    rows = json.loads((output / "results.json").read_text(encoding="utf-8"))
    contracts = {case["id"]: case for case in CASES}
    details = []
    for row in rows:
        path = output / row["case_id"] / "full_state.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        action = (state.get("reply_sales_judgment") or {}).get("next_sales_action") or {}
        closing = (state.get("policy_decision") or {}).get("closing_decision") or {}
        contract = contracts[row["case_id"]]
        good_source = row.get("reply_source") in {
            "main_model",
            "single_targeted_repair_model",
            "single_full_task_retry_model",
        }
        failures = []
        if not good_source:
            failures.append("reply_not_admitted")
        if not row.get("required_structures_present"):
            failures.append("missing_required_structure")
        if action.get("type") not in contract["expected_actions"]:
            failures.append("unexpected_action")
        if closing.get("customer_state") not in contract["expected_states"]:
            failures.append("unexpected_customer_state")
        if not row.get("replay_same_messages") or not row.get("replay_same_request_id"):
            failures.append("unstable_durable_replay")
        if not row.get("http_transport_verified") or not row.get("finalization_verified"):
            failures.append("incomplete_http_or_finalization")
        if any((row.get("counts") or {}).get(key, 0) for key in ("message_dispatches", "strategy_data_outbox")):
            failures.append("unexpected_external_side_effect")
        repair_ms = 0
        for entry in state.get("trace", []):
            if entry.get("node") != "synthesize_reply":
                continue
            for call in entry.get("tool_calls", []):
                retry = call.get("retry") or {}
                if isinstance(retry, dict):
                    repair_ms += int((retry.get("usage") or {}).get("overall_duration_ms") or 0)
        details.append(
            {
                "case_id": row["case_id"],
                "passed": not failures,
                "failures": failures,
                "runtime_http_replay_and_finalization_ms": row.get("http_lifecycle_ms"),
                "http_and_finalization_timings": row.get("http_and_finalization_timings"),
                "repair_reported_usage_ms": repair_ms,
                "node_timings": row.get("node_timings"),
            }
        )
    usage = [
        call.get("usage") or {}
        for row in rows
        for call in [*row.get("reply_and_tool_model_calls", []), *row.get("router_and_retrieval_model_calls", [])]
    ]
    summary = {
        "mode": "real_graph_deepseek_synthetic_external_facts",
        "cases": len(rows),
        "contract_passed": sum(row["passed"] for row in details),
        "sources": dict(Counter(row.get("reply_source", "exception") for row in rows)),
        "durable_replay_equal": sum(
            bool(row.get("replay_same_messages") and row.get("replay_same_request_id")) for row in rows
        ),
        "model_calls": len(usage),
        "models": sorted({str(item.get("model") or item.get("winner_model") or "") for item in usage} - {""}),
        "fallback_used": any(item.get("fallback_used") or int(item.get("fallback_index") or 0) > 0 for item in usage),
        "message_dispatches": sum((row.get("counts") or {}).get("message_dispatches", 0) for row in rows),
        "strategy_data_outbox": sum((row.get("counts") or {}).get("strategy_data_outbox", 0) for row in rows),
        "http_transport_verified": bool(rows) and all(row.get("http_transport_verified") for row in rows),
        "finalization_verified": bool(rows) and all(row.get("finalization_verified") for row in rows),
        "complete_l3_passed": (
            {row["case_id"] for row in rows} == {case["id"] for case in CASES if case["l3"]}
            and all(row["passed"] for row in details)
            and bool(usage)
            and all(
                str(item.get("model") or item.get("winner_model") or "") == "deepseek-chat"
                and not item.get("fallback_used")
                and not item.get("fallback_index")
                for item in usage
            )
        ),
        "scope_note": "Production ASGI route/middleware, real graph, temporary SQLite, HTTP replay and finalization; synthetic external adapters. No live TCP listener or production integration is exercised.",
        "details": details,
    }
    (output / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _model_names(value: Any) -> list[str]:
    names = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"model", "winner_model", "selected_model"} and isinstance(item, str):
                names.append(item)
            else:
                names.extend(_model_names(item))
    elif isinstance(value, list):
        for item in value:
            names.extend(_model_names(item))
    return names


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--baseline-ref", default="")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--register-synthetic-media", action="store_true")
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if not args.summarize_only:
        asyncio.run(run(args))
    summary = summarize(args.output)
    print(json.dumps({key: value for key, value in summary.items() if key != "details"}, ensure_ascii=False))
    raise SystemExit(0 if summary["complete_l3_passed"] else 1)
