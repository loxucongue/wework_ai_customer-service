from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.evaluation.v3_critic import evaluate_with_critic
from app.evaluation.v3_golden import golden_case_to_simulation, simulation_result_to_golden_result
from app.services.model_client import ModelClient
from app.simulation.runtime import SimulationRuntime
from scripts.evaluate_v3_trusted_golden import evaluate


async def run(args: argparse.Namespace) -> Path:
    repo_root = args.repo_root.resolve()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    cases = [item for item in golden.get("cases") or [] if isinstance(item, dict)]
    if args.partition:
        cases = [item for item in cases if item.get("evaluation_partition") == args.partition]
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

    settings = Settings()
    runtime = SimulationRuntime(repo_root=repo_root, run_root=run_dir / "simulation", base_settings=settings)
    critic_settings = settings.model_copy(
        update={
            "model_balanced": args.critic_model or "gpt-5.4-mini",
            "model_balanced_fallbacks": "gpt-5.4",
        }
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

    payload = {
        "schema_version": "v3_trusted_golden_run_v1",
        "run_id": run_id,
        "git_commit": commit,
        "golden_git_commit": golden.get("git_commit"),
        "critic_model": "" if args.skip_critic else (args.critic_model or "gpt-5.4-mini"),
        "human_calibration_status": "pending_review",
        "results": results,
    }
    results_path = run_dir / "results.json"
    results_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    evaluation = evaluate(golden, payload)
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
            f"- Gate recall: `{evaluation.get('gate_recall')}`",
            f"- Reply adoption: `{evaluation.get('reply_adoption')}`",
            f"- Delivery completion: `{evaluation.get('delivery_completion')}`",
            f"- Forbidden action cases: `{evaluation.get('forbidden_action_case_count')}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _git_commit(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V3 against the trusted golden set offline.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--golden", type=Path, default=Path("workflow_tests/fixtures/v3_trusted_golden_set_v1.json"))
    parser.add_argument("--output-root", type=Path, default=Path(".tmp_runtime/v3_evaluations"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--partition", choices=("calibration", "holdout"), default="")
    parser.add_argument("--case-id", default="")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--critic-model", default="gpt-5.4-mini")
    parser.add_argument("--skip-critic", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(run(args)).resolve())


if __name__ == "__main__":
    main()
