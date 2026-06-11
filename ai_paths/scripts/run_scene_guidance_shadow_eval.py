from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "ai_paths") not in sys.path:
    sys.path.insert(0, str(ROOT / "ai_paths"))

from app.policies.scene_guidance_retriever import retrieve_scene_guidance  # noqa: E402


SMOKE_CASES = [
    {"case_id": "price_hidden_fee_001", "family": "SF7_PRICE_ACTIVITY", "question": "到店会乱收费吗", "expected_scene_id": "SF7_HIDDEN_FEE_WORRY"},
    {"case_id": "price_repeat_001", "family": "SF7_PRICE_ACTIVITY", "question": "你还是没告诉我价格", "expected_scene_id": "SF7_PRICE_REPEAT_ASK"},
    {"case_id": "price_deposit_001", "family": "SF7_PRICE_ACTIVITY", "question": "为什么要交10元定金", "expected_scene_id": "SF7_DEPOSIT_EXPLAIN"},
    {"case_id": "store_location_001", "family": "SF6_STORE_INQUIRY", "question": "厦门附近有门店吗", "expected_scene_id": "SF6_STORE_LOCATION"},
    {"case_id": "store_doubt_001", "family": "SF6_STORE_INQUIRY", "question": "为什么不敢发详细地址", "expected_scene_id": "SF6_STORE_ADDRESS_DOUBT"},
    {"case_id": "appointment_weekend_001", "family": "SF9_APPOINTMENT", "question": "周六下午能约吗", "expected_scene_id": "SF9_WEEKEND_AVAILABLE"},
    {"case_id": "trust_qualification_001", "family": "SF10_TRUST_BUILD", "question": "你们有资质吗", "expected_scene_id": "SF10_QUALIFICATION_INQUIRY"},
    {"case_id": "trust_safety_001", "family": "SF10_TRUST_BUILD", "question": "会伤害皮肤吗", "expected_scene_id": "SF10_SAFETY_WORRY"},
    {"case_id": "after_sales_refund_001", "family": "SF12_AFTER_SALES", "question": "把定金退给我，不然我投诉", "expected_scene_id": "SF12_REFUND_REQUEST_HANDOFF"},
    {"case_id": "competitor_cheaper_001", "family": "SF5_COMPETITOR_COMPARE", "question": "别家299你们能同价吗", "expected_scene_id": "SF5_COMPETITOR_CHEAPER"},
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scene guidance shadow retrieval evaluation.")
    parser.add_argument("--input", default="", help="Optional input file. Supports .jsonl, .csv, or plain text.")
    parser.add_argument("--output", default="workflow_tests/reports/scene_guidance_shadow_eval.csv", help="CSV output path.")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    cases = load_cases(Path(args.input)) if args.input else list(SMOKE_CASES)
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        family = str(case.get("family") or "").strip()
        question = str(case.get("question") or case.get("user_question") or "").strip()
        expected_scene_id = str(case.get("expected_scene_id") or "").strip()
        candidates = retrieve_scene_guidance(family=family, user_message=question, top_k=args.top_k)
        top_scene_id = str(candidates[0].get("scene_id") if candidates else "")
        rows.append(
            {
                "case_id": str(case.get("case_id") or f"case_{index:03d}"),
                "family": family,
                "question": question,
                "expected_scene_id": expected_scene_id,
                "top_scene_id": top_scene_id,
                "top_score": candidates[0].get("score", "") if candidates else "",
                "top_match_level": candidates[0].get("match_level", "") if candidates else "",
                "top_status": candidates[0].get("status", "") if candidates else "",
                "candidate_scene_ids": "|".join(str(item.get("scene_id") or "") for item in candidates),
                "hit": "1" if expected_scene_id and expected_scene_id == top_scene_id else "0",
            }
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output, rows)

    expected_rows = [row for row in rows if row["expected_scene_id"]]
    hit_count = sum(1 for row in expected_rows if row["hit"] == "1")
    print(f"cases={len(rows)} expected={len(expected_rows)} top1_hits={hit_count}")
    if expected_rows:
        print(f"top1_accuracy={hit_count / len(expected_rows):.2%}")
    print(f"output={output}")
    return 0


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    cases: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        question = line.strip()
        if question:
            cases.append({"case_id": f"line_{index:03d}", "family": "", "question": question})
    return cases


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "case_id",
        "family",
        "question",
        "expected_scene_id",
        "top_scene_id",
        "top_score",
        "top_match_level",
        "top_status",
        "candidate_scene_ids",
        "hit",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
