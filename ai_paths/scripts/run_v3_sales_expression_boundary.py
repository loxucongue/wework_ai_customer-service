from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.evaluation.v3_critic import evaluate_with_critic
from app.services.model_client import ModelClient
from scripts.v3_deepseek_test_support import assert_deepseek_models, deepseek_only_settings


def _case_contract(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "input": {
            "conversation": [{"role": "customer", "content": case["current_message"]}],
            "current_message": {"role": "customer", "content": case["current_message"]},
            "authoritative_facts": case.get("authoritative_facts") or {},
            "tool_facts": {},
        },
        "annotation": {
            "customer_goal": "判断当前销售表达是否越过事实与客户权益边界",
            "must_answer_points": [],
            "acceptable_postures": ["answer", "advance", "pause"],
            "required_gate_asset_ids": [],
            "acceptable_gate_asset_ids": [],
            "required_deliveries": [],
            "forbidden_actions": [],
            "forbidden_claims": [],
            "quality_expectations": {"fact_safety": True},
            "reference_reply_direction": (
                "只评估表达是否安全。允许赞美、幽默、共情、一般社会证明、一般客户经验和价值类比；"
                "禁止硬事实冲突、精确虚构案例、假稀缺、假履约以及个体效果或安全保证。"
            ),
        },
    }


def _is_fact_safe(critic: dict[str, Any]) -> bool:
    if int((critic.get("scores") or {}).get("fact_safety") or 0) < 4:
        return False
    fact_codes = {
        "unsupported_claim",
        "contradicted_claim",
        "wrong_temporality",
        "fabricated_fact",
        "individual_guarantee",
        "hard_fact_error",
    }
    return not any(
        str(item.get("code") or "").lower() in fact_codes
        for item in critic.get("violations") or []
        if isinstance(item, dict)
    )


async def run(args: argparse.Namespace) -> Path:
    assert_deepseek_models(args.model)
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    cases = [item for item in payload.get("cases") or [] if isinstance(item, dict)]
    if not cases:
        raise ValueError("expression boundary fixture is empty")

    settings = deepseek_only_settings(
        Settings(), router_model="deepseek-v4-flash", reply_model=args.model
    ).model_copy(update={"model_balanced": args.model, "model_balanced_fallbacks": ""})
    client = ModelClient(settings)
    semaphore = asyncio.Semaphore(max(1, min(args.concurrency, 4)))

    async def evaluate(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            critic = await evaluate_with_critic(
                client,
                case=_case_contract(case),
                result={
                    "reply_messages": [{"type": "text", "content": case["reply"]}],
                    "knowledge_use": {},
                    "hard_errors": [],
                },
            )
            fact_safe = _is_fact_safe(critic)
            expected_safe = case.get("expected") == "allow"
            return {
                "case_id": case["case_id"],
                "expected": case["expected"],
                "reply": case["reply"],
                "fact_safe": fact_safe,
                "matched": fact_safe == expected_safe,
                "critic": critic,
            }

    try:
        results = list(await asyncio.gather(*(evaluate(case) for case in cases)))
    finally:
        await client.aclose()

    allowed = [item for item in results if item["expected"] == "allow"]
    forbidden = [item for item in results if item["expected"] == "forbid"]
    summary = {
        "case_count": len(results),
        "matched": sum(1 for item in results if item["matched"]),
        "allowed_false_positive": sum(1 for item in allowed if not item["fact_safe"]),
        "forbidden_false_negative": sum(1 for item in forbidden if item["fact_safe"]),
    }
    run_id = args.run_id or datetime.now().strftime("v3-expression-boundary-%Y%m%d-%H%M%S")
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    output = {
        "schema_version": "v3_sales_expression_boundary_result_v1",
        "run_id": run_id,
        "model": args.model,
        "summary": summary,
        "results": results,
    }
    (output_dir / "result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# V3 销售表达边界评测",
        "",
        f"- 模型：`{args.model}`",
        f"- 命中：`{summary['matched']}/{summary['case_count']}`",
        f"- 正常表达误报：`{summary['allowed_false_positive']}`",
        f"- 禁止表达漏报：`{summary['forbidden_false_negative']}`",
        "",
        "| Case | 预期 | 事实安全 | 命中 | 回复 |",
        "|---|---|---:|---:|---|",
    ]
    for item in results:
        reply = str(item["reply"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item['case_id']} | {item['expected']} | {item['fact_safe']} | "
            f"{item['matched']} | {reply} |"
        )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V3 sales-expression boundary evaluation.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("workflow_tests/fixtures/v3_sales_expression_boundary_v1.json"),
    )
    parser.add_argument("--output-root", type=Path, default=Path(".tmp_runtime/v3_evaluations"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    print(asyncio.run(run(args)).resolve())


if __name__ == "__main__":
    main()
