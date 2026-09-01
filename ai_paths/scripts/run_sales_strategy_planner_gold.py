from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.graph.planner.brain_v2 import run_planner_brain_v2
from app.services.ai_sales_policy_service import AiSalesPolicyService
from app.services.model_client import ModelClient
from app.services.runtime_budget import build_runtime_budget
from app.services.sales_strategy_service import SalesStrategyService


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _macro_f1(rows: list[dict[str, Any]], expected_key: str, actual_key: str) -> float:
    labels = sorted({str(row[expected_key]) for row in rows if str(row.get(expected_key) or "")})
    scores: list[float] = []
    for label in labels:
        true_positive = sum(row.get(expected_key) == label and row.get(actual_key) == label for row in rows)
        false_positive = sum(row.get(expected_key) != label and row.get(actual_key) == label for row in rows)
        false_negative = sum(row.get(expected_key) == label and row.get(actual_key) != label for row in rows)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _recall(rows: list[dict[str, Any]], expected_key: str, actual_key: str, label: str) -> float | None:
    relevant = [row for row in rows if row.get(expected_key) == label]
    return round(sum(row.get(actual_key) == label for row in relevant) / len(relevant), 4) if relevant else None


def _state(settings: Settings, case: dict[str, Any], catalog: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": case.get("current_message") or "",
        "normalized_content": case.get("current_message") or "",
        "conversation_history": case.get("history") or [],
        "request_context": {
            "category_id": "S10N",
            "memory_persist_allowed": False,
            "test_isolated": True,
            "corp_id": "gold-test-corp",
            "wechat": "GOLD_TEST_WECHAT",
            "external_userid": "gold-test-external",
            "customer_id": "gold-test-customer",
        },
        "customer_id": "gold-test-customer",
        "external_userid": "gold-test-external",
        "corp_id": "gold-test-corp",
        "wechat": "GOLD_TEST_WECHAT",
        "runtime_budget": build_runtime_budget(settings),
        "ai_sales_policy": policy,
        "sales_strategy_catalog": catalog,
    }


def _consistent(plan: dict[str, Any]) -> bool:
    intent = str((plan.get("realtime_intent") or {}).get("type") or "")
    emotion = str((plan.get("emotion_decision") or {}).get("label") or "")
    flow = str((plan.get("emotion_decision") or {}).get("flow_action") or "")
    closing = plan.get("closing_decision") or {}
    progression = plan.get("sales_progression") or {}
    primary = str((plan.get("primary_task") or {}).get("type") or "")
    if intent == "explicit_exit":
        return (
            primary == "hard_stop"
            and closing.get("action") == "complete"
            and closing.get("customer_state") == "hard_stop"
            and progression.get("status") == "terminal"
        )
    if flow == "pause_marketing_turn" or emotion == "impatient":
        return progression.get("status") != "continue" and closing.get("action") in {"none", "pause", "complete"}
    if intent == "defer":
        return progression.get("status") != "continue" and closing.get("action") in {"none", "pause", "fallback", "complete"}
    if intent == "normal_exchange" and primary == "normal_conversation":
        return closing.get("action") == "none" and progression.get("status") != "continue"
    return True


async def _run_case(
    case: dict[str, Any],
    *,
    settings: Settings,
    client: ModelClient,
    service: SalesStrategyService,
    catalog: dict[str, Any],
    policy: dict[str, Any],
    semaphore: asyncio.Semaphore,
    timeout_seconds: float,
) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        try:
            plan, call = await asyncio.wait_for(
                run_planner_brain_v2(_state(settings, case, catalog, policy), client),
                timeout=max(1.0, timeout_seconds),
            )
            error = ""
        except Exception as exc:  # noqa: BLE001
            plan = {}
            call = {}
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)
        expected = case.get("expected") or {}
        cardpoint = plan.get("cardpoint_decision") or {}
        actual_category = str(cardpoint.get("category_key") or "")
        expected_scenario = str(expected.get("scenario_key") or "")
        retrieval_top3 = False
        if plan and actual_category and expected_scenario:
            result = service.retrieve(
                category_key=actual_category,
                scenario_query=str(cardpoint.get("scenario_query") or case.get("current_message") or ""),
                tactic_tags=[str(item) for item in cardpoint.get("tactic_tags") or []],
                fact_context={},
                limit=3,
            )
            retrieval_top3 = any(
                expected_scenario in (candidate.get("scenario_keys") or [])
                for candidate in result.get("candidates") or []
            )
        return {
            "id": case.get("id"),
            "expected_category": str(expected.get("cardpoint_category") or ""),
            "actual_category": actual_category,
            "expected_intent": str(expected.get("realtime_intent") or ""),
            "actual_intent": str((plan.get("realtime_intent") or {}).get("type") or ""),
            "expected_emotion": str(expected.get("emotion") or ""),
            "actual_emotion": str((plan.get("emotion_decision") or {}).get("label") or ""),
            "retrieval_top3": retrieval_top3 if expected_scenario else None,
            "consistent": _consistent(plan) if plan else False,
            "duration_ms": duration_ms,
            "model": (call.get("usage") or {}).get("winner_model") or (call.get("usage") or {}).get("model") or "",
            "error": error,
        }


async def _run(args: argparse.Namespace) -> int:
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    cases = list(fixture.get("cases") or [])
    if args.limit:
        cases = cases[: args.limit]
    settings = Settings(_env_file=args.env_file)
    settings = settings.model_copy(
        update={
            "model_fast": args.test_model,
            "model_fast_fallbacks": "",
            "model_planner": args.test_model,
            "model_planner_fallbacks": "",
            "model_balanced": args.test_model,
            "model_balanced_fallbacks": "",
            "model_reply": args.test_model,
            "model_reply_fallbacks": "",
        }
    )
    client = ModelClient(settings)
    service = SalesStrategyService(settings)
    catalog = service.runtime_summary()
    policy = AiSalesPolicyService(settings).runtime_snapshot()
    semaphore = asyncio.Semaphore(max(1, min(args.concurrency, 2)))
    rows = list(
        await asyncio.gather(
            *(
                _run_case(
                    case,
                    settings=settings,
                    client=client,
                    service=service,
                    catalog=catalog,
                    policy=policy,
                    semaphore=semaphore,
                    timeout_seconds=args.call_timeout_seconds,
                )
                for case in cases
            )
        )
    )
    successful = [row for row in rows if not row["error"]]
    category_rows = [row for row in successful if row["expected_category"]]
    scenario_rows = [row for row in successful if row["retrieval_top3"] is not None]
    summary = {
        "fixture_version": fixture.get("version"),
        "gold_status": fixture.get("gold_status"),
        "test_model": args.test_model,
        "runs": len(rows),
        "successful_runs": len(successful),
        "infrastructure_failure_rate": round((len(rows) - len(successful)) / len(rows), 4) if rows else 0.0,
        "intent_macro_f1": _macro_f1(successful, "expected_intent", "actual_intent"),
        "cardpoint_top1": round(sum(row["expected_category"] == row["actual_category"] for row in category_rows) / len(category_rows), 4) if category_rows else 0.0,
        "retrieval_top3_recall": round(sum(bool(row["retrieval_top3"]) for row in scenario_rows) / len(scenario_rows), 4) if scenario_rows else 0.0,
        "explicit_exit_recall": _recall(successful, "expected_intent", "actual_intent", "explicit_exit"),
        "impatient_recall": _recall(successful, "expected_emotion", "actual_emotion", "impatient"),
        "angry_recall": _recall(successful, "expected_emotion", "actual_emotion", "angry"),
        "consistency_rate": round(sum(bool(row["consistent"]) for row in successful) / len(successful), 4) if successful else 0.0,
        "planner_p50_ms": int(statistics.median([row["duration_ms"] for row in successful])) if successful else 0,
        "planner_p90_ms": _percentile([row["duration_ms"] for row in successful], 0.9),
    }
    summary["acceptance_ready"] = bool(
        fixture.get("gold_status") == "human_reviewed"
        and len(rows) >= 400
        and summary["infrastructure_failure_rate"] < 0.01
        and summary["intent_macro_f1"] >= 0.90
        and summary["cardpoint_top1"] >= 0.85
        and summary["retrieval_top3_recall"] >= 0.95
        and summary["explicit_exit_recall"] is not None
        and summary["explicit_exit_recall"] == 1.0
        and summary["impatient_recall"] is not None
        and summary["impatient_recall"] == 1.0
        and summary["angry_recall"] is not None
        and summary["angry_recall"] == 1.0
        and summary["consistency_rate"] >= 0.95
        and summary["planner_p90_ms"] < 10_000
    )
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.enforce_thresholds and not summary["acceptance_ready"]:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--report", required=True)
    parser.add_argument("--test-model", default="deepseek-chat")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--call-timeout-seconds", type=float, default=55.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--enforce-thresholds", action="store_true")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
