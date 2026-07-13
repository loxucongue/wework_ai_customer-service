from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


PAID_DEPOSIT_STATES = {"paid_by_order", "paid_by_screenshot"}


def normalize_prepay_facts(order: dict[str, Any]) -> dict[str, Any]:
    """Normalize current and legacy platform prepayment fields without inferring intent."""
    required_raw = order.get("prepay_required")
    if required_raw in (None, ""):
        required_raw = order.get("fee_required")
    paid_raw = order.get("prepay_paid")
    if paid_raw in (None, ""):
        paid_raw = order.get("fee_paid")

    required = _positive_or_true(required_raw)
    paid = _positive_or_true(paid_raw)
    return {
        "prepay_required": required_raw,
        "prepay_paid": paid_raw,
        "deposit_state": "paid_by_order" if paid else ("required_unpaid" if required else "unknown"),
        "deposit_source": "platform_agent.order_index",
    }


def payment_fact_from_image(image_info: Any) -> dict[str, Any]:
    if not isinstance(image_info, dict) or image_info.get("image_type") != "payment_proof":
        return {}
    result = str(image_info.get("payment_result") or "unclear").strip().lower()
    if result not in {"success", "pending", "failed", "unclear"}:
        result = "unclear"
    output: dict[str, Any] = {
        "payment_result": result,
        "amount": image_info.get("payment_amount"),
        "order_no_hint": str(image_info.get("payment_order_no") or "")[:80],
        "confidence": image_info.get("confidence", 0),
        "source": "vision.payment_proof",
    }
    if result == "success":
        output["deposit_state"] = "paid_by_screenshot"
    return _drop_empty(output)


def resolved_payment_fact(
    *,
    orders: Any,
    image_info: Any = None,
    existing_state: str = "",
    existing_source: str = "",
    existing_fact: Any = None,
) -> dict[str, Any]:
    """Resolve payment facts with monotonic paid-state precedence."""
    order_facts = []
    for order in orders if isinstance(orders, list) else []:
        if not isinstance(order, dict):
            continue
        state = str(order.get("deposit_state") or "").strip()
        if not state:
            state = str(normalize_prepay_facts(order).get("deposit_state") or "unknown")
        order_facts.append(
            {
                "order_id": str(order.get("id") or order.get("order_id") or ""),
                "order_no": str(order.get("order_no") or ""),
                "store_id": str(order.get("store_id") or ""),
                "store_name": str(order.get("store_name") or ""),
                "status": str(order.get("status") or ""),
                "deposit_state": state,
                "source": "platform_agent.order_index",
                "prepay_required": order.get("prepay_required"),
                "prepay_paid": order.get("prepay_paid"),
            }
        )

    paid_order = next((item for item in order_facts if item["deposit_state"] == "paid_by_order"), None)
    image_fact = payment_fact_from_image(image_info)
    existing_paid = existing_state in PAID_DEPOSIT_STATES or existing_state == "deposit_paid"

    if paid_order:
        selected = dict(paid_order)
    elif image_fact.get("deposit_state") == "paid_by_screenshot":
        selected = dict(image_fact)
        related_order = next((item for item in order_facts if item.get("order_id")), None)
        if related_order:
            selected.update({key: related_order.get(key) for key in ("order_id", "order_no", "store_id", "store_name")})
    elif existing_paid:
        selected = {
            **(existing_fact if isinstance(existing_fact, dict) else {}),
            "deposit_state": existing_state,
            "source": existing_source or "customer_memory",
        }
    else:
        selected = next((dict(item) for item in order_facts if item["deposit_state"] == "required_unpaid"), {})

    if not selected:
        return {}
    selected["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _drop_empty(selected)


def is_paid_deposit_state(value: Any) -> bool:
    return str(value or "").strip() in {*PAID_DEPOSIT_STATES, "deposit_paid"}


def _positive_or_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return str(value or "").strip().lower() in {
            "true",
            "yes",
            "paid",
            "success",
            "已支付",
            "已支付预约金",
            "需支付预约金",
        }


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
