from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.graph import planner_helpers, task_state
from app.graph.customer_need_questions import customer_friendly_type_question
from app.graph.nodes.price_question_frames import build_price_question_frame
from app.graph.nodes.intent_signals import is_broad_ad_intro
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
    price_frame = build_price_question_frame(content, facts.get("customer_seen_price") if isinstance(facts.get("customer_seen_price"), list) else None)
    if (
        "price_inquiry" in intents
        and not prices
        and any(term in content for term in ["多少钱", "价格", "预算", "费用"])
    ):
        if price_frame:
            hard_instruction = (
                "客户当前在追问价格口径，但没有可直接引用的明确价格。"
                f"本轮价格追问类型是：{price_frame.name}。"
                "先回答当前价格口径问题本身，再说明还缺少可直接引用的真实价格事实。"
                "禁止编造数字、价格区间、档位、费用明细，也不要重新泛问客户想改善什么。"
            )
        else:
            hard_instruction = (
                "客户当前在问价格，但fact_brief里没有可直接引用的明确价格。"
                "必须先回答暂未查到可直接引用的明确价格，不能乱报数字；"
                "可以简短说明当前更偏哪个改善方向，并给低预算核价方式：先核对该方向里的基础单次、当前活动是否可用，不先推组合配置；"
                "禁止追问，禁止说具体金额、价格区间、档位、费用明细或让客户继续补斑点信息。"
            )
    if any(term in content for term in ["需要带什么", "要带什么", "带什么", "能不能化妆", "可以化妆", "要不要空腹", "需要空腹"]):
        hard_instruction = (
            "客户当前询问到店前准备事项。必须直接回答：建议素颜或淡妆，避免浓妆、假睫毛和刺激性护肤；"
            "一般皮肤咨询不需要空腹。禁止改问项目、门店、城市或预约时间。"
        )
    if _effect_guarantee_question(content):
        hard_instruction = (
            "客户在问效果是否有保障。先给确定感，再补真实边界。"
            "可以表达基础改善和服务跟进这类确定性，但不要承诺人人同样程度或一次固定变化。"
            "第一句不能以不能保证、不能承诺、因人而异、效果不能承诺完全消失开头。"
            "禁止出现当前消息没有明确提到的具体项目名、门店名、顾客反馈、案例节点、持证、备案、资质、所有项目或定金可退。"
        )
    if "trust_issue" in intents and planner_helpers._is_soft_fee_concern(content):
        hard_instruction = (
            "客户当前担心机构是否正规以及到店会不会乱收费。必须直接回答收费透明顾虑："
            "到店前会把项目、价格、包含项和是否需要另加项目逐项说清楚，客户有疑问可以逐项核对，确认清楚后再决定。"
            "可以简短承接正规性顾虑为“资料和项目口径都可以现场核验”，但不要编造具体资质、备案、证照、老师资历或所有门店背书。"
            "禁止说不会乱收费、绝对没有其他收费、没有隐形消费、明码标价、正规备案、资质认证、所有项目、所有门店。"
            "禁止追问城市、门店、预约时间、项目方向或斑点情况；禁止主动推进预约。"
            "这一轮默认只输出1条text，不要拆成两条，也不要在结尾追加问句。"
        )
    case_asset_image_url = str(facts.get("case_asset_image_url") or "").strip()
    type_question = str(facts.get("customer_friendly_type_question") or "").strip() or customer_friendly_type_question(
        content,
        visible_concerns=facts.get("visible_concerns") if isinstance(facts.get("visible_concerns"), list) else [],
    )
    if (
        not hard_instruction
        and case_asset_image_url
        and ask_one_case_followup_needed(state, content, intents, facts)
    ):
        hard_instruction = (
            "本轮已有真实同类案例图，客户又是在做宽需求了解。"
            "文字不能只停在案例说明，需要顺手补一个客户听得懂的类型问题。"
            f"优先使用这个问题：{type_question or '你更像零散小点、成片颜色重一点，还是整体肤色暗沉不均？'}"
            "不要追问专业项目名，也不要改问城市、门店、价格。"
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
            "sales_strategy": _sales_strategy_for_payload(state),
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
    opening = state.get("tool_results", {}).get("appointment_opening") or {}
    if isinstance(opening, dict) and opening.get("status"):
        return True
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


def should_use_store_fact_reply(state: AgentState, callbacks: ReplyPayloadCallbacks) -> bool:
    if callbacks.should_suspend_active_task(state, state.get("active_task", {}), state.get("intents", [])):
        return False
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if intents != {"store_inquiry"}:
        return False
    brief = callbacks.reply_brief(state)
    facts = brief.get("available_facts", {}) if isinstance(brief.get("available_facts"), dict) else {}
    stores = facts.get("stores") if isinstance(facts.get("stores"), list) else []
    recommended = facts.get("recommended_store") if isinstance(facts.get("recommended_store"), dict) else {}
    content = str(state.get("normalized_content") or "")
    if stores or recommended:
        return True
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    if isinstance(lookup, dict) and (lookup.get("missing") or lookup.get("platform_error")):
        return True
    return any(term in content for term in ["门店", "地址", "导航", "停车", "营业", "关门", "闭店", "停业"])


def store_reply_payload_for_model(state: AgentState, callbacks: ReplyPayloadCallbacks) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "")
    brief = callbacks.reply_brief(state)
    facts = brief.get("available_facts", {}) if isinstance(brief.get("available_facts"), dict) else {}
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    stores = facts.get("stores") if isinstance(facts.get("stores"), list) else []
    recommended = facts.get("recommended_store") if isinstance(facts.get("recommended_store"), dict) else {}
    generic_store_query = any(term in content for term in ["门店在哪里", "门店在哪", "哪里有店", "有哪些门店", "哪里有门店"])
    wants_names_only = any(term in content for term in ["门店名字", "店名", "叫什么店", "哪几家店", "有哪些店"])
    wants_address_pack = any(term in content for term in ["地址", "导航", "停车"])
    has_location_anchor = any(term in content for term in ["机场", "高铁", "火车站", "地铁", "商圈", "附近", "近一点", "最近"])
    city_only_multiple_stores = _is_city_only_store_reply(
        content=content,
        city=str(lookup.get("city") or "").strip() if isinstance(lookup, dict) else "",
        stores=stores,
        recommended=recommended,
        generic_store_query=generic_store_query,
        wants_names_only=wants_names_only,
        wants_address_pack=wants_address_pack,
        has_location_anchor=has_location_anchor,
    )
    location_hint_options = _store_location_hint_options(stores) if city_only_multiple_stores else []
    if city_only_multiple_stores:
        preferred_shape = "refine_location"
    elif wants_address_pack:
        preferred_shape = "address_pack"
    elif recommended and has_location_anchor:
        preferred_shape = "recommend_one"
    elif wants_names_only or generic_store_query:
        preferred_shape = "list_and_stop"
    else:
        preferred_shape = "recommend_one" if recommended else "list_and_stop"
    stores_payload = [] if city_only_multiple_stores else stores[:3]
    return {
        "content": content,
        "sales_strategy": _sales_strategy_for_payload(state),
        "recent_assistant_replies": callbacks.recent_assistant_replies(state, 3),
        "answer_first": brief.get("answer_first", [])[:3],
        "must_answer": brief.get("must_answer", [])[:8],
        "do_not_say": brief.get("do_not_say", [])[:24],
        "follow_up": brief.get("follow_up", ""),
        "store_lookup": {
            "city": str(lookup.get("city") or "").strip() if isinstance(lookup, dict) else "",
            "requested_store": str(lookup.get("requested_store") or "").strip() if isinstance(lookup, dict) else "",
            "location_preference": str(lookup.get("location_preference") or "").strip() if isinstance(lookup, dict) else "",
            "missing": list(lookup.get("missing") or []) if isinstance(lookup, dict) else [],
            "platform_error": str(lookup.get("platform_error") or "").strip() if isinstance(lookup, dict) else "",
            "source": str(lookup.get("source") or "").strip() if isinstance(lookup, dict) else "",
        },
        "stores": stores_payload,
        "store_count": len(stores),
        "recommended_store": recommended,
        "query_type": {
            "generic_store_query": generic_store_query,
            "wants_names_only": wants_names_only,
            "wants_address_pack": wants_address_pack,
            "has_location_anchor": has_location_anchor,
            "city_only_multiple_stores": city_only_multiple_stores,
        },
        "location_hint_options": location_hint_options[:3],
        "preferred_reply_shape": preferred_shape,
        "reply_goal": "直接回答门店、地址、导航、停车、营业状态或最近门店推荐问题；不发散到项目咨询、价格咨询或预约确认。",
    }


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
    opening = state.get("tool_results", {}).get("appointment_opening") or {}
    action = state.get("tool_results", {}).get("appointment_action") or {}
    opening_fact = facts.get("appointment_opening") if isinstance(facts.get("appointment_opening"), dict) else {}
    action_fact = facts.get("appointment_action") if isinstance(facts.get("appointment_action"), dict) else {}
    if isinstance(opening, dict) and opening and not opening_fact:
        opening_facts = opening.get("facts") if isinstance(opening.get("facts"), dict) else {}
        opening_fact = {
            "status": opening.get("status") or "",
            "order_id": opening.get("order_id") or "",
            "store_id": opening_facts.get("store_id") or "",
            "store_name": opening_facts.get("store_name") or "",
            "appointment_date": opening_facts.get("date") or "",
            "appointment_time": opening_facts.get("time") or "",
            "prepay": opening_facts.get("prepay") or "",
            "missing": opening.get("missing") or [],
            "error": opening.get("error") or "",
        }
    if isinstance(action, dict) and action and not action_fact:
        action_facts = action.get("facts") if isinstance(action.get("facts"), dict) else {}
        action_fact = {
            "operation": action.get("operation") or "",
            "status": action.get("status") or "",
            "order_id": action_facts.get("order_id") or "",
            "store_id": action_facts.get("store_id") or "",
            "store_name": action_facts.get("store_name") or "",
            "appointment_date": action_facts.get("date") or "",
            "appointment_time": action_facts.get("time") or "",
            "available_time_slots": action_facts.get("available_time_slots") or [],
            "preferred_time_available": action_facts.get("preferred_time_available"),
            "missing": action.get("missing") or [],
            "error": action.get("error") or "",
        }
    must_not_say = ["已预约成功", "约好了", "已经确认", "帮你预留", "锁位"]
    if preferred_time_available is False:
        must_not_say.extend(["可以直接到店", "有空位", "可约"])
    return {
        "content": content,
        "sales_strategy": _sales_strategy_for_payload(state),
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
        "appointment_opening": opening_fact,
        "appointment_action": action_fact,
        "appointment_confirmed": False,
        "direct_arrival_question": is_direct_arrival_question(content),
        "must_not_say": must_not_say,
        "reply_goal": "用客户能听懂的话回答当前预约确认问题，事实不足时让门店同事核对，不要切到项目咨询。",
    }


def is_direct_arrival_question(content: str) -> bool:
    return any(term in content for term in ["直接到店", "直接去", "直接过去", "到店就可以", "直接来", "到店可以"])


def _sales_strategy_for_payload(state: AgentState) -> dict[str, Any]:
    strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    if not strategy:
        plan = state.get("action_plan") if isinstance(state.get("action_plan"), dict) else {}
        strategy = plan.get("sales_strategy") if isinstance(plan.get("sales_strategy"), dict) else {}
    if not isinstance(strategy, dict):
        return {}
    return {
        "sales_stage": strategy.get("sales_stage", ""),
        "stage_label": strategy.get("stage_label", ""),
        "known_slots": strategy.get("known_slots", {}),
        "missing_slots": strategy.get("missing_slots", []),
        "ask_policy": strategy.get("ask_policy", ""),
        "next_best_action": strategy.get("next_best_action", ""),
        "reply_rhythm": strategy.get("reply_rhythm", ""),
    }


def _effect_guarantee_question(content: str) -> bool:
    return any(
        term in content
        for term in [
            "效果有保障",
            "效果保障",
            "效果能保证",
            "能保证吗",
            "有保障吗",
            "保障效果",
            "保证效果",
            "有效果吗",
            "会不会反弹",
            "反弹",
            "返弹",
            "反复",
            "又回来",
            "怕反弹",
            "担心反弹",
            "能维持多久",
            "维持多久",
            "保持多久",
            "能保持多久",
        ]
    )


def ask_one_case_followup_needed(
    state: AgentState,
    content: str,
    intents: set[str],
    facts: dict[str, Any],
) -> bool:
    sales_strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    ask_policy = str(sales_strategy.get("ask_policy") or "")
    if ask_policy != "ask_one":
        return False
    if not (is_broad_ad_intro(content) or any(term in content for term in ["祛斑", "淡斑", "黑色素", "抗衰", "毛孔", "暗沉"])):
        return False
    if not (intents & {"project_inquiry", "image_inquiry", "case_request"}):
        return False
    visible = facts.get("visible_concerns") if isinstance(facts.get("visible_concerns"), list) else []
    if any(any(term in str(item) for term in ["点状", "片状", "成片", "暗沉"]) for item in visible):
        return False
    return True


def _is_city_only_store_reply(
    *,
    content: str,
    city: str,
    stores: list[dict[str, Any]],
    recommended: dict[str, Any],
    generic_store_query: bool,
    wants_names_only: bool,
    wants_address_pack: bool,
    has_location_anchor: bool,
) -> bool:
    if not city or len(stores) <= 1 or recommended:
        return False
    if generic_store_query or wants_names_only or wants_address_pack or has_location_anchor:
        return False
    text = str(content or "").strip()
    if not text:
        return False
    for prefix in ["我在", "人在", "目前在", "现在在", "住在"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    for suffix in ["这边", "这儿", "附近"]:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    text = text.strip(" ，。！？?~～")
    return text in {city, f"{city}市"}


def _store_location_hint_options(stores: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []
    for store in stores[:5]:
        if not isinstance(store, dict):
            continue
        for value in [str(store.get("address") or "").strip(), str(store.get("name") or "").strip()]:
            hint = _extract_store_area_hint(value)
            if hint and hint not in hints:
                hints.append(hint)
        if len(hints) >= 3:
            break
    return hints


def _extract_store_area_hint(text: str) -> str:
    import re

    value = str(text or "").strip()
    if not value:
        return ""
    matched = re.search(r"(?:[\u4e00-\u9fa5]{2,8}市)?([\u4e00-\u9fa5]{2,8}区)", value)
    if matched:
        return matched.group(1)
    matched = re.search(r"(?:[\u4e00-\u9fa5]{2,8}市)?([\u4e00-\u9fa5]{2,8}(?:机场|火车站|高铁站|商圈))", value)
    if matched:
        return matched.group(1)
    return ""
