from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph.nodes.common import dedupe_strings, looks_bad_text
from app.graph.nodes.image_info import has_image_concern, image_concern_terms
from app.graph.nodes.intent_signals import (
    has_ad_price_check,
    has_case_request,
    has_project_process_question,
    is_broad_ad_intro,
)
from app.graph.nodes.result_compaction import price_question_without_explicit_project
from app.graph.planner_query_terms import need_query_from_state


@dataclass(frozen=True)
class ActionQueryCallbacks:
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[dict[str, Any]], str]
    extract_price_digits: Callable[[str], list[str]]
    extract_project: Callable[[str], str]


def safe_query(content: str, skill: Any, callbacks: ActionQueryCallbacks) -> str:
    text = (content or "").strip()
    if text and not looks_bad_text(text):
        skill_name = str(skill)
        project = callbacks.extract_project(text)
        needs = []
        if "淡斑" in text:
            needs.append("淡斑")
        if "祛斑" in text:
            needs.append("祛斑")
        if "点状" in text:
            needs.append("点状斑")
        if "片状" in text:
            needs.append("片状斑")
        if "色沉" in text or "肤色不均" in text or "暗沉" in text:
            needs.append("肤色不均 暗沉 色沉")
        if "毛孔" in text or "出油" in text or "黑头" in text:
            needs.append("毛孔 出油 黑头")
        if "痘印" in text or "痘坑" in text or "闭口" in text:
            needs.append("痘印 痘坑 闭口")
        if "敏感" in text or "泛红" in text or "屏障" in text:
            needs.append("敏感泛红 屏障修护")
        if "松弛" in text or "法令纹" in text or "抗衰" in text:
            needs.append("松弛 抗衰 法令纹")
        if skill_name == "project_consult":
            if has_case_request(text):
                parts = [project, *needs, "案例", "效果", "前后对比", "改善参考"]
                return " ".join(part for part in parts if part).strip()
            if has_project_process_question(text):
                parts = [project, *needs, "操作流程", "时长", "恢复", "注意事项"]
                return " ".join(part for part in parts if part).strip()
            parts = [project, *needs, "项目建议", "适合人群"]
            return " ".join(part for part in parts if part).strip()
        if skill_name == "price_consult":
            if has_ad_price_check(text):
                parts = [project, *callbacks.extract_price_digits(text)[:2], "广告价", "预约金", "尾款", "包含项", "是否另收费"]
                return " ".join(part for part in parts if part).strip()
            return project or "项目价格"
        if skill_name == "trust_build":
            return "正规 资质 医疗机构执业许可证 门店"
        if skill_name == "competitor":
            terms = competitor_query_terms(text, callbacks.extract_price_digits)
            return " ".join(part for part in [project, *terms, "竞品对比", "不诋毁", "不跟价"] if part).strip()
        if skill_name == "after_sales":
            symptoms = after_sales_query_terms(text)
            return " ".join(part for part in [project, *symptoms, "术后护理"] if part).strip()
        return text
    fallback = {
        "project_consult": "项目咨询 适合项目",
        "price_consult": "项目价格 当前报价",
        "trust_build": "正规 资质 产品来源 服务保障",
        "competitor": "竞品对比 不诋毁 不跟价",
        "after_sales": "术后护理 恢复 注意事项",
    }
    return fallback.get(str(skill), "医美客服咨询")


def safe_query_from_state(state: dict[str, Any], skill: Any, callbacks: ActionQueryCallbacks) -> str:
    content = state.get("normalized_content") or ""
    skill_name = str(skill)
    if skill_name == "price_consult":
        if price_question_without_explicit_project(state):
            parts = [*callbacks.extract_price_digits(content)[:2], "广告价", "预约金", "尾款", "包含项", "是否另收费"]
            return " ".join(part for part in parts if part).strip()
        if has_ad_price_check(content):
            project = callbacks.canonical_price_project(callbacks.extract_project(content))
            parts = [project, *callbacks.extract_price_digits(content)[:2], "广告价", "预约金", "尾款", "包含项", "是否另收费"]
            return " ".join(part for part in parts if part).strip()
        project = callbacks.canonical_price_project(callbacks.contextual_price_project(state))
        return project or "项目价格"
    if skill_name == "project_consult":
        if is_broad_ad_intro(content):
            if "祛斑" in content or "淡斑" in content:
                return "祛斑 项目建议 替换词名称"
            return "项目建议 替换词名称"
        if has_case_request(content) or _has_case_intent(state):
            return " ".join(part for part in [need_query_from_state(state, content), "案例", "效果", "前后对比", "改善参考"] if part).strip()
        if has_project_process_question(content):
            project = callbacks.extract_project(content)
            return " ".join(part for part in [project, "操作流程", "时长", "恢复", "注意事项"] if part).strip()
        image_info = state.get("image_info") or {}
        if has_image_concern(image_info, ["点状斑", "褐色斑点", "色沉", "肤色不均", "斑点"]):
            return "点状斑 色沉 肤色不均 针对性色素淡化 肤色改善 项目建议"
        visible_text = " ".join(image_concern_terms(image_info))
        if visible_text:
            return f"{visible_text} 项目建议 替换词名称"
    return safe_query(content, skill, callbacks)


def _has_case_intent(state: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict) and item.get("intent") == "case_request"
        for item in (state.get("intents") or [])
    )


def after_sales_query_terms(content: str) -> list[str]:
    terms: list[str] = []
    mapping = [
        ("结痂", "皮秒 祛斑后结痂 不能抠痂"),
        ("抠", "不能抠痂"),
        ("反黑", "光电术后反黑担心 色沉观察"),
        ("变黑", "光电术后反黑担心 色沉观察"),
        ("红肿", "红肿"),
        ("疼", "疼痛"),
        ("恢复", "恢复期"),
        ("脱皮", "化学焕肤后脱皮 护理建议"),
        ("流脓", "流脓 分泌物"),
        ("出血", "出血"),
        ("没效果", "效果反馈"),
        ("护理", "售后总则 安全优先"),
    ]
    for trigger, query_term in mapping:
        if trigger in content:
            terms.append(query_term)
    return dedupe_strings(terms)


def competitor_query_terms(content: str, extract_price_digits: Callable[[str], list[str]]) -> list[str]:
    terms: list[str] = []
    if any(word in content for word in ["别家", "更便宜", "同价", "做到这个价"]):
        terms.append("低价对比")
    if any(word in content for word in ["报价", "截图", "套餐"]):
        terms.append("竞品报价截图")
    if any(word in content for word in ["一次见效", "包效果", "保证"]):
        terms.append("竞品承诺效果")
    if any(word in content for word in ["坑", "套路", "隐形消费"]):
        terms.append("担心被坑")
    terms.extend(extract_price_digits(content)[:2])
    return dedupe_strings(terms)
