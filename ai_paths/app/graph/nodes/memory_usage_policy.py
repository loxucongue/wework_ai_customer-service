from __future__ import annotations

from app.graph.planner.runtime_plan import planner_task_views
from app.graph.signals.general import is_low_information_content
from app.graph.state import AgentState


CONTINUATION_TERMS = [
    "刚刚",
    "刚才",
    "前面",
    "之前",
    "上次",
    "继续",
    "接着",
    "还是",
    "那个",
    "这个",
    "你说的",
    "刚说的",
    "上一条",
    "前一条",
]

GENERIC_OPENING_TERMS = [
    "了解一下",
    "咨询一下",
    "问一下",
    "介绍一下",
    "有什么项目",
    "项目有哪些",
    "看看项目",
    "你好",
    "在吗",
]

SPECIFIC_NEED_TERMS = [
    "斑",
    "痘",
    "痣",
    "毛孔",
    "暗沉",
    "色沉",
    "细纹",
    "松弛",
    "抗衰",
    "补水",
    "提亮",
    "黑眼圈",
    "眼袋",
    "价格",
    "多少钱",
    "预算",
    "门店",
    "地址",
    "预约",
    "活动",
    "优惠",
    "案例",
    "效果",
    "正规吗",
    "靠谱吗",
]

LOW_INFORMATION_TASK_TYPES = {"general_consult", "emotion_chat"}


def has_continuation_reference(content: str) -> bool:
    text = str(content or "")
    return any(term in text for term in CONTINUATION_TERMS)


def is_generic_opening_without_specific_need(content: str) -> bool:
    text = str(content or "").strip()
    if not text or has_continuation_reference(text):
        return False
    if not any(term in text for term in GENERIC_OPENING_TERMS):
        return False
    return not any(term in text for term in SPECIFIC_NEED_TERMS)


def should_suppress_profile_memory_for_reply(state: AgentState) -> bool:
    """低信息量开场轮次，不主动带出旧画像、旧项目和旧痛点。"""
    content = str(state.get("normalized_content") or "").strip()
    if has_continuation_reference(content):
        return False
    if is_low_information_content(content):
        return True
    if is_generic_opening_without_specific_need(content):
        return True

    task_views = planner_task_views(state)
    if not task_views:
        return False
    task_types = {
        str(view.get("type") or "").strip()
        for view in task_views
        if isinstance(view, dict)
    }
    return bool(task_types) and task_types <= LOW_INFORMATION_TASK_TYPES


def memory_usage_policy_for_reply(state: AgentState) -> dict[str, object]:
    suppress = should_suppress_profile_memory_for_reply(state)
    return {
        "active_profile_memory": not suppress,
        "reason": (
            "current_turn_low_information_or_generic_opening"
            if suppress
            else "current_turn_allows_contextual_memory"
        ),
        "instruction": (
            "本轮是低信息量开场或简单承接，不要主动带出旧画像、旧项目、旧痛点或客户标签。"
            if suppress
            else "可以在不盖过当前问题的前提下，少量引用相关历史信息。"
        ),
    }


def order_session_state(state: AgentState) -> dict[str, object]:
    """提取本轮成交/到店链路的硬状态。

    这类信息不是软画像；即使低信息开场或清空画像测试，也应该给最终回复模型使用，
    避免客户已经给过城市、地标、门店或预约时间后又被重复追问。
    """
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    structured = _structured_facts(state)
    store_lookup_status = (
        structured.get("store_lookup_status")
        if isinstance(structured.get("store_lookup_status"), dict)
        else {}
    )
    recommended_store = (
        structured.get("recommended_store")
        if isinstance(structured.get("recommended_store"), dict)
        else {}
    )

    session: dict[str, object] = {}
    _put(
        session,
        "city",
        _pick(
            store_lookup_status.get("city"),
            request_context.get("city"),
            state.get("city"),
        ),
    )
    _put(
        session,
        "area_or_landmark",
        _pick(
            store_lookup_status.get("area_or_landmark"),
            store_lookup_status.get("location_preference"),
            request_context.get("area_or_landmark"),
            request_context.get("location_preference"),
            state.get("area_or_landmark"),
        ),
    )
    _put(
        session,
        "confirmed_store_name",
        _pick(
            request_context.get("confirmed_store_name"),
            state.get("confirmed_store_name"),
            recommended_store.get("name"),
        ),
    )
    _put(
        session,
        "confirmed_store_id",
        _pick(
            request_context.get("confirmed_store_id"),
            state.get("confirmed_store_id"),
            recommended_store.get("store_id"),
            recommended_store.get("id"),
        ),
    )
    _put(
        session,
        "visit_date",
        _pick(
            request_context.get("visit_date"),
            request_context.get("appointment_date"),
            state.get("visit_date"),
        ),
    )
    _put(
        session,
        "visit_time",
        _pick(
            request_context.get("visit_time"),
            request_context.get("appointment_time"),
            state.get("visit_time"),
        ),
    )
    _put(
        session,
        "appointment_order_id",
        _pick(
            request_context.get("order_id"),
            request_context.get("appointment_id"),
            state.get("appointment_order_id"),
        ),
    )
    offer_explained = _offer_already_explained(state)
    if offer_explained:
        session["offer_explained"] = True

    if session.get("appointment_order_id"):
        session["signup_state"] = "created_order"
    elif session.get("visit_date") or session.get("visit_time"):
        session["signup_state"] = "time_intent_known"
    elif session.get("confirmed_store_name") or session.get("confirmed_store_id"):
        session["signup_state"] = "store_matched"
    elif session.get("area_or_landmark"):
        session["signup_state"] = "area_known"
    elif session.get("city"):
        session["signup_state"] = "city_known"

    if session:
        session["next_slot"] = _next_order_slot(session)
        session["deposit_ready_candidate"] = bool(
            session.get("confirmed_store_name")
            or session.get("confirmed_store_id")
        ) and bool(offer_explained)
        session["usage_note"] = "这是本轮成交/到店链路硬状态，不属于软画像；不要重复追问已存在字段。"
    return session


def _structured_facts(state: AgentState) -> dict[str, object]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts")
    return structured if isinstance(structured, dict) else {}


def _offer_already_explained(state: AgentState) -> bool:
    texts: list[str] = []
    for item in state.get("conversation_history") or []:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role not in {"assistant", "staff", "bot"}:
                continue
            content = item.get("content")
            texts.append(str(content.get("text") if isinstance(content, dict) else content or ""))
        else:
            raw = str(item or "")
            if raw.startswith(("小贝：", "客服：", "AI回复：")):
                texts.append(raw.split("：", 1)[-1])
    combined = "\n".join(texts[-8:])
    return any(term in combined for term in ("268", "预约金", "做付258", "周年庆", "活动价", "报名10"))


def _next_order_slot(session: dict[str, object]) -> str:
    if not session.get("city"):
        return "city"
    if not session.get("area_or_landmark"):
        return "area_or_landmark"
    if not (session.get("confirmed_store_name") or session.get("confirmed_store_id")):
        return "confirmed_store"
    if not session.get("offer_explained"):
        return "offer_explained"
    if not (session.get("visit_date") or session.get("visit_time")):
        return "visit_time"
    if not session.get("appointment_order_id"):
        return "signup_state"
    return "confirmed"


def _pick(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _put(target: dict[str, object], key: str, value: object) -> None:
    text = str(value or "").strip()
    if text:
        target[key] = text
