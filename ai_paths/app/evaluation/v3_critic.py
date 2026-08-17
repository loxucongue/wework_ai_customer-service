from __future__ import annotations

import json
from typing import Any

from app.services.model_client import ModelClient


CRITIC_SYSTEM_PROMPT = """You are an offline evaluation critic for a Chinese sales reply system.
Evaluate the generated reply against the supplied reviewed case contract and evidence.
This is evaluation only. Do not write a replacement reply, do not choose a new sales action,
and do not infer facts that are absent. Reference examples are intentionally excluded.
Return valid json only with this schema:
{
  "status": "pass|fail",
  "scores": {
    "current_question": 1,
    "history_continuity": 1,
    "natural_advance": 1,
    "evidence_relevance": 1,
    "human_tone": 1,
    "fact_safety": 1
  },
  "failure_owner": "none|gate|reply|delivery|facts",
  "violations": [{"code": "", "quote": "", "reason": ""}],
  "reason": ""
}
Pass requires every must-answer point and required delivery, no forbidden action or claim,
no hard factual error, and scores of at least 4 for all dimensions required by the case.
Use empty quote when the problem is an omission rather than text that was said."""


async def evaluate_with_critic(
    model_client: ModelClient,
    *,
    case: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    annotation = case.get("annotation") if isinstance(case.get("annotation"), dict) else {}
    case_input = case.get("input") if isinstance(case.get("input"), dict) else {}
    payload = {
        "case_id": case.get("case_id"),
        "conversation": case_input.get("conversation") or [],
        "current_message": case_input.get("current_message") or {},
        "authoritative_facts": case_input.get("authoritative_facts") or {},
        "tool_facts": case_input.get("tool_facts") or {},
        "case_contract": {
            "customer_goal": annotation.get("customer_goal"),
            "must_answer_points": annotation.get("must_answer_points") or [],
            "acceptable_postures": annotation.get("acceptable_postures") or [],
            "required_gate_asset_ids": annotation.get("required_gate_asset_ids") or [],
            "acceptable_gate_asset_ids": annotation.get("acceptable_gate_asset_ids") or [],
            "required_deliveries": annotation.get("required_deliveries") or [],
            "forbidden_actions": annotation.get("forbidden_actions") or [],
            "forbidden_claims": annotation.get("forbidden_claims") or [],
            "quality_expectations": annotation.get("quality_expectations") or {},
            "reference_reply_direction": annotation.get("reference_reply_direction") or "",
        },
        "generated": {
            "reply_messages": result.get("reply_messages") or [],
            "content_selection_metrics": result.get("content_selection_metrics") or {},
            "content_decisions": result.get("content_decisions") or [],
            "hard_errors": result.get("hard_errors") or [],
        },
    }
    raw = await model_client.chat_json(
        [
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        tier="balanced",
        temperature=0.0,
    )
    return validate_critic_result(raw)


def validate_critic_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("critic result must be an object")
    status = str(value.get("status") or "").lower()
    if status not in {"pass", "fail"}:
        raise ValueError("critic status must be pass or fail")
    scores = value.get("scores") if isinstance(value.get("scores"), dict) else {}
    required_scores = (
        "current_question",
        "history_continuity",
        "natural_advance",
        "evidence_relevance",
        "human_tone",
        "fact_safety",
    )
    normalized_scores: dict[str, int] = {}
    for key in required_scores:
        score = int(scores.get(key) or 0)
        if score < 1 or score > 5:
            raise ValueError(f"critic score out of range: {key}")
        normalized_scores[key] = score
    owner = str(value.get("failure_owner") or "none").lower()
    if owner not in {"none", "gate", "reply", "delivery", "facts"}:
        raise ValueError("invalid critic failure_owner")
    violations = []
    for item in value.get("violations") or []:
        if isinstance(item, dict):
            violations.append(
                {
                    "code": str(item.get("code") or "")[:100],
                    "quote": str(item.get("quote") or "")[:500],
                    "reason": str(item.get("reason") or "")[:1000],
                }
            )
    return {
        "status": status,
        "scores": normalized_scores,
        "failure_owner": owner,
        "violations": violations,
        "reason": str(value.get("reason") or "")[:1500],
    }
