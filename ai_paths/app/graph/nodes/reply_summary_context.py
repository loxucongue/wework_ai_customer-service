from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.graph.nodes.result_compaction import ad_price_without_explicit_project
from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES


@dataclass(frozen=True)
class ReplySummaryCallbacks:
    recent_assistant_replies: Callable[[AgentState, int], list[str]]
    canonical_price_project: Callable[[str], str]
    contextual_price_project: Callable[[AgentState], str]
    extract_project: Callable[[str], str]
    filter_pricing_rows_for_project: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]]
    pricing_rows_from_kb: Callable[[dict[str, Any]], list[dict[str, Any]]]
    pricing_rows: Callable[[dict[str, Any]], list[dict[str, Any]]]
    price_bits: Callable[[dict[str, Any]], list[str]]
    business_project_slices: Callable[[list[dict[str, str]], AgentState | None], list[dict[str, str]]]
    project_slices_from_tool_results: Callable[[dict[str, Any]], list[dict[str, str]]]
    project_direction_name_candidates: Callable[[str], list[str]]
    dedupe_strings: Callable[[list[str]], list[str]]


def has_pre_visit_question(content: str) -> bool:
    return any(
        term in content
        for term in ["需要带什么", "要带什么", "带什么", "能不能化妆", "可以化妆", "要不要空腹", "需要空腹", "到店流程", "第一次去注意"]
    )


def pre_visit_message(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    if "化妆" in content:
        return "如果是去看皮肤和做光电类项目，建议尽量素颜或淡妆，到店前别叠太多酸类、去角质这类刺激性护肤。"
    if "空腹" in content:
        return "这类皮肤面诊/光电咨询一般不需要空腹，正常作息就行；如果当天要做项目，按门店确认的项目注意事项来。"
    return "如果周六过去，带上手机和基础身份信息就可以；皮肤类建议尽量素颜或淡妆，前一天别刷酸、去角质，也先别暴晒。"


def is_strong_multi_recap_request(content: str) -> bool:
    flags = [
        has_pre_visit_question(content),
        asks_store_or_address_recap(content),
        asks_price_recap(content),
    ]
    return sum(1 for flag in flags if flag) >= 2


def asks_other_store_options(content: str) -> bool:
    return any(term in content for term in ["其他门店", "还有哪家", "还有别的", "其他店", "更多门店", "附近门店", "哪家更方便"])


def build_multi_recap_messages(state: AgentState, callbacks: ReplySummaryCallbacks) -> list[dict[str, Any]]:
    content = state.get("normalized_content") or ""
    messages: list[dict[str, Any]] = []
    if asks_price_recap(content):
        messages.append({"type": "text", "order": len(messages) + 1, "content": price_summary_message(state, callbacks)})
    if asks_store_or_address_recap(content):
        store_text = store_summary_message(state, callbacks)
        if store_text:
            messages.append({"type": "text", "order": len(messages) + 1, "content": store_text})
    if has_pre_visit_question(content):
        messages.append({"type": "text", "order": len(messages) + 1, "content": pre_visit_message(state)})
    return messages[:3] or [{"type": "text", "order": 1, "content": pre_visit_message(state)}]


def asks_store_or_address_recap(content: str) -> bool:
    recap_terms = ["再说一下", "顺一下", "帮我顺", "再发一下", "再给我", "再捋一下", "捋一下", "重复一下"]
    target_terms = ["地址", "门店", "哪家店", "店名", "位置"]
    return any(term in content for term in target_terms) and any(term in content for term in recap_terms)


def asks_price_recap(content: str) -> bool:
    recap_terms = ["再说一下", "顺一下", "帮我顺", "再给我", "再捋一下", "捋一下", "重复一下", "参考价格", "价格帮我"]
    target_terms = ["价格", "价位", "多少钱", "预算", "费用"]
    return any(term in content for term in target_terms) and any(term in content for term in recap_terms)


def reply_has_pre_visit_answer(text: str) -> bool:
    return any(term in text for term in ["素颜", "淡妆", "空腹", "护肤", "去角质", "身份信息", "手机"])


def reply_has_store_answer(text: str) -> bool:
    if "地址：" in text or "地址这边" in text:
        return True
    return any(term in text for term in ["徐汇店", "静安店", "浦东店", "思明店", "武侯店", "导航："])


def reply_has_price_answer(text: str) -> bool:
    return bool(re.search(r"\d+\s*元?", text)) or any(term in text for term in ["没查到明确单次价", "不拿别的项目价格代替", "新客体验价", "活动价", "日常单次价"])


def store_summary_message(state: AgentState, callbacks: ReplySummaryCallbacks) -> str:
    history_store = latest_store_summary_from_history(state, callbacks)
    if history_store:
        return history_store
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    stores = lookup.get("stores") if isinstance(lookup, dict) else []
    if isinstance(stores, list) and stores:
        store = stores[0] if isinstance(stores[0], dict) else {}
        name = str(store.get("name") or "").strip()
        address = str(store.get("address") or "").strip()
        if name and address:
            return f"门店地址事实：门店={name}；地址={address}。"
        if name:
            return f"门店事实：门店={name}。"
    return ""


def price_summary_message(state: AgentState, callbacks: ReplySummaryCallbacks) -> str:
    tool_results = state.get("tool_results", {}) or {}
    content = state.get("normalized_content") or ""
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state) or callbacks.extract_project(content))
    if ad_price_without_explicit_project(state, project):
        return "价格事实：客户看到广告/活动价，但未提供明确广告项目或截图；暂不能拿知识库相似商品价代替，需要核对广告对应项目、包含项、尾款和是否另收费。"
    rows = callbacks.filter_pricing_rows_for_project(callbacks.pricing_rows_from_kb(tool_results), project) or callbacks.filter_pricing_rows_for_project(
        callbacks.pricing_rows(tool_results),
        project,
    )
    if rows:
        row = rows[0]
        name = str(row.get("project_name") or project or "相关项目")
        bits = callbacks.price_bits(row)
        if bits:
            return "价格事实：项目=" + name + "；" + "；".join(bits[:4]) + "。"
    project_slices = callbacks.business_project_slices(callbacks.project_slices_from_tool_results(tool_results), state)
    direction_candidates: list[str] = []
    for item in project_slices:
        direction_candidates.extend(callbacks.project_direction_name_candidates(str(item.get("replacement_name") or "")))
    direction_names = callbacks.dedupe_strings(direction_candidates)
    if direction_names:
        return "价格事实：暂未查到可直接引用的明确价格；不能拿不相关项目代替；可按" + "、".join(direction_names[:3]) + "方向继续核价。"
    if project:
        return f"价格事实：{project}暂未查到可直接引用的明确价格；不能拿不相关项目代替。"
    history_price = latest_price_summary_from_history(state, callbacks)
    if history_price:
        return history_price
    return "价格事实：暂未查到可直接引用的明确价格；不能拿不相关项目代替。"


def latest_store_summary_from_history(state: AgentState, callbacks: ReplySummaryCallbacks) -> str:
    for reply in reversed(callbacks.recent_assistant_replies(state, 8)):
        text = str(reply).strip()
        if "地址" not in text:
            continue
        match = re.search(r"([^。]*地址[^。]*。?)", text)
        candidate = match.group(1).strip() if match else text
        if any(city in candidate for city in CITY_NAMES) and len(candidate) <= 120:
            return f"历史门店地址事实：{candidate.rstrip('。')}。"
    return ""


def latest_price_summary_from_history(state: AgentState, callbacks: ReplySummaryCallbacks) -> str:
    for reply in reversed(callbacks.recent_assistant_replies(state, 8)):
        text = str(reply).strip()
        if re.search(r"\d+\s*元?", text):
            match = re.search(r"([^。]*\d+\s*元?[^。]*。?)", text)
            candidate = match.group(1).strip() if match else text
            if len(candidate) <= 120:
                return f"历史价格事实：{candidate.rstrip('。')}。"
            break
    return ""
