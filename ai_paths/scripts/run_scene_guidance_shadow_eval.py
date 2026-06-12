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


FAMILY_BY_FLOW = {
    "SF5_competitor_response": "SF5_COMPETITOR_COMPARE",
    "SF6_store_match": "SF6_STORE_INQUIRY",
    "SF7_price_consult": "SF7_PRICE_ACTIVITY",
    "SF9_appointment": "SF9_APPOINTMENT",
    "SF10_trust_build": "SF10_TRUST_BUILD",
    "SF12_after_sales": "SF12_AFTER_SALES",
    "SF5_COMPETITOR_RESPONSE": "SF5_COMPETITOR_COMPARE",
    "SF6_STORE_MATCH": "SF6_STORE_INQUIRY",
    "SF7_PRICE_CONSULT": "SF7_PRICE_ACTIVITY",
    "SF9_APPOINTMENT": "SF9_APPOINTMENT",
    "SF10_TRUST_BUILD": "SF10_TRUST_BUILD",
    "SF12_AFTER_SALES": "SF12_AFTER_SALES",
}

FAMILY_BY_INTENT = {
    "competitor_compare": "SF5_COMPETITOR_COMPARE",
    "store_inquiry": "SF6_STORE_INQUIRY",
    "price_inquiry": "SF7_PRICE_ACTIVITY",
    "campaign_inquiry": "SF7_PRICE_ACTIVITY",
    "appointment_intent": "SF9_APPOINTMENT",
    "trust_issue": "SF10_TRUST_BUILD",
    "after_sales": "SF12_AFTER_SALES",
    "complaint_refund": "SF12_AFTER_SALES",
    "human_request": "HUMAN_HANDOFF",
}

QUESTION_KEYS = (
    "question",
    "user_question",
    "content",
    "用户问题",
)


SMOKE_CASES = [
    {"case_id": "price_hidden_fee_001", "family": "SF7_PRICE_ACTIVITY", "question": "到店会乱收费吗", "expected_scene_id": "SF7_HIDDEN_FEE_WORRY"},
    {"case_id": "price_repeat_001", "family": "SF7_PRICE_ACTIVITY", "question": "你还是没告诉我价格", "expected_scene_id": "SF7_PRICE_REPEAT_ASK"},
    {"case_id": "price_deposit_001", "family": "SF7_PRICE_ACTIVITY", "question": "为什么要交10元定金", "expected_scene_id": "SF7_DEPOSIT_EXPLAIN"},
    {"case_id": "price_ad_58_001", "family": "SF7_PRICE_ACTIVITY", "question": "看广告58元是真的吗", "expected_scene_id": "SF7_PRICE_AD_58"},
    {"case_id": "price_mole_001", "family": "SF7_PRICE_ACTIVITY", "question": "可以去痣吗 去痣要多少钱", "expected_scene_id": "SF7_MOLE_PRICE_INQUIRY"},
    {"case_id": "price_lowest_001", "family": "SF7_PRICE_ACTIVITY", "question": "最低价多少，能不能再便宜", "expected_scene_id": "SF7_LOWEST_PRICE_HANDOFF"},
    {"case_id": "store_location_001", "family": "SF6_STORE_INQUIRY", "question": "厦门附近有门店吗", "expected_scene_id": "SF6_STORE_LOCATION"},
    {"case_id": "store_doubt_001", "family": "SF6_STORE_INQUIRY", "question": "为什么不敢发详细地址", "expected_scene_id": "SF6_STORE_ADDRESS_DOUBT"},
    {"case_id": "store_prepare_001", "family": "SF6_STORE_INQUIRY", "question": "到店要带身份证吗", "expected_scene_id": "SF6_PRE_VISIT_ID_CARD"},
    {"case_id": "store_name_001", "family": "SF6_STORE_INQUIRY", "question": "你们门店名字叫什么", "expected_scene_id": "SF6_STORE_NAME_INQUIRY"},
    {"case_id": "appointment_weekend_001", "family": "SF9_APPOINTMENT", "question": "周六下午能约吗", "expected_scene_id": "SF9_WEEKEND_AVAILABLE"},
    {"case_id": "trust_qualification_001", "family": "SF10_TRUST_BUILD", "question": "你们有资质吗", "expected_scene_id": "SF10_QUALIFICATION_INQUIRY"},
    {"case_id": "trust_safety_001", "family": "SF10_TRUST_BUILD", "question": "会伤害皮肤吗", "expected_scene_id": "SF10_SAFETY_WORRY"},
    {"case_id": "trust_identity_001", "family": "SF10_TRUST_BUILD", "question": "你是门店的人吗", "expected_scene_id": "SF10_IDENTITY_WORRY"},
    {"case_id": "human_real_person_001", "family": "HUMAN_HANDOFF", "question": "我要跟真人说话", "expected_scene_id": "HUMAN_REQUEST_REAL_PERSON"},
    {"case_id": "after_sales_refund_001", "family": "SF12_AFTER_SALES", "question": "把定金退给我，不然我投诉", "expected_scene_id": "SF12_REFUND_REQUEST_HANDOFF"},
    {"case_id": "competitor_cheaper_001", "family": "SF5_COMPETITOR_COMPARE", "question": "别家299你们能同价吗", "expected_scene_id": "SF5_COMPETITOR_CHEAPER"},
    {"case_id": "competitor_screenshot_001", "family": "SF5_COMPETITOR_COMPARE", "question": "我发你一张别家报价截图，你帮我看看", "expected_scene_id": "SF5_COMPETITOR_SCREENSHOT"},
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scene guidance shadow retrieval evaluation.")
    parser.add_argument("--input", default="", help="Optional input file. Supports .jsonl, .csv, or plain text.")
    parser.add_argument("--output", default="workflow_tests/reports/scene_guidance_shadow_eval.csv", help="CSV output path.")
    parser.add_argument("--summary-output", default="", help="Optional summary CSV output path.")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    cases = load_cases(Path(args.input)) if args.input else list(SMOKE_CASES)
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        family = family_for_case(case)
        question = question_for_case(case)
        expected_scene_id = str(case.get("expected_scene_id") or "").strip()
        candidates = retrieve_scene_guidance(family=family, user_message=question, top_k=args.top_k)
        top_scene_id = str(candidates[0].get("scene_id") if candidates else "")
        rows.append(
            {
                "case_id": str(case.get("case_id") or f"case_{index:03d}"),
                "source_flow": source_flow_for_case(case),
                "family": family,
                "question": question,
                "expected_scene_id": expected_scene_id,
                "top_scene_id": top_scene_id,
                "top_score": candidates[0].get("score", "") if candidates else "",
                "top_match_level": candidates[0].get("match_level", "") if candidates else "",
                "top_status": candidates[0].get("status", "") if candidates else "",
                "top_reply_goal": candidates[0].get("reply_goal", "") if candidates else "",
                "candidate_scene_ids": "|".join(str(item.get("scene_id") or "") for item in candidates),
                "hit": "1" if expected_scene_id and expected_scene_id == top_scene_id else "0",
            }
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output, rows)
    summary_rows = summarize(rows)
    if args.summary_output:
        summary_output = Path(args.summary_output)
    else:
        summary_output = output.with_name(output.stem + "_summary.csv")
    write_summary_csv(summary_output, summary_rows)

    expected_rows = [row for row in rows if row["expected_scene_id"]]
    hit_count = sum(1 for row in expected_rows if row["hit"] == "1")
    family_rows = [row for row in rows if row["family"]]
    covered_rows = [row for row in family_rows if row["top_scene_id"]]
    print(f"cases={len(rows)} expected={len(expected_rows)} top1_hits={hit_count}")
    if expected_rows:
        print(f"top1_accuracy={hit_count / len(expected_rows):.2%}")
    if family_rows:
        print(f"family_cases={len(family_rows)} covered={len(covered_rows)} coverage={len(covered_rows) / len(family_rows):.2%}")
    print(f"output={output}")
    print(f"summary_output={summary_output}")
    return 0


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
            return [item for item in raw["cases"] if isinstance(item, dict)]
        raise ValueError(f"Unsupported json shape: {path}")
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    cases: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        question = line.strip()
        if question:
            cases.append({"case_id": f"line_{index:03d}", "family": "", "question": question})
    return cases


def family_for_case(case: dict[str, Any]) -> str:
    direct = str(case.get("family") or "").strip()
    if direct:
        return direct
    for key in ("target_flow", "subflow", "should_route"):
        value = str(case.get(key) or "").strip()
        if value in FAMILY_BY_FLOW:
            return FAMILY_BY_FLOW[value]
    expected = case.get("expected")
    if isinstance(expected, dict):
        route = str(expected.get("should_route") or "").strip()
        if route in FAMILY_BY_FLOW:
            return FAMILY_BY_FLOW[route]
        intent = str(expected.get("intent") or "").strip()
        if intent in FAMILY_BY_INTENT:
            return FAMILY_BY_INTENT[intent]
    intent = str(case.get("intent") or "").strip()
    return FAMILY_BY_INTENT.get(intent, "")


def source_flow_for_case(case: dict[str, Any]) -> str:
    for key in ("target_flow", "subflow", "intent"):
        value = str(case.get(key) or "").strip()
        if value:
            return value
    expected = case.get("expected")
    if isinstance(expected, dict):
        return str(expected.get("should_route") or expected.get("intent") or "").strip()
    return ""


def question_for_case(case: dict[str, Any]) -> str:
    for key in QUESTION_KEYS:
        value = str(case.get(key) or "").strip()
        if value:
            return value
    return ""


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        family = str(row.get("family") or "UNMAPPED")
        buckets.setdefault(family, []).append(row)
    summary: list[dict[str, Any]] = []
    for family, items in sorted(buckets.items()):
        covered = [row for row in items if row.get("top_scene_id")]
        high = [row for row in covered if str(row.get("top_match_level") or "") == "high"]
        expected = [row for row in items if row.get("expected_scene_id")]
        hits = [row for row in expected if row.get("hit") == "1"]
        top_counts: dict[str, int] = {}
        for row in covered:
            scene_id = str(row.get("top_scene_id") or "")
            if scene_id:
                top_counts[scene_id] = top_counts.get(scene_id, 0) + 1
        summary.append(
            {
                "family": family,
                "cases": len(items),
                "covered": len(covered),
                "coverage": _rate(len(covered), len(items)),
                "high_matches": len(high),
                "high_rate": _rate(len(high), len(items)),
                "expected": len(expected),
                "top1_hits": len(hits),
                "top1_accuracy": _rate(len(hits), len(expected)) if expected else "",
                "top_scenes": "|".join(f"{scene_id}:{count}" for scene_id, count in sorted(top_counts.items(), key=lambda item: item[1], reverse=True)[:8]),
            }
        )
    return summary


def _rate(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return ""
    return f"{numerator / denominator:.2%}"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "case_id",
        "source_flow",
        "family",
        "question",
        "expected_scene_id",
        "top_scene_id",
        "top_score",
        "top_match_level",
        "top_status",
        "top_reply_goal",
        "candidate_scene_ids",
        "hit",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "family",
        "cases",
        "covered",
        "coverage",
        "high_matches",
        "high_rate",
        "expected",
        "top1_hits",
        "top1_accuracy",
        "top_scenes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
