from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph import planner_helpers, task_state
from app.graph.state import AgentState


@dataclass(frozen=True)
class ReplyPayloadCallbacks:
    available_slot_list: Callable[[Any], list[str]]
    recent_assistant_replies: Callable[[AgentState, int], list[str]]
    reply_brief: Callable[[AgentState], dict[str, Any]]
    should_suspend_active_task: Callable[[AgentState, dict[str, Any] | None, list[dict[str, Any]] | None], bool]


def reply_forced_payload_for_model(state: AgentState, callbacks: ReplyPayloadCallbacks) -> dict[str, Any]:
    brief = callbacks.reply_brief(state)
    facts = brief.get("available_facts", {}) if isinstance(brief.get("available_facts"), dict) else {}
    hard_instruction = ""
    content = str(state.get("normalized_content") or "")
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    prices = facts.get("prices") if isinstance(facts.get("prices"), list) else []
    if (
        "price_inquiry" in intents
        and not prices
        and any(term in content for term in ["多少钱", "价格", "预算", "费用"])
    ):
        hard_instruction = (
            "客户当前在问价格，但fact_brief里没有可直接引用的明确价格。"
            "必须先回答暂未查到可直接引用的明确价格，不能乱报数字；"
            "可以简短说明当前更偏哪个改善方向，但禁止追问，禁止说档位、费用明细、预算参考或让客户继续补斑点信息。"
        )
    if _effect_guarantee_question(content):
        hard_instruction = (
            "客户只是泛问效果是否有保障。必须回答不能做绝对效果承诺；"
            "改善会受皮肤基础、方案匹配、操作细节和后续护理影响；"
            "小贝可以先帮客户把这些关键点确认清楚。"
            "禁止出现当前消息没有明确提到的具体项目名、门店名、顾客反馈、案例节点、持证、备案、资质、所有项目或定金可退。"
        )
    preferred_time = str(facts.get("customer_preferred_time") or "").strip()
    slots = facts.get("available_time_slots") if isinstance(facts.get("available_time_slots"), list) else []
    direct_active_task = state.get("active_task") or {}
    direct_known_slots = (
        direct_active_task.get("known_slots")
        if isinstance(direct_active_task, dict) and isinstance(direct_active_task.get("known_slots"), dict)
        else {}
    )
    direct_preferred_time = str(direct_known_slots.get("visit_time") or "").strip()
    direct_available = state.get("tool_results", {}).get("available_time") or {}
    direct_slots = callbacks.available_slot_list(direct_available.get("slots") or {}) if isinstance(direct_available, dict) else []
    if not preferred_time and direct_preferred_time:
        preferred_time = direct_preferred_time
    if not slots and direct_slots:
        slots = direct_slots[:12]
    preferred_available = preferred_time in slots if preferred_time and slots else facts.get("preferred_time_available")
    if preferred_time and slots and preferred_available is False:
        direct_arrival_instruction = ""
        if is_direct_arrival_question(str(state.get("normalized_content") or "")):
            direct_arrival_instruction = "客户在问是否可以直接到店，必须明确不建议直接按该时间过去，或说明直接过去可能不太方便。"
        hard_instruction = (
            f"客户偏好的{preferred_time}不在可约时间列表内。必须回答{preferred_time}暂时没看到可约；"
            f"只能提供这些可选时间：{'、'.join(str(item) for item in slots[:8])}。"
            f"{direct_arrival_instruction}"
            "禁止说客户偏好的时间可以、可约、有空位、已预约或可以直接到店。"
        )
        facts = dict(facts)
        facts["customer_preferred_time"] = preferred_time
        facts["available_time_slots"] = slots[:12]
        facts["preferred_time_available"] = False
    return {
        "content": state.get("normalized_content"),
        "hard_instruction": hard_instruction,
        "fact_brief": {
            "answer_first": brief.get("answer_first", [])[:3],
            "must_answer": brief.get("must_answer", [])[:6],
            "available_facts": facts,
            "known_facts": brief.get("known_facts", [])[:8],
            "do_not_say": brief.get("do_not_say", [])[:24],
            "follow_up": brief.get("follow_up", ""),
        },
        "intents": [
            {"intent": item.get("intent"), "skill": item.get("skill")}
            for item in (state.get("intents") or [])[:3]
            if isinstance(item, dict)
        ],
        "recent_assistant_replies": callbacks.recent_assistant_replies(state, 2),
    }


def should_use_appointment_fact_reply(state: AgentState, callbacks: ReplyPayloadCallbacks) -> bool:
    content = state.get("normalized_content") or ""
    if callbacks.should_suspend_active_task(state, state.get("active_task", {}), state.get("intents", [])):
        return False
    if planner_helpers._has_fee_or_refund_dispute(content):
        return False
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    appointment_intents = {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
    if not (intents & appointment_intents or task_state.is_active_appointment_task(state)):
        return False
    available = state.get("tool_results", {}).get("available_time") or {}
    if not isinstance(available, dict):
        return False
    slot_list = callbacks.available_slot_list(available.get("slots") or {})
    active_task = state.get("active_task") or {}
    known_slots = active_task.get("known_slots") if isinstance(active_task, dict) and isinstance(active_task.get("known_slots"), dict) else {}
    preferred_time = str(known_slots.get("visit_time") or "").strip()
    if slot_list and preferred_time:
        return True
    if slot_list and is_direct_arrival_question(content):
        return True
    return bool(available.get("error"))


def appointment_reply_payload_for_model(state: AgentState, callbacks: ReplyPayloadCallbacks) -> dict[str, Any]:
    content = state.get("normalized_content") or ""
    brief = callbacks.reply_brief(state)
    facts = brief.get("available_facts", {}) if isinstance(brief.get("available_facts"), dict) else {}
    active_task = state.get("active_task") or {}
    known_slots = active_task.get("known_slots") if isinstance(active_task, dict) and isinstance(active_task.get("known_slots"), dict) else {}
    available = state.get("tool_results", {}).get("available_time") or {}
    slot_list = facts.get("available_time_slots") if isinstance(facts.get("available_time_slots"), list) else []
    if not slot_list and isinstance(available, dict):
        slot_list = callbacks.available_slot_list(available.get("slots") or {})
    preferred_time = str(facts.get("customer_preferred_time") or known_slots.get("visit_time") or "").strip()
    preferred_time_available = facts.get("preferred_time_available")
    if preferred_time and slot_list:
        preferred_time_available = preferred_time in slot_list
    selected_slots = slot_list[:6]
    must_not_say = ["已预约成功", "约好了", "已经确认", "帮你预留", "锁位"]
    if preferred_time_available is False:
        must_not_say.extend(["可以直接到店", "有空位", "可约"])
    return {
        "content": content,
        "conversation_history": state.get("conversation_history", [])[-4:],
        "recent_assistant_replies": callbacks.recent_assistant_replies(state, 3),
        "store_name": str(known_slots.get("store_name") or state.get("confirmed_store_name") or "").strip(),
        "city": str(known_slots.get("city") or state.get("detected_city") or "").strip(),
        "visit_date_label": str(known_slots.get("visit_date_label") or "").strip(),
        "visit_date_value": str(known_slots.get("visit_date_value") or "").strip(),
        "party_size": str(known_slots.get("party_size") or "").strip(),
        "preferred_time": preferred_time,
        "preferred_time_available": preferred_time_available,
        "available_time_slots": selected_slots,
        "available_time_error": str(available.get("error") or "").strip() if isinstance(available, dict) else "",
        "appointment_confirmed": False,
        "direct_arrival_question": is_direct_arrival_question(content),
        "must_not_say": must_not_say,
        "reply_goal": "用客户能听懂的话回答当前预约确认问题，事实不足时让门店同事核对，不要切到项目咨询。",
    }


def is_direct_arrival_question(content: str) -> bool:
    return any(term in content for term in ["直接到店", "直接去", "直接过去", "到店就可以", "直接来", "到店可以"])


def _effect_guarantee_question(content: str) -> bool:
    return any(term in content for term in ["效果有保障", "效果保障", "有保障吗", "保障效果", "保证效果", "有效果吗"])
