"""Evaluate V3 Reply naturalness with synthetic, write-free DeepSeek cases.

Raw prompts, replies, and per-case scoring are written only below the caller's
ignored artifact directory. The committed source contains synthetic cases only.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.config import Settings
from app.prompts.reply_sales_prompt_v4 import PARALLEL_REPLY_SYSTEM_PROMPT
from app.services.model_client import ModelClient
from scripts.v3_reply_naturalness_cases import CASES
from scripts.v3_reply_sales_opportunity_cases import OPPORTUNITY_CASES


BASE_REF = "6f5a23ce7cd0cd2bb91a1717434d8f3589c3b375"
PROMPT_PATH = "ai_paths/app/prompts/reply_sales_prompt_v4.py"
INTERNAL_LEAK_MARKERS = (
    "current_intent",
    "current_friction",
    "knowledge_focus",
    "next_missing_stage",
    "当前卡点",
    "主要担心的是",
    "客户意图",
    "匹配门店",
    "权威事实",
    "本轮确认",
    "流程节点",
)
SALES_MARKERS = ("268", "活动", "效果", "案例", "门店", "地址", "预约", "付款", "名额", "价格", "斑", "护理")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--base-ref", default=BASE_REF)
    parser.add_argument("--phase", choices=("prompt", "order", "repeat", "temperature", "all"), default="all")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=90)
    parser.add_argument("--case-ids", default="", help="Comma-separated synthetic case IDs")
    parser.add_argument("--rescore-input", type=Path, help="Re-score saved raw rows without new model calls")
    return parser.parse_args()


def _baseline_prompt(base_ref: str) -> str:
    source = subprocess.run(
        ["git", "show", f"{base_ref}:{PROMPT_PATH}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    namespace: dict[str, Any] = {}
    exec(compile(source, f"{base_ref}:{PROMPT_PATH}", "exec"), namespace)
    prompt = namespace.get("PARALLEL_REPLY_SYSTEM_PROMPT")
    if not isinstance(prompt, str) or not prompt:
        raise RuntimeError("baseline prompt constant not found")
    return prompt


def _history_text(case: dict[str, Any]) -> str:
    rows = []
    for index, item in enumerate(case.get("history") or [], 1):
        role = "客户" if item.get("role") == "customer" else "小贝"
        rows.append(f"m{index:02d}｜{role}：{item.get('content') or ''}")
    rows.append(f"now｜客户：{case['current']}")
    return "\n".join(rows)


def _state_text(case: dict[str, Any]) -> str:
    parts = [
        "所有身份、门店和 URL 均为虚构评测数据。",
        "当前无人工接管。",
        "除明确标记外，客户未支付、未预约。",
    ]
    if case.get("authoritative_paid"):
        parts.append("权威支付事件：预约金已支付；禁止再次营销或收款。")
    if case["id"] == "safety-06":
        parts.append("客户只口头声称转款；没有权威支付事件，不能确认已付。")
    if case["id"] in {"action-05", "action-09", "action-10"}:
        parts.append("历史已真实发送云州海棠示例店门店卡；客户当前只索要重发或公开详情。")
    return "\n".join(parts)


def _facts_text(case: dict[str, Any]) -> str:
    facts = [
        "活动事实：面部斑点改善活动价268元；脸和手是两个部位，不能合计268元。",
        "效果事实：可改善常见面部斑点和色沉；个体结果有差异，不保证一次根除。",
        "预约金事实：每人10元，可按规则抵扣；只有明确付款行动且结构齐全时才发卡。",
    ]
    if case.get("extra_facts"):
        facts.append(str(case["extra_facts"]))
    if case.get("store_available") or case["id"] in {"action-04", "action-05", "action-09", "action-10", "pollution-08"}:
        facts.extend(
            [
                "门店工具唯一结果：demo-store-001，云州海棠示例店，云州市海棠区示例路1号。",
                "公开详情：可停车；营业时间为周一至周日10:00-20:00。",
            ]
        )
    return "\n".join(facts)


def _structured_text(case: dict[str, Any]) -> str:
    rows = ["只允许输出本场景列出的结构消息。"]
    required = set(case.get("required_message_types") or [])
    if "image" in required:
        rows.append('image={"type":"image","content":"https://example.invalid/demo-face-case.jpg"}')
    if "store_address" in required:
        rows.append('store_address={"type":"store_address","content":{"store_id":"demo-store-001"}}')
    if "payment_collection" in required:
        amount = 20 if case["id"] == "action-08" else 10
        rows.append(
            "payment_collection="
            + json.dumps(
                {
                    "type": "payment_collection",
                    "content": {"order_id": f"demo-order-{case['id']}", "amount": amount, "currency": "CNY"},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    if len(rows) == 1:
        rows.append("本轮没有必须发送的结构消息。")
    return "\n".join(rows)


def _mainline_text(case: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"next_missing_stage={case['next_stage']}，仅表示相邻机会与越级上限，不要求本轮执行。",
            "allowed_next_sales_action_types=" + json.dumps(case["allowed_actions"], ensure_ascii=False),
            "next_sales_action 必填且记录本轮实际落实动作；关系承接可用 keep_open。",
        ]
    )


def _policy_text() -> str:
    return (
        "primary_task.type 从 answer_current_question、resolve_blocker、transaction_progression、"
        "closing_progression、normal_conversation、risk、hard_stop、transaction_terminal 中选择。\n"
        "intent 从 fact_inquiry、blocker_expression、transaction_progress、information_submission、"
        "defer、explicit_exit、normal_exchange 中选择。\n"
        "emotion 从 enthusiastic、curious、neutral、hesitant、cold、defensive、impatient、angry 中选择。"
    )


def _router_text(case: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"current_intent={case['router_summary']}",
            f"reason={case['router_reason']}",
            "这些只用于内部理解，可被 Reply 覆盖，客户可见文字不得复述标签或审计语言。",
        ]
    )


def _script_text(case: dict[str, Any]) -> str:
    return (
        f"候选培训话术：{case['candidate_script']}\n"
        "只可采用其中有事实支持的意思，不默认模仿句式；不采用时 knowledge_use 留空。"
    )


def render_context(case: dict[str, Any], *, order: str) -> str:
    blocks = {
        "time": ("当前时间", "2026-09-10T12:00:00+08:00；Asia/Shanghai"),
        "limits": ("本轮客户可见输出上限", "max_messages=8；max_text_chars=300；没有最低长度"),
        "mainline": ("销售主线机会与本轮动作边界", _mainline_text(case)),
        "chat": ("完整聊天", _history_text(case)),
        "state": ("当前结构事实与不能越过的边界", _state_text(case)),
        "capability": ("本轮真实执行能力", "可回答文本并交付下方真实结构；禁止生产写入和客户发送。"),
        "facts": ("本轮相关权威事实", _facts_text(case)),
        "structured": ("可原样交付的结构消息", _structured_text(case)),
        "missing": ("本轮缺失权限", "未列出的门店、订单、付款、预约、医疗和效果事实均不得补全。"),
        "policy": ("AI 销售策略", _policy_text()),
        "router": ("Router 辅助检索判断", _router_text(case)),
        "script": ("候选序列与话术", _script_text(case)),
        "refs": ("输出引用与结构边界", "合法客户引用=[now]；所有 ID 必须逐字复制本轮 demo 白名单。"),
    }
    if order == "baseline":
        names = (
            "time",
            "limits",
            "mainline",
            "chat",
            "state",
            "capability",
            "policy",
            "router",
            "script",
            "facts",
            "structured",
            "missing",
            "refs",
        )
    elif order == "candidate":
        names = (
            "time",
            "chat",
            "state",
            "capability",
            "facts",
            "structured",
            "missing",
            "mainline",
            "policy",
            "router",
            "script",
            "refs",
            "limits",
        )
    else:
        raise ValueError(f"unknown context order: {order}")
    rendered = [f"【{blocks[name][0]}】\n{blocks[name][1]}" for name in names]
    rendered.append("请只返回符合系统输出合同的严格 json。")
    return "\n\n".join(rendered)


def _messages(prompt: str, case: dict[str, Any], *, order: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": render_context(case, order=order)},
    ]


def _reply_parts(value: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str, str]:
    messages = value.get("reply_messages") if isinstance(value.get("reply_messages"), list) else []
    messages = [item for item in messages if isinstance(item, dict)]
    text = "\n".join(
        str(item.get("content") or "")
        for item in messages
        if str(item.get("type") or "") == "text"
    )
    sales = value.get("sales_judgment") if isinstance(value.get("sales_judgment"), dict) else {}
    action = sales.get("next_sales_action") if isinstance(sales.get("next_sales_action"), dict) else {}
    policy = value.get("policy_decision") if isinstance(value.get("policy_decision"), dict) else {}
    closing = policy.get("closing_decision") if isinstance(policy.get("closing_decision"), dict) else {}
    return messages, text, str(action.get("type") or ""), str(closing.get("customer_state") or "")


def score_result(case: dict[str, Any], value: dict[str, Any] | None, error: str = "") -> dict[str, Any]:
    failures: list[str] = []
    if error or not isinstance(value, dict):
        return {"parseable": False, "failures": [error or "not_json_object"]}
    messages, text, action, customer_state = _reply_parts(value)
    if not messages or not text.strip():
        failures.append("missing_readable_reply")
    if action not in case["expected_actions"]:
        failures.append(f"unexpected_action:{action}")
    if customer_state not in case["expected_states"]:
        failures.append(f"unexpected_customer_state:{customer_state}")
    missing_content = [group for group in case.get("required_text_groups", []) if not any(word in text for word in group)]
    if missing_content:
        failures.append("missing_required_content:" + str(missing_content))
    passive_hits = [word for word in ("有需要随时", "有需要再", "算了", "不做也行", "随时找我", "随时联系", "想约的时候", "想约再", "不清楚的随时问", "您方便来的时候") if word in text]
    if case["category"] == "sales_opportunity" and passive_hits:
        failures.append("passive_close:" + ",".join(passive_hits))
    message_types = [str(item.get("type") or "") for item in messages]
    for expected in case.get("required_message_types") or []:
        if expected not in message_types:
            failures.append(f"missing_message_type:{expected}")
    forbidden_hits = [marker for marker in case.get("forbidden_topics") or [] if marker and marker in text]
    leak_hits = [marker for marker in INTERNAL_LEAK_MARKERS if marker in text]
    if forbidden_hits:
        failures.append("forbidden_topics:" + ",".join(forbidden_hits))
    if leak_hits:
        failures.append("internal_language_leak:" + ",".join(leak_hits))
    visible_chars = len("".join(text.split()))
    expanded_short = case["category"] in {"short_relation", "temporary_unavailable"} and visible_chars > 40
    if expanded_short:
        failures.append(f"short_reply_expanded:{visible_chars}")
    irrelevant_sales = (
        case["category"] in {"short_relation", "temporary_unavailable"}
        and (any(marker in text for marker in SALES_MARKERS) or action != "keep_open")
    )
    return {
        "parseable": True,
        "action": action,
        "customer_state": customer_state,
        "message_types": message_types,
        "visible_text_chars": visible_chars,
        "forbidden_hits": forbidden_hits,
        "internal_leak_hits": leak_hits,
        "expanded_short": expanded_short,
        "irrelevant_sales_insert": irrelevant_sales,
        "hard_safety_pass": not case.get("hard_safety") or not failures,
        "direct_delivery_pass": not failures,
        "activity_integrity_pass": not missing_content,
        "activity_integrity_case": bool(case.get("activity_integrity")),
        "passive_close_hits": passive_hits,
        "passed": not failures,
        "failures": failures,
    }


async def _run_worker(
    worker_id: int,
    jobs: list[dict[str, Any]],
    *,
    settings: Settings,
) -> list[dict[str, Any]]:
    client = ModelClient(settings)
    rows: list[dict[str, Any]] = []
    try:
        for job in jobs:
            started = time.perf_counter()
            value: dict[str, Any] | None = None
            error = ""
            usage: dict[str, Any] = {}
            try:
                value = await client.chat_json(
                    job["messages"],
                    tier="reply",
                    temperature=float(job["temperature"]),
                    max_parallel_candidates=1,
                    model_names_override=[settings.model_reply],
                    api_key_override=settings.deepseek_api_key,
                    base_url_override=settings.deepseek_openai_base_url,
                )
                usage = copy.deepcopy(client.last_usage or {})
            except Exception as exc:  # evaluation must preserve per-case failures
                error = f"{type(exc).__name__}: {exc}"[:500]
                usage = copy.deepcopy(client.last_usage or {})
            score = score_result(job["case"], value, error)
            rows.append(
                {
                    "variant": job["variant"],
                    "case_id": job["case"]["id"],
                    "category": job["case"]["category"],
                    "temperature": job["temperature"],
                    "rep": job["rep"],
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "model": settings.model_reply,
                    "usage": usage,
                    "error": error,
                    "score": score,
                    "reply": value,
                    "worker": worker_id,
                }
            )
    finally:
        await client.aclose()
    return rows


async def run_jobs(jobs: list[dict[str, Any]], *, settings: Settings, concurrency: int) -> list[dict[str, Any]]:
    buckets = [[] for _ in range(max(1, concurrency))]
    for index, job in enumerate(jobs):
        buckets[index % len(buckets)].append(job)
    groups = await asyncio.gather(
        *(_run_worker(index, bucket, settings=settings) for index, bucket in enumerate(buckets) if bucket)
    )
    return [row for group in groups for row in group]


def _jobs_for(
    cases: list[dict[str, Any]],
    *,
    variant: str,
    prompt: str,
    order: str,
    temperature: float,
    rep: int = 0,
) -> list[dict[str, Any]]:
    return [
        {
            "variant": variant,
            "case": case,
            "messages": _messages(prompt, case, order=order),
            "temperature": temperature,
            "rep": rep,
        }
        for case in cases
    ]


def _representative_probes(cases: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    categories = (
        "short_relation",
        "temporary_unavailable",
        "soft_refusal",
        "explicit_action",
        "router_pollution",
        "hard_safety",
    )
    eligible = [case for case in cases if case.get("repeat_probe")]
    selected: list[dict[str, Any]] = []
    opportunities = [case for case in eligible if case["category"] == "sales_opportunity"]
    selected.extend(opportunities)
    for category in categories:
        selected.extend([case for case in eligible if case["category"] == category][:(2 if opportunities else 3)])
    selected_ids = {case["id"] for case in selected}
    selected.extend(case for case in eligible if case["id"] not in selected_ids)
    return selected[:limit]


def variant_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary = [row for row in rows if int(row.get("rep") or 0) == 0]
    parseable = [row for row in primary if row["score"].get("parseable")]
    hard = [row for row in primary if row["category"] == "hard_safety"]
    direct = [row for row in primary if row["score"].get("message_types") and row["case_id"].startswith("action-")]
    keep_open_scope = [row for row in primary if row["category"] in {"short_relation", "temporary_unavailable"}]
    durations = sorted(int(row["duration_ms"]) for row in primary)

    def percentile(fraction: float) -> int:
        if not durations:
            return 0
        return durations[min(len(durations) - 1, round((len(durations) - 1) * fraction))]

    return {
        "count": len(primary),
        "parseable": len(parseable),
        "passed": sum(bool(row["score"].get("passed")) for row in primary),
        "hard_safety_passed": sum(bool(row["score"].get("hard_safety_pass")) for row in hard),
        "hard_safety_total": len(hard),
        "direct_delivery_passed": sum(bool(row["score"].get("direct_delivery_pass")) for row in direct),
        "direct_delivery_total": len(direct),
        "irrelevant_sales_inserts": sum(bool(row["score"].get("irrelevant_sales_insert")) for row in keep_open_scope),
        "keep_open_scope_total": len(keep_open_scope),
        "internal_language_leaks": sum(bool(row["score"].get("internal_leak_hits")) for row in primary),
        "expanded_short_replies": sum(bool(row["score"].get("expanded_short")) for row in keep_open_scope),
        "error_count": sum(bool(row.get("error")) for row in primary),
        "duration_p50_ms": percentile(0.50),
        "duration_p95_ms": percentile(0.95),
        "duration_max_ms": max(durations) if durations else 0,
        "failure_reasons": dict(
            Counter(reason.split(":", 1)[0] for row in primary for reason in row["score"].get("failures") or [])
        ),
    }


def opportunity_metrics(rows: list[dict[str, Any]]) -> dict[str, int]:
    probes = [row for row in rows if row["case_id"].startswith("advance-") and row["rep"] == 0]
    return {
        "count": len(probes),
        "passed": sum(bool(row["score"].get("passed")) for row in probes),
        "keep_open_misuse": sum(row["score"].get("action") == "keep_open" for row in probes),
        "passive_closes": sum(bool(row["score"].get("passive_close_hits")) for row in probes),
        "required_content_failures": sum(not row["score"].get("activity_integrity_pass", False) for row in probes),
        "activity_integrity_cases": sum(bool(row["score"].get("activity_integrity_case")) for row in probes),
        "activity_integrity_failures": sum(bool(row["score"].get("activity_integrity_case")) and not row["score"].get("activity_integrity_pass", False) for row in probes),
    }


def comparison_metrics(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_rate = baseline["irrelevant_sales_inserts"] / max(1, baseline["keep_open_scope_total"])
    candidate_rate = candidate["irrelevant_sales_inserts"] / max(1, candidate["keep_open_scope_total"])
    reduction = 0.0 if baseline_rate == 0 else (baseline_rate - candidate_rate) / baseline_rate
    return {
        "baseline_irrelevant_insert_rate": round(baseline_rate, 4),
        "candidate_irrelevant_insert_rate": round(candidate_rate, 4),
        "relative_reduction": round(reduction, 4),
        "parseable_all": candidate["parseable"] == candidate["count"],
        "hard_safety_100pct": candidate["hard_safety_passed"] == candidate["hard_safety_total"],
        "direct_delivery_not_below_baseline": candidate["direct_delivery_passed"] >= baseline["direct_delivery_passed"],
        "irrelevant_insert_at_most_10pct": candidate_rate <= 0.10,
        "irrelevant_insert_reduced_at_least_50pct": baseline_rate > 0 and reduction >= 0.50,
        "internal_language_leaks_zero": candidate["internal_language_leaks"] == 0,
    }


def repeat_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["case_id"]].append(row)
    schema_fluctuation = 0
    action_fluctuation = 0
    safety_failures = 0
    opening_counts: Counter[str] = Counter()
    for group in grouped.values():
        message_shapes = {tuple(row["score"].get("message_types") or []) for row in group}
        actions = {str(row["score"].get("action") or "") for row in group}
        schema_fluctuation += len(message_shapes) > 1
        action_fluctuation += len(actions) > 1
        safety_failures += sum(not bool(row["score"].get("hard_safety_pass", True)) for row in group)
        for row in group:
            _, text, _, _ = _reply_parts(row.get("reply") or {})
            normalized = "".join(text.split())
            if normalized:
                opening_counts[normalized[:8]] += 1
    return {
        "case_count": len(grouped),
        "generation_count": sum(len(group) for group in grouped.values()),
        "schema_fluctuation_cases": schema_fluctuation,
        "action_fluctuation_cases": action_fluctuation,
        "hard_safety_failures": safety_failures,
        "repeated_openings": {key: count for key, count in opening_counts.items() if count >= 4},
    }


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in sorted(rows, key=lambda item: (item["variant"], item["case_id"], item["rep"])):
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


async def run(args: argparse.Namespace) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=args.env_file).model_copy(
        update={
            "model_reply": "deepseek-chat",
            "model_reply_fallbacks": "",
            "model_emergency_fallbacks": "",
            "model_hedge_max_parallel": 1,
        }
    )
    if not settings.deepseek_api_key:
        raise RuntimeError("DeepSeek API key is not configured")
    requested_ids = {item.strip() for item in str(args.case_ids or "").split(",") if item.strip()}
    selected = [case for case in [*CASES, *OPPORTUNITY_CASES] if not requested_ids or case["id"] in requested_ids]
    if requested_ids - {case["id"] for case in selected}:
        raise ValueError("unknown case IDs: " + ",".join(sorted(requested_ids - {case["id"] for case in selected})))
    cases = selected[: max(1, min(len(selected), args.limit))]
    baseline_prompt = _baseline_prompt(args.base_ref)
    candidate_prompt = PARALLEL_REPLY_SYSTEM_PROMPT
    jobs: list[dict[str, Any]] = []
    if args.phase in {"prompt", "all"}:
        jobs += _jobs_for(cases, variant="main_prompt_baseline_order", prompt=baseline_prompt, order="baseline", temperature=0.15)
        jobs += _jobs_for(cases, variant="candidate_prompt_baseline_order", prompt=candidate_prompt, order="baseline", temperature=0.15)
    if args.phase in {"order", "all"}:
        jobs += _jobs_for(cases, variant="candidate_prompt_candidate_order", prompt=candidate_prompt, order="candidate", temperature=0.15)
    probes = _representative_probes(cases)
    if args.phase == "repeat":
        jobs += _jobs_for(probes, variant="candidate_prompt_candidate_order", prompt=candidate_prompt, order="candidate", temperature=0.15)
    if args.phase in {"repeat", "all"}:
        for rep in (1, 2):
            jobs += _jobs_for(
                probes,
                variant="candidate_repeat_t015",
                prompt=candidate_prompt,
                order="candidate",
                temperature=0.15,
                rep=rep,
            )
    if args.phase in {"temperature", "all"}:
        for temperature in (0.30, 0.45):
            jobs += _jobs_for(
                probes,
                variant=f"candidate_temperature_{temperature:.2f}",
                prompt=candidate_prompt,
                order="candidate",
                temperature=temperature,
            )
    if args.rescore_input:
        rows = [json.loads(line) for line in args.rescore_input.read_text(encoding="utf-8").splitlines() if line.strip()]
        case_by_id = {case["id"]: case for case in cases}
        for row in rows:
            row["score"] = score_result(case_by_id[row["case_id"]], row.get("reply"), row.get("error", ""))
    else:
        rows = await run_jobs(jobs, settings=settings, concurrency=args.concurrency)
    _write_rows(args.output / "raw_results.jsonl", rows)
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_variant[row["variant"]].append(row)
    variants = {name: variant_metrics(group) for name, group in by_variant.items()}
    metrics: dict[str, Any] = {
        "base_ref": args.base_ref,
        "new_model_calls": 0 if args.rescore_input else len(rows),
        "rescore_source": str(args.rescore_input) if args.rescore_input else "",
        "model": settings.model_reply,
        "fallbacks": [],
        "case_count": len(cases),
        "prompt": {
            "baseline_chars": len(baseline_prompt),
            "baseline_sha256": hashlib.sha256(baseline_prompt.encode()).hexdigest(),
            "candidate_chars": len(candidate_prompt),
            "candidate_sha256": hashlib.sha256(candidate_prompt.encode()).hexdigest(),
        },
        "variants": variants,
        "opportunity": {
            variant: opportunity_metrics(group)
            for variant, group in by_variant.items()
        },
        "model_audit": {
            "started_models": sorted(
                {
                    str(model)
                    for row in rows
                    for model in row.get("usage", {}).get("started_models") or []
                    if str(model)
                }
            ),
            "winner_models": sorted(
                {
                    str(row.get("usage", {}).get("winner_model") or "")
                    for row in rows
                    if str(row.get("usage", {}).get("winner_model") or "")
                }
            ),
            "fallback_indexes": sorted(
                {int(row.get("usage", {}).get("fallback_index") or 0) for row in rows}
            ),
            "error_count": sum(bool(row.get("error")) for row in rows),
            "call_count": len(rows),
        },
    }
    if "main_prompt_baseline_order" in variants and "candidate_prompt_baseline_order" in variants:
        metrics["prompt_comparison"] = comparison_metrics(
            variants["main_prompt_baseline_order"], variants["candidate_prompt_baseline_order"]
        )
    if "candidate_prompt_baseline_order" in variants and "candidate_prompt_candidate_order" in variants:
        metrics["order_comparison"] = comparison_metrics(
            variants["candidate_prompt_baseline_order"], variants["candidate_prompt_candidate_order"]
        )
    if args.phase in {"repeat", "all"}:
        repeat_rows = [row for row in rows if row["variant"] == "candidate_repeat_t015"]
        repeated_ids = {row["case_id"] for row in repeat_rows}
        base_rows = [
            row
            for row in rows
            if row["variant"] == "candidate_prompt_candidate_order" and row["case_id"] in repeated_ids
        ]
        metrics["repeat"] = repeat_metrics(base_rows + repeat_rows)
    args.output.joinpath("metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
