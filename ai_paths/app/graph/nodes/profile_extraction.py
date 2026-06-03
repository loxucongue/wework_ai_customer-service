from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.nodes.profile_events import extract_event_updates as _extract_event_updates
from app.graph.nodes.profile_updates import extract_profile_update as _extract_profile_update
from app.graph.state import AgentState


@dataclass(frozen=True)
class ProfileExtractionCallbacks:
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    extract_price_digits: Callable[[str], list[str]]
    extract_project: Callable[[str], str]
    known_visible_concerns: Callable[[AgentState], list[str]]
    project_direction_names: Callable[[AgentState], list[str]]


def extract_profile_update(state: AgentState, callbacks: ProfileExtractionCallbacks) -> dict[str, Any]:
    return _extract_profile_update(state, callbacks)


def extract_event_updates(
    state: AgentState,
    profile_update: dict[str, Any],
    callbacks: ProfileExtractionCallbacks,
) -> list[dict[str, Any]]:
    return _extract_event_updates(state, profile_update, callbacks)

