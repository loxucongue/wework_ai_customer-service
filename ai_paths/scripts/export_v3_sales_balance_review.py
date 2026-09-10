"""Export 50 blinded synthetic pairs, including all 30 opportunity cases."""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

from scripts.v3_reply_naturalness_cases import CASES
from scripts.v3_reply_sales_opportunity_cases import OPPORTUNITY_CASES
from scripts.evaluate_v3_reply_naturalness import _facts_text


def export(source: Path, output: Path) -> None:
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    baseline = {row["case_id"]: row for row in rows if row["variant"] == "main_prompt_baseline_order"}
    candidate = {row["case_id"]: row for row in rows if row["variant"] == "candidate_prompt_candidate_order"}
    selected = list(OPPORTUNITY_CASES)
    for category, quota in {"short_relation": 3, "temporary_unavailable": 3, "soft_refusal": 2,
                            "explicit_action": 6, "hard_safety": 6}.items():
        selected.extend([case for case in CASES if case["category"] == category][:quota])
    assert len(selected) == 50
    rng = random.Random(20260910)
    rng.shuffle(selected)
    review = []
    key = []
    for index, case in enumerate(selected, 1):
        versions = [("baseline", baseline[case["id"]]), ("candidate", candidate[case["id"]])]
        rng.shuffle(versions)
        row = {"编号": index, "对话历史": json.dumps(case["history"], ensure_ascii=False),
               "当前客户消息": case["current"], "本轮事实供核对": _facts_text(case), "本轮是否存在自然销售机会": ""}
        mapping = {"编号": index, "case_id": case["id"], "category": case["category"]}
        for label, (version, result) in zip(("A", "B"), versions):
            row[f"{label}回复含结构消息"] = json.dumps((result.get("reply") or {}).get("reply_messages", []), ensure_ascii=False)
            mapping[label] = version
            for field in ("自然度1至5", "先答当前问题", "活动信息完整", "硬安全正确", "是否完成合适推进",
                          "是否明显过于被动", "是否无必要结束对话", "是否无关硬转销售"):
                row[f"{label}{field}"] = ""
        row.update({"偏好A或B或持平": "", "评审意见": ""})
        review.append(row)
        key.append(mapping)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "blind_review_50.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(review[0]))
        writer.writeheader()
        writer.writerows(review)
    (output / "blind_review_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.input, args.output)
