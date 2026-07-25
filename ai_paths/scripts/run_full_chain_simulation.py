from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from app.simulation.runner import run_suite


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fail-closed offline full-chain simulation suite.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("workflow_tests/fixtures/full_chain_simulation_v1.json"),
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--scenario", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--critical-attempts", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--reviewer-model", default="")
    parser.add_argument("--skip-review", action="store_true")
    parser.add_argument("--baseline", type=Path)
    return parser.parse_args()


async def _main() -> None:
    args = _args()
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = args.output_dir or (
        repo_root / ".tmp_runtime" / "simulation" / datetime.now().strftime("suite-%Y%m%d-%H%M%S")
    )
    report = await run_suite(
        repo_root=repo_root,
        fixture=(repo_root / args.fixture).resolve() if not args.fixture.is_absolute() else args.fixture,
        output_dir=output_dir.resolve(),
        scenario_id=args.scenario,
        category=args.category,
        attempts=args.attempts,
        critical_attempts=args.critical_attempts,
        concurrency=args.concurrency,
        max_cases=args.max_cases,
        reviewer_model=args.reviewer_model,
        skip_review=args.skip_review,
        baseline_path=args.baseline,
    )
    print((output_dir / "report.md").resolve())
    print(report["summary"])


if __name__ == "__main__":
    asyncio.run(_main())
