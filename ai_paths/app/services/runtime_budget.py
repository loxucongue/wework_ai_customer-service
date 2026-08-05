from __future__ import annotations

import time
from typing import Any


DEFAULT_ROUND_TIMEOUT_SECONDS = 120.0
DEFAULT_STRONG_ROUND_TIMEOUT_SECONDS = 120.0
DEFAULT_REPLY_RESERVE_SECONDS = 30.0
DEFAULT_MIN_RETRY_REMAINING_SECONDS = 8.0


def build_runtime_budget(settings: Any | None, *, started_monotonic: float | None = None) -> dict[str, Any]:
    started = float(started_monotonic if started_monotonic is not None else time.monotonic())
    ordinary = _positive_setting(settings, "model_round_timeout_seconds", DEFAULT_ROUND_TIMEOUT_SECONDS)
    strong = max(
        ordinary,
        _positive_setting(settings, "model_strong_round_timeout_seconds", DEFAULT_STRONG_ROUND_TIMEOUT_SECONDS),
    )
    reserve = min(
        ordinary,
        _non_negative_setting(settings, "model_reply_reserve_seconds", DEFAULT_REPLY_RESERVE_SECONDS),
    )
    min_retry = _non_negative_setting(
        settings,
        "model_min_retry_remaining_seconds",
        DEFAULT_MIN_RETRY_REMAINING_SECONDS,
    )
    enforced = bool(getattr(settings, "model_round_budget_enforced", False)) if settings is not None else False
    return {
        "mode": "enforced" if enforced else "shadow",
        "enforced": enforced,
        "started_monotonic": started,
        "ordinary_deadline_monotonic": started + ordinary,
        "strong_deadline_monotonic": started + strong,
        "ordinary_timeout_seconds": ordinary,
        "strong_timeout_seconds": strong,
        "reply_reserve_seconds": reserve,
        "min_retry_remaining_seconds": min_retry,
    }


def model_deadline_monotonic(
    state: dict[str, Any],
    *,
    tier: str,
    reserve_reply: bool = False,
) -> float | None:
    budget = _runtime_budget(state)
    if not budget or not bool(budget.get("enforced")):
        return None
    deadline = _deadline_for_tier(budget, tier=tier)
    if reserve_reply:
        deadline -= float(budget.get("reply_reserve_seconds") or 0.0)
    return deadline


def graph_deadline_monotonic(
    state: dict[str, Any],
    *,
    phase: str,
    strong_reply: bool = False,
) -> float | None:
    budget = _runtime_budget(state)
    if not budget or not bool(budget.get("enforced")):
        return None
    if phase == "planner":
        return float(budget.get("ordinary_deadline_monotonic") or 0.0) - float(
            budget.get("reply_reserve_seconds") or 0.0
        )
    return _deadline_for_tier(budget, tier="strong" if strong_reply else "reply")


def can_start_model_retry(
    state: dict[str, Any],
    *,
    tier: str,
    reserve_reply: bool = False,
    now_monotonic: float | None = None,
) -> bool:
    snapshot = runtime_budget_snapshot(
        state,
        tier=tier,
        reserve_reply=reserve_reply,
        now_monotonic=now_monotonic,
    )
    if snapshot.get("mode") in {"unconfigured", "shadow"}:
        return True
    return not bool(snapshot.get("would_skip_retry"))


def runtime_budget_snapshot(
    state: dict[str, Any],
    *,
    tier: str,
    reserve_reply: bool = False,
    now_monotonic: float | None = None,
) -> dict[str, Any]:
    budget = _runtime_budget(state)
    if not budget:
        return {"mode": "unconfigured"}
    now = float(now_monotonic if now_monotonic is not None else time.monotonic())
    deadline = _deadline_for_tier(budget, tier=tier)
    if reserve_reply:
        deadline -= float(budget.get("reply_reserve_seconds") or 0.0)
    remaining = max(0.0, deadline - now)
    minimum = float(budget.get("min_retry_remaining_seconds") or 0.0)
    return {
        "mode": str(budget.get("mode") or "shadow"),
        "tier": str(tier or ""),
        "reserve_reply": bool(reserve_reply),
        "elapsed_seconds": round(max(0.0, now - float(budget.get("started_monotonic") or now)), 3),
        "remaining_seconds": round(remaining, 3),
        "min_retry_remaining_seconds": minimum,
        "would_skip_retry": remaining < minimum,
    }


def _runtime_budget(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("runtime_budget") if isinstance(state, dict) else None
    return value if isinstance(value, dict) else {}


def _deadline_for_tier(budget: dict[str, Any], *, tier: str) -> float:
    key = "strong_deadline_monotonic" if str(tier or "") == "strong" else "ordinary_deadline_monotonic"
    return float(budget.get(key) or 0.0)


def _positive_setting(settings: Any | None, name: str, default: float) -> float:
    value = getattr(settings, name, default) if settings is not None else default
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        return default


def _non_negative_setting(settings: Any | None, name: str, default: float) -> float:
    value = getattr(settings, name, default) if settings is not None else default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default
