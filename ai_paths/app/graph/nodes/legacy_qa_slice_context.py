from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.common import dedupe_strings
from app.graph.nodes.kb_slice_parsing import extract_label


def first_competitor_slice(items: list[Any]) -> dict[str, str]:
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if not content:
            continue
        parsed = {
            "scene_type": extract_label(content, "场景类型"),
            "target": extract_label(content, "回复目标"),
            "say": extract_label(content, "可说话术"),
            "collect": extract_label(content, "需收集信息"),
            "forbidden": extract_label(content, "禁用表达"),
            "next_step": extract_label(content, "下一步动作"),
        }
        if any(parsed.values()):
            return parsed
    return {}


def clean_competitor_text(text: str) -> str:
    value = text.strip()
    value = value.replace("我这边", "小贝这边")
    value = value.replace("我们", "我们这边")
    return value


def competitor_scenario(content: str) -> str:
    if any(word in content for word in ["一次见效", "一次就", "淡很多", "包效果", "保证", "承诺"]):
        return "effect_claim"
    if any(word in content for word in ["199", "299", "更便宜", "同价", "做到这个价", "低价"]):
        return "price_compare"
    if any(word in content for word in ["报价", "截图", "套餐"]):
        return "quote_compare"
    if any(word in content for word in ["更好", "朋友说", "案例"]):
        return "positive_competitor"
    if any(word in content for word in ["坑", "套路", "隐形消费"]):
        return "fear_trap"
    return "general_compare"


def competitor_slice_matches(scenario: str, scene_type: str, say: str) -> bool:
    text = f"{scene_type} {say}"
    if scenario == "price_compare":
        return any(word in text for word in ["price", "低价", "便宜", "同价", "价格", "报价"])
    if scenario == "effect_claim":
        return any(word in text for word in ["effect", "效果", "承诺", "保证", "一次见效"])
    if scenario == "quote_compare":
        return any(word in text for word in ["quote", "报价", "截图", "套餐"])
    if scenario == "fear_trap":
        return any(word in text for word in ["trap", "坑", "套路", "隐形消费"])
    if scenario == "positive_competitor":
        return any(word in text for word in ["positive", "更好", "朋友", "案例"])
    return True


def competitor_default_reply(content: str, project: str, price_digits: list[str], scenario: str) -> str:
    if scenario == "price_compare":
        price_text = price_digits[0] if price_digits else ""
        if project and price_text:
            return f"竞品价格对比事实：项目={project}；本轮提到价格={price_text}；最终回复必须保留该价格数字，并从项目配置、产品/剂量、部位、次数和售后维度对比；不能承诺同价或贬低竞品。"
        return "竞品价格对比策略：先承接对比需求，再从项目配置、产品/剂量、部位、次数和售后维度解释；不能承诺同价或贬低竞品。"
    if scenario == "effect_claim":
        return "竞品效果承诺策略：不能跟随承诺一次见效或保证效果；需说明效果与个人基础、方案匹配、操作细节和恢复护理有关。"
    if scenario == "quote_compare":
        return "竞品报价截图策略：拆清项目、产品/剂量、部位、次数、操作人员和售后；不能只按总价判断。"
    if scenario == "fear_trap":
        return "竞品避坑策略：围绕价格透明、隐形加项、产品来源和售后跟进解释；不要指责其他机构。"
    return "竞品通用策略：认可对比，不评价别家好坏，重点拆清项目配置、价格包含项和后续服务。"


def extract_price_digits(content: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", content or "")


def competitor_risk_terms(content: str) -> list[str]:
    terms = []
    for word in ["别家", "同价", "更便宜", "最低", "底价", "包效果", "一次见效", "保证有效"]:
        if word in content:
            terms.append(word)
    terms.extend(extract_price_digits(content)[:2])
    return dedupe_strings(terms)


def first_after_sales_slice(items: list[Any]) -> dict[str, str]:
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if not content:
            continue
        parsed = {
            "risk_level": extract_label(content, "风险等级"),
            "say": extract_label(content, "可说话术"),
            "collect": extract_label(content, "需收集信息"),
            "next_step": extract_label(content, "下一步动作"),
            "scene_type": extract_label(content, "场景类型"),
        }
        if any(parsed.values()):
            return parsed
    return {}


def clean_after_sales_text(text: str) -> str:
    value = text.strip()
    value = value.replace("我这边", "小贝这边")
    value = value.replace("护理老师", "专业同事")
    return value


def split_collect_items(text: str) -> list[str]:
    normalized = re.sub(r"[，、/；;]", ",", text)
    return [item.strip(" 。") for item in normalized.split(",") if item.strip(" 。")]
