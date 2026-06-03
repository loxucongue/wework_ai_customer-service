from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.state import AgentState


@dataclass(frozen=True)
class ProjectSkillCallbacks:
    business_project_slices: Callable[[list[dict[str, str]], AgentState], list[dict[str, str]]]
    case_request_lacks_specific_context: Callable[..., bool]
    dedupe_strings: Callable[[list[str]], list[str]]
    has_image_concern: Callable[..., bool]
    known_visible_concerns_from_state: Callable[[AgentState], list[str]]
    project_direction_name_candidates: Callable[[str], list[str]]
    project_slices_from_tool_results: Callable[[dict[str, Any]], list[dict[str, str]]]


def project_skill_output(
    content: str,
    tool_results: dict[str, Any],
    state: AgentState,
    callbacks: ProjectSkillCallbacks,
) -> dict[str, Any]:
    project_qa = tool_results.get("project_qa") or {}
    if isinstance(project_qa, dict):
        items = project_qa.get("items") or project_qa.get("outputList") or []
        if not items and (project_qa.get("content") or project_qa.get("output")):
            items = [project_qa]
    elif isinstance(project_qa, list):
        items = project_qa
    else:
        items = []

    image_info = state.get("image_info", {})
    project_slices = callbacks.project_slices_from_tool_results(tool_results)
    lacks_case_context = callbacks.case_request_lacks_specific_context(
        state,
        known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state,
    )
    business_slices = [] if lacks_case_context else callbacks.business_project_slices(project_slices, state)

    if lacks_case_context:
        facts = ["客户想看效果案例，但本轮没有明确项目、皮肤问题或图片线索；不能把项目知识库相似切片当作案例事实。"]
    else:
        facts = [f"项目知识库命中{len(items)}条"] if items else ["暂未命中明确项目知识库结果"]

    for item in business_slices[:2]:
        name = item.get("replacement_name") or item.get("title") or ""
        if name:
            facts.append(f"推荐表达/方向：{name}")
        if item.get("direction"):
            facts.append(f"可考虑方向：{item['direction']}")
        if item.get("reply_point"):
            facts.append(f"回复要点：{item['reply_point']}")

    if image_info.get("has_image"):
        if image_info.get("visible_concerns"):
            facts.append(f"图片可见问题：{', '.join(map(str, image_info.get('visible_concerns', [])[:5]))}")
        else:
            facts.append("客户本轮包含图片，但视觉模型未返回明确可见问题")

    visible = image_info.get("visible_concerns") or []
    if lacks_case_context:
        reply_points = ["客户要看效果案例时，先承接可以看同类改善参考；本轮没有项目或问题方向时，只问“想看哪个项目或哪类问题的效果参考”，不要引入无关项目建议。"]
    else:
        reply_points = ["项目咨询应从客户需求和可见问题切入，不强迫客户先说专业项目名。"]

    if business_slices:
        replacement_names: list[str] = []
        for item in business_slices:
            replacement_names.extend(callbacks.project_direction_name_candidates(str(item.get("replacement_name") or "")))
        if replacement_names:
            reply_points.append("优先使用知识库里的替换词名称：" + "、".join(callbacks.dedupe_strings(replacement_names)[:3]))
    if visible:
        reply_points.append(f"必须承接已上传图片：可见{', '.join(map(str, visible[:4]))}，不要再要求重复发照片。")
    if callbacks.has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
        reply_points.append("项目方向优先说明：肤色改善类项目偏肤色不均、泛红暗沉和浅层色沉；针对性色素淡化类项目偏更明确的点状色素，最终看深浅、范围和皮肤耐受。")
    if "点状" in content or "斑" in content:
        reply_points.append("本轮涉及点状斑或斑点，应先给淡斑/色素淡化方向判断，再说明深浅、范围和皮肤耐受会影响具体配置。")

    return {
        "skill": "project_consult",
        "intent": "case_request" if lacks_case_context else "project_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": [],
        "risk_flags": [],
        "suggested_next_step": "确认客户想看的案例方向" if lacks_case_context else "按已知需求给出项目方向，必要时只追问一个关键因素",
        "confidence": 0.7,
    }
