from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.graph.nodes.common import dedupe_strings, looks_bad_text
from app.graph.nodes.image_info import has_image_concern, image_concern_terms
from app.graph.nodes.intent_signals import has_ad_price_check, has_case_request, has_project_process_question


PROJECT_TASK_TYPES = {"project_inquiry", "image_consult"}
PROJECT_TASK_SUBTYPES = {"project_direction", "project_process", "image_consult", "image_direction"}
PRICE_TASK_TYPES = {"price_inquiry"}
TRUST_TASK_TYPES = {"trust_issue"}
COMPETITOR_TASK_TYPES = {"competitor_compare"}
AFTER_SALES_TASK_TYPES = {"after_sales"}


def safe_query(
    content: str,
    task_type: Any,
    *,
    task_subtype: Any = "",
    canonical_price_project: Callable[[str], str],
    contextual_price_project: Callable[[dict[str, Any]], str],
    extract_price_digits: Callable[[str], list[str]],
    extract_project: Callable[[str], str],
) -> str:
    text = (content or "").strip()
    task_type_name = str(task_type or "").strip()
    task_subtype_name = str(task_subtype or "").strip()

    if text and not looks_bad_text(text):
        project = extract_project(text)
        needs = _need_terms(text)

        if task_type_name in PROJECT_TASK_TYPES or task_subtype_name in PROJECT_TASK_SUBTYPES:
            if has_case_request(text):
                parts = [project, *needs, "案例", "效果", "前后对比", "改善参考"]
                return " ".join(part for part in parts if part).strip()
            if has_project_process_question(text):
                parts = [project, *needs, "操作流程", "时长", "恢复", "注意事项"]
                return " ".join(part for part in parts if part).strip()
            parts = [project, *needs, "项目建议", "适合方向"]
            return " ".join(part for part in parts if part).strip()

        if task_type_name in PRICE_TASK_TYPES:
            if has_ad_price_check(text):
                parts = [
                    project,
                    *extract_price_digits(text)[:2],
                    "活动价",
                    "预约金",
                    "尾款",
                    "包含项",
                    "是否另收费",
                ]
                return " ".join(part for part in parts if part).strip()
            return project or "项目价格"

        if task_type_name in TRUST_TASK_TYPES:
            return "资质 正规 收费透明 服务保障"

        if task_type_name in COMPETITOR_TASK_TYPES:
            terms = competitor_query_terms(text, extract_price_digits)
            parts = [project, *terms, "竞品对比", "不跟价", "不诋毁同行"]
            return " ".join(part for part in parts if part).strip()

        if task_type_name in AFTER_SALES_TASK_TYPES:
            symptoms = after_sales_query_terms(text)
            return " ".join(part for part in [project, *symptoms, "术后护理"] if part).strip()

        return text

    fallback = {
        "project_inquiry": "项目咨询 适合方向",
        "image_consult": "图片面诊 改善方向",
        "price_inquiry": "项目价格 当前报价",
        "trust_issue": "资质 正规 服务保障 收费透明",
        "competitor_compare": "竞品对比 不跟价 同行报价",
        "after_sales": "术后护理 恢复 注意事项",
    }
    return fallback.get(task_type_name, "客服咨询")


def safe_query_from_state(
    state: dict[str, Any],
    task_type: Any,
    *,
    task_subtype: Any = "",
    canonical_price_project: Callable[[str], str],
    contextual_price_project: Callable[[dict[str, Any]], str],
    extract_price_digits: Callable[[str], list[str]],
    extract_project: Callable[[str], str],
) -> str:
    content = str(state.get("normalized_content") or "")
    task_type_name = str(task_type or "").strip()
    task_subtype_name = str(task_subtype or "").strip()

    if task_type_name in PRICE_TASK_TYPES:
        if has_ad_price_check(content):
            project = canonical_price_project(contextual_price_project(state) or extract_project(content))
            parts = [
                project,
                *extract_price_digits(content)[:2],
                "活动价",
                "预约金",
                "尾款",
                "包含项",
                "是否另收费",
            ]
            return " ".join(part for part in parts if part).strip()
        project = canonical_price_project(contextual_price_project(state))
        return project or "项目价格"

    if task_type_name in PROJECT_TASK_TYPES or task_subtype_name in PROJECT_TASK_SUBTYPES:
        project = extract_project(content)
        if has_case_request(content):
            return " ".join(part for part in [project, "案例", "效果", "前后对比", "改善参考"] if part).strip()
        if has_project_process_question(content):
            return " ".join(part for part in [project, "操作流程", "时长", "恢复", "注意事项"] if part).strip()

        image_info = state.get("image_info") or {}
        if has_image_concern(image_info, ["点状斑点", "褐色斑点", "色沉", "肤色不均", "斑点"]):
            return "点状斑点 色沉 肤色不均 改善方向 项目建议"
        visible_text = " ".join(image_concern_terms(image_info))
        if visible_text:
            return f"{visible_text} 项目建议 改善方向".strip()

    return safe_query(
        content,
        task_type_name,
        task_subtype=task_subtype_name,
        canonical_price_project=canonical_price_project,
        contextual_price_project=contextual_price_project,
        extract_price_digits=extract_price_digits,
        extract_project=extract_project,
    )


def after_sales_query_terms(content: str) -> list[str]:
    terms: list[str] = []
    mapping = [
        ("结痂", "结痂 护理"),
        ("反黑", "反黑 观察"),
        ("变黑", "反黑 观察"),
        ("红肿", "红肿"),
        ("疼", "疼痛"),
        ("恢复", "恢复期"),
        ("脱皮", "脱皮 护理"),
        ("流脓", "流脓 分泌物"),
        ("出血", "出血"),
        ("没效果", "效果反馈"),
        ("护理", "术后护理"),
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
        terms.append("竞品效果承诺")
    if any(word in content for word in ["坑", "套路", "隐形消费"]):
        terms.append("担心被坑")
    terms.extend(extract_price_digits(content)[:2])
    return dedupe_strings(terms)


def _need_terms(text: str) -> list[str]:
    needs: list[str] = []
    mapping = [
        (["淡斑", "祛斑", "斑点"], "淡斑"),
        (["点状", "点状斑"], "点状色素"),
        (["片状", "片状斑"], "片状色素"),
        (["色沉", "肤色不均", "暗沉"], "肤色不均 暗沉 色沉"),
        (["毛孔", "出油", "黑头"], "毛孔 出油 黑头"),
        (["痘印", "痘坑", "闭口"], "痘印 痘坑 闭口"),
        (["敏感", "泛红", "屏障"], "敏感泛红 屏障修护"),
        (["松弛", "抗衰", "法令纹"], "松弛 抗衰 法令纹"),
    ]
    for triggers, label in mapping:
        if any(trigger in text for trigger in triggers):
            needs.append(label)
    return dedupe_strings(needs)
