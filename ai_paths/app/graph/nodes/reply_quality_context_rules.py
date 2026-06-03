from __future__ import annotations

from typing import Any

from app.graph import task_state
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.state import AgentState

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


def case_request_invented_specific_context(state: AgentState, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if not case_request_lacks_specific_context(state, known_visible_concerns_from_state=callbacks.known_visible_concerns_from_state):
        return False
    return any(term in text for term in ["点状斑", "肤色改善", "淡斑", "修护", "术后修护", "斑点深浅"])


def contains_forbidden_customer_claims(text: str) -> bool:
    return any(term in text for term in HARD_FORBIDDEN_TERMS + SOFT_EFFECT_PROMISE_TERMS)


def claims_image_without_image(state: AgentState, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if callbacks.has_actual_image_context(state):
        return False
    return any(term in text for term in ["你发的图片", "您发的图片", "从你发的图片", "从您发的图片", "结合照片", "照片里", "发的照片"])


def contains_unsupported_diagnosis(text: str) -> bool:
    return any(term in text for term in DIAGNOSIS_TERMS)


def injects_unavailable_trust_assets(text: str, intents: set[str]) -> bool:
    if "trust_issue" in intents:
        return False
    return any(term in text for term in ["医疗机构执业许可证", "资质图片", "正规资质", "执业许可证"])


def injects_store_info_without_store_intent(text: str, intents: set[str], content: str, callbacks: ReplyQualityCallbacks) -> bool:
    if "store_inquiry" in intents or callbacks.is_strong_multi_recap_request(content):
        return False
    return any(term in text for term in ["地址是：", "停车场", "直接导航到"])


def continues_old_appointment_when_not_requested(state: AgentState, text: str, intents: set[str]) -> bool:
    if intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
        return False
    if task_state.is_active_appointment_task(state):
        return False
    return contains_appointment_push(text)


def violates_multi_recap_boundary(content: str, text: str, callbacks: ReplyQualityCallbacks) -> bool:
    if not callbacks.is_strong_multi_recap_request(content):
        return False
    if any(term in text for term in ["可约时段", "可预约时间", "帮你查时间", "哪个时间更方便", "继续确认接待"]):
        return True
    if not callbacks.asks_other_store_options(content) and any(term in text for term in ["其他门店", "更多门店", "你看哪家更方便"]):
        return True
    return False


def violates_pre_visit_makeup_answer(content: str, text: str) -> bool:
    if "化妆" not in content:
        return False
    if "空腹" in text:
        return True
    return any(term in text for term in ["不建议化妆", "建议不化妆", "不要化妆", "不能化妆"]) and "淡妆" not in text and "素颜" not in text


def contains_store_or_appointment_push(text: str) -> bool:
    return any(term in text for term in ["哪家更方便", "最近可约时段", "想约哪一天", "可预约的时间段", "帮你查当天", "最近有空档"])


def contains_appointment_push(text: str) -> bool:
    return any(term in text for term in APPOINTMENT_PUSH_TERMS)
