from __future__ import annotations

import argparse
import asyncio
import copy
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.evaluation.v3_critic import evaluate_with_critic
from app.evaluation.sales_reply_critic import evaluate_sales_reply
from app.evaluation.v3_golden import golden_case_to_simulation, simulation_result_to_golden_result
from app.services.model_client import ModelClient
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import FollowKnowledgeClient
from app.services.v3_semantic_router_service import V3SemanticRouterService
from app.simulation.runtime import SimulationRuntime
from scripts.evaluate_v3_trusted_golden import evaluate
from scripts.v3_deepseek_test_support import (
    assert_deepseek_models,
    collect_model_names,
    deepseek_only_settings,
)


class _OfflineKnowledgeConditionRouter:
    """Test-only evidence overlay; never imported by the runtime package."""

    def __init__(self, base: V3SemanticRouterService, *, condition: str, overlay: dict[str, Any]) -> None:
        self.base = base
        self.condition = condition
        self.by_case_id: dict[str, dict[str, Any]] = {}
        for candidate in overlay.get("cases") or []:
            if not isinstance(candidate, dict):
                continue
            for case_id in candidate.get("evaluation_case_ids") or []:
                self.by_case_id[str(case_id)] = candidate

    @property
    def available(self) -> bool:
        return self.base.available

    async def load_sequence_index(self) -> dict[str, Any]:
        return await self.base.load_sequence_index()

    async def load_checkpoint_taxonomy(self) -> dict[str, Any]:
        return await self.base.load_checkpoint_taxonomy()

    async def route(self, **kwargs: Any) -> dict[str, Any]:
        result = await self.base.route(**kwargs)
        return self._apply(result, kwargs.get("shared_context") or {})

    async def route_after_store(self, **kwargs: Any) -> dict[str, Any]:
        result = await self.base.route_after_store(**kwargs)
        return self._apply(result, kwargs.get("shared_context") or {})

    def _apply(self, result: dict[str, Any], shared_context: dict[str, Any]) -> dict[str, Any]:
        output = copy.deepcopy(result)
        route = output.get("semantic_route") if isinstance(output.get("semantic_route"), dict) else {}
        if str(route.get("phase") or "") == "pre_store_pending":
            return output
        if self.condition == "online":
            return output
        if self.condition == "none":
            output["knowledge_evidence"] = _empty_offline_knowledge("disabled_for_ab")
            return output
        case_id = _simulation_case_id(shared_context)
        candidate = self.by_case_id.get(case_id)
        output["knowledge_evidence"] = (
            _candidate_knowledge(candidate)
            if isinstance(candidate, dict)
            else _empty_offline_knowledge("no_candidate_for_case")
        )
        return output


def _simulation_case_id(shared_context: dict[str, Any]) -> str:
    scope = shared_context.get("customer_scope") if isinstance(shared_context.get("customer_scope"), dict) else {}
    customer_id = str(scope.get("customer_id") or "")
    prefix = "sim_customer_"
    return customer_id[len(prefix):] if customer_id.startswith(prefix) else ""


def _empty_offline_knowledge(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "v3_knowledge_evidence_v1",
        "status": "empty",
        "source": "offline_ab",
        "support_level": "none",
        "sequence_candidates": [],
        "candidates": [],
        "candidate_count": 0,
        "reason": reason,
    }


def _candidate_knowledge(candidate: dict[str, Any]) -> dict[str, Any]:
    raw_sequence = candidate.get("sequence") if isinstance(candidate.get("sequence"), dict) else {}
    sequence_id = str(raw_sequence.get("sequence_id") or raw_sequence.get("id") or "")
    sequence_steps = []
    for raw_step in raw_sequence.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        sequence_steps.append(
            {
                **copy.deepcopy(raw_step),
                "step_id": str(raw_step.get("step_id") or raw_step.get("id") or ""),
            }
        )
    sequence = {
        **copy.deepcopy(raw_sequence),
        "sequence_id": sequence_id,
        "steps": sequence_steps,
    }
    scripts = []
    for raw in candidate.get("scripts") or []:
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        item["source_id"] = str(item.get("script_code") or item.get("id") or "")
        item["source_ref"] = "offline_candidate:" + str(candidate.get("candidate_key") or "")
        item["reference_text"] = ""
        action_code = str(item.get("action_code") or "")
        item["sequence_links"] = [
            {
                "sequence_id": sequence_id,
                "step_id": str(step.get("step_id") or ""),
                "action_code": action_code,
            }
            for step in sequence_steps
            if sequence_id
            and str(step.get("step_id") or "")
            and str(step.get("action_code") or "") == action_code
        ]
        scripts.append(item)
    return {
        "schema_version": "v3_knowledge_evidence_v1",
        "status": "ok",
        "source": "offline_candidate_pending_review",
        "support_level": "script_exact",
        "sequence_candidates": [sequence] if sequence_id else [],
        "candidates": scripts,
        "candidate_count": len(scripts),
        "paragraph_candidate_count": sum(len(item.get("paragraphs") or []) for item in scripts),
        "candidate_key": str(candidate.get("candidate_key") or ""),
        "candidate_objective": str(candidate.get("objective") or ""),
        "candidate_boundaries": [
            str(item).strip()
            for item in candidate.get("forbidden") or []
            if str(item).strip()
        ],
        "runtime_allowed": False,
        "callback_allowed": False,
    }


async def run(args: argparse.Namespace) -> Path:
    repo_root = args.repo_root.resolve()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    cases = [item for item in golden.get("cases") or [] if isinstance(item, dict)]
    if args.partition:
        cases = [item for item in cases if item.get("evaluation_partition") == args.partition]
    if args.source_kind:
        cases = [item for item in cases if item.get("source_kind") == args.source_kind]
    if args.case_id:
        wanted = {item.strip() for item in args.case_id.split(",") if item.strip()}
        cases = [item for item in cases if str(item.get("case_id") or "") in wanted]
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        raise ValueError("no golden cases matched")

    commit = _git_commit(repo_root)
    run_id = args.run_id or datetime.now().strftime("v3-golden-%Y%m%d-%H%M%S")
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=args.resume)
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_dir.mkdir(exist_ok=args.resume)

    assert_deepseek_models(args.router_model, args.reply_model, args.critic_model)
    settings = deepseek_only_settings(
        Settings(), router_model=args.router_model, reply_model=args.reply_model
    )
    settings = settings.model_copy(update={
        "ai_sales_policy_enabled": bool(args.enable_sales_policy),
        "sales_strategy_catalog_enabled": bool(args.enable_sales_strategy_catalog),
    })
    simulation_root = repo_root / ".tmp_runtime" / "simulation" / run_id
    follow_knowledge = FollowKnowledgeClient(settings)
    deepseek_semantic = DeepSeekSemanticClient(settings, fallback_client=None)
    semantic_router = V3SemanticRouterService(
        semantic_client=deepseek_semantic,
        knowledge_client=follow_knowledge,
        script_threshold=settings.deepseek_semantic_script_threshold,
        max_scripts=settings.deepseek_semantic_max_scripts,
    )
    knowledge_overlay = (
        json.loads(args.knowledge_overlay.read_text(encoding="utf-8"))
        if args.knowledge_condition == "local"
        else {"cases": []}
    )
    runtime_router = _OfflineKnowledgeConditionRouter(
        semantic_router,
        condition=args.knowledge_condition,
        overlay=knowledge_overlay,
    )
    runtime = SimulationRuntime(
        repo_root=repo_root,
        run_root=simulation_root,
        base_settings=settings,
        semantic_router_service=runtime_router,
    )
    critic_settings = settings.model_copy(
        update={"model_balanced": args.critic_model, "model_balanced_fallbacks": ""}
    )
    critic = None if args.skip_critic else ModelClient(critic_settings)
    semaphore = asyncio.Semaphore(max(1, min(args.concurrency, 4)))

    async def run_case(case: dict[str, Any]) -> dict[str, Any]:
        case_id = str(case.get("case_id") or "")
        checkpoint = checkpoint_dir / f"{case_id}.json"
        if args.resume and checkpoint.exists():
            return json.loads(checkpoint.read_text(encoding="utf-8"))
        async with semaphore:
            try:
                simulation = await runtime.run_scenario(golden_case_to_simulation(case), attempt=1)
                result = simulation_result_to_golden_result(case, simulation)
                if critic is not None and not result.get("infrastructure_errors"):
                    result["critic"] = await evaluate_with_critic(critic, case=case, result=result)
                    if args.sales_focus:
                        result["sales_focus"] = await evaluate_sales_reply(critic, case=case, result=result)
                    result["critic_model_usage"] = critic.last_usage or {}
            except Exception as exc:  # noqa: BLE001 - preserve per-case infrastructure failures
                result = {
                    "case_id": case_id,
                    "partition": case.get("evaluation_partition"),
                    "category": case.get("category"),
                    "reply_messages": [],
                    "hard_pass": False,
                    "hard_errors": [],
                    "infrastructure_errors": [f"{type(exc).__name__}: {exc}"],
                    "human_review": {"status": "pending", "verdict": "", "notes": ""},
                }
            checkpoint.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return result

    try:
        results = list(await asyncio.gather(*(run_case(case) for case in cases)))
    finally:
        if critic is not None:
            await critic.aclose()
        await deepseek_semantic.aclose()
        await follow_knowledge.aclose()

    payload = {
        "schema_version": "v3_trusted_golden_run_v1",
        "run_id": run_id,
        "git_commit": commit,
        "golden_git_commit": golden.get("git_commit"),
        "models": {
            "router": args.router_model,
            "reply": args.reply_model,
            "critic": "" if args.skip_critic else args.critic_model,
        },
        "knowledge_condition": args.knowledge_condition,
        "source_kind": args.source_kind,
        "sales_focus": bool(args.sales_focus),
        "sales_policy_enabled": bool(args.enable_sales_policy),
        "sales_strategy_catalog_enabled": bool(args.enable_sales_strategy_catalog),
        "critic_model": "" if args.skip_critic else args.critic_model,
        "human_calibration_status": "pending_review",
        "results": results,
    }
    results_path = run_dir / "results.json"
    actual_models = collect_model_names(results)
    assert_deepseek_models(*actual_models)
    unexpected = [
        model
        for model in actual_models
        if model not in {args.router_model, args.reply_model, args.critic_model}
    ]
    if unexpected:
        raise RuntimeError(f"Unexpected model usage: {', '.join(unexpected)}")
    payload["actual_models"] = actual_models
    results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    evaluation = evaluate(golden, payload)
    if args.sales_focus:
        evaluation["sales_focus"] = _sales_focus_summary(results)
    (run_dir / "evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(_report(payload, evaluation), encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "git_commit": commit,
                "created_at": datetime.now().astimezone().isoformat(),
                "case_count": len(results),
                "results": "results.json",
                "evaluation": "evaluation.json",
                "report": "report.md",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


def _sales_focus_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name in ("cardpoint", "closing", "priority"):
        rows = [item["sales_focus"][name] for item in results if isinstance(item.get("sales_focus"), dict) and isinstance(item["sales_focus"].get(name), dict) and item["sales_focus"][name].get("applicable")]
        summary[name] = {
            "applicable_cases": len(rows), "passed_cases": sum(bool(item.get("pass")) for item in rows),
            "pass_rate": round(sum(bool(item.get("pass")) for item in rows) / len(rows), 4) if rows else None,
            "average_score": round(sum(int(item.get("score") or 0) for item in rows) / len(rows), 2) if rows else None,
        }
    multi_issue = [item["sales_focus"]["priority"] for item in results if isinstance(item.get("sales_focus"), dict) and isinstance(item["sales_focus"].get("priority"), dict) and item["sales_focus"]["priority"].get("multi_issue")]
    summary["priority_multi_issue"] = {
        "cases": len(multi_issue), "passed_cases": sum(bool(item.get("pass")) for item in multi_issue),
        "pass_rate": round(sum(bool(item.get("pass")) for item in multi_issue) / len(multi_issue), 4) if multi_issue else None,
    }
    summary["human_review_required"] = sum(bool((item.get("sales_focus") or {}).get("human_review_required")) for item in results)
    summary["fact_safe_cases"] = sum(bool((item.get("sales_focus") or {}).get("fact_safe")) for item in results)
    return summary


def _report(payload: dict[str, Any], evaluation: dict[str, Any]) -> str:
    critic_pass = sum(
        1 for item in payload["results"] if (item.get("critic") or {}).get("status") == "pass"
    )
    infra = sum(1 for item in payload["results"] if item.get("infrastructure_errors"))
    lines = [
        "# V3 Trusted Golden Evaluation",
        "",
        f"- Run: `{payload['run_id']}`",
        f"- Git commit: `{payload['git_commit']}`",
        f"- Cases: `{len(payload['results'])}`",
        f"- Critic pass: `{critic_pass}/{len(payload['results']) - infra}`",
        f"- Infrastructure failures: `{infra}`",
        "- Human calibration: `pending_review`",
        "",
        "> Critic results are diagnostic predictions. They are not a calibrated acceptance result until the calibration cases receive human verdicts.",
        "",
        "| Case | Partition | Category | Hard | Critic | Owner | Reply |",
        "|---|---|---|---:|---|---|---|",
    ]
    for item in payload["results"]:
        critic = item.get("critic") if isinstance(item.get("critic"), dict) else {}
        text = " / ".join(
            str(msg.get("content") or "")
            for msg in item.get("reply_messages") or []
            if isinstance(msg, dict) and msg.get("type") == "text"
        ).replace("|", "\\|").replace("\n", " ")[:240]
        lines.append(
            f"| {item.get('case_id')} | {item.get('partition')} | {item.get('category')} | "
            f"{'pass' if item.get('hard_pass') else 'fail'} | {critic.get('status', '-')} | "
            f"{critic.get('failure_owner', '-')} | {text} |"
        )
    lines.extend(
        [
            "",
            "## Mechanical Metrics",
            "",
            f"- Knowledge recall: `{evaluation.get('knowledge_recall')}`",
            f"- Reply adoption: `{evaluation.get('reply_adoption')}`",
            f"- Delivery completion: `{evaluation.get('delivery_completion')}`",
            f"- Forbidden action cases: `{evaluation.get('forbidden_action_case_count')}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _git_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False
    )
    commit = completed.stdout.strip() if completed.returncode == 0 else ""
    if commit:
        return commit
    release_commit = repo_root / "RELEASE_COMMIT"
    if release_commit.is_file():
        commit = release_commit.read_text(encoding="utf-8").strip()
    if not commit:
        raise RuntimeError("cannot determine git commit for V3 evaluation")
    return commit


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V3 against the trusted golden set offline.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--golden", type=Path, default=Path("workflow_tests/fixtures/v3_trusted_golden_set_v1.json"))
    parser.add_argument("--output-root", type=Path, default=Path(".tmp_runtime/v3_evaluations"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--partition", choices=("calibration", "holdout"), default="")
    parser.add_argument("--source-kind", choices=("real_replay", "fixture", "counterfactual"), default="")
    parser.add_argument("--case-id", default="")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--router-model", default="deepseek-v4-flash")
    parser.add_argument("--reply-model", default="deepseek-v4-pro")
    parser.add_argument("--critic-model", default="deepseek-v4-pro")
    parser.add_argument("--skip-critic", action="store_true")
    parser.add_argument("--sales-focus", action="store_true")
    parser.add_argument("--enable-sales-policy", action="store_true")
    parser.add_argument("--enable-sales-strategy-catalog", action="store_true")
    parser.add_argument("--knowledge-condition", choices=("online", "none", "local"), default="online")
    parser.add_argument(
        "--knowledge-overlay",
        type=Path,
        default=Path("workflow_tests/fixtures/v3_local_sales_knowledge_candidates_v1.json"),
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(run(args)).resolve())


if __name__ == "__main__":
    main()
