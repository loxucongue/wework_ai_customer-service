from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
import time
from typing import Any

from app.services.customer_order_context import order_status_text
from app.services.customer_payment_state import order_created_at_value
from app.services.platform_agent_client import PlatformAgentClient


_ORDER_PROGRESS = {
    "unknown": 0,
    "no_order": 1,
    "lost_refunded": 2,
    "cancelled": 2,
    "timeout": 2,
    "pending": 3,
    "paid": 4,
    "waiting_schedule": 4,
    "scheduled": 5,
    "visited": 6,
    "finished": 7,
    "evaluated": 8,
}


class PlatformOrderOutcomeProvider:
    """Read a compact current order outcome without exposing raw platform rows."""

    def __init__(
        self,
        client: PlatformAgentClient,
        *,
        enabled: bool,
        request_timeout_seconds: float = 8.0,
        max_retries: int = 2,
        retry_base_seconds: float = 0.5,
    ) -> None:
        self.client = client
        self.enabled = bool(enabled)
        self.request_timeout_seconds = max(0.5, float(request_timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    @property
    def available(self) -> bool:
        return self.enabled and self.client.available

    def runtime_status(self) -> dict[str, Any]:
        status = "ready" if self.available else "disabled" if not self.enabled else "unavailable"
        return {"enabled": self.enabled, "available": self.available, "status": status}

    def reset_batch(self) -> None:
        with self._lock:
            self._cache.clear()

    def __call__(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {
                "status": "disabled",
                "order_state": "",
                "source": "platform_agent.order_index",
                "error": "",
            }
        if not self.client.available:
            return {
                "status": "unavailable",
                "order_state": "",
                "source": "platform_agent.order_index",
                "error": "platform_agent_not_configured",
            }
        customer_id = str(event.get("customer_id") or "").strip()
        if not customer_id:
            return {
                "status": "skipped",
                "order_state": "",
                "source": "platform_agent.order_index",
                "error": "missing_customer_id",
            }
        cache_key = "|".join(
            str(event.get(key) or "").strip()
            for key in ("corp_id", "wechat", "external_userid", "customer_id")
        )
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is None:
            last_error: Exception | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    orders = self.client.list_orders(
                        customer_id=customer_id,
                        page=1,
                        limit=100,
                        request_context={
                            "user_id": event.get("user_id"),
                            "corp_id": event.get("corp_id"),
                            "wechat": event.get("wechat"),
                            "external_userid": event.get("external_userid"),
                        },
                        timeout_seconds=self.request_timeout_seconds,
                        retry_attempts=1,
                        retry_backoff_seconds=0,
                    )
                    cached = {"status": "ok", "orders": list(orders)}
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < self.max_retries:
                        time.sleep(self.retry_base_seconds * (2**attempt))
            else:
                cached = {
                    "status": "error",
                    "orders": [],
                    "error": f"{type(last_error).__name__}: {last_error}"[:500],
                }
            with self._lock:
                self._cache[cache_key] = dict(cached)
        if cached.get("status") != "ok":
            return {
                "status": "error",
                "order_state": "",
                "source": "platform_agent.order_index",
                "error": str(cached.get("error") or "platform_order_query_failed")[:500],
            }
        orders = [item for item in cached.get("orders") or [] if isinstance(item, dict)]
        try:
            relevant_orders, selection_mode = _relevant_orders(orders, event)
            if orders and not relevant_orders and selection_mode == "missing_baseline":
                result = {
                    "status": "insufficient_baseline",
                    "order_state": "",
                    "source": "platform_agent.order_index",
                    "error": "missing_order_baseline",
                    "selection_mode": selection_mode,
                }
                return result
            selected = max(
                relevant_orders,
                key=lambda item: _ORDER_PROGRESS.get(order_status_text(item.get("status")), 0),
            ) if relevant_orders else None
            state = order_status_text(selected.get("status")) if selected else "no_order"
            result = {
                "status": "ok",
                "order_state": state,
                "source": "platform_agent.order_index",
                "error": "",
                "selection_mode": selection_mode,
                "selected_order_id": str((selected or {}).get("id") or (selected or {}).get("order_id") or ""),
            }
        except Exception as exc:
            result = {
                "status": "error",
                "order_state": "",
                "source": "platform_agent.order_index",
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        return result


def _relevant_orders(
    orders: list[dict[str, Any]],
    event: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    if not orders:
        return [], "verified_empty"
    baseline_id = str(event.get("order_id_before") or "").strip()
    baseline_state = str(event.get("order_state_before") or "").strip()
    anchor = _parse_time(event.get("delivered_at") or event.get("occurred_at"))
    if baseline_id:
        relevant = [
            order
            for order in orders
            if str(order.get("id") or order.get("order_id") or "").strip() == baseline_id
            or _created_after(order, anchor)
        ]
        return relevant, "baseline_order_or_new_order"
    if baseline_state and baseline_state not in {"unknown", "no_order"}:
        baseline_matches = [
            order
            for order in orders
            if order_status_text(order.get("status")) == baseline_state
        ]
        if len(baseline_matches) == 1:
            new_orders = [order for order in orders if _created_after(order, anchor)]
            relevant = list({id(order): order for order in [*baseline_matches, *new_orders]}.values())
            return relevant, "unique_baseline_state_or_new_order"
        if len(orders) == 1:
            return list(orders), "single_order_baseline_progression"
        return [], "missing_baseline"
    if anchor is None:
        return [], "missing_baseline"
    relevant = [order for order in orders if _created_after(order, anchor)]
    return (relevant, "new_order_after_event") if relevant else ([], "missing_baseline")


def _created_after(order: dict[str, Any], anchor: datetime | None) -> bool:
    created = _parse_time(order_created_at_value(order))
    return bool(anchor and created and created >= anchor)


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            raw = float(value)
            if raw > 10_000_000_000:
                raw /= 1000
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None
