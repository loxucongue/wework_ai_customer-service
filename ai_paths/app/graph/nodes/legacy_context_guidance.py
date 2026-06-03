from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class LegacyContextGuidanceCallbacks:
    has_actual_image_context: Callable[[AgentState], bool]
    has_image_concern: Callable[[dict[str, Any], list[str]], bool]
    known_visible_concerns_from_state: Callable[[AgentState], list[str]]


def project_context_source(state: AgentState, callbacks: LegacyContextGuidanceCallbacks) -> str:
    return "前面照片" if callbacks.has_actual_image_context(state) else "前面描述"


def sanitize_project_direction(direction: str, state: AgentState, callbacks: LegacyContextGuidanceCallbacks) -> str:
    if not direction or callbacks.has_actual_image_context(state):
        return direction
    replacements = {
        "如果图片显示": "如果实际情况是",
        "图片显示": "实际情况是",
        "图片里": "当前情况里",
        "照片里": "当前情况里",
        "照片/描述": "情况",
        "前面照片/描述": "前面描述",
    }
    for source, target in replacements.items():
        direction = direction.replace(source, target)
    return direction


def project_guidance_inline(content: str, project: str) -> str:
    if project == "皮秒" and ("斑" in content or "淡斑" in content or "祛斑" in content or "点状" in content):
        return "另外，从你说的点状斑/淡斑方向看，皮秒一般属于光电淡斑方向，但还要看斑的深浅、范围和皮肤耐受。"
    if "斑" in content or "淡斑" in content or "祛斑" in content:
        return "另外，淡斑类不能只看项目名，主要还要看斑型、深浅、范围和恢复期。"
    return ""


def context_guidance_inline(
    state: AgentState,
    content: str,
    project: str,
    callbacks: LegacyContextGuidanceCallbacks,
) -> str:
    image_guidance = image_guidance_inline(state, project, callbacks)
    if image_guidance:
        return image_guidance
    return project_guidance_inline(content, project)


def image_guidance_inline(
    state: AgentState,
    project: str,
    callbacks: LegacyContextGuidanceCallbacks,
) -> str:
    image_info = state.get("image_info") or {}
    visible_items = image_info.get("visible_concerns") or callbacks.known_visible_concerns_from_state(state)
    if not visible_items:
        return ""
    visible = "、".join(str(item) for item in visible_items[:3])
    if not visible:
        return ""
    spot_like = callbacks.has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]) or any(
        term in visible for term in ["点状斑", "斑点", "色沉", "肤色不均", "面部斑"]
    )
    source = "从你发的图片看" if callbacks.has_actual_image_context(state) else "按你前面描述"
    if spot_like:
        if project == "皮秒":
            return f"另外，{source}，主要是{visible}，先进淡斑技术方向属于淡斑方向之一，但还要看斑的深浅、范围和皮肤耐受。"
        return f"另外，{source}，主要是{visible}，淡斑类还要结合斑型、深浅和范围判断项目方向。"
    acne_like = callbacks.has_image_concern(image_info, ["痘印", "痘坑", "毛孔", "泛红"]) or any(
        term in visible for term in ["痘印", "痘坑", "毛孔", "泛红"]
    )
    if acne_like:
        return f"另外，{source}，主要是{visible}，这类要先区分痘印、痘坑、毛孔或泛红，再看适合的改善方式。"
    return f"另外，{source}，主要是{visible}，后续会结合你想改善的重点看项目方向。"


def memory_context_sentence(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    portrait = state.get("customer_profile") or {}
    if not isinstance(portrait, dict):
        return ""
    pain_points = [str(item) for item in portrait.get("pain_points", []) if item]
    needs = [str(item) for item in portrait.get("needs", []) if item]
    if "热玛吉" in content or "超声炮" in content:
        if any(need in needs for need in ["抗衰", "紧致", "轮廓改善"]):
            return "你前面提到过抗衰或紧致需求，这类项目还要结合松弛程度和部位看配置。"
        return ""
    if "水光" in content:
        if any(need in needs for need in ["补水", "肤质改善", "肤色改善"]) or any(
            point in pain_points for point in ["暗沉", "肤色不均", "面部色沉"]
        ):
            return "我也会结合你前面的肤质改善需求来看水光配置，不只按项目名判断。"
        return ""
    if "点状斑点" in pain_points:
        return "你前面说点状斑为主，这类还是要看斑的深浅和范围，再确认更适合的项目配置。"
    if "面部色沉" in pain_points:
        return "你前面提到有面部色沉，后面看项目时要结合色沉范围、深浅和恢复期一起判断。"
    if needs:
        return f"我也会结合你前面提到的{needs[0]}需求来帮你看，不会只按项目名直接判断。"
    return ""
