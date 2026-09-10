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

from app.config import Settings
from app.services.ai_sales_policy_service import AiSalesPolicyService
from app.services.customer_context import CustomerContextService
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.model_client import ModelClient
from app.services.sales_strategy_service import SalesStrategyService
from app.services.store_service import StoreService
from app.services.v3_semantic_router_service import V3SemanticRouterService
from app.services.workflow_compat import workflow_response_from_chat
from scripts.evaluate_v3_full_chain_deepseek import build_case_runtime, build_request, ephemeral_counts
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
            return [{"id": "900003", "store_id": "900004", "prepay_required": 10,
                     "prepay_paid": 10, "prepay_status": "paid", "status": "pending"}]
        return []

    def list_stores(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append("list_stores")
        return [{"id": "900004", "name": "杭州青禾护理中心"}]

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"Synthetic platform operation unavailable: {name}")


class SyntheticStores:
    def load(self, **kwargs: Any) -> dict[str, Any]:
        return {"source": "synthetic_platform", "stores": [{
            "store_id": "900004", "store_name": "杭州青禾护理中心", "province": "浙江省",
            "city": "杭州市", "district": "西湖区", "store_address": "杭州市西湖区青禾路18号",
            "business_hours": "10:00-20:00", "is_open": True,
            "parking_name": "青禾停车场", "parking_address": "青禾路18号",
            "latitude": 30.27, "longitude": 120.13, "source": "synthetic_platform",
        }], "appointment_extra_stores": []}

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
            self.calls.append({"tier": kwargs.get("tier"), "duration_ms": int((time.perf_counter() - started) * 1000),
                               "usage": copy.deepcopy(self.last_usage)})


class AuditedSemantic(DeepSeekSemanticClient):
    async def chat_json(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return await super().chat_json(*args, **kwargs)
        finally:
            self.calls.append({"duration_ms": int((time.perf_counter() - started) * 1000),
                               "usage": copy.deepcopy(self.last_usage)})


async def run(args: argparse.Namespace) -> None:
    args.output = args.output.resolve()
    # Credentials are read only for DeepSeek. No production SDK is constructed.
    settings = Settings(_env_file=args.env_file)
    settings = settings.model_copy(update={
        "service_role": "reply", "background_workers_enabled": False,
        "model_provider": "relay", "model_relay_api_key": settings.deepseek_api_key,
        "model_relay_base_url": settings.deepseek_api_base_url,
        "model_reply": "deepseek-chat", "model_fast": "deepseek-chat",
        "model_planner": "deepseek-chat", "model_balanced": "deepseek-chat", "model_strong": "deepseek-chat",
        "model_store_destination": "deepseek-chat", "model_reply_fallbacks": "",
        "model_planner_fallbacks": "", "model_balanced_fallbacks": "", "model_strong_fallbacks": "",
        "model_fast_fallbacks": "", "model_store_destination_fallbacks": "",
        "model_emergency_fallbacks": "", "model_secondary_provider": "",
        "model_secondary": "", "model_hedge_max_parallel": 1,
        "deepseek_semantic_model": "deepseek-chat", "deepseek_semantic_max_tokens": 1800,
        "deepseek_semantic_timeout_seconds": 45, "model_timeout_seconds": 60,
        "model_round_timeout_seconds": 150, "model_reply_total_timeout_seconds": 60,
        "geocode_workflow_id": "", "distance_workflow_id": "",
        "aics_storage_backend": "sqlite", "ai_sales_policy_enabled": True,
        "sop_platform_pull_enabled": False, "service_rule_data_enabled": False,
    })
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
            "model_client": model, "semantic_client": semantic,
            "coze_client": SyntheticCoze(settings), "platform_client": platform,
            "customer_context": CustomerContextService(platform),
            "store_knowledge": SyntheticStores(), "store_service": StoreService(platform),
            "semantic_router": V3SemanticRouterService(semantic_client=semantic, knowledge_client=None),
            "sales_strategy": SalesStrategyService(settings), "policy": AiSalesPolicyService(settings),
            "outreach_system_client": SyntheticStatus(),
        }
        def substitute(text: str) -> str:
            return text.replace("云州海棠示例店", "杭州青禾护理中心").replace("云州市", "杭州市").replace("云州", "杭州")
        sample = {
            "content": substitute(case["current"]), "customer_id": "900001",
            "corp_id": "synthetic-corp", "wechat": "synthetic-wechat",
            "external_userid": "synthetic-" + case["id"], "customer_add_wechat_id": "900002",
            "conversation_history": [("客户：" if item["role"] == "customer" else "客服：") + substitute(item["content"]) for item in case["history"]],
            "request_context": {"msgid": "full-graph-" + case["id"]},
        }
        runtime = build_case_runtime(settings=settings, shared=shared, sample=sample,
                                     case_dir=args.output / case["id"])
        row: dict[str, Any] = {"case_id": case["id"], "external_fact_mode": "synthetic"}
        try:
            response = await runtime["runtime"].run_platform_reply(build_request(sample))
            body = workflow_response_from_chat(response)
            final = runtime["graph"].final_by_request.get(response.request_id, {})
            (args.output / case["id"] / "full_state.json").write_text(
                json.dumps(final, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            row.update(request_id=response.request_id, response=body,
                       reply_source=final.get("reply_source"), decision_status=final.get("decision_status"),
                       errors=final.get("errors"), reply_error=final.get("reply_error"),
                       node_timings=[{"node": entry.get("node"), "duration_ms": entry.get("duration_ms")}
                                     for entry in final.get("trace", [])],
                       model_names=sorted(set(_model_names(final))),
                       counts=ephemeral_counts(runtime["store"]))
            replay = await runtime["runtime"].run_platform_reply(build_request(sample))
            row["replay_same_request_id"] = replay.request_id == response.request_id
            row["replay_same_messages"] = replay.reply_messages == response.reply_messages
            row["response_types"] = [item.get("type") for item in (body.get("data") or {}).get("reply_messages", [])]
            row["required_structures_present"] = all(kind in row["response_types"] for kind in case["required_message_types"])
        except Exception as exc:
            row["exception"] = f"{type(exc).__name__}: {exc}"
        finally:
            row["http_lifecycle_ms"] = int((time.perf_counter() - started) * 1000)
            row["platform_calls"] = platform.calls
            row["reply_and_tool_model_calls"] = model.calls
            row["router_and_retrieval_model_calls"] = semantic.calls
            rows.append(row)
            (args.output / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            runtime["store"].close()
            await model.aclose()
            await semantic.aclose()
        print(json.dumps({"case": case["id"], "source": row.get("reply_source"), "exception": row.get("exception"),
                          "structures": row.get("required_structures_present")}, ensure_ascii=False), flush=True)

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
        good_source = row.get("reply_source") in {"main_model", "single_targeted_repair_model", "single_full_task_retry_model"}
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
        repair_ms = 0
        for entry in state.get("trace", []):
            if entry.get("node") != "synthesize_reply":
                continue
            for call in entry.get("tool_calls", []):
                retry = call.get("retry") or {}
                if isinstance(retry, dict):
                    repair_ms += int((retry.get("usage") or {}).get("overall_duration_ms") or 0)
        details.append({"case_id": row["case_id"], "passed": not failures, "failures": failures,
                        "runtime_response_and_replay_ms": row.get("http_lifecycle_ms"),
                        "repair_reported_usage_ms": repair_ms, "node_timings": row.get("node_timings")})
    usage = [call.get("usage") or {} for row in rows for call in
             [*row.get("reply_and_tool_model_calls", []), *row.get("router_and_retrieval_model_calls", [])]]
    summary = {
        "mode": "real_graph_deepseek_synthetic_external_facts",
        "cases": len(rows), "contract_passed": sum(row["passed"] for row in details),
        "sources": dict(Counter(row.get("reply_source", "exception") for row in rows)),
        "durable_replay_equal": sum(bool(row.get("replay_same_messages") and row.get("replay_same_request_id")) for row in rows),
        "model_calls": len(usage),
        "models": sorted({str(item.get("model") or item.get("winner_model") or "") for item in usage} - {""}),
        "fallback_used": any(item.get("fallback_used") or int(item.get("fallback_index") or 0) > 0 for item in usage),
        "message_dispatches": sum((row.get("counts") or {}).get("message_dispatches", 0) for row in rows),
        "strategy_data_outbox": sum((row.get("counts") or {}).get("strategy_data_outbox", 0) for row in rows),
        "http_transport_verified": False, "finalization_verified": False,
        "complete_l3_passed": False,
        "scope_note": "Real model graph plus runtime persistence and response serialization; external facts are synthetic. HTTP transport and finalization are not exercised by this harness.",
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
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if not args.summarize_only:
        asyncio.run(run(args))
    summary = summarize(args.output)
    print(json.dumps({key: value for key, value in summary.items() if key != "details"}, ensure_ascii=False))
    raise SystemExit(0 if summary["complete_l3_passed"] else 1)
