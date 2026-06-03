from __future__ import annotations

import re
from typing import Any

from app.graph import planner_helpers, reply_filters, task_state
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.reply_quality_appointment import claims_unavailable_preferred_time_available
from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES


HARD_FORBIDDEN_TERMS = [
    "预留",
    "留名额",
    "锁位",
    "锁定这个时段",
    "帮你锁位",
    "电话联系",
    "电话确认",
    "安排医生",
    "专业医生",
    "持证医生",
    "医生面诊",
    "卫生许可证",
    "执业许可证",
    "营业执照发你",
    "把营业执照发你",
    "发送营业执照",
    "包效果",
    "一定有效",
    "肯定有效",
    "根治",
    "100%见效",
    "一次见效",
    "不反弹",
    "真人客服",
    "我是人工",
    "不是AI",
    "不是机器人",
]

SOFT_EFFECT_PROMISE_TERMS = [
    "做完就能看到明显变化",
    "做完后会有明显变化",
    "效果非常好",
    "一次效果就很理想",
    "大部分顾客一次效果就很理想",
]

DIAGNOSIS_TERMS = ["雀斑", "晒斑", "黄褐斑", "皮炎", "感染", "玫瑰痤疮", "毛囊炎"]

APPOINTMENT_PUSH_TERMS = [
    "面诊名额",
    "帮你约",
    "确认具体时间",
    "什么时候方便",
    "近期可约时段",
    "可预约时间",
    "哪个时间更方便",
    "安排到店咨询",
    "到店进一步确认",
    "面诊确认",
]


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
    if _case_request_invented_specific_context(state, text, callbacks):
        return True
    if _contains_forbidden_customer_claims(text):
        return True
    if _claims_image_without_image(state, text, callbacks):
        return True
    if _contains_unsupported_diagnosis(text):
        return True
    if _injects_unavailable_trust_assets(text, intents):
        return True
    if _injects_store_info_without_store_intent(text, intents, content, callbacks):
        return True
    if _continues_old_appointment_when_not_requested(state, text, intents):
        return True
    if _violates_multi_recap_boundary(content, text, callbacks):
        return True
    if _violates_pre_visit_makeup_answer(content, text):
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
    if "trust_issue" in intents and "store_inquiry" not in intents and _contains_store_or_appointment_push(text):
        return True
    if not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"} and not task_state.is_active_appointment_task(state):
        if _contains_appointment_push(text):
            return True
    if not intents & {"price_inquiry", "campaign_inquiry", "competitor_compare"}:
        if any(term in text for term in ["新客体验价", "活动价", "日常单次"]):
            return True
    if "case_request" in intents and _case_reply_misses_case_intent(state, text, callbacks):
        return True
    if "project_process" in intents and not any(term in text for term in ["流程", "步骤", "操作", "清洁", "检测", "评估", "分钟", "时长", "多久"]):
        return True
    if "ad_price_check" in intents and _ad_price_reply_invalid(state, text, content, callbacks):
        return True
    if claims_unavailable_preferred_time_available(state, text, callbacks):
        return True
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        available = state.get("tool_results", {}).get("available_time") or {}
        if isinstance(available, dict) and callbacks.available_slot_list(available.get("slots") or {}) and re.search(r"\d{1,2}:\d{2}", text):
            return False
    if callbacks.too_similar_to_recent_assistant_reply(state, text):
        return True
    return False


def _case_request_invented_specific_context(state: AgentState, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if not case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state):
        return False
    return any(term in text for term in ["点状斑", "肤色改善", "淡斑", "修护", "术后修护", "斑点深浅"])


def _contains_forbidden_customer_claims(text: str) -> bool:
    return any(term in text for term in HARD_FORBIDDEN_TERMS + SOFT_EFFECT_PROMISE_TERMS)


def _claims_image_without_image(state: AgentState, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if callbacks.has_actual_image_context(state):
        return False
    return any(term in text for term in ["你发的图片", "您发的图片", "从你发的图片", "从您发的图片", "结合照片", "照片里", "发的照片"])


def _contains_unsupported_diagnosis(text: str) -> bool:
    return any(term in text for term in DIAGNOSIS_TERMS)


def _injects_unavailable_trust_assets(text: str, intents: set[str]) -> bool:
    if "trust_issue" in intents:
        return False
    return any(term in text for term in ["医疗机构执业许可证", "资质图片", "正规资质", "执业许可证"])


def _injects_store_info_without_store_intent(text: str, intents: set[str], content: str, callbacks: ReplyQualityCallbacks) -> bool:
    if "store_inquiry" in intents or callbacks.is_strong_multi_recap_request(content):
        return False
    return any(term in text for term in ["地址是：", "停车场", "直接导航到"])


def _continues_old_appointment_when_not_requested(state: AgentState, text: str, intents: set[str]) -> bool:
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        return False
    if task_state.is_active_appointment_task(state):
        return False
    return _contains_appointment_push(text)


def _violates_multi_recap_boundary(content: str, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if not callbacks.is_strong_multi_recap_request(content):
        return False
    if any(term in text for term in ["可约时段", "可预约时间", "帮你查时间", "哪个时间更方便", "继续确认接待"]):
        return True
    if not callbacks.asks_other_store_options(content) and any(term in text for term in ["其他门店", "更多门店", "你看哪家更方便"]):
        return True
    return False


def _violates_pre_visit_makeup_answer(content: str, text: str) -> bool:
    if "化妆" not in content:
        return False
    if "空腹" in text:
        return True
    return any(term in text for term in ["不建议化妆", "建议不化妆", "不要化妆", "不能化妆"]) and "淡妆" not in text and "素颜" not in text


def _store_reply_conflicts_with_query(state: AgentState, text: str, content: str, callbacks: ReplyQualityCallbacks) -> bool:
    city = callbacks.extract_city(content)
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    stores = lookup.get("stores", []) if isinstance(lookup, dict) else []
    if city and city not in text:
        return True
    if city and not stores and any(other_city in text for other_city in CITY_NAMES if other_city != city):
        return True
    if "停车" in content and "停车场" not in text:
        return True
    if "地址" in content and "地址" not in text and city not in text:
        return True
    return False


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
    if (
        any(term in need_text for term in ["斑", "点状", "色沉", "肤色不均", "暗沉"])
        and not any(term in need_text for term in ["敏感", "泛红", "屏障", "刺痛", "干痒", "红血丝"])
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


def _contains_store_or_appointment_push(text: str) -> bool:
    return any(term in text for term in ["哪家更方便", "最近可约时段", "想约哪一天", "可预约的时间段", "帮你查当天", "最近有空档"])


def _contains_appointment_push(text: str) -> bool:
    return any(term in text for term in APPOINTMENT_PUSH_TERMS)


def _case_reply_misses_case_intent(state: AgentState, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if not any(term in text for term in ["案例", "前后", "对比", "改善参考", "同类"]):
        return True
    if any(term in text for term in ["案例价格", "哪家门店可以看案例"]) and not any(term in text for term in ["同类", "祛斑", "淡斑", "改善"]):
        return True
    if any(term in text for term in ["第1次", "第一次", "第3次", "第三次", "几天后", "几周后", "顾客反馈"]):
        return not callbacks.tool_results_contain(state, ["真实案例", "案例图", "前后对比"])
    return False


def _ad_price_reply_invalid(state: AgentState, text: str, content: str, callbacks: ReplyQualityCallbacks) -> bool:
    digits = callbacks.extract_price_digits(content)
    if digits and not any(digit in text for digit in digits[:2]):
        return True
    if not any(term in text for term in ["广告", "活动", "预约金", "尾款", "包含", "另收费", "隐形"]):
        return True
    project = callbacks.canonical_price_project(callbacks.contextual_price_project(state) or callbacks.extract_project(content))
    if callbacks.ad_price_without_explicit_project(state, project):
        return any(term in text for term in ["确实有", "真实有", "目前有这个活动", "可以按这个价格", "就是这个活动"])
    return False
