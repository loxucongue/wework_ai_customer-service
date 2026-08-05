from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timezone
from typing import Any


PAID_DEPOSIT_STATES = {"paid_by_order", "paid_by_screenshot", "paid_by_platform_transfer_event"}
ACTIVE_ORDER_STATUSES = {"1", "2", "3", "pending", "waiting_schedule", "scheduled"}
COMPLETED_ORDER_STATUSES = {"finished", "completed", "done", "closed", "complete", "已完成"}
INACTIVE_ORDER_STATUSES = {
    "0",
    "4",
    "8",
    "lost",
    "lost_refunded",
    "refund",
    "refunded",
    "cancelled",
    "canceled",
    "timeout",
    "failed",
    "void",
    "invalid",
    "已退款",
    "已取消",
    "流失退款",
    "预约超时",
}
PAID_ORDER_PROTECTION_MONTHS = 3
UNPAID_ORDER_CURRENT_MONTHS = 3


def normalize_prepay_facts(order: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Normalize current and legacy platform prepayment fields without inferring intent."""
    required_raw = order.get("prepay_required")
    if required_raw in (None, ""):
        required_raw = order.get("fee_required")
    paid_raw = order.get("prepay_paid")
    if paid_raw in (None, ""):
        paid_raw = order.get("fee_paid")

    required = _positive_number(required_raw)
    paid = _positive_number(paid_raw)
    needs_binding = paid and _order_binding_missing(order, required=required)
    inactive = is_inactive_order(order)
    completed = is_completed_order(order)
    protection = paid_order_protection_fact(order, now=now) if paid else unpaid_order_recency_fact(order, now=now)
    deposit_state = "paid_by_order" if paid else ("required_unpaid" if required else "unknown")
    if paid and inactive:
        deposit_state = "historical_paid_inactive"
    elif paid and completed:
        deposit_state = "historical_paid_completed"
    elif protection.get("paid_protection_status") == "expired":
        deposit_state = "historical_paid_expired"
    elif required and protection.get("order_recency_status") == "historical_unpaid_expired":
        deposit_state = "historical_unpaid_expired"
    return {
        "prepay_required": required_raw,
        "prepay_paid": paid_raw,
        "deposit_state": deposit_state,
        "deposit_source": "platform_agent.order_index",
        "order_binding_state": "needs_binding" if needs_binding else ("bound" if paid or required else "unknown"),
        **protection,
    }


def unpaid_order_recency_fact(order: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Classify an active unpaid order without turning old contact history into a current booking."""
    if _positive_number(order.get("prepay_paid") if order.get("prepay_paid") not in (None, "") else order.get("fee_paid")):
        return {}
    if not _positive_number(order.get("prepay_required") if order.get("prepay_required") not in (None, "") else order.get("fee_required")):
        return {}
    if is_inactive_order(order) or is_completed_order(order):
        return {
            "order_recency_status": "historical_unpaid_inactive",
            "order_time_source": "order_status",
            "order_time_value": _order_status_value(order),
        }
    raw_created_at = order_created_at_value(order)
    created_at = _parse_datetime(raw_created_at)
    if created_at is None:
        return {
            "order_recency_status": "unknown_time_current",
            "order_time_source": "order_created_at",
            "order_time_value": raw_created_at,
        }
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = _subtract_months(current.astimezone(timezone.utc), UNPAID_ORDER_CURRENT_MONTHS)
    return {
        "order_recency_status": (
            "historical_unpaid_expired" if created_at < cutoff else "current_unpaid"
        ),
        "order_time_source": "order_created_at",
        "order_time_value": created_at.isoformat(),
        "order_recency_cutoff": cutoff.isoformat(),
    }


def paid_order_protection_fact(order: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Classify a paid order using order creation time as the agreed temporary proxy."""
    if not _positive_number(order.get("prepay_paid") if order.get("prepay_paid") not in (None, "") else order.get("fee_paid")):
        return {}
    if is_inactive_order(order):
        return {
            "paid_protection_status": "inactive_order_expired",
            "paid_time_source": "order_status",
            "paid_time_value": _order_status_value(order),
        }
    if is_completed_order(order):
        return {
            "paid_protection_status": "completed_order_expired",
            "paid_time_source": "order_status",
            "paid_time_value": _order_status_value(order),
        }
    raw_created_at = order_created_at_value(order)
    created_at = _parse_datetime(raw_created_at)
    if created_at is None:
        return {
            "paid_protection_status": "unknown_time_protected",
            "paid_time_source": "order_created_at_proxy",
            "paid_time_value": raw_created_at,
        }
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = _subtract_months(current.astimezone(timezone.utc), PAID_ORDER_PROTECTION_MONTHS)
    expired = created_at < cutoff
    return {
        "paid_protection_status": "expired" if expired else "protected",
        "paid_time_source": "order_created_at_proxy",
        "paid_time_value": created_at.isoformat(),
        "paid_protection_cutoff": cutoff.isoformat(),
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
        "source": str(image_info.get("source") or "vision.payment_proof"),
    }
    if result == "success":
        output["deposit_state"] = (
            "paid_by_platform_transfer_event"
            if output["source"] == "platform.unknown_message_transfer"
            else "paid_by_screenshot"
        )
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
        normalized_payment = normalize_prepay_facts(order)
        state = str(order.get("deposit_state") or "").strip()
        if state in PAID_DEPOSIT_STATES and (is_inactive_order(order) or is_completed_order(order)):
            state = str(normalized_payment.get("deposit_state") or "historical_paid_completed")
        if not state:
            state = str(normalized_payment.get("deposit_state") or "unknown")
        order_facts.append(
            {
                "order_id": str(order.get("id") or order.get("order_id") or ""),
                "order_no": str(order.get("order_no") or ""),
                "store_id": str(order.get("store_id") or ""),
                "store_name": str(order.get("store_name") or ""),
                "category_id": str(order.get("category_id") or ""),
                "status": str(order.get("status") or ""),
                "deposit_state": state,
                "source": "platform_agent.order_index",
                "prepay_required": order.get("prepay_required"),
                "prepay_paid": order.get("prepay_paid"),
                "order_binding_state": order.get("order_binding_state")
                or normalized_payment.get("order_binding_state"),
                "paid_protection_status": order.get("paid_protection_status")
                or normalized_payment.get("paid_protection_status"),
                "paid_time_source": order.get("paid_time_source")
                or normalized_payment.get("paid_time_source"),
                "paid_time_value": order.get("paid_time_value")
                or normalized_payment.get("paid_time_value"),
                "is_current_order": bool(order.get("is_current_order")),
            }
        )

    paid_order = next(
        (
            item
            for item in order_facts
            if item["deposit_state"] == "paid_by_order"
            and item.get("paid_protection_status") not in {"expired", "inactive_order_expired", "completed_order_expired"}
        ),
        None,
    )
    current_order = next((item for item in order_facts if item.get("is_current_order")), None)
    current_flow_facts = [current_order] if current_order else order_facts[:1]
    image_fact = payment_fact_from_image(image_info)
    existing_screenshot_paid = existing_state == "paid_by_screenshot" or (
        existing_state == "deposit_paid" and existing_source == "vision.payment_proof"
    )

    if paid_order:
        selected = dict(paid_order)
    elif image_fact.get("deposit_state") in PAID_DEPOSIT_STATES:
        selected = dict(image_fact)
        related_order = next((item for item in current_flow_facts if item.get("order_id")), None)
        if related_order:
            selected.update({key: related_order.get(key) for key in ("order_id", "order_no", "store_id", "store_name")})
    elif existing_screenshot_paid or existing_state == "paid_by_platform_transfer_event":
        selected = {
            **(existing_fact if isinstance(existing_fact, dict) else {}),
            "deposit_state": (
                "paid_by_platform_transfer_event"
                if existing_state == "paid_by_platform_transfer_event"
                else "paid_by_screenshot"
            ),
            "source": existing_source or "customer_memory",
        }
    else:
        selected = next((dict(item) for item in current_flow_facts if item["deposit_state"] == "required_unpaid"), {})

    if not selected:
        return {}
    selected["updated_at"] = datetime.now(timezone.utc).isoformat()
    return _drop_empty(selected)


def is_paid_deposit_state(value: Any) -> bool:
    return str(value or "").strip() in {*PAID_DEPOSIT_STATES, "deposit_paid"}


def is_completed_order(order: dict[str, Any]) -> bool:
    """Return whether an order is a finished historical service, not a current deposit hold."""
    status = _order_status_value(order).strip().lower()
    return status in COMPLETED_ORDER_STATUSES


def is_inactive_order(order: dict[str, Any]) -> bool:
    """Return whether an order status means the previous deposit is no longer an active hold."""
    status = _order_status_value(order).strip().lower()
    return status in INACTIVE_ORDER_STATUSES


def _order_status_value(order: dict[str, Any]) -> str:
    value = order.get("status")
    return "" if value is None else str(value)


def order_created_at_value(order: dict[str, Any]) -> Any:
    """Read platform order creation time across known snake/camel-case response versions."""
    return next(
        (
            order.get(key)
            for key in (
                "created_at",
                "create_at",
                "created_time",
                "create_time",
                "order_created_at",
                "createdAt",
                "createAt",
                "createdTime",
                "createTime",
                "orderCreatedAt",
                "orderCreateTime",
                "add_time",
                "addTime",
            )
            if order.get(key) not in (None, "")
        ),
        None,
    )


def payment_collection_order_fact(state: dict[str, Any], *, amount: Any = None) -> dict[str, Any]:
    """Return the matching active unpaid order for backend linkage when available."""
    expected_amount = _numeric_amount(amount or _payment_decision_value(state, "amount"))
    expected_store_id = _payment_store_id(state)
    if expected_amount not in {10, 20, 30, 40} or not expected_store_id:
        return {}

    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    created = tool_results.get("create_work_order") if isinstance(tool_results.get("create_work_order"), dict) else {}
    if _order_supports_card(created, store_id=expected_store_id, amount=expected_amount, allow_created=True):
        return {**created, "support_source": "create_work_order"}

    context = state.get("customer_context") if isinstance(state.get("customer_context"), dict) else {}
    orders = [order for order in context.get("orders") or [] if isinstance(order, dict)]
    current = [order for order in orders if order.get("is_current_order")]
    candidates = current or orders
    for order in candidates:
        if _order_supports_card(order, store_id=expected_store_id, amount=expected_amount, allow_created=False):
            return {**order, "support_source": "platform_agent.order_index"}
    return {}


def _positive_number(value: Any) -> bool:
    """Accept only a numeric positive amount from platform prepayment fields."""
    if isinstance(value, bool):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 100_000_000_000:
            seconds /= 1000
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            return _parse_datetime(float(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _subtract_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + (value.month - 1) - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _order_binding_missing(order: dict[str, Any], *, required: bool) -> bool:
    store_id = str(order.get("store_id") or "").strip().lower()
    category_id = str(order.get("category_id") or "").strip().lower()
    return store_id in {"", "0", "none", "null"} or category_id in {"", "0", "none", "null"} or not required


def _payment_decision_value(state: dict[str, Any], key: str) -> Any:
    """Read a normalized payment decision value from planner state."""
    decision = state.get("payment_decision") if isinstance(state.get("payment_decision"), dict) else {}
    return decision.get(key)


def _payment_store_id(state: dict[str, Any]) -> str:
    """Resolve the confirmed transaction store without falling back to preferred-store memory."""
    order_decision = state.get("order_decision") if isinstance(state.get("order_decision"), dict) else {}
    turn = state.get("current_turn_context") if isinstance(state.get("current_turn_context"), dict) else {}
    confirmed = turn.get("confirmed_store") if isinstance(turn.get("confirmed_store"), dict) else {}
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    order_state = basic.get("order_state") if isinstance(basic.get("order_state"), dict) else {}
    candidates = [
        order_decision.get("store_id"),
        state.get("confirmed_store_id"),
        confirmed.get("store_id"),
        basic.get("confirmed_store_id"),
        order_state.get("store_id"),
    ]
    for tool in state.get("planner_tool_calls") or []:
        if isinstance(tool, dict) and str(tool.get("name") or "") == "create_work_order":
            candidates.append(tool.get("store_id"))
    return next((str(value).strip() for value in candidates if str(value or "").strip()), "")


def _order_supports_card(order: dict[str, Any], *, store_id: str, amount: int, allow_created: bool) -> bool:
    """Validate store, amount, active status and unpaid state for payment-card authorization."""
    status = str(order.get("status") or "").strip().lower()
    allowed_statuses = {"created", "reused"} if allow_created else ACTIVE_ORDER_STATUSES
    if status not in allowed_statuses:
        return False
    if str(order.get("store_id") or "").strip() != store_id:
        return False
    required = _numeric_amount(order.get("prepay_required") or order.get("fee_required"))
    if required != amount:
        return False
    return str(normalize_prepay_facts(order).get("deposit_state") or "") == "required_unpaid"


def _numeric_amount(value: Any) -> int:
    """Normalize a platform monetary field to an integer amount."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
