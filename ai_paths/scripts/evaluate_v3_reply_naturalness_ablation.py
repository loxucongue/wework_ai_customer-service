"""Controlled V0-V3 naturalness ablation on the frozen synthetic matrix."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from app.config import Settings
from scripts.evaluate_v3_reply_naturalness import (
    PROMPT_PATH, render_context, run_jobs, variant_metrics, opportunity_metrics,
)
from scripts.v3_reply_naturalness_cases import CASES
from scripts.v3_reply_sales_opportunity_cases import OPPORTUNITY_CASES

FOCUS_IDS = (
    "action-07", "advance-booking-03", "advance-effect-02", "advance-effect-03",
    "advance-opening-02", "advance-price-01", "advance-price-03", "advance-resolved-01",
    "advance-resolved-02", "short-07", "short-12", "soft-01",
)
PAIR_IDS = (
    "action-01", "action-03", "advance-booking-01", "advance-effect-01",
    "advance-opening-01", "advance-price-02", "advance-resolved-03", "short-01",
    "short-04", "soft-02", "soft-09", "safety-01",
)


def _git_prompt(ref: str) -> str:
    source = subprocess.run(["git", "show", f"{ref}:{PROMPT_PATH}"], check=True,
                            capture_output=True, text=True, encoding="utf-8").stdout
    namespace: dict[str, Any] = {}
    exec(compile(source, f"{ref}:{PROMPT_PATH}", "exec"), namespace)
    return namespace["PARALLEL_REPLY_SYSTEM_PROMPT"]


def _resolve_baseline(ref: str) -> tuple[str, str]:
    sha = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    if len(sha) != 40:
        raise ValueError(f"baseline ref did not resolve to a full commit SHA: {ref!r}")
    return sha, _git_prompt(sha)


def _v3_prompt(v0: str) -> str:
    old = next(line for line in v0.splitlines() if line.startswith("4) 主动捕捉销售机会。"))
    new = (
        "4) 先做上下文对照：只确认收到、感谢、祝福、夸赞回复速度、已讲清后的‘行吧’是关系承接，用 keep_open；"
        "认可具体效果、价格、方案，或明确说顾虑已解除/可以继续，才推进 next_missing_stage。首次无因‘考虑一下’问一个真实顾虑；"
        "已经问过顾虑后的再次暂缓只短承接。明确索要价格、图片、地址、预约、付款时完成请求优先。价值、活动、门店均已交付后的积极认可要邀请日期，不能用泛化收口代替。"
    )
    return v0.replace(old, new)


def _isolate_script(context: str) -> str:
    old = "只可采用其中有事实支持的意思，不默认模仿句式；不采用时 knowledge_use 留空。"
    new = "只提供销售逻辑、事实线索和论据；不参考语气、句式、称呼或固定表达；不采用时 knowledge_use 留空。"
    return context.replace(old, new)


def _messages(prompt: str, case: dict[str, Any], *, isolate_script: bool) -> list[dict[str, str]]:
    context = render_context(case, order="candidate")
    if isolate_script:
        context = _isolate_script(context)
    return [{"role": "system", "content": prompt}, {"role": "user", "content": context}]


def _jobs(
    cases: list[dict[str, Any]], *, repetitions: int, baseline_prompt: str
) -> list[dict[str, Any]]:
    v0 = baseline_prompt
    variants = {"v0_main": (v0, False), "v1_activity_facts": (v0, False),
                "v2_script_isolation": (v0, True), "v3_context_rules": (_v3_prompt(v0), True)}
    jobs = []
    for name, (prompt, isolate) in variants.items():
        for rep in range(repetitions):
            for case in cases:
                jobs.append({"variant": name, "case": case, "messages": _messages(prompt, case, isolate_script=isolate),
                             "temperature": 0.15, "rep": rep})
    return jobs


def _completed_state_claim(text: str) -> bool:
    return any(term in text for term in ("已预约成功", "已登记完成", "名额已锁定", "档期已确认", "已排客", "预约金已到账"))


def _base_report(args: argparse.Namespace, *, case_count: int, baseline_sha: str) -> dict[str, Any]:
    return {
        "phase": args.phase,
        "case_count": case_count,
        "repetitions": args.repetitions,
        "temperature": 0.15,
        "baseline_ref": args.baseline_ref,
        "baseline_sha": baseline_sha,
        "variants": {},
    }


async def main(args: argparse.Namespace) -> int:
    baseline_sha, baseline_prompt = _resolve_baseline(args.baseline_ref)
    all_cases = [*CASES, *OPPORTUNITY_CASES]
    selected_ids = set(FOCUS_IDS + PAIR_IDS) if args.phase == "screen" else {case["id"] for case in all_cases}
    cases = [case for case in all_cases if case["id"] in selected_ids]
    settings = Settings(_env_file=args.env_file).model_copy(update={"model_reply": "deepseek-chat",
        "model_reply_fallbacks": "", "model_emergency_fallbacks": "", "model_hedge_max_parallel": 1})
    rows = await run_jobs(
        _jobs(cases, repetitions=args.repetitions, baseline_prompt=baseline_prompt),
        settings=settings,
        concurrency=args.concurrency,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "raw_results.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    report = _base_report(args, case_count=len(cases), baseline_sha=baseline_sha)
    for variant in ("v0_main", "v1_activity_facts", "v2_script_isolation", "v3_context_rules"):
        group = [row for row in rows if row["variant"] == variant]
        primary = [row for row in group if row["rep"] == 0]
        completed = []
        for row in group:
            messages = (row.get("reply") or {}).get("reply_messages") or []
            visible = " ".join(str(item.get("content") or "") for item in messages if item.get("type") == "text")
            if _completed_state_claim(visible):
                completed.append({"case_id": row["case_id"], "rep": row["rep"], "text": visible})
        report["variants"][variant] = {"primary": variant_metrics(primary),
            "opportunity_primary": opportunity_metrics(primary), "generation_count": len(group),
            "all_generation_passed": sum(bool(row["score"]["passed"]) for row in group),
            "completed_state_claims": completed}
    report["v1_model_input_equivalent_to_v0"] = True
    (args.output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--phase", choices=("screen", "full"), default="screen")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(build_parser().parse_args())))
