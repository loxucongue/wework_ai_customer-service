from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

try:
    from .run_online_appointment_long_flows import (
        DEFAULT_API_URL,
        DEFAULT_WORKFLOW_ID,
        _run_scenario,
    )
    from .test_conversation_store import append_test_conversations
except ImportError:
    from run_online_appointment_long_flows import DEFAULT_API_URL, DEFAULT_WORKFLOW_ID, _run_scenario
    from test_conversation_store import append_test_conversations


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "sales-ad-multi-intent",
        "title": "Codex销冠节奏-广告多意图咨询",
        "turns": [
            "我看到你们祛斑活动广告加的，想了解祛斑、价格、效果和到店安排。",
            "一次做好吗？",
            "会不会伤害皮肤？",
            "确定是268吗？",
            "这个是一次的费用吗？",
            "要做多少次？",
            "到店会不会乱收费？",
            "我想看客户做完之后的效果。",
            "如果我人在厦门机场附近，哪家店近一点？",
            "明天下午可以去看看吗？",
            "10元预约金是做什么的？",
            "不交定金，到店付全款可以吗？",
        ],
    },
    {
        "id": "sales-effect-trust",
        "title": "Codex销冠节奏-效果信任与案例承接",
        "turns": [
            "你好，我脸上的黑色素想改善一下。",
            "你们这个能解决吗，不会做完没效果吧？",
            "我想先看一下你们客户做完后的变化。",
            "图片上的客户做了多少次？",
            "这个会反弹吗？",
            "会不会越做皮肤越薄？",
            "我年纪比较大了，也能做吗？",
            "我预算不想太高，你先说个方向。",
            "如果先去店里看看，要怎么安排？",
            "上海有离浦东近一点的门店吗？",
            "把你推荐那家地址和停车发我。",
        ],
    },
    {
        "id": "sales-platform-price",
        "title": "Codex销冠节奏-平台价与收费口径",
        "turns": [
            "广告上写199，是不是没有其他收费？",
            "为什么一样的地方还有380的价格？",
            "手上的价格是多少，199是一只还是一双？",
            "是做疗付费吗？",
            "有车费报销吗，可以接送吗？",
            "可以去吗，去要多少钱？",
            "为什么不敢发详细地址？",
            "你们门店名字叫什么？",
            "我看到是268的，你怎么跟我说380？",
            "太远了，没有时间去。",
            "这个店我去过，一点效果都没有。",
        ],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sales champion long dialogues and append them to local preview data.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--api-key", default=os.environ.get("AI_EXTERNAL_API_KEY", ""))
    parser.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)
    parser.add_argument("--conversation-output", default="projects/public/test-conversations.json")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--scenario-id", action="append", default=[])
    parser.add_argument("--run-suffix", default=time.strftime("%Y%m%d%H%M%S"))
    args = parser.parse_args()

    report_path = Path(args.report_output or f"logs/sales_champion_round_{args.run_suffix}.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    conversation_path = Path(args.conversation_output)

    selected_ids = {item for item in args.scenario_id if item}
    scenarios = [item for item in SCENARIOS if not selected_ids or item["id"] in selected_ids]
    reports: list[dict[str, Any]] = []

    for scenario in scenarios:
        conversation, report = _run_scenario(
            args.api_url,
            args.api_key,
            args.workflow_id,
            scenario,
            args.run_suffix,
        )
        append_test_conversations([conversation], path=conversation_path)
        reports.append(report)
        print(json.dumps({"scenario": scenario["id"], "turns": report["turn_count"]}, ensure_ascii=False))

    report_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": len(reports), "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
