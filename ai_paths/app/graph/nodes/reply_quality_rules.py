from __future__ import annotations

import re
from typing import Any

from app.graph import planner_helpers, reply_filters, task_state
from app.graph.nodes.intent_signals import is_broad_ad_intro
from app.graph.nodes.price_question_frames import detect_price_question_frame
from app.graph.nodes.reply_quality_appointment import claims_unavailable_preferred_time_available
from app.graph.nodes.reply_quality_context_rules import (
    case_request_invented_specific_context,
    claims_image_without_image,
    contains_appointment_push,
    contains_forbidden_customer_claims,
    contains_store_or_appointment_push,
    contains_unsupported_diagnosis,
    continues_old_appointment_when_not_requested,
    injects_store_info_without_store_intent,
    injects_unavailable_trust_assets,
    store_status_confirmation_reply_allowed,
    violates_multi_recap_boundary,
    violates_pre_visit_makeup_answer,
)
from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES


def check_forbidden_and_context(
    state: AgentState,
    text: str,
    intents: set[str],
    content: str,
    project: str,
    image_info: dict[str, Any],
    known_visible: list[Any],
    message_count: int,
    callbacks: ReplyQualityCallbacks,
) -> bool | None:
    if case_request_invented_specific_context(state, text, callbacks):
        return True
    if not store_status_confirmation_reply_allowed(text, intents) and contains_forbidden_customer_claims(text):
        return True
    if claims_image_without_image(state, text, callbacks):
        return True
    if contains_unsupported_diagnosis(text):
        return True
    if injects_unavailable_trust_assets(text, intents):
        return True
    if injects_store_info_without_store_intent(text, intents, content, callbacks):
        return True
    if continues_old_appointment_when_not_requested(state, text, intents):
        return True
    if violates_multi_recap_boundary(content, text, callbacks):
        return True
    if violates_pre_visit_makeup_answer(content, text):
        return True
    return None


def check_store_appointment_price(
    state: AgentState,
    text: str,
    intents: set[str],
    content: str,
    project: str,
    image_info: dict[str, Any],
    known_visible: list[Any],
    callbacks: ReplyQualityCallbacks,
) -> bool | None:
    if _allow_tool_backed_store_reply(state, text, content, intents):
        return False
    if "store_inquiry" in intents and _store_reply_conflicts_with_query(state, text, content, callbacks):
        return True
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        if _appointment_reply_conflicts_with_slots(state, text, content, callbacks):
            return True
    if "after_sales" in intents and any(term in text for term in ["是正常的", "属于正常", "不用担心", "没事"]):
        return True
    if "price_inquiry" in intents and _price_reply_invalid(state, text, content, project, image_info, known_visible, intents, callbacks):
        return True
    return None


def check_final_intent_rules(
    state: AgentState,
    text: str,
    intents: set[str],
    content: str,
    project: str,
    image_info: dict[str, Any],
    known_visible: list[Any],
    callbacks: ReplyQualityCallbacks,
) -> bool | None:
    if "trust_issue" in intents and "store_inquiry" not in intents and contains_store_or_appointment_push(text):
        return True
    if not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"} and not task_state.is_active_appointment_task(state):
        if contains_appointment_push(text):
            return True
    if not intents & {"price_inquiry", "campaign_inquiry", "competitor_compare"}:
        if any(term in text for term in ["新客体验价", "活动价", "日常单次"]):
            return True
    if "case_request" in intents and _case_reply_misses_case_intent(state, text, callbacks):
        return True
    if "project_process" in intents and not any(term in text for term in ["流程", "步骤", "操作", "清洁", "检测", "评估", "分钟", "时长", "多久"]):
        return True
    if "project_process" in intents and not any(term in text for term in ["分钟", "小时", "半小时"]):
        return True
    if "project_process" in intents and callbacks.asks_followup_question(text):
        return True
    if is_broad_ad_intro(content) and any(term in text for term in ["点状斑", "点状斑点", "片状色沉", "肤色不均", "深浅范围"]):
        return True
    if "ad_price_check" in intents and _ad_price_reply_invalid(state, text, content, callbacks):
        return True
    if claims_unavailable_preferred_time_available(state, text, callbacks):
        return True
    if _allow_store_recommendation_followup_similarity(state, text, content):
        return False
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        available = state.get("tool_results", {}).get("available_time") or {}
        if isinstance(available, dict) and callbacks.available_slot_list(available.get("slots") or {}) and re.search(r"\d{1,2}:\d{2}", text):
            return False
    if callbacks.too_similar_to_recent_assistant_reply(state, text):
        return True
    return False


def _store_reply_conflicts_with_query(state: AgentState, text: str, content: str, callbacks: ReplyQualityCallbacks) -> bool:
    city = callbacks.extract_city(content)
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    stores = lookup.get("stores", []) if isinstance(lookup, dict) else []
    if _store_reply_mentions_unknown_store_name(state, text):
        return True
    if _store_reply_invents_distance_or_duration(stores, text):
        return True
    if city and city not in text:
        return True
    if city and not stores and any(other_city in text for other_city in CITY_NAMES if other_city != city):
        return True
    if "停车" in content and "停车场" not in text:
        return True
    if "地址" in content and "地址" not in text and city not in text:
        return True
    return False


def _store_reply_invents_distance_or_duration(stores: list[Any], text: str) -> bool:
    if not re.search(r"\d+\s*(分钟|公里|km|KM|千米)", text):
        return False
    source_text = " ".join(
        str(value or "")
        for store in stores
        if isinstance(store, dict)
        for value in store.values()
    )
    return not re.search(r"\d+\s*(分钟|公里|km|KM|千米)", source_text)


def _store_reply_mentions_unknown_store_name(state: AgentState, text: str) -> bool:
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    stores = lookup.get("stores", []) if isinstance(lookup, dict) else []
    recommended = lookup.get("recommended_store") if isinstance(lookup, dict) else {}
    allowed_names = {
        str(store.get("name") or "").strip()
        for store in stores
        if isinstance(store, dict) and str(store.get("name") or "").strip()
    }
    if isinstance(recommended, dict):
        recommended_name = str(recommended.get("name") or "").strip()
        if recommended_name:
            allowed_names.add(recommended_name)
    active_task = state.get("active_task") or {}
    known_slots = active_task.get("known_slots") if isinstance(active_task, dict) and isinstance(active_task.get("known_slots"), dict) else {}
    for raw_name in [
        state.get("confirmed_store_name"),
        state.get("store_name"),
        known_slots.get("store_name"),
    ]:
        name = str(raw_name or "").strip()
        if name:
            allowed_names.add(name)
    if not allowed_names:
        return False
    masked_text = text
    for name in sorted(allowed_names, key=len, reverse=True):
        masked_text = masked_text.replace(name, "")
    candidates = {
        match.strip()
        for match in re.findall(r"([\u4e00-\u9fa5A-Za-z0-9]{2,16}(?:门店|店))", masked_text)
        if match
        and match
        not in {
            "到店",
            "来店",
            "门店",
            "哪家店",
            "这家店",
            "那家店",
            "一家门店",
            "优先门店",
            "推荐门店",
        }
    }
    candidates = {
        name
        for name in candidates
        if not re.match(r"^[一二三四五六七八九十两几多0-9]+家店$", name)
        and not re.match(r"^目前有[一二三四五六七八九十两几多0-9]+家店$", name)
        and not re.match(r"^共[一二三四五六七八九十两几多0-9]+家店$", name)
    }
    if not candidates:
        return False
    return any(name not in allowed_names for name in candidates)


def _appointment_reply_conflicts_with_slots(state: AgentState, text: str, content: str, callbacks: ReplyQualityCallbacks) -> bool:
    active_task = state.get("active_task") or {}
    slots = active_task.get("known_slots") if isinstance(active_task, dict) and isinstance(active_task.get("known_slots"), dict) else {}
    missing = active_task.get("missing_slots") if isinstance(active_task, dict) and isinstance(active_task.get("missing_slots"), list) else []
    if "日期" in missing and any(term in text for term in ["目前可约", "是可约的", "有可约时间", "有空档", "可以预约"]):
        return True
    if not slots.get("visit_date_value") and "今天" not in content and "今天" in text:
        return True
    available = state.get("tool_results", {}).get("available_time") or {}
    if isinstance(available, dict) and available.get("slots") and not re.search(r"\d{1,2}:\d{2}", text):
        return True
    if isinstance(available, dict) and available.get("error") and any(term in text for term in ["有空档", "可以安排", "可以预约"]):
        return True
    if callbacks.is_direct_arrival_question(content) and not any(term in text for term in ["不建议直接", "先别直接", "直接过去可能不太方便", "不太方便", "不要直接"]):
        return True
    return False


def _price_reply_invalid(
    state: AgentState,
    text: str,
    content: str,
    project: str,
    image_info: dict[str, Any],
    known_visible: list[Any],
    intents: set[str],
    callbacks: ReplyQualityCallbacks,
) -> bool:
    if any(term in text for term in ["稍后同步给你", "稍后发你", "回头给你", "我去问下再回复你"]):
        return True
    need_text = " ".join([content, *map(str, known_visible), str(image_info.get("image_desc") or "")])
    has_spot_need = any(term in need_text for term in ["斑", "点状", "色沉", "肤色不均", "暗沉"])
    has_sensitive_repair_need = any(term in need_text for term in ["敏感", "泛红", "屏障", "刺痛", "干痒", "红血丝"])
    if has_spot_need and callbacks.has_no_price_fact_phrase(text) and _asks_spot_price_followup(text):
        return True
    if (
        has_spot_need
        and not has_sensitive_repair_need
        and any(term in text for term in ["舒缓修护", "屏障重建", "敏感修护", "修护方向", "泛红"])
    ):
        return True
    if reply_filters.has_unsupported_no_price_commitment(text):
        return True
    if callbacks.lacks_price_answer_for_price_question(state, text):
        return True
    if project and f"没有{project}项目" in text:
        return True
    if project and project not in text and not re.search(r"\d+\s*元", text):
        direction_names = callbacks.project_direction_names_from_state(state)
        has_replacement_direction = any(name and name in text for name in direction_names)
        has_broad_spot_direction = project in {"淡斑", "祛斑"} and any(term in text for term in ["色素淡化", "肤色改善", "斑点", "色沉"])
        if not (has_replacement_direction or has_broad_spot_direction):
            return True
    if not re.search(r"\d+\s*元", text) and any(term in text for term in ["具体价格要看", "价格要看", "准确价格", "配置"]):
        return not callbacks.has_no_price_fact_phrase(text)
    if reply_filters.asks_daily_single_price(content) and "日常单次" not in text and "日常价" not in text:
        return True
    if callbacks.has_price_objection(content):
        if not reply_filters.has_budget_or_price_answer(text):
            return True
        if reply_filters.is_project_only_after_price_objection(text):
            return True
    if not callbacks.is_strong_multi_recap_request(content) and not intents & {"store_inquiry", "appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        if any(term in text for term in ["所在城市", "附近门店", "门店优惠", "到店时间", "哪家更方便", "对应门店"]):
            return True
    return False


def _asks_spot_price_followup(text: str) -> bool:
    followup_terms = [
        "斑出现多久",
        "出现多久",
        "晒后",
        "方便的话",
        "如果方便",
        "告诉我",
        "可以告诉我",
        "想问下",
    ]
    return "？" in text or "?" in text or any(term in text for term in followup_terms)


def _case_reply_misses_case_intent(state: AgentState, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if not any(term in text for term in ["案例", "前后", "对比", "改善参考", "同类"]):
        return True
    if any(term in text for term in ["案例价格", "哪家门店可以看案例"]) and not any(term in text for term in ["同类", "祛斑", "淡斑", "改善"]):
        return True
    if any(term in text for term in ["第1次", "第一次", "第3次", "第三次", "几天后", "几周后", "顾客反馈"]):
        return not callbacks.tool_results_contain(state, ["真实案例", "案例图", "前后对比"])
    return False


def _ad_price_reply_invalid(state: AgentState, text: str, content: str, callbacks: ReplyQualityCallbacks) -> bool:
    frame = detect_price_question_frame(content)
    digits = callbacks.extract_price_digits(content)
    if digits and not any(digit in text for digit in digits[:2]):
        return True
    if frame == "single_fee":
        if not any(term in text for term in ["一次", "单次", "局部", "全脸", "双侧", "范围", "疗程"]):
            return True
    elif frame == "times_question":
        if not any(term in text for term in ["一次", "分阶段", "变化", "节奏", "范围", "皮肤反应"]):
            return True
    elif not any(term in text for term in ["广告", "活动", "预约金", "尾款", "包含", "另收费", "隐形", "单次", "一次费用", "范围口径"]):
        return True
    if any(term in text for term in ["斑点出现多久", "出现多久", "晒后明显", "想改善哪一点", "更适合怎么推进"]):
        return True
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state) or callbacks.extract_project(content))
    if callbacks.ad_price_without_explicit_project(state, project):
        return any(term in text for term in ["确实有", "真实有", "目前有这个活动", "可以按这个价格", "就是这个活动"])
    return False


def _allow_store_recommendation_followup_similarity(state: AgentState, text: str, content: str) -> bool:
    if not any(term in (content or "") for term in ["直接推荐", "推荐一家", "帮我选", "你选一家", "哪家方便"]):
        return False
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    if not isinstance(lookup, dict):
        return False
    recommended = lookup.get("recommended_store")
    if not isinstance(recommended, dict):
        return False
    name = str(recommended.get("name") or "").strip()
    if not name or name not in text:
        return False
    blocked_terms = ["匹配到", "1.", "2.", "3.", "另外还有", "你看哪家更方便", "总共"]
    return not any(term in text for term in blocked_terms)


def _allow_tool_backed_store_reply(
    state: AgentState,
    text: str,
    content: str,
    intents: set[str],
) -> bool:
    mixed_project_store = intents == {"store_inquiry", "project_inquiry"}
    if intents != {"store_inquiry"} and not mixed_project_store:
        return False
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    if not isinstance(lookup, dict):
        return False

    blocked_terms = [
        "预约",
        "可约",
        "锁位",
        "留名额",
        "什么时候方便",
        "哪天方便",
        "价格",
        "收费",
    ]
    if not mixed_project_store:
        blocked_terms.append("项目")
    if any(term in text for term in blocked_terms):
        return False

    stores = lookup.get("stores") if isinstance(lookup.get("stores"), list) else []
    recommended = lookup.get("recommended_store") if isinstance(lookup.get("recommended_store"), dict) else {}
    status_summary = str(lookup.get("status_summary") or "").strip()
    business_hours = str(lookup.get("business_hours") or "").strip()

    wants_names = any(term in content for term in ["门店名字", "店名", "叫什么店", "哪几家店", "有哪些店"])
    wants_status = any(term in content for term in ["关门", "闭店", "停业", "还在", "还开", "营业"])
    wants_address = any(term in content for term in ["地址", "导航", "停车"])
    generic_store_query = any(term in content for term in ["门店在哪里", "门店在哪", "哪里有店", "有哪些门店", "哪里有门店"])
    store_names = [str(store.get("name") or "").strip() for store in stores if isinstance(store, dict)]
    store_addresses = [str(store.get("address") or "").strip() for store in stores if isinstance(store, dict)]

    if isinstance(recommended, dict):
        recommended_name = str(recommended.get("name") or "").strip()
        recommended_address = str(recommended.get("address") or "").strip()
        if recommended_name and recommended_name in text:
            if wants_address and recommended_address and recommended_address not in text:
                return False
            return True

    if store_names and any(name and name in text for name in store_names):
        if wants_address and store_addresses and not any(address and address in text for address in store_addresses):
            return False
        return True

    if generic_store_query and store_names and any(name for name in store_names):
        return True

    if wants_status and (status_summary or business_hours):
        if status_summary and status_summary in text:
            return True
        if business_hours and business_hours in text:
            return True
        if any(term in text for term in ["营业时间", "营业安排", "先确认一下", "去之前可以先确认一下"]):
            return True

    if wants_address and stores:
        if any(address and address in text for address in store_addresses):
            return True

    return False
