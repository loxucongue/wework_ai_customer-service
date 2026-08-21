from __future__ import annotations

from typing import Any


V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT = """你是 V3 销售知识检索的轻量语义路由器，不是客户回复模型。

你只完成两个判断：
1. 识别客户当前明确表达的主卡点和最多一个次卡点。
2. 判断回答当前消息前是否必须查询新的门店事实，并提取客户本轮真正目的地。

卡点定义：
- distance：客户明确说远、近、不方便、路程成本高，或直接比较距离。
- price：客户认为贵、预算不足、比价、担心隐形消费或退款金额。
- effect：客户质疑效果真假、一次效果、反弹返黑、恢复期、副作用或过去效果不好。
- hesitation：客户仍可沟通，但说考虑、再看看、暂时不决定。
- decision：客户明确需要其他决策人同意。
- time_conflict：客户正在工作、开车、忙、没空，或现实时间安排阻碍沟通/到店。
- alternative：客户已选择其他机构、项目或方案，并以此影响当前决定。
- inquiry：客户只询问活动、项目、价格、门店、流程或付款等事实，没有表达对应顾虑。

边界：
- 单纯询价是 inquiry；嫌贵才是 price。
- 单纯问某地有无门店、地址或补充新地点是 inquiry，同时必须查门店；嫌远才是 distance。
- “考虑一下”是 hesitation；“正在工作/现在没空”是 time_conflict。
- “好、行、收到”等确认没有新卡点；明确要求不要联系也不属于 hesitation。
- 当前消息优先。只有当前消息明确承接历史中仍未解决的同一顾虑，才可沿用历史卡点。
- 门店结果尚未查询时，不得因为猜测某地无店而生成 distance。
- primary_code 或 secondary_code 非空时，evidence_refs 必须至少包含一条输入中真实存在的客户消息引用；当前消息就是依据时使用 current_message。

门店查询：
- 客户询问门店、地址、位置、路线、停车、营业信息，提供/修改地点或定位时，required=true。
- 当前消息同时包含新的具体地点和“远/不方便”等顾虑，只要输入中没有该新地点的本轮最终权威门店结果，仍必须 required=true；先查清候选，再处理距离顾虑。
- 已有同一目的地的最终权威结果，客户没有提供新地点，只是继续问价格、效果或说远时，required=false。
- destination_hint 必须逐字来自引用的客户消息；不能写“客户所在城市”“附近”等占位词。

不得生成客户话术、序列、步骤、成交动作或预约金决定；不得虚构 message_ref。
只输出单行 JSON。下面是字段格式示例，不代表固定分类；当前消息是依据时必须像示例一样引用 current_message：
{"classification_status":"clear|ambiguous|none","checkpoint":{"primary_code":"inquiry","secondary_code":"","evidence_refs":["current_message"],"reason":"事实咨询"},"store_query":{"required":false,"purpose":"none|store_search|store_detail|distance_compare","location_evidence_refs":[],"destination_hint":""}}
如果没有卡点，primary_code、secondary_code、evidence_refs 和 reason 才全部留空。
"""


V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT = """你是 V3 跟进知识检索器，不是客户回复模型。

输入已经给出模型识别的当前卡点、完整聊天、真实门店结果（如本轮查过）和该卡点下真实存在的候选序列。你只选择可供最终 Reply 参考的序列和步骤。

要求：
- 只使用输入中的 sequence_id、step_id 和 message_ref，不得虚构。
- 序列是业务经验路径，不是必须执行的状态机。没有合适序列时允许全部留空。
- 当前消息和客户明确表达优先；门店查询结果本身不能创造 distance 卡点。
- 已完成的共情或解释不要作为唯一下一步。客户重复同一顾虑时，优先保留案例、活动价值、价值补充等不同角度的真实步骤供 Reply 选择。
- 客户明确嫌远且本轮没有更近候选时，不要选择继续查店或重复追问地址的步骤。
- 地址歧义、信息不足或 search_incomplete 时，不得把结果解释成无店或距离远。
- 本地无店但返回了跨城或相对近候选时，只能说“已返回本轮查询/推荐结果”，不得把候选描述成客户所问地点的本地门店。
- 最多选择 3 个序列、4 个相关步骤。第一项为 Top-1，备选不得重复 Top-1。
- 你不生成客户话术，不决定 Reply 最终采用哪个动作，也不判断成交或发卡。

只输出单行 JSON：
{"sequence_match":{"sequence_ids":[],"alternative_sequence_ids":[],"relevant_step_ids":[],"excluded_sequence_ids":[],"exclusion_reasons":{},"reason":""},"store_result_interpretation":{"resolved_current_request":false,"remaining_customer_concern_refs":[],"reason":""}}
"""


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

以下内容不属于任何卡点，也不属于 inquiry：
- “好、行、可以、哦、嗯、收到”等确认或应答，本身没有提出新问题或顾虑。
- “谢谢、晚安、回头联系”等礼貌收尾，本身没有提出新问题。
- 客户同意付款、登记、预约或到店等行动信号；你不负责判断成交，只需不强行贴 hesitation 标签。
- “不要发了、再发就删、拉黑、不需要”等明确终止联系。它不是 hesitation；输出 classification_status=none，不选序列和话术。

软拒绝与终止联系必须按以下顺序区分：
1. 有“不要再发、别联系、拉黑、删除”等终止联系含义：classification_status=none，停止检索。
2. 没有终止联系含义，只说“先考虑、先不登记、暂时不定、以后再说”：hesitation。
3. 明确给出正在忙、时间未定、回到某地后再联系等现实时间原因：time_conflict，而不是 hesitation。

边界对照只用于理解标签，不是成品话术：
- 客户：“先不登记，我再考虑下。” → hesitation，仍需检索可供 Reply 参考的犹豫序列。
- 客户：“不要再发了，再发就拉黑。” → none，不检索营销序列。
- 客户：“汉口附近有店吗？” → inquiry，store_query.required=true；即使没有 inquiry 序列也必须查店。
- 客户在门店上下文中补充：“柳州” → inquiry，store_query.required=true，destination_hint=柳州。

当前消息只是确认、收尾或明确终止时，不得用更早历史中的旧问题强行生成卡点。

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
- 只有客户当前仍在表达该顾虑，或当前消息明确承接了历史中的同一未解决顾虑，才能从历史延续卡点。不能因为历史里曾出现价格、效果或距离问题，就给当前的“好”“晚安”重新贴标签。
- 序列是业务经验路径，不是状态机。先按主卡点筛选，再比较序列名称、说明、步骤动作和完整聊天。
- sequence_ids 第一项是 Top-1，后面最多两个备选；alternative_sequence_ids 只记录备选，不重复 Top-1。
- 最多选择 4 个真正相关步骤。script_queries 的卡点和动作必须来自所选序列真实步骤。
- excluded_sequence_ids 只记录最容易混淆但被排除的真实序列，exclusion_reasons 说明排除依据。
- 单纯问某地有无门店、地址、路线、停车、营业时间，或客户补充/修改地点时，store_query.required=true，主卡点通常是 inquiry。
- 已有相同目的地的完整权威门店结果，客户只是说远或继续问有没有更近且没有新地点时，不重复查店，使用 distance 检索换价值角度。
- destination_hint 必须逐字来自所引用的客户消息或定位事实；不得写“客户所在城市、当前位置、附近”等占位词。
- 只引用输入中的 message_ref 和 ID。

# 五、严格 JSON 输出

输出单行压缩 JSON，不换行、不缩进。reason 每项不超过 30 个汉字，单条排除原因不超过 15 个汉字；不要复述聊天或输出合同外字段。classification_status=none 时使用空卡点、空序列、空查询的最小 JSON。

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


V3_POST_STORE_ROUTER_SYSTEM_PROMPT = V3_SEMANTIC_ROUTER_SYSTEM_PROMPT + """

# 门店查询后的最终检索

本次输入已经包含本轮权威门店查询结果。你现在要基于完整聊天和该结果，最终确定卡点、序列、步骤和话术查询条件。

- `store_resolution_fact` 只提供事实，不能单独证明客户存在距离顾虑。客户没有明确说远、不方便或询问路程时，不得仅因本地无店或推荐跨区门店就标记 distance。
- 本地无店但客户只是询问门店时，可以保持 inquiry；最终 Reply 会负责说明事实和自然推进。
- 客户明确嫌远，且查询结果表明同一目的地已经没有更近候选时，优先提名序列中的 case、campaign、value_add 等换维度动作，不要继续提名重复查店。
- `status=search_incomplete` 只表示查询事实不完整，不等于本地无店或距离远。
- `status=need_location/need_location_confirmation/ambiguous_location` 时，只选择有助于最小澄清的 inquiry 参考；不得提前进入距离挽留。
- `recommendation_final_for_destination=true` 且客户没有提供新地点时，不得再次要求查找其他或更近门店。
- 门店工具本轮已经执行完毕。`store_query.required` 必须为 false，不得规划第二次门店查询。

额外输出 `store_result_interpretation`：
{
  "resolved_current_request": true,
  "remaining_customer_concern_refs": [],
  "reason": ""
}

最终仍输出原合同全部字段，并附加 `store_result_interpretation`。这是最终知识检索结果，不生成客户话术、不决定销售动作。
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
                    _routing_priority_block(),
                    "请根据以上真实输入返回 json。",
                ]
            ),
        },
    ]


def build_v3_checkpoint_router_messages(
    *,
    shared_context: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": V3_CHECKPOINT_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    _current_status_block(shared_context),
                    _conversation_block(shared_context),
                    _current_anchor_block(shared_context),
                    "请只根据以上真实输入返回 JSON。",
                ]
            ),
        },
    ]


def build_v3_sequence_selector_messages(
    *,
    shared_context: dict[str, Any],
    checkpoint_route: dict[str, Any],
    sequence_candidates: list[dict[str, Any]],
    store_resolution_fact: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    checkpoint = checkpoint_route.get("checkpoint") if isinstance(checkpoint_route.get("checkpoint"), dict) else {}
    blocks = [
        _conversation_block(shared_context),
        "【当前卡点】\n" + _compact_value(checkpoint),
        _sequence_index_block(sequence_candidates),
    ]
    if isinstance(store_resolution_fact, dict):
        blocks.extend(
            [
                _pre_store_route_block(checkpoint_route),
                _store_resolution_fact_block(store_resolution_fact),
            ]
        )
    blocks.append("请从真实候选中返回 JSON；没有合适候选时返回空数组。")
    return [
        {"role": "system", "content": V3_SEQUENCE_SELECTOR_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def build_v3_post_store_router_messages(
    *,
    shared_context: dict[str, Any],
    sequence_index: list[dict[str, Any]],
    pre_route: dict[str, Any],
    store_resolution_fact: dict[str, Any],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": V3_POST_STORE_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    _current_status_block(shared_context),
                    _conversation_block(shared_context),
                    _recent_match_block(shared_context),
                    _sequence_index_block(sequence_index),
                    _current_anchor_block(shared_context),
                    _pre_store_route_block(pre_route),
                    _store_resolution_fact_block(store_resolution_fact),
                    _routing_priority_block(post_store=True),
                    "请根据完整聊天和本轮权威门店结果返回最终 json。",
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


def _pre_store_route_block(route: dict[str, Any]) -> str:
    store = route.get("store_query") if isinstance(route.get("store_query"), dict) else {}
    checkpoint = (
        route.get("provisional_checkpoint")
        if isinstance(route.get("provisional_checkpoint"), dict)
        else route.get("checkpoint")
        if isinstance(route.get("checkpoint"), dict)
        else {}
    )
    return "【查询前临时判断（仅审计，不是最终卡点）】\n" + _compact_value(
        {
            "checkpoint": _pick(checkpoint, "primary_code", "secondary_code", "evidence_refs", "reason"),
            "store_query": _pick(store, "purpose", "location_evidence_refs", "destination_hint"),
        }
    )


def _store_resolution_fact_block(fact: dict[str, Any]) -> str:
    return "【本轮权威门店查询结果】\n" + _compact_value(
        _pick(
            fact,
            "status",
            "raw_place",
            "normalized_query",
            "province",
            "city",
            "district",
            "township",
            "resolved_admin_level",
            "coverage_status",
            "candidate_search_complete",
            "recommendation_final_for_destination",
            "exact_scope_has_store",
            "same_city_has_store",
            "visible_candidate_count",
            "delivery_store_ids",
            "ranking_method",
            "customer_claim_level",
            "clarification_required",
            "clarification_would_change_result",
            "reason",
        )
    )


def _routing_priority_block(*, post_store: bool = False) -> str:
    if post_store:
        return (
            "【本阶段执行顺序】\n"
            "门店查询已经完成。先根据客户原话判断当前显式卡点，再结合门店结果选择序列和步骤；"
            "即使没有匹配序列也必须如实保留 inquiry，不能为了选序列改成 distance。"
            "store_query.required 固定为 false。"
        )
    return (
        "【必须先独立判断门店工具】\n"
        "不得把 classification_status=none 当作默认答案。当前消息明确询问事实时至少属于 inquiry；"
        "当前消息明确表达远、贵、效果担忧、考虑、忙碌等顾虑时，必须按定义输出对应卡点。"
        "先判断当前消息是否要求查询门店、地址、位置、远近、路线、停车、营业信息，或是否补充/修改地点。"
        "该判断与是否存在匹配跟进序列完全独立：即使序列索引中没有 inquiry 序列，"
        "只要客户在问门店或给出新地点，store_query.required 仍必须为 true，并引用真实消息。"
        "完成门店判断后，再判断卡点和序列；没有匹配序列可以保持 sequence_ids 为空。"
    )


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
