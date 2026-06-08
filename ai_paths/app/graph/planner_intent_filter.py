from __future__ import annotations

import re
from typing import Any

from app.graph.case_query_terms import build_case_query_candidates, expand_case_query_terms
from app.graph.appointment_identity_signals import appointment_identity_followup_value
from app.graph.customer_need_questions import (
    customer_need_type_label,
    is_customer_need_type_followup,
)
from app.graph.planner_content_signals import (
    has_ad_price_check,
    has_advantage_question,
    has_case_request,
    has_current_after_sales_signal,
    has_effect_guarantee_request,
    has_generic_project_request,
    has_price_objection,
    has_project_consult_intent,
    has_project_process_question,
    has_recent_action_context,
    has_store_inquiry,
    is_ad_source_only_project_question,
    is_low_information_closing,
    is_low_information_content,
    is_pre_visit_only_question,
    is_service_response_complaint,
)
from app.graph.nodes.memory_usage_policy import is_generic_opening_without_specific_need
from app.graph.nodes.intent_signals import is_broad_ad_intro
from app.graph.nodes.price_question_frames import detect_price_question_frame
from app.graph.planner_dispute_signals import (
    has_effect_dispute,
    has_fee_or_refund_dispute,
    has_recent_complaint_context,
    has_recent_competitor_context,
    is_mild_effect_dissatisfaction,
    is_deposit_rule_question,
    is_pre_service_effect_concern,
    is_soft_fee_concern,
    recent_conversation_text,
)
from app.graph.planner_intent_meta import dedupe_intents
from app.graph.planner_intent_meta import extract_city
from app.graph.planner_store_followup import (
    is_store_city_followup,
    is_store_recommendation_followup,
    store_city_followup_intent,
    store_recommendation_followup_intent,
    store_location_preference_from_context,
)
from app.graph.state import AgentState
from app.policies.constants import (
    AFTER_SALES_KEYWORDS,
    APPOINTMENT_KEYWORDS,
    CAMPAIGN_KEYWORDS,
    CITY_NAMES,
    COMPETITOR_KEYWORDS,
    PRICE_KEYWORDS,
    SALES_TALK_KB_NAME,
    TRUST_KEYWORDS,
)


def filter_spurious_intents(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_info = state.get("image_info") or {}
    content = state.get("normalized_content") or ""
    appointment_identity = appointment_identity_followup_value(state)
    has_current_competitor = any(word in content for word in COMPETITOR_KEYWORDS)
    has_current_trust = any(word in content for word in TRUST_KEYWORDS)
    price_objection = has_price_objection(content)
    pre_service_effect_concern = is_pre_service_effect_concern(content)
    pre_visit_only = is_pre_visit_only_question(content)
    price_frame = detect_price_question_frame(content)
    if _has_medical_risk_signal(content):
        return [_human_risk_intent()]
    if pre_visit_only:
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户询问到店前准备事项，直接回答准备口径，不进入项目、门店或预约查询",
                "tool_plan": [{"name": "no_tool", "purpose": "到店准备常见问题无需实时工具"}],
            }
        ]
    semantic_intent = _semantic_priority_intent(content, state)
    if semantic_intent:
        return [semantic_intent]
    if _is_age_suitability_question(content):
        return [
            {
                "intent": "project_inquiry",
                "skill": "project_consult",
                "priority": 1,
                "reason": "客户询问年龄较大是否还能做，属于项目适配和安全顾虑，应先正面回答而不是按闲聊处理。",
                "known_info": ["客户担心年纪较大是否适合做皮肤改善"],
                "missing_info": [],
                "reply_goal": "先回答年纪大不是单独否定条件，重点看身体情况、皮肤状态和是否有禁忌；再轻引导到店先做检测确认。如果还不知道城市或位置，最后只问一句所在城市或附近区域，不要只回复寒暄。",
                "should_ask": True,
                "tool_plan": [
                    {
                        "name": "kb_search",
                        "kb_name": SALES_TALK_KB_NAME,
                        "query": "年纪大 能不能做 皮肤改善 安全 顾虑 到店检测",
                        "purpose": "检索年龄适配、安全顾虑和到店检测承接话术。",
                    },
                    {
                        "name": "kb_search",
                        "kb_name": "project_qa",
                        "query": "年纪大 皮肤改善 能不能做 禁忌 身体情况",
                        "purpose": "检索项目适配、禁忌和安全边界。",
                    },
                    {
                        "name": "kb_search",
                        "kb_name": "case_studies",
                        "query": "成熟年龄段 淡斑 肤色改善 同类案例 参考",
                        "purpose": "检索成熟客群同类改善案例素材，用于增强客户信任。",
                    },
                ],
            }
        ]
    if appointment_identity:
        known_info = []
        if appointment_identity.get("customer_name"):
            known_info.append(f"客户补充姓名：{appointment_identity['customer_name']}")
        if appointment_identity.get("phone"):
            known_info.append(f"客户补充电话：{appointment_identity['phone']}")
        missing_info = ["电话"] if appointment_identity.get("customer_name") and not appointment_identity.get("phone") else []
        return [
            {
                "intent": "appointment_intent",
                "skill": "appointment",
                "priority": 1,
                "reason": "客户正在接着上一轮预约确认补充姓名或电话信息，应继续预约确认而不是按闲聊承接。",
                "known_info": known_info,
                "missing_info": missing_info,
                "reply_goal": "继续复用已知门店、日期和时间，只补下一个缺失的预约信息，不重复讲门店和可约时间。",
                "should_ask": False,
                "tool_plan": [{"name": "no_tool", "purpose": "继续用已知预约上下文补齐预约信息。"}],
            }
        ]
    appointment_operation = _explicit_appointment_operation(content)
    if appointment_operation:
        intent = "appointment_cancel" if appointment_operation == "cancel" else "appointment_change"
        reason = "客户明确要求取消预约。" if appointment_operation == "cancel" else "客户明确要求改约或换时间。"
        return [
            {
                "intent": intent,
                "skill": "appointment",
                "priority": 1,
                "reason": reason,
                "known_info": [],
                "missing_info": [],
                "reply_goal": "按客户当前预约动作处理，复用已有预约、订单、门店和时间上下文，不要切到门店查询或项目咨询。",
                "should_ask": False,
                "tool_plan": [{"name": "no_tool", "purpose": "当前是预约动作确认，交由预约动作服务处理。"}],
            }
        ]
    if _is_appointment_schedule_confirmation(content, state):
        return [
            {
                "intent": "appointment_confirm",
                "skill": "appointment",
                "priority": 1,
                "reason": "客户正在确认上一轮预约时间或预约安排，应继续排客/预约动作而不是当普通闲聊处理。",
                "known_info": [],
                "missing_info": [],
                "reply_goal": "复用近期已确认的门店、日期、时间和订单上下文，继续走预约排客动作；不要重新问项目或泛寒暄。",
                "should_ask": False,
                "tool_plan": [{"name": "no_tool", "purpose": "当前是预约排客确认，交由预约动作服务处理。"}],
            }
        ]
    if is_deposit_rule_question(content):
        return [
            {
                "intent": "price_inquiry",
                "skill": "price_consult",
                "priority": 1,
                "reason": "客户只是询问定金、预约金或10元规则，不进入广告价格、门店或可约时间查询",
            }
        ]
    if _is_effect_result_invite_moment(content):
        return [
            {
                "intent": "project_inquiry",
                "skill": "project_consult",
                "priority": 1,
                "reason": "客户已补充斑点类型并追问能否达到案例效果，应承接效果预期并进入邀约节奏。",
                "known_info": ["客户关注零散小点/局部斑点类改善效果"],
                "missing_info": [],
                "reply_goal": "先给确定感，说明这类可先看淡斑改善方向；再轻推到店检测或询问今天/明天哪个时间方便，不要继续追问同类问题。",
                "should_ask": True,
                "tool_plan": [
                    {
                        "name": "kb_search",
                        "kb_name": SALES_TALK_KB_NAME,
                        "query": "零散小点 淡斑 效果保障 邀约 到店",
                        "purpose": "检索效果承接和邀约话术。",
                    },
                    {
                        "name": "kb_search",
                        "kb_name": "case_studies",
                        "query": "零散小点 淡斑 效果参考",
                        "purpose": "检索同类效果案例素材。",
                    },
                ],
            }
        ]
    if _is_effect_rebound_or_maintenance_question(content):
        return [
            {
                "intent": "trust_issue",
                "skill": "trust_build",
                "priority": 1,
                "reason": "客户当前询问做完会不会反弹或效果维持问题，属于效果信任顾虑，应先正面解释效果维护和回访保障。",
                "known_info": ["客户关注做完后的维持和反弹问题"],
                "missing_info": [],
                "reply_goal": "先回答不会一概说反弹，说明多数客户能看到基础改善，后续维护与个人情况有关；再顺势引导到店先看具体情况，不要切到门店查询。",
                "should_ask": False,
                "tool_plan": [
                    {
                        "name": "kb_search",
                        "kb_name": SALES_TALK_KB_NAME,
                        "query": content or "反弹 维持多久 效果保障 回访",
                        "purpose": "检索效果维持、反弹顾虑和回访保障的承接话术。",
                    },
                    {
                        "name": "kb_search",
                        "kb_name": "case_studies",
                        "query": "淡斑 黑色素 效果维持 同类案例",
                        "purpose": "检索同类改善案例素材，用于增强信任。",
                    },
                ],
            }
        ]
    if pre_visit_only and not has_current_after_sales_signal(content):
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户询问到店前准备事项，直接回答准备口径，不进入项目、门店或预约查询",
            }
        ]
    if is_low_information_closing(content):
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 9,
                "reason": "客户当前只是感谢、收尾或暂缓，没有新的业务诉求，不继承历史预约或门店任务",
            }
        ]
    soft_fee_concern = is_soft_fee_concern(content)
    if soft_fee_concern:
        return [
            {
                "intent": "trust_issue",
                "skill": "trust_build",
                "priority": 1,
                "reason": "客户当前在问到店会不会乱收费、隐形消费等透明度问题，先解释收费口径，不推进预约/投诉流程。",
                "tool_plan": [
                    {
                        "name": "kb_search",
                        "kb_name": SALES_TALK_KB_NAME,
                        "query": content or "乱收费 隐形消费 收费透明",
                        "purpose": "检索收费透明、到店顾虑和信任承接策略。",
                    },
                    {
                        "name": "kb_search",
                        "kb_name": "trust_assets",
                        "query": content or "收费透明 资质 服务保障",
                        "purpose": "检索收费透明、服务保障或资质背书事实。",
                    },
                ],
            }
        ]
    if _is_city_appointment_without_store_or_area(content):
        city = extract_city(content)
        return [
            {
                "intent": "store_inquiry",
                "skill": "store",
                "priority": 1,
                "reason": "客户已表达预约意向且只给出城市，查询可约时间前需要先确定更方便的门店或片区。",
                "known_info": [f"客户当前城市：{city}", "客户想预约到店咨询"],
                "missing_info": ["门店或附近片区"],
                "reply_goal": "先承接可以安排，再基于城市门店列表让客户选更方便的片区或门店；不能默认某一家门店查询空档。",
                "should_ask": True,
                "tool_plan": [
                    {
                        "name": "kb_search",
                        "kb_name": SALES_TALK_KB_NAME,
                        "query": f"{city} 门店 预约 到店",
                        "purpose": "检索城市门店承接和预约前补门店的销售话术。",
                    },
                    {
                        "name": "store_lookup",
                        "query": city,
                        "purpose": "先查询该城市可选门店，避免默认错误门店查可约时间。",
                    },
                ],
            }
        ]
    if _is_store_area_only_followup(state):
        area_text = str(content or "").strip()
        city = extract_city(recent_conversation_text(state, limit=6)) or _city_from_area_text(area_text)
        preference = store_location_preference_from_context(state)
        query = " ".join(part for part in [area_text, preference] if part).strip()
        known_info = [f"客户补充位置：{area_text}"]
        if city:
            known_info.append(f"所属城市：{city}")
        return [
            {
                "intent": "store_inquiry",
                "skill": "store",
                "priority": 1,
                "reason": "客户用区域或地标短句承接上一轮门店查询。",
                "known_info": known_info,
                "missing_info": [],
                "reply_goal": "根据客户补充的区域或地标直接缩到更方便的门店，不再回到普通寒暄。",
                "should_ask": False,
                "tool_plan": [
                    {
                        "name": "store_lookup",
                        "query": query or area_text,
                        "purpose": "按客户补充的区域或地标继续查询更方便的门店。",
                    }
                ],
            }
        ]
    if _is_customer_need_type_followup_state(state):
        return [_type_followup_project_intent(state)]
    if _is_city_only_store_opening(content):
        city = _extract_city_only_value(content)
        query = " ".join(
            part for part in [city, store_location_preference_from_context(state)] if part
        ).strip()
        return [
            {
                "intent": "store_inquiry",
                "skill": "store",
                "priority": 1,
                "reason": "客户当前只补充了所在城市或区域，默认先按最近门店方向承接。",
                "known_info": [f"客户当前位置线索：{city}"] if city else [],
                "missing_info": [],
                "reply_goal": "根据客户当前城市或区域直接推荐更方便的门店，不继续泛寒暄。",
                "should_ask": False,
                "tool_plan": [
                    {
                        "name": "kb_search",
                        "kb_name": SALES_TALK_KB_NAME,
                        "query": query or city,
                        "purpose": "检索门店匹配、位置补位和推荐门店的承接策略。",
                    },
                    {
                        "name": "store_lookup",
                        "query": query or city,
                        "purpose": "按客户当前城市或区域直接查询最近或更方便的门店。",
                    }
                ],
            }
        ]
    intents = _drop_spurious_image_intent_for_store_turn(state, intents)
    if is_store_city_followup(state):
        kept = [
            item
            for item in intents
            if item.get("intent") not in {"project_inquiry", "image_inquiry", "price_inquiry", "campaign_inquiry", "emotion_chat"}
        ]
        if not any(item.get("intent") == "store_inquiry" for item in kept):
            kept.append(store_city_followup_intent(state))
        return dedupe_intents(kept)
    if is_store_recommendation_followup(state):
        kept = [
            item
            for item in intents
            if item.get("intent") not in {"project_inquiry", "image_inquiry", "price_inquiry", "campaign_inquiry", "emotion_chat"}
        ]
        if not any(item.get("intent") == "store_inquiry" for item in kept):
            kept.append(store_recommendation_followup_intent(state))
        return dedupe_intents(kept)
    if _is_ad_multi_intent_opening(content):
        return [
            {"intent": "project_inquiry", "skill": "project_consult", "priority": 1, "reason": "广告引流开场里已明确包含项目兴趣和改善诉求"},
            {"intent": "ad_price_check", "skill": "price_consult", "priority": 2, "reason": "广告引流开场里已明确包含价格或活动口径诉求"},
        ]
    if price_frame and _has_recent_project_or_ad_context(state):
        intents = [
            item
            for item in intents
            if item.get("intent") != "emotion_chat"
            and item.get("skill") != "direct_reply"
        ]
        if price_frame == "times_question":
            if not any(item.get("intent") == "project_inquiry" for item in intents):
                intents.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 1, "reason": "客户在延续项目或广告上下文追问做几次、一次能不能好这类节奏问题"})
        else:
            if not any(item.get("intent") == "price_inquiry" for item in intents):
                intents.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 1, "reason": "客户在延续广告或项目上下文追问价格口径细节"})
            if _recent_ad_context(state) and not any(item.get("intent") == "ad_price_check" for item in intents):
                intents.append({"intent": "ad_price_check", "skill": "price_consult", "priority": 2, "reason": "当前价格追问发生在广告活动上下文中，需要解释广告价口径"})
        return dedupe_intents(intents)
    if _is_project_direction_followup(content, state):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"emotion_chat", "price_inquiry", "campaign_inquiry", "ad_price_check"}
            and item.get("skill") not in {"direct_reply", "price_consult"}
        ]
        if not any(item.get("intent") == "project_inquiry" for item in intents):
            intents.append(
                {
                    "intent": "project_inquiry",
                    "skill": "project_consult",
                    "priority": 1,
                    "reason": "客户不想继续被追问，要求在当前项目/图片上下文里直接给改善方向",
                }
            )
        return dedupe_intents(intents)
    explicit_need_intro = (
        has_project_consult_intent(content)
        or has_generic_project_request(content)
        or _is_explicit_need_intro(content)
        or _has_effect_need_intro_signal(content)
    )
    if explicit_need_intro:
        intents = _normalize_explicit_need_project_intents(state, intents)
    if explicit_need_intro and not any(
        item.get("intent") in {"project_inquiry", "image_inquiry", "case_request", "price_inquiry", "campaign_inquiry"}
        for item in intents
        if isinstance(item, dict)
    ):
        known_info: list[str] = []
        city = extract_city(content)
        if city:
            known_info.append(f"客户当前城市：{city}")
        need_hint = _first_need_hint(content)
        if need_hint:
            known_info.append(f"客户当前需求：{need_hint}")
        return [
            {
                "intent": "project_inquiry",
                "skill": "project_consult",
                "priority": 1,
                "reason": "客户当前已明确给出改善需求，应直接进入项目承接，不先按普通寒暄处理。",
                "known_info": known_info,
                "missing_info": [],
                "reply_goal": "先承接这类大多数都可以做，再给方向、案例参考和一个客户听得懂的问题；如果城市已知，只把它作为后续到店信息，不回头先问城市。",
                "should_ask": False,
                "tool_plan": _need_intro_tool_plan(content, need_hint),
            }
        ]
    explicit_store_or_appointment = _has_current_store_address_signal(content) or _has_explicit_appointment_signal(content)
    if explicit_store_or_appointment:
        intents = _recover_business_intents_from_explicit_customer_need(content, intents)
    if _is_passive_add_wechat_opening(content) and not _recent_appointment_context(state):
        return [_opening_guidance_intent()]
    if _is_pure_opening_greeting(content) and not has_recent_action_context(state) and not _recent_appointment_context(state):
        return [_opening_guidance_intent()]
    if (
        (is_low_information_content(content) and not has_recent_action_context(state))
        or is_generic_opening_without_specific_need(content)
    ) and not explicit_need_intro and not explicit_store_or_appointment:
        return [_opening_guidance_intent()]
    if is_service_response_complaint(content):
        return [
            {
                "intent": "emotion_chat",
                "skill": "direct_reply",
                "priority": 1,
                "reason": "客户在反馈回复慢或等待体验不佳，需要先道歉并承接当前诉求",
            }
        ]
    if has_fee_or_refund_dispute(content):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"store_inquiry", "ad_price_check", "price_inquiry", "campaign_inquiry"}
        ]
        if not any(item.get("intent") == "complaint_refund" for item in intents):
            intents.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "费用、退款或门店收费口径争议"})
    if is_mild_effect_dissatisfaction(content):
        intents = [
            item
            for item in intents
            if item.get("intent")
            not in {
                "complaint_refund",
                "store_inquiry",
                "appointment_intent",
                "appointment_confirm",
                "appointment_change",
                "appointment_cancel",
            }
        ]
        if not any(item.get("intent") == "after_sales" for item in intents):
            intents.append({"intent": "after_sales", "skill": "after_sales", "priority": 1, "reason": "客户已做过项目，当前在咨询效果不明显的调整建议"})
    if _negates_price_inquiry(content):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"price_inquiry", "campaign_inquiry", "ad_price_check"}
            and item.get("skill") != "price_consult"
        ]
    if _is_case_request_or_followup(content, state):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"price_inquiry", "campaign_inquiry", "ad_price_check", "emotion_chat"}
            and item.get("skill") != "price_consult"
        ]
        if not any(item.get("intent") == "case_request" for item in intents):
            intents.append(
                {
                    "intent": "case_request",
                    "skill": "project_consult",
                    "priority": 2,
                    "reason": "客户在延续效果案例或前后对比诉求，需要按已有改善方向检索案例素材",
                }
            )
    elif _is_effect_timeline_followup(content, state):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"emotion_chat", "price_inquiry", "campaign_inquiry", "ad_price_check"}
            and item.get("skill") not in {"direct_reply", "price_consult"}
        ]
        if not any(item.get("intent") == "project_inquiry" for item in intents):
            intents.append(
                {
                    "intent": "project_inquiry",
                    "skill": "project_consult",
                    "priority": 2,
                    "reason": "客户延续淡斑或效果案例上下文询问改善周期，需要按项目咨询承接",
                }
            )
    if has_effect_dispute(content):
        intents = [
            item
            for item in intents
            if item.get("intent")
            not in {
                "appointment_intent",
                "appointment_confirm",
                "appointment_change",
                "appointment_cancel",
                "store_inquiry",
                "project_inquiry",
                "price_inquiry",
                "campaign_inquiry",
            }
        ]
        if not any(item.get("intent") == "complaint_refund" for item in intents):
            intents.append({"intent": "complaint_refund", "skill": "handoff", "priority": 0, "reason": "效果不满或投诉倾向"})
    if has_effect_guarantee_request(content):
        intents = [item for item in intents if item.get("intent") != "price_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户要求效果保证或一次见效承诺"})
    if is_ad_source_only_project_question(content):
        intents = [
            item
            for item in intents
            if item.get("intent") not in {"campaign_inquiry", "ad_price_check", "price_inquiry"}
            and item.get("skill") != "price_consult"
        ]
        if has_project_process_question(content) and not any(item.get("intent") == "project_process" for item in intents):
            intents.append({"intent": "project_process", "skill": "project_consult", "priority": 2, "reason": "广告只是信息来源，客户实际询问项目流程或时长"})
        elif not any(item.get("intent") == "project_inquiry" for item in intents):
            intents.append({"intent": "project_inquiry", "skill": "project_consult", "priority": 2, "reason": "广告只是信息来源，客户实际询问项目内容"})
    if has_advantage_question(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        target_skill = "competitor" if has_recent_competitor_context(state) else "trust_build"
        target_intent = "competitor_compare" if target_skill == "competitor" else "trust_issue"
        if not any(item.get("intent") == target_intent for item in intents):
            intents.append({"intent": target_intent, "skill": target_skill, "priority": 2, "reason": "客户询问优势或差异点"})
    if has_current_trust and not has_store_inquiry(content):
        intents = [item for item in intents if item.get("intent") != "store_inquiry"]
        if not any(item.get("intent") == "trust_issue" for item in intents):
            intents.append({"intent": "trust_issue", "skill": "trust_build", "priority": 1, "reason": "客户当前表达资质或正规性顾虑"})
    if price_objection:
        intents = [item for item in intents if item.get("intent") != "project_inquiry"]
        if not any(item.get("intent") == "price_inquiry" for item in intents):
            intents.append({"intent": "price_inquiry", "skill": "price_consult", "priority": 2, "reason": "价格异议或议价"})
    if pre_service_effect_concern:
        intents = [item for item in intents if item.get("intent") != "after_sales"]
    if pre_visit_only and not has_current_after_sales_signal(content):
        intents = [item for item in intents if item.get("intent") != "after_sales"]
    if has_current_competitor and not has_current_trust:
        intents = [item for item in intents if item.get("intent") != "trust_issue"]
    if image_info.get("has_image") and image_info.get("image_intent") == "face_consult":
        allowed = {"image_inquiry", "project_inquiry"}
        if has_recent_complaint_context(state):
            allowed.add("complaint_refund")
            allowed.add("after_sales")
        if any(word in content for word in PRICE_KEYWORDS):
            allowed.add("price_inquiry")
        if any(word in content for word in CAMPAIGN_KEYWORDS):
            allowed.add("campaign_inquiry")
        if any(word in content for word in APPOINTMENT_KEYWORDS):
            allowed.add("appointment_intent")
        if any(word in content for word in TRUST_KEYWORDS):
            allowed.add("trust_issue")
        if has_store_inquiry(content):
            allowed.add("store_inquiry")
        if any(word in content for word in AFTER_SALES_KEYWORDS) and not pre_service_effect_concern:
            allowed.add("after_sales")
        filtered = [item for item in intents if item.get("intent") in allowed]
        if filtered:
            return dedupe_intents(filtered)
    intents = _recover_business_intents_from_explicit_customer_need(content, intents)
    return dedupe_intents(intents)


def _recover_business_intents_from_explicit_customer_need(content: str, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    explicit_store = has_store_inquiry(content) or _has_current_store_address_signal(content)
    explicit_project = has_project_consult_intent(content) or has_generic_project_request(content)
    explicit_appointment = _has_explicit_appointment_signal(content)
    if not explicit_store and not explicit_project and not explicit_appointment:
        return intents

    recovered = list(intents)
    if explicit_store and not any(item.get("intent") == "store_inquiry" for item in recovered):
        recovered = [
            item
            for item in recovered
            if item.get("intent") != "emotion_chat" or item.get("skill") != "direct_reply"
        ]
        recovered.append(
            {
                "intent": "store_inquiry",
                "skill": "store",
                "priority": 3,
                "reason": "客户当前明确在问门店、地址、附近位置或路线信息，不能按普通寒暄处理。",
            }
        )
    if explicit_appointment and not any(
        item.get("intent") in {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
        for item in recovered
    ):
        recovered = [
            item
            for item in recovered
            if item.get("intent") != "emotion_chat" or item.get("skill") != "direct_reply"
        ]
        recovered.append(
            {
                "intent": "appointment_intent",
                "skill": "appointment",
                "priority": 3 if explicit_store else 2,
                "reason": "客户当前补充了到店时间或预约承接信息，应和门店问题一起处理。",
            }
        )
    if explicit_project and not any(item.get("intent") in {"project_inquiry", "case_request", "project_process"} for item in recovered):
        recovered = [
            item
            for item in recovered
            if item.get("intent") != "emotion_chat" or item.get("skill") != "direct_reply"
        ]
        recovered.append(
            {
                "intent": "project_inquiry",
                "skill": "project_consult",
                "priority": 4,
                "reason": "客户当前明确在问改善需求、项目方向、效果或相关内容，不能按普通寒暄处理。",
            }
        )
    return recovered


def _has_explicit_appointment_signal(content: str) -> bool:
    text = content or ""
    if not text:
        return False
    if any(
        term in text
        for term in [
            "帮我登记",
            "先帮我登记",
            "可以先帮我登记",
            "帮我预约",
            "开预约",
            "开预约入口",
            "发预约入口",
            "发小程序",
            "预约金小程序",
            "按这个信息",
            "就按这个",
            "确认这个时间",
            "确认预约",
            "帮我开单",
        ]
    ):
        return True
    return any(word in text for word in APPOINTMENT_KEYWORDS) or bool(
        re.search(r"(上午|下午|晚上|早上|中午)?\s*\d{1,2}\s*(?:点|:\d{2})", text)
    )


def _is_passive_add_wechat_opening(content: str) -> bool:
    text = re.sub(r"\s+", "", str(content or ""))
    return any(term in text for term in ["我已经添加了你", "已经添加了你", "现在我们可以开始聊天了", "可以开始聊天了"])


def _is_pure_opening_greeting(content: str) -> bool:
    text = re.sub(r"[\s,，。.!！?？~～、]+", "", str(content or "")).lower()
    return text in {"你好", "您好", "在吗", "哈喽", "hello", "hi"}


def _is_effect_result_invite_moment(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    type_terms = ["零散小点", "零散的小点", "小点", "斑点", "黑色素", "浅斑", "淡斑", "色沉"]
    effect_terms = ["这种效果", "这样效果", "能有效果", "能不能有", "能不能改善", "有没有效果", "能达到", "能做到"]
    action_terms = ["去做", "我做", "我去", "做了", "能有"]
    return any(term in text for term in type_terms) and any(term in text for term in effect_terms) and any(
        term in text for term in action_terms
    )


def _explicit_appointment_operation(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    cancel_terms = ["取消预约", "确认取消", "帮我取消", "先取消", "不去了", "明天不去", "取消一下"]
    change_terms = ["改约", "改时间", "换个时间", "改到", "换到"]
    if any(term in text for term in cancel_terms):
        return "cancel"
    if any(term in text for term in change_terms):
        return "change"
    return ""


def _is_effect_rebound_or_maintenance_question(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    rebound_terms = ["反弹", "返弹", "反复", "又回来", "回到原来", "白搭", "越弄越", "会不会回去", "会不会又长", "会不会复发"]
    maintenance_terms = ["维持多久", "保持多久", "能保持多久", "管多久", "管好久", "多久又会"]
    if any(term in text for term in rebound_terms + maintenance_terms):
        return True
    return False


def _semantic_priority_intent(content: str, state: AgentState) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if not text:
        return None
    if _has_medical_risk_signal(text):
        return _human_risk_intent()
    if _has_refund_or_complaint_semantic(text):
        return {
            "intent": "complaint_refund",
            "skill": "handoff",
            "priority": 0,
            "reason": "客户表达投诉、退款、强烈不满或已做无效争议，需要专业同事协助。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "先接住情绪和事实诉求，再让专业同事接着核对处理。",
            "should_ask": False,
            "tool_plan": [{"name": "professional_assist", "purpose": "投诉退款或效果争议需要专业协助。"}],
        }
    if _has_pre_service_discomfort_question(text, state):
        return {
            "intent": "project_inquiry",
            "skill": "project_consult",
            "priority": 1,
            "reason": "客户在做前询问操作疼不疼或感受问题，属于项目咨询和安全顾虑，不应误判为做后售后。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "直接回答操作感受和舒适度边界，再轻带到店先看皮肤状态。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": "project_qa", "query": text, "purpose": "检索操作感受、流程和做前顾虑说明。"}],
        }
    if _has_after_sales_semantic(text, state):
        return {
            "intent": "after_sales",
            "skill": "after_sales",
            "priority": 1,
            "reason": "客户描述做后反应或护理问题，应进入售后护理承接。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "先确认做后状态和护理方向，必要时让专业同事协助，不要当普通闲聊。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": "after_sales_qa", "query": text, "purpose": "检索做后护理和风险承接口径。"}],
        }
    if _has_image_consult_semantic(text):
        return {
            "intent": "image_inquiry",
            "skill": "face_consult",
            "priority": 1,
            "reason": "客户明确准备发照片或让客服看图判断，应进入图片/面诊咨询承接。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "承接客户可以发图看情况，不要误当案例请求或普通闲聊。",
            "should_ask": False,
            "tool_plan": [{"name": "no_tool", "purpose": "等待客户上传图片或由图片理解节点处理。"}],
        }
    if _has_case_semantic_followup(text, state):
        return {
            "intent": "case_request",
            "skill": "project_consult",
            "priority": 1,
            "reason": "客户在追问案例、效果图、做几次或同类对比，应继承案例上下文。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "承接客户要看效果参考或案例细节，不要退回泛项目咨询。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": "case_studies", "query": text, "purpose": "检索同类案例和效果参考。"}],
        }
    if _has_appointment_record_semantic(text):
        return {
            "intent": "appointment_confirm",
            "skill": "appointment",
            "priority": 1,
            "reason": "客户在查询自己是否已经预约或约上。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "先承接查询预约记录，不要当普通闲聊。",
            "should_ask": False,
            "tool_plan": [{"name": "appointment_record_query", "purpose": "查询客户已有预约记录。"}],
        }
    if _is_effect_rebound_or_maintenance_question(text) or _has_effect_safety_or_stability_question(text):
        return {
            "intent": "trust_issue",
            "skill": "trust_build",
            "priority": 1,
            "reason": "客户当前关注效果是否稳定、会不会反复或安全顾虑，应先做效果信任承接。",
            "known_info": ["客户关注效果稳定性或安全感"],
            "missing_info": [],
            "reply_goal": "先给确定感，再讲影响维持的真实因素和做后跟进；不要切回门店或普通闲聊。",
            "should_ask": False,
            "tool_plan": [
                {"name": "kb_search", "kb_name": SALES_TALK_KB_NAME, "query": text, "purpose": "检索效果稳定、安全感和信任承接话术。"},
                {"name": "kb_search", "kb_name": "case_studies", "query": "效果稳定 同类案例 改善参考", "purpose": "检索同类效果参考。"},
            ],
        }
    if _has_campaign_or_ad_entry_semantic(text):
        return {
            "intent": "campaign_inquiry",
            "skill": "campaign",
            "priority": 1,
            "reason": "客户从广告、直播或抖音活动入口询问活动、名额或参与方式，应先按活动承接。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "先承接活动入口和参与方式，再顺势推进门店或预约登记。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": SALES_TALK_KB_NAME, "query": text, "purpose": "检索广告活动、名额和参与方式承接话术。"}],
        }
    if _has_competitor_semantic(text):
        return {
            "intent": "competitor_compare",
            "skill": "competitor",
            "priority": 1,
            "reason": "客户提到别家、隔壁、别人家或外部承诺价格效果，应进入竞品对比承接。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "先承接客户对比心理，再解释不能只看价格或承诺，要看项目范围和服务口径。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": "competitor_qa", "query": text, "purpose": "检索竞品对比承接。"}],
        }
    if _has_price_rule_semantic(text):
        return {
            "intent": "price_inquiry",
            "skill": "price_consult",
            "priority": 1,
            "reason": "客户询问预约金、定金、老客价、付款方式或价格冲突，应进入价格/收费口径。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "直接解释价格或收费口径，不被历史预约任务带跑。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": SALES_TALK_KB_NAME, "query": text, "purpose": "检索收费口径和价格解释话术。"}],
        }
    if _has_trust_or_fee_semantic_question(text):
        return {
            "intent": "trust_issue",
            "skill": "trust_build",
            "priority": 1,
            "reason": "客户在问资质、身份、老师专业或收费透明问题，属于普通信任顾虑。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "直接肯定并解释资质、收费透明或接待身份，不要当闲聊或门店问题处理。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": SALES_TALK_KB_NAME, "query": text, "purpose": "检索信任和收费透明承接话术。"}],
        }
    if _has_project_process_semantic_question(text, state):
        return {
            "intent": "project_process",
            "skill": "project_consult",
            "priority": 1,
            "reason": "客户询问操作流程、次数、时长或到店步骤，应直接解释流程。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "直接回答流程、次数或时长，不要回到预约或泛寒暄。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": "project_qa", "query": text, "purpose": "检索流程、次数和时长说明。"}],
        }
    if _has_time_slot_appointment_semantic(text):
        return {
            "intent": "appointment_intent",
            "skill": "appointment",
            "priority": 1,
            "reason": "客户询问具体日期或时段是否有位置，属于预约可约时间查询，不是普通门店位置问题。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "按预约时段查询或补齐门店，不要当门店地址问题处理。",
            "should_ask": False,
            "tool_plan": [{"name": "appointment_availability", "purpose": "查询或准备查询指定时段是否可约。"}],
        }
    if _has_store_detail_semantic_question(text, state):
        return {
            "intent": "store_inquiry",
            "skill": "store",
            "priority": 1,
            "reason": "客户询问门店名称、营业时间、路线、楼层、停车或距离，应进入门店查询。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "基于实时门店事实回答客户问的门店细节，不要退回普通闲聊。",
            "should_ask": False,
            "tool_plan": [{"name": "store_lookup", "query": text, "purpose": "查询门店、地址、营业、停车或距离事实。"}],
        }
    if _has_appointment_semantic_followup(text, state):
        return {
            "intent": "appointment_intent",
            "skill": "appointment",
            "priority": 1,
            "reason": "客户表现出报名、参加、留名额、继续办理或补充预约信息，应继续预约推进。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "沿最近预约或活动任务继续推进，只收一个必要信息或说明下一步。",
            "should_ask": False,
            "tool_plan": [{"name": "no_tool", "purpose": "先按预约上下文推进，必要事实由后续预约节点处理。"}],
        }
    if _has_project_need_semantic_question(text, state):
        return {
            "intent": "project_inquiry",
            "skill": "project_consult",
            "priority": 1,
            "reason": "客户用口语描述改善需求、部位或适配疑问，应进入项目承接。",
            "known_info": [],
            "missing_info": [],
            "reply_goal": "先回答能不能看、适合什么方向或大概怎么做，再轻推进到店。",
            "should_ask": False,
            "tool_plan": [{"name": "kb_search", "kb_name": "project_qa", "query": text, "purpose": "检索客户口语需求对应的改善方向。"}],
        }
    return None


def _has_medical_risk_signal(text: str) -> bool:
    return any(
        term in text
        for term in [
            "怀孕",
            "孕妇",
            "哺乳",
            "喂奶",
            "未成年",
            "不满18",
            "十八岁以下",
            "十六岁",
            "十七岁",
            "孩子",
            "小孩",
            "过敏期",
            "正在过敏",
            "严重过敏",
            "过敏了",
        ]
    )


def _human_risk_intent() -> dict[str, Any]:
    return {
        "intent": "human_request",
        "skill": "handoff",
        "priority": 0,
        "reason": "客户提到孕期、哺乳期、未成年、过敏期等高风险适配问题，需要专业同事确认。",
        "known_info": [],
        "missing_info": [],
        "reply_goal": "先接住客户问题，再说明这类情况要让专业同事确认是否适合，不继续承诺可做。",
        "should_ask": False,
        "tool_plan": [{"name": "professional_assist", "purpose": "高风险适配需要专业协助确认。"}],
    }


def _has_refund_or_complaint_semantic(text: str) -> bool:
    complaint_terms = ["投诉", "曝光", "起诉", "维权", "退钱", "退款", "退给我", "发网上", "发到网上"]
    if any(term in text for term in complaint_terms):
        return not any(term in text for term in ["不是要投诉", "不是投诉", "不投诉"])
    done_terms = ["去过", "做了", "做过", "做完", "已经做", "弄了"]
    dissatisfied_terms = ["一点用没有", "没啥变化", "没什么变化", "不见效果", "没有效果", "没效果", "还是那样"]
    return any(term in text for term in done_terms) and any(term in text for term in dissatisfied_terms)


def _has_after_sales_semantic(text: str, state: AgentState) -> bool:
    after_terms = ["刚弄完", "刚做完", "做完", "弄完", "术后", "结痂", "结的痂", "红得厉害", "流黄水", "流脓", "能喝酒", "能运动", "能化妆"]
    if not any(term in text for term in after_terms):
        return False
    future_terms = ["会不会", "怕", "担心", "能不能", "是不是"]
    if any(term in text for term in future_terms) and not any(term in text for term in ["刚", "已经", "昨天", "前天", "做完", "术后", "结痂", "流"]):
        return False
    return True


def _has_pre_service_discomfort_question(text: str, state: AgentState) -> bool:
    if not any(term in text for term in ["疼", "痛", "难受", "刺痛"]):
        return False
    if any(term in text for term in ["刚", "已经", "昨天", "前天", "做完", "弄完", "术后"]):
        return False
    if not any(term in text for term in ["会不会", "是不是", "疼不疼", "痛不痛", "能不能", "操作", "做的时候"]):
        return False
    return bool(_has_recent_project_or_ad_context(state) or any(term in text for term in ["这个", "做", "操作", "项目"]))


def _has_effect_safety_or_stability_question(text: str) -> bool:
    question_terms = ["会不会", "是不是", "怕", "担心", "能不能", "疼不疼"]
    effect_terms = ["效果", "留印", "伤皮肤", "伤脸", "越弄越", "越做越", "白搭", "回到原来", "原来一样", "没用", "花", "又长出来"]
    if any(q in text for q in question_terms) and any(term in text for term in effect_terms):
        return True
    if "一次" in text and any(term in text for term in ["变化", "效果", "看到"]):
        return True
    return any(term in text for term in ["又长出来", "保持几个月", "保持多久", "维持几个月", "维持多久", "越做越黑"])


def _has_trust_or_fee_semantic_question(text: str) -> bool:
    trust_terms = ["资质", "正规", "靠谱", "骗子", "骗", "糊弄", "真人", "机器", "自动回复", "负责活动", "活动方", "负责接待", "老师", "谁给我做", "门店里面的人"]
    if "身份证" in text:
        return False
    if "证" in text and not any(term in text for term in ["身份证", "凭证"]):
        return True
    fee_terms = ["套路", "乱收费", "加项目", "加钱", "额外", "隐形", "别坑", "不满意", "能退", "可退"]
    if "不是要投诉" in text and any(term in text for term in fee_terms):
        return True
    return any(term in text for term in trust_terms + fee_terms)


def _has_competitor_semantic(text: str) -> bool:
    return any(term in text for term in ["别人家", "别家", "隔壁", "外面", "朋友在别家", "其他家", "其他机构", "另一家", "某团"])


def _has_price_rule_semantic(text: str) -> bool:
    if re.fullmatch(r"1[3-9]\d{9}", text):
        return False
    if "直播" in text and "还在" in text:
        return False
    price_terms = ["10块", "十块", "10元", "十元", "定金", "订金", "预约金", "老客户", "老顾客", "老客", "全款", "尾款", "一次的钱", "一只", "一双", "268", "380", "199"]
    if any(term in text for term in price_terms):
        return True
    return "这个价" in text or "用这个价" in text or ("直播" in text and "图" in text) or ("直播" in text and any(term in text for term in ["说", "讲", "活动", "价格"]))


def _has_appointment_record_semantic(text: str) -> bool:
    return any(term in text for term in ["约没约上", "约上没", "有没有约上", "到底约没约", "是不是预约了", "查下预约", "查一下预约", "约过", "预约成功没", "预约成功没有"])


def _has_case_semantic_followup(text: str, state: AgentState) -> bool:
    if "图上" in text and any(term in text for term in ["价", "钱", "268", "380", "199"]):
        return False
    if "没有图" in text:
        return False
    case_terms = ["照片", "效果图", "对比", "案例", "同龄", "年龄差不多", "做了几次", "几次效果", "再发一个", "发你图", "同一张", "有别的", "有别的吗"]
    if any(term in text for term in case_terms):
        return True
    recent = recent_conversation_text(state, limit=6)
    return any(term in recent for term in ["案例", "效果图", "对比照", "[图片]"]) and any(term in text for term in ["几次", "多久", "变化", "像不像"])


def _has_image_consult_semantic(text: str) -> bool:
    if not any(term in text for term in ["拍张脸", "发图", "发照片", "照片发", "发你照片", "拍照"]):
        return False
    return any(term in text for term in ["你看看", "看一下", "判断", "像不像", "适不适合", "能不能看", "能判断"])


def _has_project_process_semantic_question(text: str, state: AgentState) -> bool:
    process_terms = [
        "流程",
        "步骤",
        "先干嘛",
        "先检测",
        "先做",
        "后干嘛",
        "怎么弄",
        "怎么做",
        "怎么操作",
        "多久",
        "多长时间",
        "耽误",
        "耽误多久",
        "耽误多长时间",
        "花多久",
        "占多久",
        "全程",
        "整个下来",
        "下来要",
        "下来得",
        "要很久",
        "几回",
        "做几次",
        "几次",
    ]
    if any(term in text for term in ["报名", "参加", "名额", "交钱"]):
        return False
    if not any(term in text for term in process_terms):
        return False
    if any(term in text for term in ["从我这", "过去", "路上", "距离"]):
        return False
    return bool(_has_recent_project_or_ad_context(state) or any(term in text for term in ["这个", "弄", "操作", "做", "到店", "全程", "下来", "流程"]))


def _has_store_detail_semantic_question(text: str, state: AgentState) -> bool:
    if any(term in text for term in ["今天", "明天", "后天", "上午", "下午", "中午", "晚上", "五点", "5点"]) and any(term in text for term in ["位置", "空位", "号"]):
        return False
    store_terms = ["店叫啥", "店名", "走错", "拉萨", "上班到几点", "营业到几点", "几点开", "楼下", "几楼", "电梯", "停车", "导航", "过去多久", "从我这过去", "离", "近的", "附近"]
    if any(term in text for term in store_terms):
        return True
    recent = recent_conversation_text(state, limit=6)
    return any(term in recent for term in ["门店", "地址", "推荐", "附近"]) and any(term in text for term in ["名字", "多久", "停车", "营业", "导航", "楼"])


def _has_time_slot_appointment_semantic(text: str) -> bool:
    return any(term in text for term in ["今天", "明天", "后天", "上午", "下午", "中午", "晚上", "五点", "5点"]) and any(term in text for term in ["位置", "空位", "号", "有吗", "能约"])


def _has_appointment_semantic_followup(text: str, state: AgentState) -> bool:
    if any(term in text for term in ["考虑", "先这样", "暂时不用", "不用了"]):
        return False
    if re.fullmatch(r"1[3-9]\d{9}", text):
        return "预约" in recent_conversation_text(state, limit=8) or "电话" in recent_conversation_text(state, limit=8)
    if re.fullmatch(r"[\u4e00-\u9fa5]{2,6}", text) and any(term in recent_conversation_text(state, limit=8) for term in ["姓名", "名字", "预约", "登记"]):
        return True
    appointment_terms = ["报名", "参加", "留一个", "留名额", "保留", "怎么参加", "名额", "帮我弄", "心动", "可以", "交钱", "怎么交"]
    recent = recent_conversation_text(state, limit=8)
    return any(term in text for term in appointment_terms) and (_has_recent_project_or_ad_context(state) or any(term in recent for term in ["预约", "登记", "方便", "名额", "活动"]))


def _has_project_need_semantic_question(text: str, state: AgentState) -> bool:
    if any(term in text for term in ["广告", "直播"]) and any(term in text for term in ["想先看看", "想看看", "先看看", "了解一下"]):
        return True
    if any(term in text for term in ["月经", "例假", "经期"]) and any(term in text for term in ["能", "可以", "弄", "做"]):
        return True
    need_terms = ["脸上斑", "老斑", "小黑点", "小点点", "零零散散", "手背斑", "脖子黑", "灰黄", "发黄", "一块一块", "一片一片", "斑重", "老了", "年纪", "年龄", "岁"]
    action_terms = ["能", "可以", "适合", "适合啥", "整", "弄", "做", "看", "没救", "方向", "处理方向"]
    if any(term in text for term in need_terms) and (any(term in text for term in action_terms) or _has_recent_project_or_ad_context(state)):
        return True
    recent = recent_conversation_text(state, limit=6)
    if any(term in text for term in ["一块一块", "一片一片", "小点点", "发黄"]) and any(term in recent for term in ["更像", "零散", "成片", "肤色"]):
        return True
    if any(term in text for term in ["我就想知道大概怎么弄", "先说我这个方向", "先说适合啥", "处理方向"]):
        return True
    return False


def _has_campaign_or_ad_entry_semantic(text: str) -> bool:
    if "广告" in text and any(term in text for term in ["想先看看", "想看看", "先看看"]):
        return False
    source_terms = ["广告", "直播", "抖音", "活动"]
    action_terms = ["还在", "名额", "怎么抢", "怎么参加", "怎么报"]
    return any(term in text for term in source_terms) and any(term in text for term in action_terms)


def _is_appointment_schedule_confirmation(content: str, state: AgentState) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if any(term in text for term in ["取消", "不去", "不用", "退款", "投诉", "改约", "改时间", "换个时间"]):
        return False
    confirm_terms = ["可以", "行", "好的", "确认", "就这个", "这个时间", "那就", "安排", "约这个"]
    if not any(term in text for term in confirm_terms):
        return False
    recent = recent_conversation_text(state, limit=8)
    context_terms = ["预约", "可约", "空位", "这个时间", "几点", "预约金", "小程序", "帮你登记", "姓名", "电话"]
    return any(term in recent for term in context_terms)


def _is_age_suitability_question(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    age_terms = ["年纪", "年龄", "岁数", "年纪大", "年龄大", "岁数大", "老了", "比较大了", "年纪比较大"]
    suitability_terms = ["能做", "能不能做", "可以做", "可不可以做", "适合", "还能做", "也能做", "还能不能"]
    return any(term in text for term in age_terms) and any(term in text for term in suitability_terms)


def _recent_appointment_context(state: AgentState) -> bool:
    recent = recent_conversation_text(state, limit=10)
    return any(term in recent for term in ["预约", "可约", "到店", "小程序", "预约金", "空位", "几点", "明天", "后天"])


def _opening_guidance_intent() -> dict[str, Any]:
    return {
        "intent": "emotion_chat",
        "skill": "direct_reply",
        "priority": 9,
        "reason": "客户刚加企微或首次问候，需要按新客接待开场承接，而不是只回复普通寒暄。",
        "known_info": [],
        "missing_info": [],
        "reply_goal": "用公司前端真人客服/活动负责人口吻自然接待；如果客户没有明确问题，先短介绍淡斑、提亮、毛孔、痘印等可看的皮肤改善方向，再收集城市或附近区域来匹配最近门店；不要给皮肤改善/活动价格/附近门店三选一，也不要只说您好。",
        "should_ask": False,
        "tool_plan": [{"name": "no_tool", "purpose": "新客开场接待，无需工具。"}],
    }


def _has_current_store_address_signal(content: str) -> bool:
    text = content or ""
    return any(term in text for term in ["门店", "地址", "在哪里", "在哪", "哪儿", "位置", "怎么过去", "导航"])


def _is_city_appointment_without_store_or_area(content: str) -> bool:
    text = content or ""
    city = extract_city(text)
    if not city:
        return False
    appointment_terms = ["预约", "想约", "约个", "约一下", "到店", "过去看看", "去店里", "来店"]
    if not (_has_explicit_appointment_signal(text) or any(term in text for term in appointment_terms)):
        return False
    if any(term in text for term in ["百星", "思明", "二店", "集美", "湖里", "机场", "附近", "区", "路", "门店"]):
        return False
    return True


def _is_explicit_need_intro(content: str) -> bool:
    text = content or ""
    if any(term in text for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS + CAMPAIGN_KEYWORDS):
        return False
    opening_terms = ["了解一下", "想了解一下", "想了解", "咨询一下", "先了解", "想看看", "看下"]
    need_terms = ["祛斑", "淡斑", "黑色素", "抗衰", "补水", "毛孔", "暗沉", "色沉", "松弛", "提升"]
    if any(term in text for term in opening_terms) and any(term in text for term in need_terms):
        return True
    if any(term in text for term in need_terms):
        return True
    if any(term in text for term in ["脸上有点", "脸上有", "脸上有些", "脸有点", "脸有些", "皮肤有点", "皮肤有些"]) and any(
        term in text for term in ["斑", "黑色素", "暗沉", "色沉", "毛孔", "松", "松弛", "细纹"]
    ):
        return True
    return False


def _has_effect_need_intro_signal(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    need_terms = ["黑色素", "淡斑", "祛斑", "色沉", "暗沉", "毛孔", "痘印", "松弛", "细纹", "提亮", "肤色"]
    ask_terms = ["改善", "变化", "效果", "能不能", "可以吗", "能做", "能看到", "有没有效果", "真的能", "会不会"]
    return any(term in text for term in need_terms) and any(term in text for term in ask_terms)


def _negates_price_inquiry(content: str) -> bool:
    text = content or ""
    return any(
        term in text
        for term in [
            "不是问价格",
            "不是问价",
            "不问价格",
            "不问价",
            "没问价格",
            "我不是问价格",
            "我不是问价",
            "不是价格",
            "不是问多少钱",
        ]
    )


def _is_city_only_store_opening(content: str) -> bool:
    city = _extract_city_only_value(content)
    if not city:
        return False
    if any(word in content for word in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS + CAMPAIGN_KEYWORDS):
        return False
    if has_store_inquiry(content) or has_project_consult_intent(content) or has_generic_project_request(content) or _is_explicit_need_intro(content):
        return False
    return True


def _extract_city_only_value(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    city = extract_city(text)
    stripped = text
    for prefix in ["我在", "人在", "目前在", "现在在", "我是", "住在"]:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].strip()
            break
    stripped = stripped.replace("这边", "").replace("这儿", "").replace("这", "").strip()
    stripped = stripped.strip(" ，。！？?~～")
    if city and stripped in {city, f"{city}市"}:
        return city
    known_area_names = {"湖里", "思明", "枋湖", "浦东", "虹桥", "小寨", "中贸", "未央", "碑林"}
    if stripped in set(CITY_NAMES) or stripped in known_area_names:
        return stripped
    if re.fullmatch(r"[\u4e00-\u9fa5]{2,8}(?:市|区|县)", stripped):
        return stripped
    return ""


def _is_store_area_only_followup(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or "").strip()
    if not content:
        return False
    if any(word in content for word in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS + CAMPAIGN_KEYWORDS):
        return False
    if has_store_inquiry(content) or has_project_consult_intent(content) or has_generic_project_request(content):
        return False
    if not (re.search(r"[\u4e00-\u9fa5]{2,8}区$", content) or any(term in content for term in ["机场附近", "火车站附近", "高铁站附近", "商圈附近"])):
        return False
    recent = recent_conversation_text(state, limit=6)
    if any(term in recent for term in ["门店", "地址", "哪家", "附近", "导航", "停车", "哪个区", "哪一片", "近一点", "推荐"]):
        return True
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    if str(basic.get("city") or "").strip():
        return True
    history_events = state.get("history_events") if isinstance(state.get("history_events"), list) else []
    for event in reversed(history_events[-5:]):
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "").strip() == "store_inquiry":
            return True
    return False


def _city_from_area_text(text: str) -> str:
    area_map = {
        "湖里": "厦门",
        "思明": "厦门",
        "枋湖": "厦门",
        "浦东": "上海",
        "虹桥": "上海",
        "小寨": "西安",
        "中贸": "西安",
        "未央": "西安",
        "碑林": "西安",
    }
    for hint, city in area_map.items():
        if hint in text:
            return city
    return ""


def _drop_spurious_image_intent_for_store_turn(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_info = state.get("image_info") or {}
    if image_info.get("has_image"):
        return intents
    if not any(item.get("intent") == "store_inquiry" for item in intents):
        return intents
    return [item for item in intents if item.get("intent") != "image_inquiry"]


def _is_case_request_or_followup(content: str, state: AgentState) -> bool:
    text = content or ""
    if has_case_request(text):
        return True
    if _negates_price_inquiry(text) and any(term in text for term in ["效果", "案例", "祛斑", "淡斑", "做完", "前后"]):
        return True
    if not _recent_case_context(state):
        return False
    followup_terms = [
        "发我看看",
        "发我看",
        "合适案例",
        "祛斑效果",
        "淡斑效果",
        "做完效果",
        "做完后的变化",
        "做完后变化",
        "效果变化",
        "做完后什么样",
        "前后对比",
        "看看效果",
        "看效果",
    ]
    return any(term in text for term in followup_terms)


def _recent_case_context(state: AgentState) -> bool:
    snippets: list[str] = []
    for item in (state.get("conversation_history") or [])[-8:]:
        if isinstance(item, str):
            snippets.append(item)
        elif isinstance(item, dict):
            snippets.append(str(item.get("content") or item.get("text") or ""))
    snippets.append(str(state.get("normalized_content") or ""))
    text = " ".join(snippet for snippet in snippets if snippet)
    return any(term in text for term in ["案例", "效果对比", "对比照", "客户做完", "前后对比", "发我看看"])


def _is_effect_timeline_followup(content: str, state: AgentState) -> bool:
    text = content or ""
    if not _recent_case_context(state):
        return False
    return any(term in text for term in ["几次", "多久", "多长时间", "看到变化", "见效", "变化", "周期"])


def _is_project_direction_followup(content: str, state: AgentState) -> bool:
    text = content or ""
    if not text:
        return False
    direct_terms = [
        "别一直问我",
        "不要一直问",
        "别老问我",
        "别问了",
        "你直接说",
        "先说方向",
        "先说我这种",
        "先说我这个",
        "你判断",
        "你就说",
        "我不懂项目",
        "先看什么方向",
        "先看哪个方向",
    ]
    if not any(term in text for term in direct_terms):
        return False
    if any(term in text for term in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS):
        return False
    recent = recent_conversation_text(state, limit=8)
    project_context_terms = [
        "点状斑",
        "片状",
        "色沉",
        "肤色不均",
        "淡斑",
        "祛斑",
        "毛孔",
        "暗沉",
        "看图",
        "照片",
        "适合什么方向",
        "可以先看",
        "项目方向",
        "改善方向",
    ]
    image_info = state.get("image_info") or {}
    visible_concerns = image_info.get("visible_concerns") or []
    return bool(
        any(term in recent for term in project_context_terms)
        or image_info.get("has_image")
        or visible_concerns
    )


def _recent_ad_context(state: AgentState) -> bool:
    recent = recent_conversation_text(state, limit=8)
    return any(term in recent for term in ["广告", "活动", "199", "268", "380", "祛斑", "淡斑", "效果", "到店"])


def _has_recent_project_or_ad_context(state: AgentState) -> bool:
    recent = recent_conversation_text(state, limit=8)
    return any(term in recent for term in ["广告", "活动", "祛斑", "淡斑", "点状斑", "色沉", "效果", "价格", "收费", "案例"])


def _is_ad_multi_intent_opening(content: str) -> bool:
    text = content or ""
    if not (is_broad_ad_intro(text) or "广告" in text or "活动" in text):
        return False
    matched = sum(1 for term in ["祛斑", "淡斑", "价格", "效果", "到店", "安排"] if term in text)
    return matched >= 2


def _first_need_hint(content: str) -> str:
    text = content or ""
    for term in ["祛斑", "淡斑", "黑色素", "抗衰", "补水", "毛孔", "暗沉", "色沉", "松弛", "提升"]:
        if term in text:
            return term
    return ""


def _need_intro_tool_plan(content: str, need_hint: str) -> list[dict[str, Any]]:
    query = need_hint or content or "项目咨询"
    tool_plan: list[dict[str, Any]] = [
        {
            "name": "kb_search",
            "kb_name": SALES_TALK_KB_NAME,
            "query": query,
            "purpose": "检索当前改善需求对应的承接策略、客户友好表达和推进节奏。",
        },
        {
            "name": "kb_search",
            "kb_name": "project_qa",
            "query": query,
            "purpose": "检索当前改善需求对应的项目方向、效果说明和回答要点。",
        }
    ]
    if need_hint:
        for case_query in build_case_query_candidates(
            need_hint,
            base_terms=[need_hint, "案例", "效果", "前后对比", "改善参考"],
            face_hint=need_hint in {"祛斑", "淡斑", "黑色素", "色沉", "暗沉", "抗衰", "松弛", "提升", "补水", "毛孔"},
        ):
            tool_plan.append(
                {
                "name": "kb_search",
                "kb_name": "case_studies",
                "query": case_query,
                "purpose": "检索当前改善需求对应的同类效果参考和案例素材。",
                }
            )
    return tool_plan


def _normalize_explicit_need_project_intents(state: AgentState, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    city = extract_city(content)
    need_hint = _first_need_hint(content)
    explicit_store = has_store_inquiry(content)
    case_queries = (
        build_case_query_candidates(
            need_hint,
            base_terms=expand_case_query_terms(need_hint, [need_hint, "案例", "效果", "前后对比", "改善参考"])[:12],
            face_hint=need_hint in {"祛斑", "淡斑", "黑色素", "色沉", "暗沉", "抗衰", "松弛", "提升", "补水", "毛孔"},
        )
        if need_hint
        else []
    )
    known_info: list[str] = []
    if city:
        known_info.append(f"客户当前城市：{city}")
    if need_hint:
        known_info.append(f"客户当前需求：{need_hint}")
    replacement = {
        "priority": 1,
        "known_info": known_info,
        "missing_info": [],
        "reply_goal": "先承接这类大多数我们都可以做，再给方向、案例参考和一个客户听得懂的问题；如果城市已知，只把它作为后续到店信息，不回头先问城市。",
        "should_ask": False,
        "tool_plan": [
            {
                "name": "kb_search",
                "kb_name": SALES_TALK_KB_NAME,
                "query": need_hint or content or "项目咨询",
                "purpose": "检索当前改善需求对应的承接策略、客户友好表达和推进节奏。",
            },
            {
                "name": "kb_search",
                "kb_name": "project_qa",
                "query": need_hint or content or "项目咨询",
                "purpose": "检索当前改善需求对应的项目方向、效果说明和回答要点。",
            },
            *(
                [
                    {
                        "name": "kb_search",
                        "kb_name": "case_studies",
                        "query": case_query,
                        "purpose": "检索当前改善需求对应的同类效果参考和案例素材。",
                    }
                    for case_query in case_queries
                ]
                if case_queries
                else []
            ),
        ],
    }
    result: list[dict[str, Any]] = []
    injected = False
    for item in intents:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or "")
        if not explicit_store and intent in {"store_inquiry", "appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
            continue
        if intent in {"project_inquiry", "image_inquiry", "case_request"} and not injected:
            merged = dict(item)
            merged.update(replacement)
            if intent == "case_request":
                merged["intent"] = "project_inquiry"
            result.append(merged)
            injected = True
            continue
        if intent == "emotion_chat" and str(item.get("skill") or "") == "direct_reply":
            continue
        result.append(item)
    if not injected:
        result.append(
            {
                "intent": "project_inquiry",
                "skill": "project_consult",
                "priority": 1,
                "reason": "客户当前已明确给出改善需求，应直接进入项目承接，不先按普通寒暄处理。",
                **replacement,
            }
        )
    return dedupe_intents(result)


def _recent_need_hint_from_state(state: AgentState) -> str:
    text = recent_conversation_text(state, limit=8)
    need_hint = _first_need_hint(text)
    if need_hint:
        return need_hint
    followup_label = _recent_type_followup_family(text)
    family_map = {
        "spot_family": "祛斑",
        "lift_family": "抗衰",
        "hydrate_family": "补水",
        "pore_family": "毛孔",
    }
    return family_map.get(followup_label, "")


def _is_customer_need_type_followup_state(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or "").strip()
    if not is_customer_need_type_followup(content):
        return False
    if any(word in content for word in PRICE_KEYWORDS + TRUST_KEYWORDS + COMPETITOR_KEYWORDS + AFTER_SALES_KEYWORDS + CAMPAIGN_KEYWORDS):
        return False
    return bool(_recent_need_hint_from_state(state))


def _type_followup_project_intent(state: AgentState) -> dict[str, Any]:
    content = str(state.get("normalized_content") or "").strip()
    need_hint = _recent_need_hint_from_state(state)
    city = extract_city(recent_conversation_text(state, limit=8)) or extract_city(content)
    type_label = customer_need_type_label(content)
    known_info: list[str] = []
    if city:
        known_info.append(f"客户当前城市：{city}")
    if need_hint:
        known_info.append(f"客户当前需求：{need_hint}")
    if type_label:
        known_info.append(f"客户已补充类型：{type_label}")
    elif content:
        known_info.append(f"客户补充类型：{content}")
    return {
        "intent": "project_inquiry",
        "skill": "project_consult",
        "priority": 1,
        "reason": "客户正在回答上一轮的类型判断问题，应继续项目承接而不是退回闲聊或门店分流。",
        "known_info": known_info,
        "missing_info": [],
        "reply_goal": "客户已经补充了上一轮要判断的类型，本轮必须把这个类型当成已知事实来承接；先给适合的改善方向、效果信心和下一步到店检测/预约钩子，禁止重复上一轮的类型三选一问题。",
        "should_ask": False,
        "tool_plan": _need_intro_tool_plan(" ".join(part for part in [need_hint, content] if part), need_hint),
    }


def _recent_type_followup_family(text: str) -> str:
    recent = str(text or "")
    if any(term in recent for term in ["零散小点", "成片颜色重", "整体肤色暗沉不均"]):
        return "spot_family"
    if any(term in recent for term in ["脸有点松", "轮廓没以前紧", "法令纹", "嘴角这些纹路更明显"]):
        return "lift_family"
    if any(term in recent for term in ["干燥缺水", "上妆卡粉", "整体肤色发闷没光泽"]):
        return "hydrate_family"
    if any(term in recent for term in ["毛孔粗", "出油黑头", "痘印痘坑"]):
        return "pore_family"
    return ""
