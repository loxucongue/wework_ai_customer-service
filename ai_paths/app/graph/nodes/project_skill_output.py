from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.customer_need_questions import customer_friendly_direction_label
from app.graph.nodes.intent_signals import is_broad_ad_intro
from app.graph.nodes.price_question_frames import is_case_times_followup, is_generic_times_or_effect_question
from app.graph.nodes.sales_talk_kb_parsing import first_sales_talk_slice
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
    sales_talk = first_sales_talk_slice(tool_results)
    project_qa = tool_results.get("project_qa") or {}
    if isinstance(project_qa, dict):
        items = project_qa.get("items") or project_qa.get("outputList") or []
        if not items and (project_qa.get("content") or project_qa.get("output")):
            items = [project_qa]
    elif isinstance(project_qa, list):
        items = project_qa
    else:
        items = []

    case_items = _kb_items(tool_results.get("case_studies"))
    intent_set = {
        str(item.get("intent") or "")
        for item in (state.get("intents") or [])
        if isinstance(item, dict)
    }
    image_info = state.get("image_info", {})
    image_intent = str(image_info.get("image_intent") or "")
    image_type = str(image_info.get("image_type") or "")
    project_slices = callbacks.project_slices_from_tool_results(tool_results)
    lacks_case_context = callbacks.case_request_lacks_specific_context(state)
    broad_ad_intro = is_broad_ad_intro(content)
    generic_times_or_effect = is_generic_times_or_effect_question(content)
    business_slices = [] if lacks_case_context else callbacks.business_project_slices(project_slices, state)
    if broad_ad_intro:
        business_slices = []
    non_skin_campaign_image = bool(
        image_info.get("has_image")
        and not (image_info.get("visible_concerns") or [])
        and image_intent == "campaign_inquiry"
        and image_type in {"campaign_poster", "chat_screenshot", "unrelated", "unclear"}
    )

    if non_skin_campaign_image:
        extracted = [str(value) for value in (image_info.get("extracted_text") or [])[:4] if str(value).strip()]
        facts = ["客户本轮发来的是活动券/广告素材类图片，不能继续按面诊图片分析皮肤问题。"]
        if extracted:
            facts.append("图片可见文字：" + "、".join(extracted))
        reply_points = [
            "如果客户在问“这个是吗/有这个券吗”，先回答这看起来像活动券或广告素材，再说明需要核对对应项目、使用条件或门店活动口径。",
            "不要切回斑点、色沉、毛孔或皮肤方向分析。",
        ]
        return {
            "skill": "project_consult",
            "intent": "campaign_inquiry",
            "facts": facts,
            "reply_points": reply_points,
            "missing_slots": [],
            "risk_flags": [],
            "suggested_next_step": "核对券对应的活动项目、使用条件或广告口径",
            "confidence": 0.82,
        }

    if broad_ad_intro:
        need_label = _opening_need_label(content)
        case_fact = "当前已有同类效果参考素材，可以直接先给客户看变化参考。" if case_items else "这轮先按方向承接客户，不需要客户先知道专业项目名。"
        facts = [
            f"客户来自广告引流开场，本轮已明确想了解{need_label or '相关改善'}的方向、价格、效果和到店安排。",
            "这类问题通常可以先按改善方向往下聊，客户不需要先知道专业项目名。",
            "当前还没有明确到斑点具体类型或真实面部图片，先不要讲得太专业。",
            case_fact,
        ]
        reply_points = [
            "先承接客户当前需求，再按客户能听懂的改善方向往下回答。",
            "如果有同类案例素材，可以顺带作为效果参考。",
            "不要一上来细分斑型，也不要展开完整疗程分析。",
        ]
        _inject_sales_talk_guidance(facts, reply_points, sales_talk)
        return {
            "skill": "project_consult",
            "intent": "project_inquiry",
            "facts": facts,
            "reply_points": reply_points,
            "missing_slots": [],
            "risk_flags": [],
            "suggested_next_step": "先给祛斑方向和效果参考，再自然承接价格或最近门店",
            "confidence": 0.86,
        }

    if lacks_case_context:
        facts = ["客户想看效果案例，但本轮没有明确项目、皮肤问题或图片线索；不能把项目知识库相似切片当作案例事实。"]
    elif case_items:
        facts = [f"案例素材库命中{len(case_items)}条，可作为同类改善参考；不能承诺客户也会达到相同变化。"]
    else:
        facts = [f"项目知识库命中{len(items)}条"] if items else ["暂未命中明确项目知识库结果"]

    for item in business_slices[:2]:
        name = customer_friendly_direction_label(str(item.get("replacement_name") or item.get("title") or ""))
        if name:
            facts.append(f"推荐表达/方向：{name}")
        if item.get("direction"):
            facts.append(f"可考虑方向：{customer_friendly_direction_label(str(item['direction']))}")
        if item.get("reply_point"):
            facts.append(f"回复要点：{item['reply_point']}")

    if image_info.get("has_image"):
        if image_info.get("visible_concerns"):
            facts.append(f"图片可见问题：{', '.join(map(str, image_info.get('visible_concerns', [])[:5]))}")
        else:
            facts.append("客户本轮包含图片，但视觉模型未返回明确可见问题")

    visible = image_info.get("visible_concerns") or []
    if lacks_case_context:
        reply_points = ["客户要看效果案例时，先承接可以看同类改善参考；本轮没有项目或问题方向时，只在必要时问“想看哪个项目或哪类问题的效果参考”，不要引入无关项目建议。"]
    elif case_items:
        reply_points = ["客户要看效果案例时，优先使用案例素材库资料做同类改善参考；先给客户安全感，再补一句具体变化会因个人情况不同。"]
    else:
        reply_points = ["项目咨询应从客户需求和可见问题切入，不强迫客户先说专业项目名。"]
        if business_slices:
            reply_points.append("能直接给方向时先给方向，不要把问题回抛给客户。")
        if case_items:
            reply_points.append("有同类案例素材时，可以把它当作改善参考顺带带出来，增强客户安全感。")

    if is_case_times_followup(content):
        facts.append("客户当前追问的是案例图或效果图对应的次数，不是重新问项目方向。")
        reply_points.append("先解释效果图更适合作为阶段性改善参考，单张图通常看不出准确做了几次；如果有明确案例记录再补充，不要重新泛问项目。")

    if generic_times_or_effect:
        facts.append("客户当前问的是改善次数、一次效果或维持时间，需要先给大致节奏，不要先反问项目名。")
        reply_points.insert(0, _generic_times_or_effect_reply_point(content))

    if business_slices:
        replacement_names: list[str] = []
        for item in business_slices:
            replacement_names.extend(
                callbacks.project_direction_name_candidates(
                    customer_friendly_direction_label(str(item.get("replacement_name") or ""))
                )
            )
        if replacement_names:
            reply_points.append("方向表达优先使用客户能听懂的替换词名称。")
    if visible:
        reply_points.append(f"必须承接已上传图片：可见{', '.join(map(str, visible[:4]))}，不要再要求重复发照片。")
    if callbacks.has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
        reply_points.append("项目方向优先说明：整体提亮方向更偏肤色不均、暗沉和浅层色沉；淡斑改善方向更偏更明确的点状色素，最终还要看深浅、范围和皮肤耐受。")
    if "点状" in content or "斑" in content:
        reply_points.append("本轮涉及点状斑或斑点，先承接能做，再继续解释改善方向。")
    _inject_sales_talk_guidance(facts, reply_points, sales_talk)

    suggested_next_step = "确认客户想看的案例方向" if lacks_case_context else "按已知需求给出项目方向，必要时只追问一个关键因素"
    if generic_times_or_effect:
        suggested_next_step = "解释改善次数和维持节奏"

    return {
        "skill": "project_consult",
        "intent": "case_request" if "case_request" in intent_set else "project_inquiry",
        "facts": facts,
        "reply_points": reply_points,
        "missing_slots": [],
        "risk_flags": [],
        "suggested_next_step": suggested_next_step,
        "confidence": 0.7,
    }


def _opening_need_label(content: str) -> str:
    text = str(content or "")
    if any(term in text for term in ["祛斑", "淡斑", "黑色素", "色沉", "斑"]):
        return "淡斑改善"
    if any(term in text for term in ["抗衰", "松弛", "法令纹", "下垂", "轮廓", "提升"]):
        return "抗衰紧致"
    if any(term in text for term in ["补水", "干燥", "缺水", "卡粉", "起皮"]):
        return "补水修护"
    if any(term in text for term in ["毛孔", "黑头", "痘印", "痘坑", "出油", "痘"]):
        return "毛孔和肤质改善"
    return ""


def _generic_times_or_effect_reply_point(content: str) -> str:
    text = str(content or "")
    if any(term in text for term in ["保持多久", "维持多久"]):
        return "先回答维持时间：保持多久和项目类型、皮肤基础、后续护理、防晒及生活习惯有关，不能说固定；可以按阶段性改善和后续维护来理解。"
    if any(term in text for term in ["一次做好", "一次能好吗", "一次能不能好", "一次能不能做好"]):
        return "先回答一次效果：一次通常可以先看基础变化，但多数改善不是一次把所有问题完全定住，后续要看范围、深浅和皮肤反应。"
    return "先回答次数：很多顾客会先看2-3次左右的变化，再根据范围、深浅和皮肤反应调整节奏，不要直接回问客户想做什么。"


def _kb_items(value: Any) -> list[Any]:
    if isinstance(value, dict):
        items = value.get("items") or value.get("outputList") or []
        if not items and (value.get("content") or value.get("output")):
            return [value]
        return items if isinstance(items, list) else []
    if isinstance(value, list):
        return value
    return []


def _inject_sales_talk_guidance(
    facts: list[str],
    reply_points: list[str],
    sales_talk: dict[str, str],
) -> None:
    if not sales_talk:
        return
    if sales_talk.get("scene_type"):
        facts.append(f"销售话术场景：{sales_talk['scene_type']}")
    if sales_talk.get("target"):
        facts.append(f"承接目标：{sales_talk['target']}")
    if sales_talk.get("next_step"):
        reply_points.append(f"下一步建议：{sales_talk['next_step']}")
    if sales_talk.get("forbidden"):
        facts.append(f"禁用表达：{sales_talk['forbidden']}")
