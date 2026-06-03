from __future__ import annotations

from typing import Any

from app.graph.nodes.legacy_skill_callback_factories import legacy_skill_dispatch_callbacks
from app.graph.nodes.legacy_skill_dispatch import skill_output as skill_output_from_dispatch
from app.graph.state import AgentState


def skill_output(skill: str, content: str, tool_results: dict[str, Any], state: AgentState) -> dict[str, Any]:
    return skill_output_from_dispatch(skill, content, tool_results, state, legacy_skill_dispatch_callbacks())
