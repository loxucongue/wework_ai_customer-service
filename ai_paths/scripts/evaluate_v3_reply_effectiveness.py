"""Blind, conversation-disjoint Reply effectiveness ablation with no side effects."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.model_client import ModelClient

JUDGE_PROMPT = """你只评审淡斑销售微信回复，不续写对话。对照完整上下文和可用事实，比较三个匿名候选。
分别按0-2评分：当前请求是否直接完整处理、论据是否真正相关、表达是否自然、多轮是否不重复不追问已知信息、下一步是否适合且保持积极销售。
正常价值、效果、活动、预约推进不是缺陷；不能因营销本身扣分。强加无关内容、机械问句、遗漏明确请求、虚构事实、完成态无依据、该交付结构却缺失才扣分。
明确退订、人工接管、医疗高风险、严重投诉退款和权威已付边界优先。“给你留着、到时候帮你安排、保留活动价”是允许的销售性软承接，不等于交易完成，不能据此判硬错误。
只有明确虚构已预约/已登记/名额已锁定/档期已确认/已排客/预约金已到账，越过硬停止，或输出上下文白名单之外的结构ID时才写 hard_failure=true，并给 hard_failure_code；不确定时为false。
输出严格JSON：{"scores":{"A":{"request":0,"argument":0,"natural":0,"continuity":0,"next_step":0,"hard_failure":false,"hard_failure_code":""},"B":{},"C":{},"D":{}},"winner":"A|B|C|D|tie","reason_codes":[]}
不输出客户原文或候选原句。"""


def _prompt_from_module(path: Path) -> str:
    spec = importlib.util.spec_from_file_location("reply_prompt_candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load prompt: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = getattr(module, "PARALLEL_REPLY_SYSTEM_PROMPT", "")
    if not isinstance(value, str) or not value:
        raise RuntimeError("prompt constant missing")
    return value


def _organizer_from_module(path: Path):
    spec = importlib.util.spec_from_file_location("reply_context_candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load organizer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.organize_rendered_reply_context


def _visible(reply: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [item for item in (reply or {}).get("reply_messages") or [] if isinstance(item, dict)]


def _ordered_variants(case_id: str, rep: int) -> list[str]:
    values = ["baseline", "input", "prompt_only", "combined"]
    random.Random(f"20260914:{case_id}:{rep}").shuffle(values)
    return values


async def _call(client: ModelClient, messages: list[dict[str, str]], settings: Settings) -> tuple[dict[str, Any] | None, int, str, dict[str, Any]]:
    started = time.perf_counter()
    try:
        value = await client.chat_json(
            messages, tier="reply", temperature=0.15, max_parallel_candidates=1,
            model_names_override=[settings.model_reply], api_key_override=settings.deepseek_api_key,
            base_url_override=settings.deepseek_openai_base_url,
        )
        return value, int((time.perf_counter()-started)*1000), "", dict(client.last_usage or {})
    except Exception as exc:
        return None, int((time.perf_counter()-started)*1000), f"{type(exc).__name__}:{exc}"[:300], dict(client.last_usage or {})


async def _worker(queue: asyncio.Queue, rows: list[dict[str, Any]], settings: Settings) -> None:
    client = ModelClient(settings)
    try:
        while True:
            job = await queue.get()
            if job is None:
                queue.task_done()
                return
            reply, duration, error, usage = await _call(client, job["messages"], settings)
            rows.append({**{k:v for k,v in job.items() if k!="messages"}, "reply":reply,
                         "duration_ms":duration, "error":error, "usage":usage})
            queue.task_done()
    finally:
        await client.aclose()


def _judge_messages(case: dict[str, Any], candidates: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "客户当前消息": case["current"], "连续聊天": case["history"],
        "事实与约束": case["context"][:40000],
        "匿名候选": candidates,
    }
    return [{"role":"system","content":JUDGE_PROMPT},{"role":"user","content":json.dumps(payload,ensure_ascii=False)}]


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    values=sorted(values)
    return values[max(0,min(len(values)-1, int((len(values)-1)*p+.999999)))]


async def main(args: argparse.Namespace) -> int:
    cases=json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.case_ids:
        selected = {item.strip() for item in args.case_ids.split(",") if item.strip()}
        cases = [case for case in cases if case.get("case_id") in selected]
    if args.limit:
        cases = cases[: args.limit]
    baseline=_prompt_from_module(args.baseline_prompt)
    candidate=_prompt_from_module(args.candidate_prompt)
    organize_reply_context = _organizer_from_module(args.organizer_module)
    all_variants={"baseline":(baseline,False),"input":(baseline,True),
                  "prompt_only":(candidate,False),"combined":(candidate,True)}
    requested = [item.strip() for item in args.variants.split(",") if item.strip()]
    if not requested or any(item not in all_variants for item in requested):
        raise ValueError("variants must be a non-empty comma-separated subset of baseline,input,prompt_only,combined")
    variants = {name: all_variants[name] for name in requested}
    settings=Settings(_env_file=args.env_file, AI_PATHS_SERVICE_ROLE="control",
                      SOP_PLATFORM_PULL_ENABLED=False, AI_PATHS_BACKGROUND_WORKERS_ENABLED=False,
                      MODEL_REPLY="deepseek-chat", MODEL_REPLY_FALLBACKS="", MODEL_EMERGENCY_FALLBACKS="")
    jobs=[]
    for rep in range(args.repetitions):
        for case in cases:
            for variant in (name for name in _ordered_variants(case["case_id"],rep) if name in variants):
                prompt, organized=variants[variant]
                context=organize_reply_context(case["context"]) if organized else case["context"]
                jobs.append({"case_id":case["case_id"],"rep":rep,"variant":variant,
                             "messages":[{"role":"system","content":prompt},{"role":"user","content":context}]})
    queue: asyncio.Queue=asyncio.Queue()
    for job in jobs:
        queue.put_nowait(job)
    rows=[]
    workers=[asyncio.create_task(_worker(queue,rows,settings)) for _ in range(args.concurrency)]
    for _ in workers:
        queue.put_nowait(None)
    await queue.join()
    await asyncio.gather(*workers)
    by_key={(r["case_id"],r["rep"],r["variant"]):r for r in rows}
    judge_jobs=[]
    aliases = {name: chr(ord("A") + index) for index, name in enumerate(variants)}
    for rep in range(args.repetitions):
        for case in cases:
            candidates={aliases[v]:_visible(by_key[(case["case_id"],rep,v)]["reply"]) for v in variants}
            judge_messages = _judge_messages(case, candidates)
            judge_messages[1]["content"] += "\n候选标签=" + ",".join(candidates) + "；winner只能从这些标签或tie选择。"
            judge_jobs.append({"case_id":case["case_id"],"rep":rep,"variant":"judge",
                               "messages":judge_messages})
    queue=asyncio.Queue()
    for job in judge_jobs:
        queue.put_nowait(job)
    judgments=[]
    workers=[asyncio.create_task(_worker(queue,judgments,settings)) for _ in range(args.concurrency)]
    for _ in workers:
        queue.put_nowait(None)
    await queue.join()
    await asyncio.gather(*workers)
    wins={v:0 for v in (*variants,"tie")}
    scores={v:{k:[] for k in ("request","argument","natural","continuity","next_step")} for v in variants}
    hard={v:0 for v in variants}
    reverse={v:k for k,v in aliases.items()}
    for row in judgments:
        value=row.get("reply") or {}
        winner=reverse.get(value.get("winner"),"tie")
        wins[winner]+=1
        for alias,data in (value.get("scores") or {}).items():
            variant=reverse.get(alias)
            if not variant or not isinstance(data,dict):
                continue
            for key in scores[variant]:
                if isinstance(data.get(key),(int,float)):
                    scores[variant][key].append(float(data[key]))
            hard[variant]+=bool(data.get("hard_failure"))
    report={"schema":"v3_reply_effectiveness_ablation_v1","baseline_sha":args.baseline_sha,
            "dataset_sha":hashlib.sha256(args.dataset.read_bytes()).hexdigest(),"case_count":len(cases),
            "repetitions":args.repetitions,"temperature":0.15,"model":settings.model_reply,
            "generation_count":len(rows),"judge_count":len(judgments),"wins":wins,"variants":{}}
    for variant in variants:
        group=[r for r in rows if r["variant"]==variant]
        report["variants"][variant]={"parseable":sum(isinstance(r.get("reply"),dict) for r in group),
            "errors":sum(bool(r.get("error")) for r in group),"p50_ms":_percentile([r["duration_ms"] for r in group],.5),
            "p95_ms":_percentile([r["duration_ms"] for r in group],.95),"hard_failures":hard[variant],
            "mean_scores":{k:round(statistics.mean(v),3) if v else None for k,v in scores[variant].items()}}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/"raw_results.json").write_text(json.dumps(rows,ensure_ascii=False),encoding="utf-8")
    (args.output/"raw_judgments.json").write_text(json.dumps(judgments,ensure_ascii=False),encoding="utf-8")
    (args.output/"metrics.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    value=argparse.ArgumentParser()
    value.add_argument("--dataset",type=Path,required=True)
    value.add_argument("--baseline-prompt",type=Path,required=True)
    value.add_argument("--candidate-prompt",type=Path,required=True)
    value.add_argument("--organizer-module",type=Path,required=True)
    value.add_argument("--baseline-sha",required=True)
    value.add_argument("--env-file",type=Path,required=True)
    value.add_argument("--output",type=Path,required=True)
    value.add_argument("--repetitions",type=int,default=3)
    value.add_argument("--concurrency",type=int,default=2)
    value.add_argument("--limit",type=int,default=0)
    value.add_argument("--variants",default="baseline,input,prompt_only,combined")
    value.add_argument("--case-ids",default="")
    return value


if __name__=="__main__":
    raise SystemExit(asyncio.run(main(parser().parse_args())))
