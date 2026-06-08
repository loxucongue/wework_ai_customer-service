from __future__ import annotations

from typing import Any

from app.graph.nodes.memory_usage_policy import should_suppress_profile_memory_for_reply
from app.graph.nodes.reply_brief_business import (
    apply_case_process_ad_dispute_context,
    apply_direct_reply_context,
    apply_image_context,
    apply_multi_recap_context,
    apply_pre_visit_context,
    apply_price_context,
    apply_price_recap_and_memory_context,
    apply_project_context,
    apply_trust_and_misc_context,
    suggested_followup_for_brief,
)
from app.graph.nodes.reply_brief_store_appointment import (
    apply_appointment_context,
    apply_store_context,
    apply_store_recap_context,
)
from app.graph.nodes.reply_brief_types import ReplyBriefCallbacks
from app.graph.state import AgentState


def reply_brief_for_model(state: AgentState, callbacks: ReplyBriefCallbacks) -> dict[str, Any]:
    """Build the factual brief consumed by the final reply model."""
    content = state.get("normalized_content") or ""
    intent_set = {item.get("intent") for item in state.get("intents", [])}
    brief: dict[str, Any] = {
        "customer_message": content,
        "intents": sorted(str(intent) for intent in intent_set if intent),
        "sales_strategy": _sales_strategy_for_brief(state),
        "must_answer": [],
        "available_facts": {},
        "answer_first": [],
        "known_facts": [],
        "do_not_say": [
            "系统查询到",
            "知识库显示",
            "我是AI",
            "转人工",
            "包效果",
            "一定有效",
            "我把营业执照发你",
            "发送营业执照",
            "营业执照发你",
        ],
        "follow_up": "",
    }
    if should_suppress_profile_memory_for_reply(state):
        brief["must_answer"].append(
            "本轮是问候、低信息承接或泛项目开场；不要主动带出旧画像、旧项目、旧痛点或客户昵称。"
        )
        brief["do_not_say"].extend(["你之前提到", "你前面提到", "比如斑点", "斑点、肤色不均", "点状斑"])
        brief["follow_up"] = "如果客户还没说需求，优先轻量收一个城市、区域或当前位置，好先帮客户找最近门店；只有客户已经明确说了需求，才顺着需求往下接。"
    _apply_sales_strategy_context(brief)

    apply_multi_recap_context(state, brief, callbacks)
    apply_direct_reply_context(state, brief, callbacks)
    apply_image_context(state, brief, callbacks)
    apply_pre_visit_context(state, brief, callbacks)
    apply_price_context(state, brief, callbacks)
    apply_project_context(state, brief, callbacks)
    apply_case_process_ad_dispute_context(state, brief, callbacks)
    apply_store_context(state, brief, callbacks)
    apply_store_recap_context(state, brief, callbacks)
    apply_appointment_context(state, brief, callbacks)
    apply_trust_and_misc_context(state, brief, callbacks)
    apply_price_recap_and_memory_context(state, brief, callbacks)

    brief["must_answer"] = callbacks.dedupe_strings([str(item).strip() for item in brief["must_answer"] if str(item).strip()])[:8]
    brief["answer_first"] = callbacks.dedupe_strings([str(item).strip() for item in brief["answer_first"] if str(item).strip()])[:3]
    brief["known_facts"] = callbacks.dedupe_strings([str(item).strip() for item in brief["known_facts"] if str(item).strip()])[:10]
    if not brief["follow_up"]:
        brief["follow_up"] = suggested_followup_for_brief(state, callbacks)
    return brief


def _apply_sales_strategy_context(brief: dict[str, Any]) -> None:
    strategy = brief.get("sales_strategy") if isinstance(brief.get("sales_strategy"), dict) else {}
    if not strategy:
        return
    stage = str(strategy.get("sales_stage") or "")
    ask_policy = str(strategy.get("ask_policy") or "")
    next_action = str(strategy.get("next_best_action") or "").strip()
    if ask_policy == "no_ask":
        brief["must_answer"].append("本轮销售节奏要求不追问，回答当前问题后收住。")
        brief["do_not_say"].extend(["你更想", "你方便说下", "你看哪家", "哪天方便", "什么时间方便"])
    elif ask_policy == "ask_one":
        brief["must_answer"].append("本轮最多只问一个必要问题。")
    elif ask_policy == "collect_required":
        missing = strategy.get("missing_slots") if isinstance(strategy.get("missing_slots"), list) else []
        if missing:
            brief["must_answer"].append(f"本轮只收集一个缺失预约信息：{missing[0]}。")
    if stage == "store_paving":
        brief["must_answer"].append("门店铺垫阶段：已知城市、区域或位置时要主动推荐最方便门店，不要只反问客户选哪家。")
    elif stage == "opening_intro":
        known_slots = strategy.get("known_slots") if isinstance(strategy.get("known_slots"), dict) else {}
        has_need = bool(str(known_slots.get("need") or "").strip() or str(known_slots.get("project_direction") or "").strip())
        has_city = bool(str(known_slots.get("city") or "").strip())
        if not has_need and not has_city:
            brief["must_answer"].append("纯开场且客户还没说需求时，优先收一个城市、区域或当前位置，好先帮客户找最近门店；不要先问项目名。")
            brief["answer_first"].append("先轻量承接，再只问客户在哪个城市或附近区域。")
            brief["do_not_say"].extend(["想了解哪方面的改善", "想做什么项目", "肤质、补水、抗衰", "轮廓提升"])
        elif has_need:
            brief["must_answer"].append("客户一旦说出需求，就先给改善方向和承接感，不要退回泛破冰。")
    elif stage == "quote":
        brief["must_answer"].append("报价阶段：先回答价格或收费口径，再补活动/预约登记边界。")
    elif stage == "close_order":
        brief["must_answer"].append("预约引导阶段：客户已有意向时，可以轻推登记活动名额或确认到店时间。")
    elif stage == "collect_info":
        brief["must_answer"].append("预约信息确认阶段：复用已知门店、日期、时间、姓名电话，只补缺失项。")
    elif stage == "handoff_at_store":
        brief["must_answer"].append("客户已到店，回复应交给门店专业同事接待，不再继续项目咨询。")
    if next_action:
        brief["known_facts"].append(f"本轮下一步：{next_action}")


def _sales_strategy_for_brief(state: AgentState) -> dict[str, Any]:
    strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    if not strategy:
        plan = state.get("action_plan") if isinstance(state.get("action_plan"), dict) else {}
        strategy = plan.get("sales_strategy") if isinstance(plan.get("sales_strategy"), dict) else {}
    if not isinstance(strategy, dict):
        return {}
    return {
        "sales_stage": strategy.get("sales_stage", ""),
        "stage_label": strategy.get("stage_label", ""),
        "ask_policy": strategy.get("ask_policy", ""),
        "known_slots": strategy.get("known_slots", {}),
        "missing_slots": strategy.get("missing_slots", []),
        "next_best_action": strategy.get("next_best_action", ""),
        "push_goal": strategy.get("push_goal", ""),
        "reply_rhythm": strategy.get("reply_rhythm", ""),
    }
