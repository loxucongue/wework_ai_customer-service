from __future__ import annotations

from typing import Any

from app.graph.state import AgentState


def appointment_slot_value(state: AgentState, name: str) -> str:
    slots = appointment_slots(state)
    return str(slots.get(name) or "").strip()


def appointment_slots(state: AgentState) -> dict[str, Any]:
    appointment_cache = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    slots: dict[str, Any] = {}
    mappings = {
        "store_name": [appointment_cache.get("store_name"), state.get("confirmed_store_name"), state.get("store_name")],
        "store_id": [appointment_cache.get("store_id"), state.get("confirmed_store_id"), state.get("store_id")],
        "visit_date_value": [appointment_cache.get("date"), appointment_cache.get("appointment_date"), request_context.get("appointment_date")],
        "date": [appointment_cache.get("date"), appointment_cache.get("appointment_date"), request_context.get("appointment_date")],
        "time": [appointment_cache.get("time"), appointment_cache.get("appointment_time"), state.get("appointment_time"), request_context.get("appointment_time")],
        "people_count": [request_context.get("people_count"), appointment_cache.get("people_count")],
    }
    for key, values in mappings.items():
        for value in values:
            text = str(value or "").strip()
            if text:
                slots[key] = text
                break
    return slots
