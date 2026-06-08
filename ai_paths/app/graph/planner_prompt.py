from __future__ import annotations

import json
from typing import Any

from app.graph.state import AgentState
from app.policies.constants import AFTER_SALES_KEYWORDS, COMPETITOR_KEYWORDS, TRUST_KEYWORDS
from app.prompts.global_contract import GLOBAL_STRUCTURED_NODE_CONTRACT
from app.prompts.prompt_sections import build_node_prompt, section


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
        GLOBAL_STRUCTURED_NODE_CONTRACT
        + build_node_prompt(
            role="你是 planner 节点，职责是识别本轮最多 3 个主要意图，并规划需要查什么事实。",
            input_items=[
                "state.normalized_content: 客户当前消息",
                "state.conversation_history: 最近历史对话",
                "state.image_info: 图片理解结果",
                "state.customer_profile / history_events / appointment_cache / active_task: 运行时上下文",
            ],
            task_items=[
                "识别本轮主要意图，并按优先级排序。",
                "为每个意图整理 known_info、missing_info、reply_goal、should_ask。",
                "只在确实需要事实补充时规划工具调用。",
            ],
            do_not_items=[
                "不要生成客户回复。",
                "不要输出 SQL、接口参数或客户可见话术。",
                "不要把未知事实脑补成确定信息。",
                "不要把普通收费顾虑直接升级为 handoff。",
            ],
            tool_items=[
                "可选 skill: project_consult, price_consult, trust_build, competitor, after_sales, store, appointment, handoff, direct_reply",
                "可选 tool: kb_search, pricing_db, local_pricing, store_lookup, available_time, appointment_record_query, appointment_create, professional_assist, no_tool",
                "可选知识库: sales_talk_qa, project_qa, project_price, case_studies, trust_assets, competitor_qa, after_sales_qa",
            ],
            output_schema=(
                "{"
                "\"intents\":[{"
                "\"intent\":\"\","
                "\"skill\":\"\","
                "\"priority\":1,"
                "\"reason\":\"\","
                "\"known_info\":[],"
                "\"missing_info\":[],"
                "\"reply_goal\":\"\","
                "\"should_ask\":false,"
                "\"tools\":[{\"name\":\"kb_search\",\"kb_name\":\"project_qa\",\"query\":\"\",\"purpose\":\"\"}]"
                "}]"
                "}"
            ),
            extra_sections=[
                (
                    "Decision Policy",
                    (
                        "普通项目咨询走 project_consult，价格和收费口径走 price_consult，"
                        "正规性和收费透明顾虑优先 trust_build，竞品和别家对比走 competitor，"
                        "门店、地址、停车、营业时间走 store，"
                        "已有门店前提下的预约时间、确认、补姓名电话走 appointment。"
                    ),
                ),
                (
                    "Context Inheritance",
                    (
                        "短句补充要继承最近明确上下文。"
                        "客户补城市、区域、机场、高铁站、附近位置时，优先延续 store。"
                        "客户补时间、姓名、电话、确认词时，优先延续 appointment。"
                        "客户追问效果图、案例、方向、做几次时，优先延续 project_consult。"
                    ),
                ),
                (
                    "Ask Policy",
                    (
                        "能先回答就先回答。"
                        "should_ask 只有在缺少关键信息会直接影响回答或下一步动作时才为 true。"
                        "每轮最多推动一个必要问题。"
                    ),
                ),
            ],
        )
        + section(
            "Tool Planning Rules",
            (
                "sales_talk_qa 用于开场、承接、信任、广告价解释和销售节奏。"
                "case_studies 只用于真实案例和效果参考。"
                "当客户只是问 10 元预约金、定金、能不能退、规则是什么时，优先 direct_reply + no_tool。"
                "只有客户已明确门店、日期、时间并表明预约/开单意图时，才规划 appointment_create。"
            ),
        )
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
