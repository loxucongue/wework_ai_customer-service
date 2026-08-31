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
    "current_question": 5,
    "history_continuity": 5,
    "natural_advance": 5,
    "evidence_relevance": 5,
    "human_tone": 5,
    "fact_safety": 5,
    "sales_humanness": 5,
    "confidence_building": 5,
    "value_reframing": 5
  },
  "failure_owner": "none|knowledge|reply|delivery|facts",
  "violations": [{"code": "", "quote": "", "reason": ""}],
  "reason": ""
}
Every score is an integer from 1 (worst) to 5 (best). Pass requires every must-answer point and
required delivery, no forbidden action or claim, no hard factual error, and scores of at least 4
for all dimensions required by the case. Never return pass with a required score below 4.
Check every must_answer_points item explicitly; omitting any item is a reply failure even when the
rest of the response is safe. Enforce quality_expectations as written. When
introduces_new_concern=false, fail a reply that volunteers a new downside, risk, restriction, pain,
rebound, or adverse reaction that the customer did not raise and that was not needed to answer.
When the customer is currently unavailable, extra campaign, quota, registration, or payment talk
is not a valid pause. After reversible hesitation, asking for registration or payment without first
adding relevant value or resolving the known friction is not natural_advance. A useful low-friction
question may replace evidence only when the missing answer would genuinely change the next action.
In a complaint or fulfillment dispute, asking whether the customer wants to continue the project is
marketing and fails the pause boundary. Claims that a quota, registration, booking, arrangement, or
asset has already been completed require authoritative completion evidence or actual delivery.
Judge must-answer coverage by meaning, not exact wording. A direct semantically equivalent statement
or arithmetic explanation satisfies the point; do not require the reviewed label to be repeated.
Treat published sales language as approved expression, not as a hard-fact claim. Natural praise,
humor, empathy, qualitative social proof (for example, "不少外地客户也会专程过来"),
general customer experience, and value analogies must not reduce fact_safety merely because they
lack an authoritative citation. They may still be judged for relevance and sales quality.
They need no authoritative citation unless the case contract explicitly forbids that expression.
Hard facts are price, payment/refund, store/location, campaign rights, gifts, staff/date,
booking/payment completion, individual effect guarantees, and individual safety guarantees.
Fail invented precise people, counts, cities, travel time, individual outcomes, fake scarcity,
or any expression that changes customer rights or contradicts hard facts. A historical business
statement must not be misread as a guarantee that this individual customer will have the same result.
failure_owner=delivery is valid only when delivery_audit.missing_required_ids or
delivery_audit.adopted_not_delivered_ids is non-empty. When required_ids is empty, never invent a
requirement to send a payment card, image, store card, or other asset. A missing answer point belongs
to reply; an unsupported or contradicted factual claim belongs to facts.
Use empty quote when the problem is an omission rather than text that was said."""


class CriticContractError(ValueError):
    """The critic returned a structurally impossible ownership decision."""


async def evaluate_with_critic(
    model_client: ModelClient,
    *,
    case: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    annotation = case.get("annotation") if isinstance(case.get("annotation"), dict) else {}
    case_input = case.get("input") if isinstance(case.get("input"), dict) else {}
    delivery_audit = build_delivery_audit(case=case, result=result)
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
            "knowledge_use": result.get("knowledge_use") or {},
            "hard_errors": result.get("hard_errors") or [],
        },
        "delivery_audit": delivery_audit,
    }
    messages = [
        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    first_contract_error = ""
    for attempt in range(2):
        current_messages = list(messages)
        if attempt:
            current_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous failure_owner violated the supplied delivery_audit. "
                        "Re-evaluate the same generated reply without changing its sales meaning. "
                        "Use failure_owner=delivery only when missing_required_ids or "
                        "adopted_not_delivered_ids is non-empty. Ordinary answer omissions belong "
                        "to reply; hard factual conflicts belong to facts. Return the full JSON again."
                    ),
                }
            )
        raw = await model_client.chat_json(
            current_messages,
            tier="balanced",
            temperature=0.0,
        )
        try:
            normalized = validate_critic_result(raw, delivery_audit=delivery_audit)
        except CriticContractError as exc:
            first_contract_error = first_contract_error or str(exc)
            if attempt == 0:
                continue
            raise CriticContractError(f"critic_contract_invalid_after_retry:{exc}") from exc
        normalized["delivery_audit"] = delivery_audit
        normalized["critic_contract_invalid"] = bool(first_contract_error)
        normalized["contract_retry_count"] = attempt
        if first_contract_error:
            normalized["initial_contract_error"] = first_contract_error[:500]
        return normalized
    raise CriticContractError("critic_contract_invalid_after_retry")


def validate_critic_result(
    value: Any,
    *,
    delivery_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "sales_humanness",
        "confidence_building",
        "value_reframing",
    )
    normalized_scores: dict[str, int] = {}
    for key in required_scores:
        score = int(scores.get(key) or 0)
        if score < 1 or score > 5:
            raise ValueError(f"critic score out of range: {key}")
        normalized_scores[key] = score
    if status == "pass" and any(score < 4 for score in normalized_scores.values()):
        raise ValueError("critic pass result contains a score below 4")
    owner = str(value.get("failure_owner") or "none").lower()
    if owner not in {"none", "knowledge", "reply", "delivery", "facts"}:
        raise ValueError("invalid critic failure_owner")
    if owner == "delivery" and isinstance(delivery_audit, dict):
        missing_required = delivery_audit.get("missing_required_ids") or []
        adopted_not_delivered = delivery_audit.get("adopted_not_delivered_ids") or []
        if not missing_required and not adopted_not_delivered:
            raise CriticContractError("delivery_owner_without_structural_delivery_gap")
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


def build_delivery_audit(
    *,
    case: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, list[str]]:
    """Describe objective delivery state without judging sales semantics."""

    annotation = case.get("annotation") if isinstance(case.get("annotation"), dict) else {}
    metrics = (
        result.get("content_selection_metrics")
        if isinstance(result.get("content_selection_metrics"), dict)
        else {}
    )
    required_ids = _string_ids(annotation.get("required_deliveries"))
    adopted_ids = _string_ids(metrics.get("adopted_ids"))
    delivered_ids = _string_ids(metrics.get("delivered_ids"))
    reply_messages = [
        item for item in result.get("reply_messages") or [] if isinstance(item, dict)
    ]
    message_types = {
        str(item.get("type") or "").strip()
        for item in reply_messages
        if str(item.get("type") or "").strip()
    }
    delivered_ids.update(message_types.intersection({"payment_collection", "store_address"}))
    for message in reply_messages:
        if str(message.get("type") or "").strip() != "payment_collection":
            continue
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        amount = content.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0:
            normalized_amount = int(amount) if float(amount).is_integer() else amount
            delivered_ids.add(f"payment_collection:{normalized_amount}")
    if "s10_activity_intro" in delivered_ids:
        delivered_ids.add("activity_image")
    if "s10_need_and_case" in delivered_ids or any(
        item.startswith("configured_effect_case_") for item in delivered_ids
    ):
        delivered_ids.add("case_image")
    return {
        "required_ids": sorted(required_ids),
        "adopted_ids": sorted(adopted_ids),
        "delivered_ids": sorted(delivered_ids),
        "missing_required_ids": sorted(required_ids - delivered_ids),
        "adopted_not_delivered_ids": sorted(adopted_ids - delivered_ids),
    }


def _string_ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}
