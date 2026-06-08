from __future__ import annotations

from typing import Any

from app.graph.case_query_terms import build_case_query_candidates, expand_case_query_terms
from app.graph.sales_talk_query_terms import expand_sales_talk_query_terms
from app.graph.planner_query_terms import (
    explicit_project_from_content,
    is_generic_query,
    need_query_from_state,
    need_terms_from_state,
    needs_project_direction_before_price,
    price_query_from_state,
)
from app.graph.planner_dispute_signals import is_pre_service_effect_concern
from app.graph.planner_project_signals import has_case_request, has_effect_guarantee_request
from app.graph.nodes.price_question_frames import is_generic_times_or_effect_question, price_frame_can_skip_project_price
from app.graph.state import AgentState
from app.graph.task_appointment_signals import is_appointment_resume_message
from app.policies.constants import SALES_TALK_KB_NAME


def needs_default_tool_plan(skill: Any, tool_plan: Any) -> bool:
    skill_name = str(skill or "")
    if not isinstance(tool_plan, list) or not tool_plan:
        return True

    tool_names = {
        str(item.get("name") or "")
        for item in tool_plan
        if isinstance(item, dict) and item.get("name")
    }
    if not tool_names:
        return True

    required_kbs_by_skill = {
        "project_consult": {SALES_TALK_KB_NAME, "project_qa", "case_studies"},
        "price_consult": {SALES_TALK_KB_NAME},
        "trust_build": {SALES_TALK_KB_NAME, "trust_assets"},
        "competitor": {SALES_TALK_KB_NAME, "competitor_qa"},
        "after_sales": {SALES_TALK_KB_NAME, "after_sales_qa"},
    }
    required_kbs = required_kbs_by_skill.get(skill_name)
    if required_kbs:
        has_kbs = {
            str(item.get("kb_name") or "")
            for item in tool_plan
            if isinstance(item, dict) and item.get("name") == "kb_search"
        }
        if skill_name == "project_consult":
            return SALES_TALK_KB_NAME not in has_kbs or not ({"project_qa", "case_studies"} & has_kbs)
        if skill_name == "price_consult":
            return SALES_TALK_KB_NAME not in has_kbs
        if skill_name in {"trust_build", "competitor", "after_sales"}:
            required_fact = {
                "trust_build": "trust_assets",
                "competitor": "competitor_qa",
                "after_sales": "after_sales_qa",
            }[skill_name]
            return SALES_TALK_KB_NAME not in has_kbs or required_fact not in has_kbs

    if skill_name == "store":
        return "store_lookup" not in tool_names
    if skill_name == "appointment":
        return not ({"store_lookup", "available_time", "no_tool"} & tool_names)
    if skill_name == "handoff":
        return "professional_assist" not in tool_names
    return False


def default_tool_plan(state: AgentState, item: dict[str, Any]) -> list[dict[str, str]]:
    skill = str(item.get("skill") or "")
    content = state.get("normalized_content") or ""
    query = default_query_for_skill(skill, content=content, state=state)
    if skill == "project_consult":
        if str(item.get("intent") or "") == "case_request" or has_case_request(content):
            return [
                {
                    "name": "kb_search",
                    "kb_name": SALES_TALK_KB_NAME,
                    "query": sales_talk_query_for_skill(skill, state, content),
                    "purpose": "检索案例承接、信任建立和下一步推进的话术策略。",
                },
                *[
                    {"name": "kb_search", "kb_name": "case_studies", "query": query, "purpose": "检索效果案例、前后对比或客户做完效果资料"}
                    for query in _case_query_candidates_from_state(state, content)
                ],
            ]
        plan: list[dict[str, str]] = [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": sales_talk_query_for_skill(skill, state, content),
                "purpose": "检索需求承接、客户友好表达和推进节奏。",
            },
            {"name": "kb_search", "kb_name": "project_qa", "query": query, "purpose": "检索改善方向和项目建议"}
        ]
        if _should_attach_case_studies_for_project(state, content):
            for case_query in _case_query_candidates_from_state(state, content):
                plan.append(
                    {
                    "name": "kb_search",
                    "kb_name": "case_studies",
                    "query": case_query,
                    "purpose": "检索同类效果参考和前后对比素材",
                    }
                )
        return plan
    if skill == "price_consult":
        plan: list[dict[str, str]] = []
        plan.append(
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": sales_talk_query_for_skill(skill, state, content),
                "purpose": "检索广告价、收费口径、定金尾款和价格解释的话术策略。",
            }
        )
        if needs_project_direction_before_price(state, content):
            plan.append(
                {
                    "name": "kb_search",
                    "kb_name": "project_qa",
                    "query": need_query_from_state(state, content),
                    "purpose": "先检索可考虑的改善方向和替换词名称",
                }
            )
        if not price_frame_can_skip_project_price(content):
            plan.append({"name": "kb_search", "kb_name": "project_price", "query": query, "purpose": "按项目或改善方向模糊匹配价格"})
        return plan
    if skill == "trust_build":
        plan = [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": sales_talk_query_for_skill(skill, state, content),
                "purpose": "检索正规性、收费透明、效果顾虑的承接策略。",
            },
            {"name": "kb_search", "kb_name": "trust_assets", "query": query, "purpose": "检索资质、背书或收费透明说明"},
        ]
        if _should_attach_case_studies_for_trust(state, content):
            for case_query in _case_query_candidates_from_state(state, content):
                plan.append(
                    {
                        "name": "kb_search",
                        "kb_name": "case_studies",
                        "query": case_query,
                        "purpose": "客户质疑效果时检索同类效果参考图，增强信任感。",
                    }
                )
        return plan
    if skill == "competitor":
        return [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": sales_talk_query_for_skill(skill, state, content),
                "purpose": "检索竞品比价、案例对比和异议承接策略。",
            },
            {"name": "kb_search", "kb_name": "competitor_qa", "query": query, "purpose": "检索竞品应对话术边界"},
        ]
    if skill == "after_sales":
        return [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": sales_talk_query_for_skill(skill, state, content),
                "purpose": "检索轻售后安抚、收集信息和承接节奏。",
            },
            {"name": "kb_search", "kb_name": "after_sales_qa", "query": query, "purpose": "检索售后护理和风险边界"},
        ]
    if skill == "store":
        return [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": sales_talk_query_for_skill(skill, state, content),
                "purpose": "检索门店匹配、地址发送和推荐门店的承接策略。",
            },
            {"name": "store_lookup", "query": content, "purpose": "查询匹配门店"},
        ]
    if skill == "appointment":
        if is_appointment_resume_message(content):
            return [{"name": "no_tool", "purpose": "客户在预约上下文中恢复对话，无需重新查询门店或可约时间"}]
        return [{"name": "store_lookup", "query": content, "purpose": "确认预约门店"}, {"name": "available_time", "query": content, "purpose": "查询可约时间"}]
    if skill == "handoff":
        return [{"name": "professional_assist", "purpose": "需要专业同事核对真实记录"}]
    return [{"name": "no_tool", "purpose": "无需工具"}]


def default_query_for_skill(skill: str, *, content: str = "", state: AgentState | None = None) -> str:
    content = (content or "").strip()
    if skill == "project_consult":
        if has_case_request(content):
            return _case_query_from_state(state, content) if state else " ".join(part for part in [content, "案例", "效果", "前后对比", "改善参考"] if part).strip()
        if state:
            image_info = state.get("image_info") or {}
            concerns = image_info.get("visible_concerns") if isinstance(image_info, dict) else []
            if isinstance(concerns, list) and concerns:
                return need_query_from_state(state, content)
        return need_query_from_state(state, content) if state else content or "项目建议 适合人群"
    if skill == "price_consult":
        if state:
            return price_query_from_state(state, content)
        project = explicit_project_from_content(content)
        return project or content or "项目价格"
    if skill == "trust_build":
        return content or "正规 靠谱 资质 收费透明"
    if skill == "competitor":
        return content or "竞品对比 不诋毁 对比维度"
    if skill == "after_sales":
        return content or "售后护理 风险边界"
    return content


def sales_talk_query_for_skill(skill: str, state: AgentState, content: str) -> str:
    text = (content or "").strip()
    need_hint = next(
        (
            term
            for term in ["祛斑", "淡斑", "黑色素", "色沉", "暗沉", "抗衰", "松弛", "提升", "补水", "毛孔"]
            if term in " ".join([text, need_query_from_state(state, text)])
        ),
        "",
    )
    if skill == "project_consult":
        terms = expand_sales_talk_query_terms(need_hint, [need_query_from_state(state, text), text])
        return " ".join(term for term in terms[:8] if term) or "项目承接 效果参考 推进"
    if skill == "price_consult":
        terms = expand_sales_talk_query_terms(need_hint, [text, price_query_from_state(state, text), "广告价", "收费口径", "定金", "尾款"])
        return " ".join(term for term in terms[:8] if term) or "广告价 收费口径 定金 尾款"
    if skill == "trust_build":
        terms = expand_sales_talk_query_terms(need_hint, [text, "正规", "靠谱", "资质", "收费透明", "效果顾虑"])
        return " ".join(term for term in terms[:8] if term) or "正规 靠谱 资质 收费透明 效果顾虑"
    if skill == "competitor":
        terms = expand_sales_talk_query_terms(need_hint, [text, "竞品比价", "同价", "竞品案例", "竞品报价"])
        return " ".join(term for term in terms[:8] if term) or "竞品比价 同价 竞品案例 竞品报价"
    if skill == "after_sales":
        terms = expand_sales_talk_query_terms(need_hint, [text, "术后恢复", "安抚", "风险边界", "轻售后"])
        return " ".join(term for term in terms[:8] if term) or "术后恢复 安抚 风险边界 轻售后"
    if skill == "store":
        terms = expand_sales_talk_query_terms(need_hint, [text, "门店匹配", "最近门店", "地址", "停车", "导航"])
        return " ".join(term for term in terms[:8] if term) or "门店匹配 最近门店 地址 停车 导航"
    return text


def normalize_tool_plan_for_intent(state: AgentState, item: dict[str, Any]) -> list[dict[str, str]]:
    plan = item.get("tool_plan")
    if not isinstance(plan, list):
        return []
    skill = str(item.get("skill") or "")
    content = state.get("normalized_content") or ""
    normalized: list[dict[str, str]] = []

    for tool in plan:
        if not isinstance(tool, dict):
            continue
        copied = {str(key): str(value) for key, value in tool.items() if value is not None}
        if copied.get("name") == "kb_search":
            kb_name = copied.get("kb_name", "")
            query = copied.get("query", "")
            if kb_name == SALES_TALK_KB_NAME:
                copied["query"] = sales_talk_query_for_skill(skill, state, content) if is_generic_query(query) else query
            elif kb_name in {"project_qa", "case_studies"}:
                copied["query"] = need_query_from_state(state, content) if is_generic_query(query) else query
            elif kb_name == "project_price":
                copied["query"] = price_query_from_state(state, content) if is_generic_query(query) else query
        normalized.append(copied)

    if skill == "project_consult" and is_generic_times_or_effect_question(content):
        normalized = [
            tool
            for tool in normalized
            if not (tool.get("name") == "kb_search" and tool.get("kb_name") == "case_studies")
        ]

    if skill == "appointment" and is_appointment_resume_message(content):
        return [{"name": "no_tool", "purpose": "客户在预约上下文中恢复对话，无需重新查询门店或可约时间"}]

    if skill == "project_consult" and not is_generic_times_or_effect_question(content) and (str(item.get("intent") or "") == "case_request" or has_case_request(content)):
        has_sales_talk = any(tool.get("name") == "kb_search" and tool.get("kb_name") == SALES_TALK_KB_NAME for tool in normalized)
        if not has_sales_talk:
            normalized.insert(
                0,
                {
                    "name": "kb_search",
                    "kb_name": SALES_TALK_KB_NAME,
                    "query": sales_talk_query_for_skill(skill, state, content),
                    "purpose": "检索案例承接、信任建立和下一步推进的话术策略。",
                },
            )
        has_case_studies = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "case_studies" for tool in normalized)
        if not has_case_studies:
            for case_query in reversed(_case_query_candidates_from_state(state, content)):
                normalized.insert(
                    0,
                    {
                    "name": "kb_search",
                    "kb_name": "case_studies",
                    "query": case_query,
                    "purpose": "检索效果案例、前后对比或客户做完效果资料",
                    },
                )

    if skill == "project_consult" and _should_attach_case_studies_for_project(state, content):
        has_sales_talk = any(tool.get("name") == "kb_search" and tool.get("kb_name") == SALES_TALK_KB_NAME for tool in normalized)
        if not has_sales_talk:
            normalized.insert(
                0,
                {
                    "name": "kb_search",
                    "kb_name": SALES_TALK_KB_NAME,
                    "query": sales_talk_query_for_skill(skill, state, content),
                    "purpose": "检索需求承接、客户友好表达和推进节奏。",
                },
            )
        has_case_studies = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "case_studies" for tool in normalized)
        if not has_case_studies:
            for case_query in _case_query_candidates_from_state(state, content):
                normalized.append(
                    {
                    "name": "kb_search",
                    "kb_name": "case_studies",
                    "query": case_query,
                    "purpose": "检索同类效果参考和前后对比素材",
                    }
                )

    if skill == "price_consult":
        has_sales_talk = any(tool.get("name") == "kb_search" and tool.get("kb_name") == SALES_TALK_KB_NAME for tool in normalized)
        has_project_qa = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "project_qa" for tool in normalized)
        has_project_price = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "project_price" for tool in normalized)
        if not has_sales_talk:
            normalized.insert(
                0,
                {
                    "name": "kb_search",
                    "kb_name": SALES_TALK_KB_NAME,
                    "query": sales_talk_query_for_skill(skill, state, content),
                    "purpose": "检索广告价、收费口径、定金尾款和价格解释的话术策略。",
                },
            )
        if needs_project_direction_before_price(state, content) and not has_project_qa:
            normalized.insert(
                1,
                {
                    "name": "kb_search",
                    "kb_name": "project_qa",
                    "query": need_query_from_state(state, content),
                    "purpose": "先检索可考虑的改善方向和替换词名称",
                },
            )
        if not has_project_price and not price_frame_can_skip_project_price(content):
            normalized.append(
                {
                    "name": "kb_search",
                    "kb_name": "project_price",
                    "query": price_query_from_state(state, content),
                    "purpose": "按项目或改善方向模糊匹配价格",
                }
            )

    if skill in {"trust_build", "competitor", "after_sales", "store"}:
        has_sales_talk = any(tool.get("name") == "kb_search" and tool.get("kb_name") == SALES_TALK_KB_NAME for tool in normalized)
        if not has_sales_talk:
            purpose_map = {
                "trust_build": "检索正规性、收费透明、效果顾虑的承接策略。",
                "competitor": "检索竞品比价、案例对比和异议承接策略。",
                "after_sales": "检索轻售后安抚、收集信息和承接节奏。",
                "store": "检索门店匹配、地址发送和推荐门店的承接策略。",
            }
            normalized.insert(
                0,
                {
                    "name": "kb_search",
                    "kb_name": SALES_TALK_KB_NAME,
                    "query": sales_talk_query_for_skill(skill, state, content),
                    "purpose": purpose_map[skill],
                },
            )
        if skill == "trust_build" and _should_attach_case_studies_for_trust(state, content):
            has_case_studies = any(tool.get("name") == "kb_search" and tool.get("kb_name") == "case_studies" for tool in normalized)
            if not has_case_studies:
                for case_query in _case_query_candidates_from_state(state, content):
                    normalized.append(
                        {
                            "name": "kb_search",
                            "kb_name": "case_studies",
                            "query": case_query,
                            "purpose": "客户质疑效果时检索同类效果参考图，增强信任感。",
                        }
                    )

    return normalized[:4]


def _case_query_from_state(state: AgentState, content: str) -> str:
    return _case_query_candidates_from_state(state, content)[0]


def _case_query_candidates_from_state(state: AgentState, content: str) -> list[str]:
    context_query = need_query_from_state(state, content)
    base_terms = need_terms_from_state(state, content)
    joined = " ".join([*base_terms, context_query, content])
    need_hint = next(
        (
            term
            for term in ["祛斑", "淡斑", "黑色素", "抗衰", "补水", "毛孔", "暗沉", "色沉", "松弛", "提升"]
            if term in joined
        ),
        "",
    )
    query_terms: list[str] = []
    query_terms.extend(expand_case_query_terms(need_hint, base_terms[:6]))
    if context_query:
        query_terms.extend(term for term in context_query.split() if term not in {"项目建议", "替换词名称"})
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    body_part = str(image_info.get("body_part") or "").strip()
    if body_part and body_part not in {"无", "未知"} and body_part not in query_terms:
        query_terms.append(body_part)
    face_hint = _looks_like_face_case_need(base_terms, context_query, content, body_part)
    return build_case_query_candidates(
        need_hint,
        base_terms=_dedupe_query_terms(query_terms),
        body_part=body_part,
        face_hint=face_hint,
    )


def _should_attach_case_studies_for_project(state: AgentState, content: str) -> bool:
    if is_generic_times_or_effect_question(content):
        return False
    if has_case_request(content):
        return True
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    if image_info.get("visible_concerns"):
        return True
    query = need_query_from_state(state, content)
    return bool(query and query != "项目建议 适合人群")


def _should_attach_case_studies_for_trust(state: AgentState, content: str) -> bool:
    text = str(content or "")
    if not (
        has_effect_guarantee_request(text)
        or is_pre_service_effect_concern(text)
        or any(term in text for term in ["没效果", "没有效果", "做完效果", "有效果吗", "有用吗", "能解决", "能改善"])
    ):
        return False
    query = need_query_from_state(state, text)
    if query and query != "项目建议 适合人群":
        return True
    recent = "\n".join(str(item) for item in (state.get("conversation_history") or [])[-6:])
    return any(term in recent for term in ["祛斑", "淡斑", "黑色素", "斑", "色沉", "肤色不均", "暗沉", "毛孔", "抗衰", "松弛", "补水"])


def _looks_like_face_case_need(
    base_terms: list[str],
    context_query: str,
    content: str,
    body_part: str,
) -> bool:
    if body_part and body_part not in {"无", "未知"}:
        return any(term in body_part for term in ["面", "脸", "颊", "额", "眼周"])
    joined = " ".join([*base_terms, context_query, content])
    face_need_terms = ["祛斑", "淡斑", "斑", "色沉", "肤色不均", "暗沉", "黑色素"]
    non_face_terms = ["手", "手背", "胳膊", "腿", "身体", "肩", "背部", "颈"]
    return any(term in joined for term in face_need_terms) and not any(term in joined for term in non_face_terms)


def _dedupe_query_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        term = str(value or "").strip()
        if not term or term in result:
            continue
        result.append(term)
    return result
