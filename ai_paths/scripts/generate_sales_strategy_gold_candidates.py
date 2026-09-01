from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


INTENT_TEMPLATES = {
    "fact_inquiry": ["这个具体怎么做", "总共多少钱", "大概要多久", "包含哪些内容"],
    "blocker_expression": ["我主要还是担心价格", "我怕效果不适合我", "过去不太方便", "家里人还不同意"],
    "transaction_progress": ["可以，下一步怎么预约", "就按这个来，怎么付款", "我选好了，帮我继续办理", "把报名步骤发我"],
    "information_submission": ["我姓李，电话尾号1234", "我在城东，周六有空", "两个人一起过去", "我选刚才那家门店"],
    "defer": ["这两天忙，晚点再说", "我先考虑一下", "过几天有空再看", "目前先不安排"],
    "explicit_exit": ["不要再联系我", "我不需要了，停止发消息", "取消后续跟进", "别再营销了"],
    "normal_exchange": ["好的，谢谢", "嗯，知道了", "收到", "辛苦了"],
}
EMOTION_PREFIX = {
    "enthusiastic": "太好了，",
    "curious": "我挺好奇，",
    "neutral": "请问，",
    "hesitant": "我有点犹豫，",
    "cold": "嗯，",
    "defensive": "我比较担心被套路，",
    "impatient": "别一直催了，",
    "angry": "我现在很生气，",
}
CATEGORY_DEFAULT_EMOTION = {
    "visit_blocked": "hesitant",
    "distance_objection": "hesitant",
    "time_objection": "neutral",
    "price_objection": "defensive",
    "effect_objection": "curious",
    "trust_objection": "defensive",
    "health_constraint": "neutral",
    "repair_objection": "curious",
    "deposit_objection": "defensive",
    "decision_hesitation": "hesitant",
    "family_decision": "hesitant",
    "need_mismatch": "neutral",
    "wrong_project_intent": "neutral",
}


def _scenario_messages(scenario: str, index: int) -> str:
    templates = (
        "我主要担心{scenario}，这个怎么处理？",
        "如果{scenario}，还有别的办法吗？",
        "我不是完全不想做，就是{scenario}。",
        "之前介绍我听懂了，但我的问题还是{scenario}。",
        "先别催付款，我想问清楚{scenario}。",
    )
    return templates[index % len(templates)].format(scenario=scenario)


def build_candidates(catalog: dict[str, Any]) -> dict[str, Any]:
    scenario_name_by_key = {
        str(item.get("scenario_key") or ""): str(item.get("name") or "").strip()
        for item in catalog.get("scenarios") or []
    }
    scenarios_by_category: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in catalog.get("contents") or []:
        category = str(item.get("category_key") or "")
        scenario_key = str((item.get("scenario_keys") or [""])[0])
        name = scenario_name_by_key.get(scenario_key) or str(item.get("scenario_name") or "").strip()
        pair = (scenario_key, name)
        if category and scenario_key and name and pair not in scenarios_by_category[category]:
            scenarios_by_category[category].append(pair)

    cases: list[dict[str, Any]] = []
    for category in [str(item.get("category_key") or "") for item in catalog.get("categories") or []]:
        scenarios = scenarios_by_category[category]
        if not scenarios:
            raise ValueError(f"category {category} has no scenarios")
        for index in range(20):
            scenario_key, scenario = scenarios[index % len(scenarios)]
            message = _scenario_messages(scenario, index)
            cases.append(
                {
                    "id": f"cardpoint_{category}_{index + 1:02d}",
                    "current_message": message,
                    "history": ["客服：我先了解一下您现在最在意的问题。"] if index % 4 == 0 else [],
                    "expected": {
                        "cardpoint_category": category,
                        "scenario_key": scenario_key,
                        "realtime_intent": "blocker_expression",
                        "emotion": CATEGORY_DEFAULT_EMOTION[category],
                    },
                    "dimensions": ["cardpoint", "multi_signal" if index < 7 else "single_signal"],
                    "review_status": "pending_human_gold",
                }
            )

    emotion_labels = list(EMOTION_PREFIX)
    for intent_index, (intent, templates) in enumerate(INTENT_TEMPLATES.items()):
        for index in range(20):
            emotion = emotion_labels[(intent_index * 20 + index) % len(emotion_labels)]
            base = templates[index % len(templates)]
            cases.append(
                {
                    "id": f"intent_{intent}_{index + 1:02d}",
                    "current_message": f"{EMOTION_PREFIX[emotion]}{base}",
                    "history": ["客服：上次我们聊到活动、门店和预约步骤。"] if index % 3 == 0 else [],
                    "expected": {
                        "cardpoint_category": "price_objection" if intent == "blocker_expression" else "",
                        "scenario_key": "",
                        "realtime_intent": intent,
                        "emotion": emotion,
                    },
                    "dimensions": ["intent", "emotion", "multi_signal" if index < 6 else "single_signal"],
                    "review_status": "pending_human_gold",
                }
            )

    if len(cases) != 400:
        raise AssertionError(f"expected 400 cases, got {len(cases)}")
    category_counts = Counter(case["expected"]["cardpoint_category"] for case in cases if case["expected"]["cardpoint_category"])
    intent_counts = Counter(case["expected"]["realtime_intent"] for case in cases)
    emotion_counts = Counter(case["expected"]["emotion"] for case in cases)
    multi_signal_count = sum("multi_signal" in case["dimensions"] for case in cases)
    return {
        "schema_version": "sales_strategy_gold_candidates_v1",
        "version": "2026-08-31.1",
        "gold_status": "pending_human_review",
        "notice": "Labels are generated from the approved taxonomy and source scenarios. Human review is required before release acceptance metrics may call this a gold set.",
        "coverage": {
            "cases": len(cases),
            "category_counts": dict(category_counts),
            "intent_counts": dict(intent_counts),
            "emotion_counts": dict(emotion_counts),
            "multi_signal_count": multi_signal_count,
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    result = build_candidates(catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["coverage"], ensure_ascii=False))


if __name__ == "__main__":
    main()
