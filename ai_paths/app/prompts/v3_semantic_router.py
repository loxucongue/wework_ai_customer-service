from __future__ import annotations

from typing import Any


V3_SEMANTIC_ROUTER_SYSTEM_PROMPT = """你是 V3 销售知识检索路由器。你只负责理解检索条件，不负责客户回复或成交决策。

# 一、职责和禁止事项

你只做三件事：
1. 从当前消息和完整聊天中识别客户当前显式卡点。
2. 从输入中选择真实存在的跟进序列、步骤和话术查询条件。
3. 判断本轮是否缺少必须实时查询的门店事实。

不得生成客户话术，不得判断是否成交、发预约金卡、暂停或如何推进；不得虚构卡点、地点、message_ref、sequence_id、step_id。

# 二、卡点标签

- distance 距离/便利：客户明确认为远、近、不方便、路程成本高，或直接询问距离。单纯说城市、找店、问地址不属于 distance。
- price 价格/费用：客户质疑贵、预算不足、比价、担心隐形消费或退款金额。单纯询问活动多少钱、包含什么属于 inquiry。
- effect 效果疑虑：客户怀疑真假、一次效果、反弹反黑、恢复期、副作用，或过去效果不好。普通项目范围咨询属于 inquiry。
- hesitation 犹豫拖延：客户仍可沟通，但表示考虑、再看看、暂不决定。明确拒绝联系不是 hesitation。
- decision 决策权：客户明确表示需要家人、朋友或其他决策人同意。多人同行本身不是 decision。
- time_conflict 时间冲突：客户明确正在工作、开车、忙、没空，或现实时间安排阻碍沟通/到店。单纯问营业时间属于 inquiry。
- alternative 已有替代：客户明确已经选择其他机构、项目或解决方案，并以此影响当前决定。普通比价优先是 price。
- inquiry 单纯咨询：活动、项目范围、门店地址、流程、付款方式等事实问题，尚未表达对应顾虑。

没有明确卡点时允许 primary_code 留空。复合情况只选一个最影响当前决策的主卡点和最多一个次卡点；每个标签必须有真实客户消息引用，不能从客服话术推断客户心理。

# 三、动作标签与历史完成度

- empathy 共情引导：承接客户现实感受。
- resolve 解决疑虑：解释事实、澄清误解。
- case 效果案例：用真实案例或效果素材举证。
- campaign 活动邀约：介绍活动价值或资格。
- low_barrier 低门槛邀请：降低客户下一步行动成本。
- value_add 价值补充：补充未覆盖的新价值角度。
- care 关怀回访：关心客户近况，不强推成交。
- appt_confirm 预约确认：确认已存在的预约或到店安排。

阅读历史中已经真实发送的文字和结构素材，判断哪些动作已经完成。动作完成只影响检索优先级，不代表客户顾虑已经解决。客户连续表达同一卡点时，不要只重复已经完成的 empathy/resolve；若序列含 case、campaign、value_add 等不同动作，应同时提名尚未交付的动作供 Reply 选择。

# 四、序列、步骤、话术和门店选择

- 当前消息权重最高；历史只用于理解指代、改口、已交付动作和尚未解决的显式顾虑。
- 序列是业务经验路径，不是状态机。先按主卡点筛选，再比较序列名称、说明、步骤动作和完整聊天。
- sequence_ids 第一项是 Top-1，后面最多两个备选；alternative_sequence_ids 只记录备选，不重复 Top-1。
- 最多选择 4 个真正相关步骤。script_queries 的卡点和动作必须来自所选序列真实步骤。
- excluded_sequence_ids 只记录最容易混淆但被排除的真实序列，exclusion_reasons 说明排除依据。
- 单纯问某地有无门店、地址、路线、停车、营业时间，或客户补充/修改地点时，store_query.required=true，主卡点通常是 inquiry。
- 已有相同目的地的完整权威门店结果，客户只是说远或继续问有没有更近且没有新地点时，不重复查店，使用 distance 检索换价值角度。
- destination_hint 必须逐字来自所引用的客户消息或定位事实；不得写“客户所在城市、当前位置、附近”等占位词。
- 只引用输入中的 message_ref 和 ID。

# 五、严格 JSON 输出

{
  "classification_status":"clear | ambiguous | none",
  "checkpoint":{"primary_code":"","secondary_code":"","evidence_refs":[],"reason":""},
  "sequence_match":{
    "sequence_ids":[],
    "alternative_sequence_ids":[],
    "relevant_step_ids":[],
    "excluded_sequence_ids":[],
    "exclusion_reasons":{},
    "reason":""
  },
  "store_query":{"required":false,"purpose":"none","location_evidence_refs":[],"destination_hint":""},
  "script_queries":[{"checkpoint_code":"","action_code":"","sequence_id":"","step_id":""}]
}
"""


V3_SCRIPT_SELECTOR_SYSTEM_PROMPT = """你是 V3 参考话术检索器，不是客户回复模型。

从输入提供的真实候选中，选择最能帮助最终 Reply 理解当前卡点和业务处理逻辑的参考话术。通常只保留 2–3 条逻辑互补的候选；只有每条都提供明显不同的处理角度时才可增加，但不得超过给定上限。只输出严格 json。

要求：
- 只选择输入存在的 script_code。
- 参考话术可能含旧价格、旧活动、赠品或不可靠承诺；这里仅选择其销售思路和语气，不把内容认定为权威事实。
- 不生成客户话术，不决定成交动作，不补充事实。
- 优先互补而不是选择多条重复表达。同一结论只是换措辞不算互补，必须删掉重复候选。

输出：{"selected_script_ids":[], "reason":""}
"""


def build_v3_semantic_router_messages(
    *,
    shared_context: dict[str, Any],
    sequence_index: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": V3_SEMANTIC_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    _current_status_block(shared_context),
                    _conversation_block(shared_context),
                    _recent_match_block(shared_context),
                    _sequence_index_block(sequence_index),
                    _current_anchor_block(shared_context),
                    "请根据以上真实输入返回 json。",
                ]
            ),
        },
    ]


def build_v3_script_selector_messages(
    *,
    shared_context: dict[str, Any],
    semantic_route: dict[str, Any],
    candidates: list[dict[str, Any]],
    max_scripts: int,
) -> list[dict[str, str]]:
    checkpoint = semantic_route.get("checkpoint") if isinstance(semantic_route.get("checkpoint"), dict) else {}
    lines = []
    for item in candidates:
        media = item.get("media") if isinstance(item.get("media"), dict) else {}
        lines.append(
            "｜".join(
                [
                    str(item.get("script_code") or ""),
                    str(item.get("checkpoint_name") or item.get("checkpoint_code") or ""),
                    str(item.get("action_name") or item.get("action_code") or ""),
                    str(item.get("script_name") or ""),
                    str(item.get("body_text") or "")[:500],
                    f"素材:{str(media.get('url') or '')[:240]}" if media.get("url") else "",
                ]
            )
        )
    return [
        {"role": "system", "content": V3_SCRIPT_SELECTOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    _conversation_block(shared_context),
                    "【本轮卡点】\n"
                    + f"{checkpoint.get('primary_code', '')}｜{checkpoint.get('reason', '')}",
                    f"【最多选择】\n{max_scripts}",
                    "【候选话术】\n" + "\n".join(lines),
                    "请返回 json。",
                ]
            ),
        },
    ]


def _current_status_block(shared: dict[str, Any]) -> str:
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    order = facts.get("orders_and_payment") if isinstance(facts.get("orders_and_payment"), dict) else {}
    registration = facts.get("registration_facts") if isinstance(facts.get("registration_facts"), dict) else {}
    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    resolved = order.get("resolved_payment") if isinstance(order.get("resolved_payment"), dict) else {}
    orders = [item for item in order.get("orders") or [] if isinstance(item, dict)]
    case_delivery = sent.get("case_image_delivery") if isinstance(sent.get("case_image_delivery"), dict) else {}
    store_delivery = sent.get("store_address_delivery") if isinstance(sent.get("store_address_delivery"), dict) else {}
    lines = [
        "支付：" + _compact_value(_pick(resolved, "deposit_state", "payment_result", "amount", "source", "paid_protection_status")),
        "订单：" + _compact_value({"count": len(orders), "latest": _pick(orders[0] if orders else {}, "status", "deposit_state", "store_id", "store_name")}),
        "登记：" + _compact_value(_pick(registration, "customer_name", "mobile")),
        "已发送：" + _compact_value(
            {
                "payment_cards": sent.get("payment_collection_count"),
                "activity_image": sent.get("activity_intro_image_sent"),
                "case_images": _pick(case_delivery, "total_events", "last_sent_at", "sent_image_urls"),
                "store_cards": _pick(store_delivery, "latest_batch_store_ids", "last_sent_at", "request_id"),
            }
        ),
    ]
    return "【当前状态】\n" + "\n".join(lines)


def _conversation_block(shared: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or "")
        role = str(item.get("role") or "")
        sent_at = str(item.get("sent_at") or item.get("timestamp") or "")
        content = str(item.get("content") or item.get("text") or "")
        lines.append(f"{ref}｜{sent_at}｜{role}：{content}")
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    lines.append(
        f"current_message｜{current.get('sent_at', '')}｜customer："
        f"{current.get('content') or current.get('raw_content') or ''}"
    )
    return "【完整聊天】\n" + "\n".join(lines)


def _recent_match_block(shared: dict[str, Any]) -> str:
    observations = shared.get("derived_observations") if isinstance(shared.get("derived_observations"), dict) else {}
    latest = observations.get("latest_follow_knowledge_usage") if isinstance(observations.get("latest_follow_knowledge_usage"), dict) else {}
    matched = observations.get("latest_follow_knowledge_match") if isinstance(observations.get("latest_follow_knowledge_match"), dict) else {}
    return "【最近知识匹配与使用（低权重，仅参考）】\n" + _compact_value({"matched": matched, "adopted": latest})


def _current_anchor_block(shared: dict[str, Any]) -> str:
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    return (
        "【当前任务锚点】\n"
        f"current_message｜{current.get('content') or current.get('raw_content') or ''}"
    )


def _sequence_index_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        steps = ", ".join(
            f"{step.get('id')}:{step.get('action_code')}"
            for step in item.get("steps") or []
            if isinstance(step, dict)
        )
        lines.append(
            "｜".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("checkpoint_code") or ""),
                    _single_line(item.get("sequence_name"), 160),
                    _single_line(item.get("description"), 240),
                    steps,
                ]
            )
        )
    return "【已启用跟进序列索引】\n" + ("\n".join(lines) or "无")


def _compact_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return "无"
    if isinstance(value, dict):
        return "；".join(
            f"{key}={_compact_value(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {}, False, 0)
        )[:1200]
    if isinstance(value, list):
        return "、".join(_compact_value(item) for item in value[:12])[:1200]
    return str(value)[:500]


def _pick(value: Any, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in keys
        if value.get(key) not in (None, "", [], {}, False, 0)
    }


def _single_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
