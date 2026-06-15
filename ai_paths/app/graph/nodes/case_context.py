from __future__ import annotations

from app.graph.planner.runtime_plan import planner_tasks
from app.graph.state import AgentState


CASE_CONTEXT_TERMS = (
    "斑",
    "黑色素",
    "色沉",
    "肤色",
    "毛孔",
    "痘印",
    "痘坑",
    "敏感",
    "泛红",
    "松弛",
    "皱纹",
    "补水",
)


def case_request_lacks_specific_context(state: AgentState) -> bool:
    """Return true when the user asks for cases but provides no visible concern."""

    tasks = planner_tasks(state)
    if not any(_is_case_task(task) for task in tasks):
        return False

    content = str(state.get("normalized_content") or "")
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    visible_concerns = " ".join(str(item) for item in (image_info.get("visible_concerns") or []))
    context_text = f"{content} {visible_concerns} {image_info.get('image_desc') or ''}"
    return not any(term in context_text for term in CASE_CONTEXT_TERMS)


def _is_case_task(task: dict[str, object]) -> bool:
    if not isinstance(task, dict):
        return False
    candidates = (
        str(task.get("type") or "").strip().lower(),
        str(task.get("subtype") or "").strip().lower(),
    )
    return any("case" in candidate for candidate in candidates)
