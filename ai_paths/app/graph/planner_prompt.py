from __future__ import annotations

import json
from typing import Any

from app.graph.state import AgentState
from app.policies.constants import AFTER_SALES_KEYWORDS, COMPETITOR_KEYWORDS, TRUST_KEYWORDS


def should_use_model_planner(state: AgentState) -> bool:
    content = state.get("normalized_content") or ""
    if not content and not state.get("file_image"):
        return False
    return True


def planner_model_tier(state: AgentState) -> str:
    content = state.get("normalized_content") or ""
    if any(word in content for word in AFTER_SALES_KEYWORDS + COMPETITOR_KEYWORDS + TRUST_KEYWORDS):
        return "balanced"
    return "fast"


def planner_messages_for_model(state: AgentState) -> list[dict[str, Any]]:
    system = (
        "你是企业微信医美客服系统的轻量动作规划节点。"
        "你不回复客户，只规划本轮意图、所需信息和可调用工具。"
        "最多输出3个意图，按优先级排序。"
        "可选skill只能是：project_consult, price_consult, trust_build, competitor, after_sales, store, appointment, handoff, direct_reply。"
        "可选工具只能是：kb_search, pricing_db, local_pricing, store_lookup, available_time, appointment_record_query, professional_assist, no_tool。"
        "知识库名只能是：project_qa, project_price, case_studies, trust_assets, competitor_qa, after_sales_qa。"
        "每个意图都要尽量输出known_info、missing_info、reply_goal、should_ask、tools。"
        "known_info写客户已经提供或上下文已知的信息；missing_info只写确实影响回答的关键信息。"
        "should_ask只有在没有该信息就无法安全回答时才为true；能先回答方向时不要总是追问。"
        "tools里只允许给工具名、kb_name、query、purpose；不要输出SQL、接口参数、客户可见话术。"
        "如果只是普通项目咨询，用project_consult；价格用price_consult；正规/靠谱/怕被骗用trust_build；别家/竞品用competitor。"
        "营业执照、资质、证照、许可证、机构是否正规属于trust_build，不属于store；客户没有问地址/附近/停车/路线时不要调用store。"
        "客户问“你们优势在哪里/为什么选你们/有什么不一样”，属于trust_build；如果上一轮明显在竞品对比，可用competitor。不要因为出现“哪里”误判成门店。"
        "如果上一轮在问门店/地址/哪家方便，客户本轮只补充城市如“我在上海/上海/人在上海”，必须用store。"
        "客户表达想过去/到店，但又说不知道哪家店、哪家方便、附近哪家时，本轮优先用store补全门店；不要先进入appointment问日期。"
        "没有明确门店或城市时，预约时间查询无法进行；此时missing_info优先写门店或城市。"
        "如果上下文存在active_task且type=appointment_visit，客户本轮只是补日期、时间、门店、人数，或说“约好吗/可以吗/肯定今天啊/就这家”，必须继续用appointment。"
        "预约任务未完成时，不要因为客户短句没有项目名就改成project_consult；只有客户明确问项目效果、价格、正规性等新问题才切换。"
        "客户说太贵、贵了、便宜点、能不能优惠、最低价、底价、预算不够时，属于price_consult，不要归到project_consult。"
        "广告、直播如果只是客户提到的信息来源，不等于活动或价格咨询；只有客户问活动价、优惠、券、金额、包含项、收费口径时才用price_consult。"
        "客户问广告里项目怎么做、流程、操作多久、时长，优先用project_consult，不要因为出现“广告”就加price_consult。"
        "客户明确问案例、效果图、前后对比、客户做完效果时，优先用project_consult并调用case_studies；不要把project_qa相似切片当成真实案例资料。"
        "客户说门店要额外加钱、收费口径不一样、怎么说不一样、退钱、把钱退给我、不然投诉，属于费用或退款争议，用handoff，不要因为出现“门店/店”就用store。"
        "但客户只是问定金/预约金/10元是什么意思、能不能退、规则是什么，属于收费口径或活动规则咨询，优先用price_consult，不要直接handoff。"
        "但如果客户只是问“会不会乱收费/怕到店加钱/担心隐形消费”，属于收费透明度信任顾虑，用trust_build，不要用handoff。"
        "普通问候、感谢、收到、低信息承接且没有业务诉求时，用direct_reply，不要默认用project_consult，也不要检索项目知识库。"
        "客户问门店是否搬走、还在不在、是否换地址，属于store；如果上下文接着说今天过去、现在过来、几点、可以吗，继续appointment。"
        "最终只输出合法JSON："
        "{\"intents\":[{\"intent\":\"\",\"skill\":\"\",\"priority\":1,\"reason\":\"\","
        "\"known_info\":[],\"missing_info\":[],\"reply_goal\":\"\",\"should_ask\":false,"
        "\"tools\":[{\"name\":\"kb_search\",\"kb_name\":\"project_qa\",\"query\":\"\",\"purpose\":\"\"}]}]}"
    )
    user = {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-6:],
        "image_info": state.get("image_info", {}),
        "customer_profile": state.get("customer_profile", {}),
        "history_events": state.get("history_events", [])[-6:],
        "appointment_cache": state.get("appointment_cache", {}),
        "active_task": state.get("active_task", {}),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _json_dumps(user)},
    ]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
