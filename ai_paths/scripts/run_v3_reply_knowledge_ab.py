from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.run_v3_trusted_golden import run as run_golden


DEFAULT_CASE_IDS = "golden_v3_019,golden_v3_023,golden_v3_047,golden_v3_049"


async def run(args: argparse.Namespace) -> Path:
    root = args.repo_root.resolve()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    annotation_by_case = {
        str(item.get("case_id") or ""): item.get("annotation") or {}
        for item in golden.get("cases") or []
        if isinstance(item, dict)
    }
    run_id = args.run_id or datetime.now().strftime("v3-knowledge-ab-%Y%m%d-%H%M%S")
    output_dir = args.output_root.resolve() / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    all_results: list[dict[str, Any]] = []
    conditions = [
        value.strip()
        for value in str(args.conditions or "none,online,local").split(",")
        if value.strip()
    ]
    invalid_conditions = [value for value in conditions if value not in {"none", "online", "local"}]
    if not conditions or invalid_conditions:
        raise ValueError(f"invalid knowledge conditions: {invalid_conditions or conditions}")

    for condition in conditions:
        for attempt in range(1, args.attempts + 1):
            child_id = f"{run_id}-{condition}-a{attempt}"
            child = SimpleNamespace(
                repo_root=root,
                golden=args.golden,
                output_root=output_dir / "runs",
                run_id=child_id,
                partition="",
                case_id=args.case_id,
                max_cases=0,
                concurrency=args.concurrency,
                router_model=args.router_model,
                reply_model=args.reply_model,
                critic_model=args.reply_model,
                skip_critic=True,
                knowledge_condition=condition,
                knowledge_overlay=args.knowledge_overlay,
                resume=False,
            )
            run_dir = await run_golden(child)
            payload = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
            for result in payload.get("results") or []:
                if not isinstance(result, dict):
                    continue
                case_id = str(result.get("case_id") or "")
                route = (
                    result.get("semantic_route_summary")
                    if isinstance(result.get("semantic_route_summary"), dict)
                    else {}
                )
                friction = (
                    route.get("current_friction")
                    if isinstance(route.get("current_friction"), dict)
                    else {}
                )
                actual_checkpoint = str(friction.get("checkpoint_code") or "")
                actual_checkpoint_name = str(friction.get("checkpoint_type_name") or "")
                acceptable = [
                    str(value or "")
                    for value in (annotation_by_case.get(case_id) or {}).get(
                        "acceptable_checkpoint_codes"
                    )
                    or []
                ]
                acceptable_names = [
                    str(value or "")
                    for value in (annotation_by_case.get(case_id) or {}).get(
                        "acceptable_checkpoint_type_names"
                    )
                    or []
                ]
                checkpoint_acceptable = (
                    actual_checkpoint in acceptable
                    or actual_checkpoint_name in acceptable_names
                )
                if not acceptable and not acceptable_names:
                    checkpoint_acceptable = actual_checkpoint == ""
                result.update(
                    {
                        "knowledge_condition": condition,
                        "attempt": attempt,
                        "actual_checkpoint_code": actual_checkpoint,
                        "actual_checkpoint_type_name": actual_checkpoint_name,
                        "acceptable_checkpoint_codes": acceptable,
                        "acceptable_checkpoint_type_names": acceptable_names,
                        "checkpoint_acceptable": checkpoint_acceptable,
                        "source_run_dir": str(run_dir),
                    }
                )
                all_results.append(result)

    payload = {
        "schema_version": "v3_reply_knowledge_ab_v1",
        "run_id": run_id,
        "models": {"router": args.router_model, "reply": args.reply_model},
        "conditions": conditions,
        "attempts": args.attempts,
        "case_ids": [item for item in args.case_id.split(",") if item],
        "results": all_results,
        "human_review_status": "pending",
    }
    (output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    return output_dir


def _report(payload: dict[str, Any]) -> str:
    rows = [
        "# V3 Reply 知识单变量 A/B",
        "",
        f"- Router: `{payload['models']['router']}`",
        f"- Reply: `{payload['models']['reply']}`",
        f"- 每组次数: `{payload['attempts']}`",
        "- 语义结论：待人工审核；本报告不使用文本相似度自动判断回复好坏。",
        "",
        "| 条件 | 次数 | Case | 卡点 | 标签命中 | 硬校验 | Reply 采用 | 客户可见回复 |",
        "|---|---:|---|---|---:|---:|---|---|",
    ]
    for item in payload.get("results") or []:
        reply = " / ".join(
            str(message.get("content") or "")
            for message in item.get("reply_messages") or []
            if isinstance(message, dict) and message.get("type") == "text"
        ).replace("|", "\\|").replace("\n", " ")
        knowledge = item.get("knowledge_use") if isinstance(item.get("knowledge_use"), dict) else {}
        checkpoint_display = item.get("actual_checkpoint_type_name") or item.get("actual_checkpoint_code") or "none"
        rows.append(
            f"| {item.get('knowledge_condition')} | {item.get('attempt')} | {item.get('case_id')} | "
            f"{checkpoint_display} | "
            f"{'yes' if item.get('checkpoint_acceptable') else 'no'} | "
            f"{'pass' if item.get('hard_pass') else 'fail'} | "
            f"{knowledge.get('sequence_id') or '-'} / {knowledge.get('step_id') or '-'} | "
            f"{reply[:500]} |"
        )
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the V3 Reply knowledge single-variable A/B.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("workflow_tests/fixtures/v3_trusted_golden_set_v1.json"),
    )
    parser.add_argument(
        "--knowledge-overlay",
        type=Path,
        default=Path("workflow_tests/fixtures/v3_local_sales_knowledge_candidates_v1.json"),
    )
    parser.add_argument("--output-root", type=Path, default=Path(".tmp_runtime/v3_reply_knowledge_ab"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--case-id", default=DEFAULT_CASE_IDS)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--conditions", default="none,online,local")
    parser.add_argument("--router-model", default="deepseek-v4-flash")
    parser.add_argument("--reply-model", default="deepseek-v4-pro")
    args = parser.parse_args()
    print(asyncio.run(run(args)).resolve())


if __name__ == "__main__":
    main()
