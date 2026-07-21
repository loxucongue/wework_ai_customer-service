from __future__ import annotations

from typing import Any


def authoritative_paid_context(state: dict[str, Any], turn_context: dict[str, Any]) -> bool:
    """Use structured order or successful payment-proof facts, never text intent."""
    if str(turn_context.get("deposit_state") or "") == "deposit_paid":
        return True
    turn_evidence = turn_context.get("turn_evidence") if isinstance(turn_context.get("turn_evidence"), dict) else {}
    payment_evidence = (
        turn_evidence.get("payment_evidence") if isinstance(turn_evidence.get("payment_evidence"), dict) else {}
    )
    if str(payment_evidence.get("structured_payment_state") or "") == "deposit_paid":
        return True
    structured = (
        payment_evidence.get("structured_payment_fact")
        if isinstance(payment_evidence.get("structured_payment_fact"), dict)
        else {}
    )
    if str(structured.get("deposit_state") or "") in {"paid_by_order", "paid_by_screenshot"}:
        return True
    image = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    return image.get("image_type") == "payment_proof" and image.get("payment_result") == "success"


def current_unpaid_order(state: dict[str, Any]) -> bool:
    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = [order for order in context.get("orders") or [] if isinstance(order, dict)]
    current = [order for order in orders if order.get("is_current_order")]
    return any(str(order.get("deposit_state") or "") == "required_unpaid" for order in (current or orders[:1]))


def postpaid_scheduling_tool_violations(
    *,
    payment_decision: dict[str, Any],
    required_tools: list[dict[str, Any]],
    has_authoritative_paid: bool,
) -> list[dict[str, str]]:
    if str(payment_decision.get("action") or "") != "after_paid_next_step" or not has_authoritative_paid:
        return []
    violations: list[dict[str, str]] = []
    for tool in required_tools:
        name = str(tool.get("name") or "") if isinstance(tool, dict) else ""
        if name not in {"available_time", "create_order_plan"}:
            continue
        violations.append(
            {
                "task_type": "tool_argument",
                "subtype": name,
                "missing": f"{name}_disabled_after_payment",
                "note": (
                    "After payment, collect and confirm name, phone, store, visit date and time only; "
                    "do not query slots or create a formal schedule."
                ),
            }
        )
    return violations
