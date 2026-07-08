from __future__ import annotations

from typing import Any


def build_appointment_evidence(
    *,
    appointment: dict[str, Any],
    missing_slots: list[str],
    blocked_actions: list[str],
    current_time_confirmed: bool,
    next_step_clarification: bool,
) -> dict[str, Any]:
    if not isinstance(appointment, dict):
        appointment = {}
    return _drop_empty(
        {
            "date": str(appointment.get("date") or "").strip(),
            "time": str(appointment.get("time") or "").strip(),
            "store_id": str(appointment.get("store_id") or "").strip(),
            "store_name": str(appointment.get("store_name") or "").strip(),
            "source": str(appointment.get("source") or "").strip(),
            "current_message_has_time_reference": current_time_confirmed,
            "current_message_asks_next_step": next_step_clarification,
            "missing_slots": list(missing_slots or []),
            "blocked_available_time_by_missing_scope": "available_time" in (blocked_actions or []),
        }
    )


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
