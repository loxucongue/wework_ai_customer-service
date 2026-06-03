from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class BasicSkillCallbacks:
    intent_for_skill: Callable[[str], str]


def basic_skill_output(
    skill: str,
    reply_points: list[str],
    callbacks: BasicSkillCallbacks,
    *,
    suggested_next_step: str = "",
    facts: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    """Build generic skill output when no specialized module is needed."""

    return {
        "skill": skill,
        "intent": callbacks.intent_for_skill(skill),
        "facts": facts or [],
        "reply_points": reply_points,
        "missing_slots": [],
        "risk_flags": risk_flags or [],
        "suggested_next_step": suggested_next_step,
        "confidence": 0.7,
    }
