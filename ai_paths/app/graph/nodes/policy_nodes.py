from __future__ import annotations

from typing import Any, Callable

from app.graph.state import AgentState
from app.policies.scene_guidance_retriever import active_scene_guidance_context, retrieve_scene_guidance
from app.services.trace_logger import TraceLogger


def _active_scene_meta(guidance_context: list[dict[str, Any]]) -> dict[str, Any]:
    if not guidance_context:
        return {"active_scene_id": "", "active_scene_match_level": "", "active_scene_score": 0}
    active = guidance_context[0]
    return {
        "active_scene_id": str(active.get("scene_id") or ""),
        "active_scene_match_level": str(active.get("match_level") or ""),
        "active_scene_score": float(active.get("score") or 0),
    }


def create_scene_guidance_node(*, trace_logger: TraceLogger) -> Callable[[AgentState], Any]:
    async def retrieve_guidance(state: AgentState) -> dict[str, Any]:
        content = str(state.get("normalized_content") or "")
        family = str(state.get("policy_family_id") or state.get("policy_id") or "").strip()
        with trace_logger.node(
            state,
            "retrieve_scene_guidance",
            {
                "content": content,
                "policy_family_id": family,
                "policy_id": state.get("policy_id", ""),
                "policy_match_level": state.get("policy_match_level", ""),
            },
        ) as span:
            candidates = retrieve_scene_guidance(family=family, user_message=content, top_k=3)
            guidance_context = active_scene_guidance_context(candidates, top_k=1)
            active_meta = _active_scene_meta(guidance_context)
            output = {
                "scene_guidance_candidates": candidates,
                "scene_guidance_context": guidance_context,
                "scene_guidance_injected": bool(guidance_context),
                **active_meta,
                "trace": state.get("trace", []),
            }
            span["output_snapshot"] = output
            return output

    return retrieve_guidance
