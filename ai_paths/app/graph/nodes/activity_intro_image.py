from __future__ import annotations

from typing import Any

from app.graph.state import AgentState
from app.policies.business_rules import load_business_rules


def activity_intro_image_url(state: AgentState) -> str:
    rules = state.get("business_rules") if isinstance(state.get("business_rules"), dict) else load_business_rules()
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    return str(offer.get("activity_intro_image_url") or "").strip()


def append_activity_intro_image(
    messages: list[dict[str, Any]],
    state: AgentState,
    warnings: list[Any] | None = None,
) -> list[dict[str, Any]]:
    return messages
