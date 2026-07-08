from __future__ import annotations

from typing import Any

from app.services.risk_hold import is_hard_health_risk_hold


def build_risk_evidence(risk_hold: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(risk_hold, dict) or not risk_hold:
        return {}
    return _drop_empty(
        {
            "risk_hold": str(risk_hold.get("risk_hold") or "").strip(),
            "reason": str(risk_hold.get("reason") or "").strip(),
            "source": str(risk_hold.get("source") or "").strip(),
            "current_hard_risk": is_hard_health_risk_hold(risk_hold),
            "advisory_context_only": bool(risk_hold and not is_hard_health_risk_hold(risk_hold)),
        }
    )


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
