from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.deepseek_semantic_client import DeepSeekSemanticClient
from app.services.follow_knowledge_client import FollowKnowledgeClient
from app.services.v3_semantic_router_service import V3SemanticRouterService
from app.simulation.runner import run_suite
from scripts.v3_deepseek_test_support import (
    assert_deepseek_models,
    collect_model_names,
    deepseek_only_settings,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated V3 chain with DeepSeek-only models.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("workflow_tests/fixtures/full_chain_simulation_v1.json"),
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--scenario", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--critical-attempts", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--router-model", default="deepseek-v4-flash")
    parser.add_argument("--reply-model", default="deepseek-v4-pro")
    parser.add_argument("--skip-review", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    args = _args()
    assert_deepseek_models(args.router_model, args.reply_model)
    repo_root = Path(__file__).resolve().parents[2]
    fixture = (repo_root / args.fixture).resolve() if not args.fixture.is_absolute() else args.fixture.resolve()
    output_dir = args.output_dir or (
        repo_root / ".tmp_runtime" / "simulation" / datetime.now().strftime("v3-deepseek-full-%Y%m%d-%H%M%S")
    )
    settings = deepseek_only_settings(
        Settings(), router_model=args.router_model, reply_model=args.reply_model
    )
    knowledge = FollowKnowledgeClient(settings)
    semantic_client = DeepSeekSemanticClient(settings, fallback_client=None)
    semantic_router = V3SemanticRouterService(
        semantic_client=semantic_client,
        knowledge_client=knowledge,
        script_threshold=settings.deepseek_semantic_script_threshold,
        max_scripts=settings.deepseek_semantic_max_scripts,
    )
    try:
        report = await run_suite(
            repo_root=repo_root,
            fixture=fixture,
            output_dir=output_dir.resolve(),
            scenario_id=args.scenario,
            category=args.category,
            attempts=args.attempts,
            critical_attempts=args.critical_attempts,
            concurrency=args.concurrency,
            max_cases=args.max_cases,
            reviewer_model=args.reply_model,
            skip_review=args.skip_review,
            base_settings=settings,
            semantic_router_service=semantic_router,
        )
    finally:
        await semantic_client.aclose()
        await knowledge.aclose()
    actual_models = collect_model_names(
        json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    )
    assert_deepseek_models(*actual_models)
    if args.router_model not in actual_models:
        raise RuntimeError(f"Router model usage was not recorded: {args.router_model}")
    if args.reply_model not in actual_models:
        raise RuntimeError(f"Reply model usage was not recorded: {args.reply_model}")
    manifest = {
        "provider": "deepseek_official",
        "router_model": args.router_model,
        "reply_and_reviewer_model": args.reply_model,
        "actual_models": actual_models,
        "gpt_called": False,
        "scenario_count": report.get("scenario_count"),
        "attempt_count": report.get("attempt_count"),
    }
    (output_dir / "deepseek_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print((output_dir / "report.md").resolve())
    print(json.dumps(report.get("summary") or {}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
