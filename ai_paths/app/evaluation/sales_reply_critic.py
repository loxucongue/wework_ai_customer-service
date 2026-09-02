from __future__ import annotations

import json
from typing import Any

from app.services.model_client import ModelClient


SYSTEM_PROMPT = """You are an offline evaluator for a Chinese medical-aesthetics sales reply system.
Evaluate only the supplied generated result. Do not write a replacement reply and do not invent facts.
The source case has been human reviewed. Judge semantics, not keyword overlap or exact wording.

Focus on three capabilities:
1. cardpoint: If the customer has an obstacle, did the system identify the right obstacle, retrieve a
relevant follow-up sequence and useful script direction, and naturally use that direction in the reply?
If there is no obstacle, cardpoint.applicable=false and it must not be penalized.
2. closing: Did the system apply or withhold closing pressure at the right moment? Closing is appropriate
only after the current question or obstacle is sufficiently handled and the customer shows a transaction,
appointment, store, payment or explicit continuation signal. Complaint, explicit exit, health risk,
unresolved obstacle or simple acknowledgement must not be pushed. A low-pressure next question may be valid.
3. priority: Detect whether multiple material issues/signals coexist. Risk, complaint, explicit exit and
transaction facts outrank normal sales progression; then answer the customer's direct question; then resolve
the active obstacle; then perform at most one sales advance. The reply must cover the primary issue and must
not silently discard another material issue.

Return JSON only:
{
  "cardpoint": {"applicable": true, "pass": true, "score": 1, "expected": "", "actual": "", "reason": ""},
  "closing": {"applicable": true, "pass": true, "score": 1, "expected": "advance|soft_advance|hold|stop", "actual": "", "reason": ""},
  "priority": {"applicable": true, "multi_issue": true, "pass": true, "score": 1, "expected_order": [], "actual_order": [], "reason": ""},
  "fact_safe": true,
  "human_review_required": false,
  "summary": ""
}
Every score is an integer 1..5. pass=true requires score>=4. When applicable=false, still use score=5
and pass=true. Mark human_review_required for ambiguous business judgment or insufficient reviewed contract.
The DeepSeek critic is diagnostic only; it cannot modify or block runtime output."""


async def evaluate_sales_reply(
    model_client: ModelClient,
    *,
    case: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    case_input = case.get("input") if isinstance(case.get("input"), dict) else {}
    annotation = case.get("annotation") if isinstance(case.get("annotation"), dict) else {}
    payload = {
        "case_id": case.get("case_id"),
        "history": case_input.get("conversation") or [],
        "current_message": case_input.get("current_message") or {},
        "authoritative_facts": case_input.get("authoritative_facts") or {},
        "tool_facts": case_input.get("tool_facts") or {},
        "reviewed_contract": {
            "customer_goal": annotation.get("customer_goal"),
            "must_answer_points": annotation.get("must_answer_points") or [],
            "acceptable_postures": annotation.get("acceptable_postures") or [],
            "forbidden_actions": annotation.get("forbidden_actions") or [],
            "forbidden_claims": annotation.get("forbidden_claims") or [],
            "reference_reply_direction": annotation.get("reference_reply_direction") or "",
        },
        "generated": {
            "reply_messages": result.get("reply_messages") or [],
            "sales_policy": result.get("sales_policy") or {},
            "hard_errors": result.get("hard_errors") or [],
        },
    }
    raw = await model_client.chat_json(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        tier="balanced",
        temperature=0.0,
    )
    return validate_sales_reply_evaluation(raw)


def validate_sales_reply_evaluation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("sales reply evaluation must be an object")
    normalized: dict[str, Any] = {}
    for name in ("cardpoint", "closing", "priority"):
        item = value.get(name) if isinstance(value.get(name), dict) else {}
        score = int(item.get("score") or 0)
        if score < 1 or score > 5:
            raise ValueError(f"invalid {name} score")
        passed = bool(item.get("pass"))
        if passed != (score >= 4):
            raise ValueError(f"{name} pass must equal score>=4")
        normalized[name] = {
            "applicable": bool(item.get("applicable")),
            "multi_issue": bool(item.get("multi_issue")) if name == "priority" else False,
            "pass": passed,
            "score": score,
            "expected": str(item.get("expected") or "")[:500],
            "actual": str(item.get("actual") or "")[:500],
            "expected_order": _strings(item.get("expected_order"))[:8],
            "actual_order": _strings(item.get("actual_order"))[:8],
            "reason": str(item.get("reason") or "")[:1200],
        }
    normalized["fact_safe"] = bool(value.get("fact_safe"))
    normalized["human_review_required"] = bool(value.get("human_review_required"))
    normalized["summary"] = str(value.get("summary") or "")[:1500]
    return normalized


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:300] for item in value if str(item).strip()]
