from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.model_client import ModelClient
from app.services.outreach_assets import build_outreach_asset_catalog
from app.services.outreach_prompts import (
    OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT,
    OUTREACH_PLAN_SYSTEM_PROMPT,
    S10_OUTREACH_CONTEXT,
)
from app.services.sop_reply_pack_service import SopReplyPackService
from app.services.outreach_service import build_outreach_activity_quote_fact


REVIEW_PROMPT = """
你是独立的销售对话质量评审。根据测试场景、当前权威活动事实、真实素材目录和计划 json，分别给以下五项 1–5 分：
psychology_accuracy、arc_diversity、asset_fit、human_tone、conversion_action。
4 分表示可上线，5 分表示优秀。paid_suppression 场景只要正确拒绝普通营销计划即可全部给 5 分。

评审边界：
- offer_context 是当前权威事实，可以被计划使用；历史客服话术只证明已经说过什么。
- asset_catalog 只证明可选择的素材。素材不是每轮必需，没有合适素材时 asset_strategy=none 应按正确选择评分。
- 这是客户沉默 24 小时后的递进触达计划，不是对当前消息的即时客服回复。
- 只有事实、安全、支付、结构或明显违背场景目标的问题才算 hard_error；轻微措辞偏好只能影响分数。
- 客户已说忙、有时间再约或等天气时，计划不得继续追问具体日期、工作日、周末或时段。
- 价格透明顾虑不应为了配素材而硬发案例图或活动图；痘印痘坑案例查询不得添加客户未提供的程度、肤质或疗程。
- 相邻角度、新价值和 CTA 索取的信息不同即可认为有递进；不能仅因为都需要客户回复就把 arc_diversity 判为 3 分。
- 发卡步骤的文字和 CTA 必须直接承接本轮 10 元预约金卡；如果让客户回复“活动/入口”后再发卡，属于支付动作不一致。
- 只有 recent_messages 中存在真实完整报价且 plan 标记发卡时，才检查文字与卡片是否一致。没有完整报价时不发卡是正确硬边界，不得因此判 hard_error 或降低 conversion_action。
- 没有完整报价时，最后一轮直接讲一个权威活动事实并用封闭式动作收口，可评为有效成交推进；不能要求客户先回复关键词才提供本轮本可直接说明的信息。
- 没有完整报价时，最后一轮清楚给出一个量化活动事实（268元、限30名或180元赠送）并封闭式询问登记，可将 conversion_action 评为4分以上；不要因为未发卡扣分。
- draft_text 已直接交付本轮 new_value，CTA 只是自然的后续选择时可以高分；任何“回复看/活动/继续/判断后我再提供”都应降低 conversion_action。
- 次数顾虑需要先给非绝对的正面预期，再说明按实际状态评估；只复读类型、深浅和检测属于没有正面回答。
- 最后一轮已经给出活动量化事实并直接问“登记一个吗”或同义封闭式动作时，conversion_action 应为4分以上。以“有空找我、需要时喊我、觉得合适告诉我、我再发完整活动”收尾只能给3分以下。
- “算了、太远、不方便、暂时不用、以后再说”属于软拒绝，不等于明确停止联系。除 paid_suppression 或明确“不要再联系/别发消息/拉黑/投诉退款”外，`should_create_plan=false` 属于 hard_error。

同时输出 hard_error、hard_error_reason 和 concise_reason。只输出有效 json。
""".strip()

ALLOWED_ANGLES = {
    "education",
    "proof",
    "professionalism",
    "empathy",
    "self_image",
    "convenience",
    "scarcity",
    "low_risk_action",
}


def _hard_errors(plan: dict[str, Any], asset_ids: set[str], case: dict[str, Any]) -> list[str]:
    if not bool(plan.get("should_create_plan", True)):
        expected = str(case.get("expected") or "").lower()
        if str(case.get("id") or "") == "paid_suppression" or "should_create_plan=false" in expected:
            return []
        return ["unexpected_suppression"]
    steps = [item for item in plan.get("steps") or [] if isinstance(item, dict)]
    errors: list[str] = []
    if len(steps) not in {2, 3}:
        errors.append("step_count")
    angles = [str(item.get("persuasion_angle") or "") for item in steps]
    if any(angle not in ALLOWED_ANGLES for angle in angles):
        errors.append("invalid_angle")
    if any(left == right for left, right in zip(angles, angles[1:])):
        errors.append("repeated_adjacent_angle")
    if sum(bool(item.get("should_send_payment_collection")) for item in steps) > 1:
        errors.append("multiple_payment_cards")
    for index, step in enumerate(steps):
        strategy = str(step.get("asset_strategy") or "none")
        if strategy in {"configured_image", "operation_video"} and str(step.get("asset_id") or "") not in asset_ids:
            errors.append(f"unknown_asset:{index + 1}")
        if bool(step.get("should_send_payment_collection")) and index != len(steps) - 1:
            errors.append("payment_not_final")
    return errors


async def _run_case(
    client: ModelClient,
    case: dict[str, Any],
    *,
    attempt: int,
    asset_catalog: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    payload = {
        "customer_id": f"model-test-{case['id']}",
        "memory": {},
        "recent_messages": case.get("recent_messages") or [],
        "customer_context": case.get("customer_context") or {},
        "current_stage": "day2_personalized_spoken_unbooked",
        "business_goal": "推动客户重新开口，并逐步推进到店或支付10元预约金",
        "offer_context": S10_OUTREACH_CONTEXT,
        "activity_quote_fact": build_outreach_activity_quote_fact(
            case.get("recent_messages") or [],
            {},
        ),
        "asset_catalog": [
            {key: item.get(key) for key in ("asset_id", "type", "source_pack_name", "sop_category", "purpose")}
            for item in asset_catalog
        ],
        "recent_media_delivery": {"urls": [], "document_ids": []},
    }
    async with semaphore:
        plan = await client.chat_json(
            [
                {"role": "system", "content": OUTREACH_PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            tier="strong",
            temperature=0.0,
        )
        plan = await client.chat_json(
            [
                {"role": "system", "content": OUTREACH_PLAN_REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_snapshot": payload,
                            "candidate_plan": plan,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            tier="strong",
            temperature=0.0,
        )
        review = await client.chat_json(
            [
                {"role": "system", "content": REVIEW_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "case": case,
                            "offer_context": S10_OUTREACH_CONTEXT,
                            "asset_catalog": payload["asset_catalog"],
                            "plan": plan,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            tier="strong",
            temperature=0.0,
        )
    hard_errors = _hard_errors(
        plan,
        {str(item.get("asset_id") or "") for item in asset_catalog},
        case,
    )
    scores = [
        int(review.get(key) or 0)
        for key in ("psychology_accuracy", "arc_diversity", "asset_fit", "human_tone", "conversion_action")
    ]
    passed = not hard_errors and not bool(review.get("hard_error")) and all(score >= 4 for score in scores)
    return {
        "case_id": case["id"],
        "attempt": attempt,
        "passed": passed,
        "hard_errors": hard_errors,
        "plan": plan,
        "review": review,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default="workflow_tests/fixtures/outreach_personalized_model_cases_20260728.json",
    )
    parser.add_argument(
        "--report",
        default=".tmp_runtime/outreach_personalized_model_report_20260728.json",
    )
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()

    settings = Settings()
    client = ModelClient(settings)
    cases = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    selected_case_ids = {str(item).strip() for item in args.case_id if str(item).strip()}
    if selected_case_ids:
        cases = [case for case in cases if str(case.get("id") or "") in selected_case_ids]
    asset_catalog = build_outreach_asset_catalog(SopReplyPackService(settings).load())
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    try:
        results = await asyncio.gather(
            *[
                _run_case(
                    client,
                    case,
                    attempt=attempt,
                    asset_catalog=asset_catalog,
                    semaphore=semaphore,
                )
                for case in cases
                for attempt in range(1, max(1, args.attempts) + 1)
            ]
        )
    finally:
        await client.aclose()

    passed = sum(1 for item in results if item["passed"])
    report = {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else 0,
        "results": results,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total", "passed", "pass_rate")}, ensure_ascii=False))
    return 0 if report["pass_rate"] >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
