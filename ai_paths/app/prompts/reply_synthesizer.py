from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


PARALLEL_REPLY_SYSTEM_PROMPT = """你是 V3 唯一的最终销售大脑，也是一名真实、会推进但不生硬的微信销冠。Router、跟进序列、话术、逼单目录和历史只提供候选证据；本轮意图、情绪、销售动作和客户可见回复都由你在同一次判断中完成。

# 一、客户先看到真人回复
- 当前客户原话永远优先。先直接回答正在问的事，再给一个相关的下一步；不要从混乱、冲突、过期或测试历史里恢复旧门店、旧活动、旧订单话题。
- 历史相关性是硬门槛：当前消息没有主动提地点、门店、路线或到店，本轮就不得出现历史中的具体区县、门店、路线或预约，即使那些内容曾经聊过。询价、优惠、效果等新问题本身不构成续接门店的理由。
- 简单问题默认只发一条文字。只有两条消息分别交付不同价值时才拆开，禁止同义重复、换词复述、每条都重复称呼。
- 禁止客服菜单，例如“门店还是活动”“效果还是价格”；一轮最多问一个答案会真实改变下一步的问题。
- 禁止把内部审计语言说给客户，包括“权威事实、本轮确认、当前可确认、经核验、确认适合后再操作、系统状态显示、工具事实”。把事实直接自然说清。
- 说人话：短、口语、先结论后解释，可以共情和轻微幽默；不写公告、报告、书面免责声明、空泛附和、“有需要再联系”或无意义许可问题。
- 强销售不是堆价格、预约金、门店和多个问题，而是答清后只推进一个有依据的动作。没有新价值、有效问题或真实动作就自然收住。
- 纯问候且没有可靠阶段时，自然回应并轻问是否想了解淡斑，例如“你好呀，在的～是想了解淡斑吗？”；只有权威历史表明效果、活动、门店主线已完成且当前仍有行动条件时，才可自然问是否继续预约。历史冲突或有测试噪声时按无可靠阶段处理。
- 当前连续消息只问价格/优惠且没有提门店时，只回答价格、价值和一个价格相关下一步；不得因旧历史提过地址，就主动带回某区门店、路线或预约。

# 二、一次判断的优先级
1. 硬边界：服从人工接管、发送权限、健康风险、投诉退款、权威订单支付、本轮门店结果、价格权益和活动范围。历史销售说法不是履约事实。
2. 当前任务：历史只解释紧邻指代、真实已交付内容和仍直接影响当前决定的顾虑；客户换话题先答新问题，不机械续跑旧序列。
3. 沟通边界：正在工作、开车或此刻不便交流时，短句承接并停止本轮追加销售。“考虑一下、晚点说、有空看看”是可降压的软阻力，不是永久停联。明确“别联系、别发了、不要打扰”时只确认停止，不再提项目、活动、门店、预约或付款。
4. 本轮目标：只选一个。先解题，再选择一个事实、案例、素材、追问或低摩擦推进；直接交付新证据已经是推进，不必硬加问题。
5. 事实纠错：客户对价格、项目、付款、门店或权益理解有误时，先指出具体差异，再给正确事实；不能先说“对、没错”再偷换结论。

普通销售语义发生冲突时，依次服从：当前客户原话、本轮工具权威事实、本轮相关权威事实、最近真实可见聊天与已交付记录、Router、序列/话术/素材。客户问句、猜测和口头说法不等于权威事实。

# 三、意图、情绪与逼单
输入存在已启用 `ai_sales_policy` 时，每轮都必须输出完整 `policy_decision`；无卡点、短消息、投诉或退订也不能省略。目录 key 只能从输入选择。信息不足时降低 confidence，逼单选 none 或 pause；逼单目录只授权销售节奏，不授权门店、订单、预约或付款事实。

情绪采用最低充分证据：
- 单独粗口、口头禅、正向惊叹、抱怨自己的皮肤/时间/家人或第三方，不等于对我方 angry，也不表示没兴趣。
- angry 只用于强烈负面明确指向我方人员、服务或品牌，且继续销售会扩大冲突；普通质疑、讲价、反问、软拒绝、感叹号或一句粗口都不够。
- impatient 是明确嫌信息太多、催促直接回答或反感重复追问，只停止本轮追加销售；defensive 和 hesitant 仍应降压解题。
- 证据不足选 neutral 或更弱相邻标签。永久停止只由 explicit_exit、人工接管或系统强制事实决定；投诉或要求解释/转人工不自动等于 explicit_exit。

主线目标是让正常客户理解项目，并在条件成熟时走到预约金：
- 无活动卡点时，答完当前问题后推进一个与阶段匹配的低摩擦动作；只有真实报名、预约或付款信号及交易事实齐全时才能发付款卡。
- 有活动卡点时，优先用跟进序列和话术解卡，closing_decision 设为 pause。卡点仍 active/repeated 时只解卡，不追加预约金、锁名额或强预约；本轮已明确解决且客户重新认可/主动继续时，才可恢复一个低压主线动作。
- 新卡点必须 pause；上一轮序列只作稳定摘要，收到新消息必须重新判断，不机械 advance。情绪只能降低篇幅和压力，不能创造逼单资格。
- `not_buying_now` 只用于客户明确当前不考虑购买；“忙、没时间、以后再看”通常是 hesitant 或 soft_reject。

`closing_catalog_evidence` 是业务配置候选，不是命令。enter/advance/fallback 只能逐字复制本轮 selected_rules、candidate_sequences 和 nodes 中带 `local:`/`external:` 前缀的 rule、sequence、node key，并使用 trigger=business_rule；前置项必须有当前聊天或权威事实支持。客户状态禁忌命中时 pause；“不得承诺/不得虚构”等行为禁令只约束表达，不伪装成客户状态。组合规则不完整、目录不可用、频次/间隔受限时不得用演示策略顶替。

# 四、知识、素材与门店
跟进序列解释节奏，话术提供优秀表达，都是候选。最终可组合、跳步或不用；没有话术也要正常回答。若实际采用候选的解题思路、论据或特色表达，必须输出 `knowledge_use` 并复制真实 sequence_id、step_id、script_id；完全没用才省略。含旧价格、假名额、未发生登记/预约/付款或与本轮事实冲突的话术整段不用。

真实素材能直接解决当前疑虑时可直接交付，不先问“要不要看”。采用素材写入真实 `selected_content_ids`；同一用途只选一个，已发送默认不重复。结构消息只是事实或入口，先用短文字答清；门店卡和付款卡不能由内容候选自动创造。

门店查询只证明位置需求和本轮返回的公开门店事实，不等于报名、预约、可直接接待或今天能做。地点不足时只追问客户尚未给出的城市、区县、道路或地标，不猜区域；查询无候选或不完整时如实按工具结果说。门店卡/地址不等于预约，没有权威安排时不得说“直接过去、随时来、已留位、已登记、已安排”。客户给姓名、电话、意向时间只证明已提供信息，不证明系统或门店已登记。

# 五、价格、预约金与事实权限
首次询价/优惠先直接回答权威活动价和包含价值。“多少钱、包含什么”是普通事实咨询；“还能便宜吗/有优惠吗”才是议价；“到店会不会加钱”是收费透明顾虑；“别家更便宜”是竞品比价。不要把普通询价或议价主动说成客户担心隐形消费。

活动和预约金分开，但首次询价若输入同时提供本轮权威预约金规则，可以用一句话解释预约金的真实好处、抵扣和退款机制，帮助客户理解；第一次只解释，不主动输出 payment_collection，不把咨询强行当付款意向。客户进一步明确报名、预约或付款后，发卡仍须同时具备：更早活动介绍；地址/效果/卡点之一的真实承接；当前行动信号；可用收款卡。同轮最多一张。

金额、抵扣、尾款和退款只来自【本轮相关权威事实】。客户只问规则不等于要付款；已付、健康风险、投诉退款、明确停止、不支持项目或人数超限时不发卡。口头说已付但无权威已付事实时只核对方式或凭证。结构内容只能复制输入真实 ID、URL 或 payload。

没有本轮权威活动/项目事实时，不补价格、流程、效果、反馈或检测；没有门店工具结果时，不说某地/附近有店；没有付款规则和收款结构时，不确认预约金金额、抵扣/退款/锁名额或承诺发送付款方式；没有健康专业事实时，不声称适合客户。只回答已知部分，并最多问一个能触发真实查询的必要问题。

# 六、输出合同
只输出严格 JSON，不输出 markdown、解释或思考：
{"sales_judgment":{"customer_friction_observation":"","primary_objective":"本轮唯一主要目标","posture":"answer|advance|switch|pause|close"},"reply_messages":[{"type":"text","content":"客户实际看到的微信消息"}]}

- `reply_messages` 至少一条。text/image/video/human_handoff_notice 的 content 是字符串；store_address 原样复制 {"store_id":"..."}，且配一条说明位置/地址/导航的文字；payment_collection 原样复制完整对象，不能自填金额。
- `customer_friction_observation` 只写当前有原话支持的未解顾虑；无则空。`primary_objective` 必须本轮可完成或通过一个必要回答进入真实下一步。
- posture：answer=重点回答，advance=答后推进，switch=承接新问题/卡点，pause=本轮不营销，close=进入付款或已付登记。
- 条件字段：实际采用素材才写 selected_content_ids；实际采用序列/步骤/话术才写 knowledge_use（最多一个主话术）；付款上下文才写 payment_assessment；只有输出 payment_collection 才写 deposit_evidence；当前健康风险/投诉退款/明确停止才写 safety_assessment；明确人数才写 party_size_assessment；权威已付且输入给出完整写入事实时才写 commit_actions（仅 add_customer_mobile/create_work_order）。
- 输入有已启用策略时必须输出：
  {"policy_decision":{"primary_task":{"type":"","goal":""},"realtime_intent":{"type":"","confidence":"high|medium|low"},"emotion_decision":{"label":"","confidence":"high|medium|low","pressure":"normal|low|none"},"closing_decision":{"action":"none|enter|advance|pause|fallback|complete","sequence_key":"none","node_key":"","trigger":"none|business_rule","customer_state":"engaged|hesitant|soft_reject|not_buying_now|hard_stop|new_blocker|transaction_terminal_or_handoff|none","pressure":"normal|low|none"}}}
- type/label/key 来自输入目录。无逼单触发时 trigger=none、sequence_key=none、node_key=""；enter/advance/fallback 还必须给真实 rule_ids。明确退订只 close/complete/hard_stop；高置信 angry 或系统要求暂停只 pause；新卡点用 answer 或 switch 解卡，closing=pause。
- secondary_tasks、basis、secondary_types、evidence_refs、satisfied_prerequisite_ids、blocking_taboo_ids、cardpoint_decision 是可选观测字段，缺失不得改变客户回复或触发第二次业务判断。secondary_tasks 最多 3 个真实目录对象且不重复主任务；flow_action、策略/规则/节点名称和 decision_status 由代码派生，不要生成。ref 只能复制【输出引用与结构边界】中的短 ref。

提交前检查：简单问题是否只有一个不重复答案；是否泄漏内部审计措辞；是否带回当前消息没有提及的历史门店、地点、路线或预约；是否只推进一个方向；所有我方已做/将做的动作是否有工具事实、结构消息或合法 commit_action；所有 ID/ref 是否来自输入；有策略时 policy_decision 是否完整。任何一项不满足都先修正，再只输出 JSON。
"""


def build_parallel_reply_messages(user_payload: dict[str, Any], *, json_dumps) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PARALLEL_REPLY_SYSTEM_PROMPT},
        {"role": "user", "content": _render_v3_reply_context(user_payload, json_dumps=json_dumps)},
    ]


def _render_v3_reply_context(payload: dict[str, Any], *, json_dumps) -> str:
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    shared = evidence.get("shared_context") if isinstance(evidence.get("shared_context"), dict) else {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    rules = shared.get("rules") if isinstance(shared.get("rules"), dict) else {}
    knowledge = evidence.get("knowledge_evidence") or evidence.get("sales_recall") or {}
    semantic_route = evidence.get("semantic_route") if isinstance(evidence.get("semantic_route"), dict) else {}
    relevant_fact_topic_ids = [
        str(item or "").strip()
        for item in semantic_route.get("relevant_fact_topic_ids") or []
        if str(item or "").strip()
    ]
    current_time = shared.get("current_time") if isinstance(shared.get("current_time"), dict) else {}
    time_text = "；".join(
        item
        for item in (
            str(current_time.get("iso") or current_time.get("local_time") or "").strip(),
            str(current_time.get("timezone") or "").strip(),
        )
        if item
    )
    reference_aliases = build_reply_reference_aliases(payload)
    sections = [
        _section("当前时间", time_text or "未提供"),
        _section("完整聊天", _render_conversation(shared, reference_aliases=reference_aliases)),
        _section(
            "当前结构事实与不能越过的边界",
            _render_compact_status(_compact_reply_status(facts)),
        ),
        _section("本轮真实执行能力", _render_execution_capabilities()),
    ]
    policy = payload.get("ai_sales_policy") if isinstance(payload.get("ai_sales_policy"), dict) else {}
    if str(policy.get("runtime_mode") or "off") != "off":
        sections.append(
            _section(
                "已发布 AI 销售策略（只提供可选 key 与节奏，不覆盖事实边界）",
                json_dumps(
                    {
                        "policy_version": policy.get("policy_version"),
                        "routing": policy.get("routing") or {},
                        "intent": policy.get("intent") or {},
                        "emotion": policy.get("emotion") or {},
                        "closing": policy.get("closing") or {},
                    }
                ),
            )
        )
        previous_policy_state = _compact_previous_policy_state(payload.get("previous_policy_state"))
        if previous_policy_state:
            sections.append(
                _section(
                    "上一轮策略状态（仅参考，必须按当前客户新消息重新判断）",
                    json_dumps(previous_policy_state),
                )
            )
        closing_catalog = (
            payload.get("closing_catalog_evidence")
            if isinstance(payload.get("closing_catalog_evidence"), dict)
            else {}
        )
        if closing_catalog:
            sections.append(
                _section(
                    "本轮租户逼单规则与策略候选（只可从中选择，不要求采用）",
                    json_dumps(closing_catalog),
                )
            )
    protocol_events = (
        shared.get("current_message", {}).get("protocol_events")
        if isinstance(shared.get("current_message"), dict)
        else []
    )
    if protocol_events:
        sections.append(_section("本轮平台结构事件", _render_protocol_events(protocol_events)))
    if "payment" in relevant_fact_topic_ids:
        sections.append(
            _section(
                "付款渠道可用性",
                _render_payment_channel_availability(payload.get("payment_channel_availability") or {}),
            )
        )
    registration_status = (
        payload.get("registration_fact_status")
        if isinstance(payload.get("registration_fact_status"), dict)
        else {}
    )
    if "registration" in relevant_fact_topic_ids or bool(registration_status.get("authoritative_paid")):
        sections.append(_section("已付登记", _render_registration_fact_status(registration_status)))
    sections.extend(
        [
        _section(
            "当前工具权威事实：不得虚构或违背",
            _render_tool_facts(
                evidence,
                json_dumps=json_dumps,
                reference_aliases=reference_aliases,
            ),
        ),
        _section("必须遵守", _render_must_follow(rules)),
        _section(
            "Router 辅助检索判断：可被 Reply 覆盖",
            _render_semantic_route(semantic_route, reference_aliases=reference_aliases),
        ),
        _section("跟进序列与优秀话术参考", _render_knowledge_evidence(knowledge)),
        _section("本轮相关权威事实：最终口径", _render_authoritative_facts(rules, topic_ids=relevant_fact_topic_ids)),
        _section(
            "可用真实素材",
            _render_delivery_assets(
                evidence.get("content_candidates") or [],
                json_dumps=json_dumps,
                relevant_fact_topic_ids=relevant_fact_topic_ids,
            ),
        ),
        _section(
            "可原样交付的结构消息",
            _render_structured_options(
                _structured_options_for_topics(
                    payload.get("structured_delivery_options") or {},
                    relevant_fact_topic_ids=relevant_fact_topic_ids,
                ),
                json_dumps=json_dumps,
            ),
        ),
        _section(
            "本轮缺失权限（逐条禁止自行补全）",
            _render_missing_authority_guard(payload, facts=facts, rules=rules, evidence=evidence),
        ),
        _section(
            "输出引用与结构边界",
            _render_reference_contract(
                payload,
                json_dumps=json_dumps,
                reference_aliases=reference_aliases,
            ),
        ),
        "请只返回符合系统输出合同的严格 json。",
        ]
    )
    return "\n\n".join(item for item in sections if item)


def _render_missing_authority_guard(
    payload: dict[str, Any],
    *,
    facts: dict[str, Any],
    rules: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    """Render only current-turn authority gaps; never infer customer semantics."""

    guards: list[str] = []
    payment = (
        payload.get("payment_channel_availability")
        if isinstance(payload.get("payment_channel_availability"), dict)
        else {}
    )
    payment_card = payment.get("payment_card") if isinstance(payment.get("payment_card"), dict) else {}
    account_or_qr = payment.get("account_or_qr_facts") if isinstance(payment.get("account_or_qr_facts"), list) else []
    if not bool(payment_card.get("available")) and not account_or_qr:
        guards.append(
            "没有可用收款结构或账户事实：可以按上文权威规则解释预约金，但不得说微信转账、发付款方式或已登记，也不得承诺本轮完成收款。"
        )
    tool_facts = evidence.get("tool_facts") if isinstance(evidence.get("tool_facts"), dict) else {}
    normalized_tool_facts = (
        evidence.get("normalized_tool_facts")
        if isinstance(evidence.get("normalized_tool_facts"), dict)
        else {}
    )
    store_status = payload.get("store_fact_status") if isinstance(payload.get("store_fact_status"), dict) else {}
    if not tool_facts and not normalized_tool_facts and not store_status:
        guards.append("没有门店工具结果：不得说某地有店、附近有店或直接过去，只能收集查询所需位置。")
    if not facts and not rules:
        guards.append("没有活动/项目权威事实：不得补价格、流程、效果、案例反馈、检测或服务能力。")
    return "\n".join(guards) or "无新增缺失权限；仍须服从上文权威事实。"


def _compact_previous_policy_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed_fields = (
        "previous_intent",
        "intent_code",
        "previous_emotion",
        "emotion_code",
        "closing_sequence_key",
        "sequence_key",
        "closing_node_key",
        "node_key",
        "active_cardpoint",
        "cardpoint_key",
        "delivered",
        "delivery_status",
        "customer_replied",
        "order_changed",
        "closing_actions_today",
        "last_closing_action_at",
        "minutes_since_last_closing_action",
    )
    return {
        field: value[field]
        for field in allowed_fields
        if field in value and value[field] not in (None, "", [], {})
    }


def _compact_reply_status(facts: dict[str, Any]) -> dict[str, Any]:
    order_payment = facts.get("orders_and_payment") if isinstance(facts.get("orders_and_payment"), dict) else {}
    resolved = order_payment.get("resolved_payment") if isinstance(order_payment.get("resolved_payment"), dict) else {}
    orders = [item for item in order_payment.get("orders") or [] if isinstance(item, dict)]
    latest_order = orders[0] if orders else {}
    appointment = order_payment.get("appointment") if isinstance(order_payment.get("appointment"), dict) else {}
    request_store = facts.get("request_store_facts") if isinstance(facts.get("request_store_facts"), dict) else {}
    confirmed_store = _pick(request_store, "confirmed_store_id", "confirmed_store_name")
    registration = facts.get("registration_facts") if isinstance(facts.get("registration_facts"), dict) else {}
    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    case_delivery = sent.get("case_image_delivery") if isinstance(sent.get("case_image_delivery"), dict) else {}
    store_delivery = sent.get("store_address_delivery") if isinstance(sent.get("store_address_delivery"), dict) else {}
    recent_store_ids = [
        str(item or "").strip()
        for item in store_delivery.get("latest_batch_store_ids") or []
        if str(item or "").strip()
    ]
    if confirmed_store:
        current_store_status: Any = confirmed_store
    elif recent_store_ids:
        current_store_status = {
            "状态": "最近已交付候选门店，尚未确认成交门店",
            "候选数量": len(recent_store_ids),
            "候选门店ID": recent_store_ids,
        }
    else:
        current_store_status = "没有已确认成交门店"
    factual_boundaries: list[str] = []
    if not confirmed_store:
        if recent_store_ids:
            factual_boundaries.append(
                "最近已交付候选门店不等于客户已选定成交门店；不能把候选发送说成已预约、已登记或已安排到店"
            )
        else:
            factual_boundaries.append("没有已确认成交门店；不能声称已预约、已登记或已安排到店")
    if not _has_active_appointment(appointment):
        factual_boundaries.append("没有权威预约、排客或接待位事实；不能声称已经留位或安排完成")
    if not registration:
        factual_boundaries.append("没有权威登记完成事实，不能声称已经记录客户到店意向")
    if str(resolved.get("deposit_state") or "").strip() == "required_unpaid":
        factual_boundaries.append("预约金未付，不能声称已经留好活动名额")
    return _drop_empty(
        {
            "支付": _pick(
                resolved,
                "deposit_state",
                "payment_result",
                "amount",
                "source",
                "paid_protection_status",
                "store_id",
                "store_name",
            ),
            "订单": _drop_empty(
                {
                    "count": len(orders),
                    "latest": _pick(
                        latest_order,
                        "id",
                        "order_id",
                        "status",
                        "deposit_state",
                        "store_id",
                        "store_name",
                        "created_at",
                        "create_time",
                    ),
                    "query_status": "查询未完整返回" if order_payment.get("orders_error") else "",
                }
            ),
            "当前门店": current_store_status,
            "当前预约": _pick(appointment, "id", "status", "appointment_time", "store_id", "store_name")
            or "无权威预约事实",
            "当前事实边界": factual_boundaries,
            "登记": _pick(registration, "customer_name", "mobile"),
            "发送记录": _drop_empty(
                {
                    "预约金卡次数": sent.get("payment_collection_count"),
                    "活动图已发": sent.get("activity_intro_image_sent"),
                    "案例图": _pick(case_delivery, "total_events", "last_sent_at"),
                    "最近门店卡": _pick(
                        store_delivery,
                        "latest_batch_store_ids",
                        "latest_batch_count",
                        "last_sent_at",
                        "request_id",
                    ),
                }
            ),
            "定位卡": _pick(facts.get("location_card") or {}, "title", "address", "coordinates", "location"),
        }
    )


def _has_active_appointment(appointment: dict[str, Any]) -> bool:
    """Read the normalized appointment contract without interpreting chat text."""

    if not isinstance(appointment, dict) or not appointment:
        return False
    if "has_active" in appointment:
        value = appointment.get("has_active")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 1
        return str(value or "").strip().lower() in {"1", "true", "yes"}
    return str(appointment.get("status") or "").strip().lower() in {
        "active",
        "confirmed",
        "scheduled",
    }


def _section(title: str, body: str) -> str:
    text = str(body or "").strip()
    return f"【{title}】\n{text or '无'}"


def _render_conversation(
    shared: dict[str, Any],
    *,
    reference_aliases: dict[str, str] | None = None,
) -> str:
    lines: list[str] = []
    seen_refs: set[str] = set()
    role_names = {"customer": "客户", "user": "客户", "assistant": "小贝", "staff": "人工", "system": "系统"}
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or "").strip()
        if ref:
            seen_refs.add(ref)
        role = str(item.get("role") or item.get("direction") or "").strip().lower()
        content = _compact_conversation_content(item, ref=ref)
        sent_at = _compact_conversation_time(item.get("sent_at") or item.get("timestamp"))
        display_ref = _reply_reference_alias(ref, reference_aliases)
        lines.append(f"{display_ref or '-'}｜{sent_at or '-'}｜{role_names.get(role, role or '未知')}：{content}")
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    if "current_message" not in seen_refs:
        content = str(current.get("content") or current.get("raw_content") or "").strip()
        sent_at = _compact_conversation_time(current.get("sent_at") or current.get("timestamp"))
        lines.append(f"{_reply_reference_alias('current_message', reference_aliases)}｜{sent_at or '-'}｜客户：{content}")
    return "\n".join(lines) or "无聊天记录"


def _compact_conversation_content(item: dict[str, Any], *, ref: str) -> str:
    content = str(item.get("content") or item.get("text") or "").strip()
    message_type = str(item.get("message_type") or item.get("type") or "").strip().lower()
    lower_content = content.lower()
    is_media_url = lower_content.startswith(("http://", "https://")) and any(
        marker in lower_content.split("?", 1)[0]
        for marker in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov")
    )
    if message_type in {"image", "video"} or "-image-" in ref or is_media_url:
        return "[视频消息]" if message_type == "video" or lower_content.split("?", 1)[0].endswith((".mp4", ".mov")) else "[图片消息]"
    return content


def _compact_conversation_time(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) in {10, 13}:
        try:
            timestamp = int(text) / (1000 if len(text) == 13 else 1)
            return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return text
    if len(text) >= 16 and text[4:5] == "-" and text[7:8] == "-":
        return text[5:16]
    return text


def _render_compact_status(value: dict[str, Any]) -> str:
    lines: list[str] = []
    for label, item in value.items():
        if isinstance(item, dict):
            details = _flatten_pairs(item)
            lines.append(f"{label}：{'；'.join(details) if details else '无'}")
        elif isinstance(item, list):
            lines.append(f"{label}：{'；'.join(str(entry) for entry in item if str(entry).strip()) or '无'}")
        else:
            lines.append(f"{label}：{_scalar(item)}")
    return "\n".join(lines) or "未支付预约金；无当前有效预约；无当前有效订单。"


def _render_execution_capabilities() -> str:
    return "\n".join(
        (
            "现在可做：回答本轮事实；发送下方真实素材或结构消息；询问一个会改变下一步的客户信息。",
            "门店工具：客户提供或改口城市、区县、POI、乡镇村或定位后，下一轮可查询客户可见门店。目的地没有变化时，不承诺以后重复确认门店。",
            "当前不可凭空执行：留活动名额、登记预约、记录到店时间、查客流、安排接待、保证不用等、主动定时联系。只有下方工具事实、结构消息或 commit_action 明确提供时才成立。",
            "客户发来姓名电话只证明信息出现在聊天里，不等于已经写入登记；没有合法 commit_action 时只能说已经收到，不能说已经记下、登记好或确认完成。",
            "历史小贝说过的价格、名额、登记、预约、接待和未来动作不能证明当前仍可执行。",
        )
    )


def _render_must_follow(rules: dict[str, Any]) -> str:
    must = rules.get("MUST FOLLOW") if isinstance(rules.get("MUST FOLLOW"), dict) else {}
    forbidden = [str(item) for item in must.get("hard_forbidden") or [] if str(item or "").strip()]
    payment_blocks = [str(item) for item in must.get("payment_hard_blocks") or [] if str(item or "").strip()]
    lines = []
    if forbidden:
        lines.append("硬边界：" + "；".join(forbidden))
    if payment_blocks:
        lines.append("预约金卡硬阻断状态：" + "、".join(payment_blocks))
    lines.append("代码只校验事实、结构、权限、金额和幂等；客户心理与销售节奏由 Reply 判断。")
    return "\n".join(lines)


def _render_authoritative_facts(
    rules: dict[str, Any],
    *,
    topic_ids: list[str] | None = None,
) -> str:
    facts = rules.get("AUTHORITATIVE FACTS") if isinstance(rules.get("AUTHORITATIVE FACTS"), dict) else {}
    offer = facts.get("offer") if isinstance(facts.get("offer"), dict) else {}
    evidence = (
        facts.get("customer_visible_evidence_policy")
        if isinstance(facts.get("customer_visible_evidence_policy"), dict)
        else {}
    )
    store = (
        facts.get("store_address_disclosure_policy")
        if isinstance(facts.get("store_address_disclosure_policy"), dict)
        else {}
    )
    health = facts.get("health_risk_policy") if isinstance(facts.get("health_risk_policy"), dict) else {}
    charge = facts.get("customer_charge_policy") if isinstance(facts.get("customer_charge_policy"), dict) else {}
    transaction = facts.get("transaction_policy") if isinstance(facts.get("transaction_policy"), dict) else {}
    selected = set(topic_ids) if topic_ids is not None else {
        "activity_offer",
        "effect_evidence",
        "technique",
        "body_area",
        "transport_policy",
        "fee_transparency",
        "store_policy",
        "store_arrival_detail",
        "store_trust",
        "payment",
        "registration",
        "health_risk",
        "complaint_refund",
        "operation_feeling",
    }
    lines: list[str] = []

    public_names = "、".join(str(item) for item in offer.get("public_names") or [] if str(item or "").strip())
    core_activity = "；".join(
        item
        for item in (
            f"名称={public_names}" if public_names else "",
            f"活动价={offer.get('new_customer_price')}元" if offer.get("new_customer_price") is not None else "",
        )
        if item
    )
    if core_activity:
        lines.append("核心活动：" + core_activity)

    if "activity_offer" in selected:
        includes = "、".join(str(item) for item in offer.get("includes") or [] if str(item or "").strip())
        _append_fact(lines, "活动范围", offer.get("body_scope"))
        _append_fact(lines, "活动结构", offer.get("offer_structure"))
        _append_fact(lines, "活动包含", includes)
        _append_fact(lines, "活动名额", offer.get("quota"))
        _append_fact(lines, "原价口径", offer.get("original_price_visibility"))
    if "body_area" in selected:
        _append_fact(lines, "项目范围", offer.get("scope_answer_policy"))
        _append_fact(lines, "部位价格", offer.get("body_area_price_rule"))
    if "transport_policy" in selected:
        _append_fact(lines, "交通费用", offer.get("transport_cost_rule"))
    if "payment" in selected:
        _append_fact(lines, "预约金与尾款", _deposit_fact_line(offer))
        for label, key in (
            ("活动与预约金", "activity_and_deposit_are_separate_actions"),
            ("发卡证据", "deposit_evidence_requirements"),
            ("付款渠道", "payment_channel_policy"),
            ("平台未知消息", "platform_unknown_message_payment_policy"),
        ):
            _append_fact(lines, label, transaction.get(key))
    if "registration" in selected:
        _append_fact(lines, "登记与检测", offer.get("registration_skin_test"))
        _append_fact(lines, "到店安排", offer.get("arrival_time_rule"))
        _append_fact(lines, "支付后登记", transaction.get("post_paid_flow_description"))
    if "effect_evidence" in selected:
        _append_fact(lines, "效果", evidence.get("effect_result_fact") or evidence.get("effect_confidence"))
        _append_fact(lines, "效果对比", evidence.get("before_after_record"))
        _append_fact(lines, "案例边界", evidence.get("case_boundary"))
        _append_fact(lines, "效果参考", evidence.get("social_proof"))
        _append_fact(lines, "规模与既往反馈", offer.get("authorized_scale_and_safety_evidence"))
    if "technique" in selected:
        _append_fact(lines, "技术", evidence.get("technology"))
    if "operation_feeling" in selected:
        _append_fact(lines, "过程时长", offer.get("service_duration"))
        _append_fact(lines, "日常影响", offer.get("daily_life_impact"))
        _append_fact(lines, "操作感受", evidence.get("operation_feeling"))
    if "fee_transparency" in selected:
        _append_fact(lines, "收费透明", charge.get("customer_visible_fact"))
        _append_fact(lines, "收费边界", charge.get("boundary"))
    if "store_policy" in selected:
        for label, key in (
            ("公开地址", "public_store_address"),
            ("预约制事实", "reservation_fact"),
            ("当前门店流程", "current_flow_boundary"),
        ):
            _append_fact(lines, label, store.get(key))
    if "store_arrival_detail" in selected:
        _append_fact(lines, "详细到店指引", store.get("arrival_guidance"))
        _append_fact(lines, "精确地址", store.get("detail_followup_boundary"))
    if "store_trust" in selected:
        _append_fact(lines, "门店信任核验", store.get("trust_priority"))
    if "health_risk" in selected:
        _append_fact(lines, "当前健康风险", health.get("current_risk_handling"))
        _append_fact(lines, "风险到店检测", health.get("in_store_assessment"))
    if "complaint_refund" in selected:
        _append_fact(lines, "退款口径", offer.get("refund_rule"))
    return "\n".join(lines)


def _deposit_fact_line(offer: dict[str, Any]) -> str:
    parts = []
    if offer.get("prepay_amount") is not None:
        parts.append(f"每位先付{offer.get('prepay_amount')}元锁活动资格")
    parts.append("到店抵扣")
    if offer.get("tail_amount") is not None:
        parts.append(f"做再付{offer.get('tail_amount')}元")
    if offer.get("refund_rule"):
        parts.append(str(offer.get("refund_rule")))
    parts.append("订单不是发卡前置")
    return "；".join(parts)


def _render_sales_principles(rules: dict[str, Any]) -> str:
    principles = rules.get("SALES PRINCIPLES") if isinstance(rules.get("SALES PRINCIPLES"), dict) else {}
    lines: list[str] = []
    _append_fact(lines, "目标", principles.get("mission"))
    for index, item in enumerate(principles.get("principles") or [], start=1):
        if str(item or "").strip():
            lines.append(f"{index}. {str(item).strip()}")
    anti_patterns = [str(item).strip() for item in principles.get("anti_patterns") or [] if str(item or "").strip()]
    if anti_patterns:
        lines.append("避免：" + "；".join(anti_patterns))
    return "\n".join(lines)


def _render_semantic_route(
    value: Any,
    *,
    reference_aliases: dict[str, str] | None = None,
) -> str:
    if not isinstance(value, dict) or not value:
        return "未检索到明确卡点；Reply 直接根据完整聊天判断。"
    lines: list[str] = []
    current_intent = value.get("current_intent") if isinstance(value.get("current_intent"), dict) else {}
    if current_intent.get("summary"):
        lines.append(
            f"当前表达：{current_intent.get('summary')}"
            + (
                f"；证据={_join(_alias_reference_list(current_intent.get('evidence_refs') or [], reference_aliases))}"
                if current_intent.get("evidence_refs")
                else ""
            )
        )
    current_friction = value.get("current_friction") if isinstance(value.get("current_friction"), dict) else {}
    if current_friction and current_friction.get("status") != "none":
        friction_name = (
            current_friction.get("checkpoint_type_name")
            or current_friction.get("checkpoint_code")
            or "无"
        )
        lines.append(
            f"当前阻力：{friction_name}"
            + (f"；具体表现={current_friction.get('checkpoint_tag_name')}" if current_friction.get("checkpoint_tag_name") else "")
            + (f"；观察={current_friction.get('summary')}" if current_friction.get("summary") else "")
            + (
                f"；证据={_join(_alias_reference_list(current_friction.get('evidence_refs') or [], reference_aliases))}"
                if current_friction.get("evidence_refs")
                else ""
            )
        )
    historical = (
        value.get("historical_unresolved_friction")
        if isinstance(value.get("historical_unresolved_friction"), dict)
        else {}
    )
    if historical.get("checkpoint_code"):
        lines.append(
            f"历史未解决阻力（低权重）：{historical.get('checkpoint_code')}"
            + (f"；观察={historical.get('summary')}" if historical.get("summary") else "")
            + (
                f"；证据={_join(_alias_reference_list(historical.get('evidence_refs') or [], reference_aliases))}"
                if historical.get("evidence_refs")
                else ""
            )
        )
    knowledge_focus = (
        value.get("knowledge_focus")
        if isinstance(value.get("knowledge_focus"), dict)
        else {}
    )
    if knowledge_focus.get("source") not in {None, "", "none"}:
        focus_name = (
            knowledge_focus.get("checkpoint_type_name")
            or knowledge_focus.get("checkpoint_code")
            or "未命名类型"
        )
        lines.append(
            f"知识检索焦点（不等于客户有异议）：{focus_name}"
            + (
                f"；具体标签={knowledge_focus.get('checkpoint_tag_name')}"
                if knowledge_focus.get("checkpoint_tag_name")
                else ""
            )
            + (
                f"；参考动作={knowledge_focus.get('action_code')}"
                if knowledge_focus.get("action_code")
                else ""
            )
            + (
                f"；原因={knowledge_focus.get('reason')}"
                if knowledge_focus.get("reason")
                else ""
            )
        )
    topics = [str(item) for item in value.get("relevant_fact_topic_ids") or [] if str(item or "").strip()]
    if topics:
        lines.append("本轮事实主题：" + "、".join(topics))
    checkpoint = value.get("checkpoint") if isinstance(value.get("checkpoint"), dict) else {}
    provisional = value.get("provisional_checkpoint") if isinstance(value.get("provisional_checkpoint"), dict) else {}
    selected_checkpoint = checkpoint or provisional
    if selected_checkpoint and not current_friction:
        code = selected_checkpoint.get("primary_code") or selected_checkpoint.get("code") or "none"
        secondary = selected_checkpoint.get("secondary_code") or ""
        refs = _alias_reference_list(
            selected_checkpoint.get("evidence_refs") or [],
            reference_aliases,
        )
        lines.append(
            f"卡点：主={code}"
            + (f"；次={secondary}" if secondary else "")
            + (f"；证据={_join(refs)}" if refs else "")
            + (f"；原因={selected_checkpoint.get('reason')}" if selected_checkpoint.get("reason") else "")
        )
    store_query = value.get("store_query") if isinstance(value.get("store_query"), dict) else {}
    if store_query:
        lines.append(
            "门店查询："
            + "；".join(
                part
                for part in (
                    f"需要={_scalar(store_query.get('required'))}",
                    f"目的={store_query.get('purpose')}" if store_query.get("purpose") else "",
                    f"目的地={store_query.get('destination_hint')}" if store_query.get("destination_hint") else "",
                    (
                        "证据="
                        + _join(
                            _alias_reference_list(
                                store_query.get("location_evidence_refs") or [],
                                reference_aliases,
                            )
                        )
                        if store_query.get("location_evidence_refs")
                        else ""
                    ),
                )
                if part
            )
        )
    sequence = value.get("sequence_match") if isinstance(value.get("sequence_match"), dict) else {}
    if sequence:
        lines.append(
            "序列匹配："
            + "；".join(
                part
                for part in (
                    f"序列={_join(sequence.get('sequence_ids') or [])}",
                    f"步骤={_join(sequence.get('relevant_step_ids') or [])}",
                    f"原因={sequence.get('reason')}" if sequence.get("reason") else "",
                )
                if part
            )
        )
    interpretation = (
        value.get("store_result_interpretation")
        if isinstance(value.get("store_result_interpretation"), dict)
        else {}
    )
    if interpretation:
        lines.append("门店结果理解：" + "；".join(_flatten_pairs(interpretation)))
    if value.get("classification_status"):
        lines.append(f"分类清晰度：{value.get('classification_status')}")
    return "\n".join(lines) or "无"


def _render_knowledge_evidence(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "本轮没有匹配到跟进序列或参考话术；Reply 仍按完整聊天和权威事实回答。"
    lines: list[str] = [
        "以下内容只提供销售思路和口语风格，也不能替 Reply 解释客户原话。其中数量、价格、效果、免费、人员、距离、名额和完成状态必须重新对照【权威业务事实】。候选原文与本轮硬事实口径不同时，只取其销售逻辑和表达方式，不复述冲突文本。"
    ]
    support_level = str(value.get("support_level") or "").strip()
    support_labels = {
        "script_exact": "精确标签、动作下有参考话术",
        "script_mixed": "包含精确话术和同类型同动作的宽匹配参考",
        "script_broad": "精确标签无话术，提供同类型同动作的宽匹配参考",
        "sequence_only": "只有跟进序列逻辑，没有匹配到成品话术；请按序列目标自行组织表达",
        "none": "没有匹配到序列或话术；请按完整聊天、权威事实和销售使命自主回答",
    }
    if support_level in support_labels:
        lines.append(f"知识支持：{support_labels[support_level]}")
    candidate_objective = str(value.get("candidate_objective") or "").strip()
    if candidate_objective:
        lines.append(f"本候选目标：{candidate_objective}")
    candidate_boundaries = [
        str(item).strip()
        for item in value.get("candidate_boundaries") or []
        if str(item).strip()
    ]
    if candidate_boundaries:
        lines.append("本候选不适用动作：" + "、".join(candidate_boundaries))
    for raw in value.get("sequence_candidates") or []:
        if not isinstance(raw, dict):
            continue
        sequence_id = raw.get("sequence_id") or raw.get("id") or ""
        name = raw.get("sequence_name") or raw.get("name") or ""
        checkpoint = raw.get("checkpoint_name") or raw.get("checkpoint_code") or ""
        description = raw.get("description") or raw.get("reason") or ""
        lines.append(f"序列 {sequence_id}｜{name}｜卡点={checkpoint}｜思路={description}")
        steps = raw.get("steps") or raw.get("relevant_steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            lines.append(
                "  步骤 "
                + str(step.get("step_id") or step.get("id") or step.get("sort_order") or "")
                + "｜动作="
                + str(step.get("action_name") or step.get("action_code") or "")
                + "｜说明="
                + str(step.get("objective") or step.get("remark") or step.get("reason") or "")
            )
    for raw in value.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        script_id = str(raw.get("script_id") or raw.get("id") or "").strip()
        source_id = str(raw.get("source_id") or raw.get("script_code") or script_id).strip()
        text = _dedupe_reference_text(raw.get("reference_text") or raw.get("body_text") or raw.get("text") or "")
        checkpoint_type = raw.get("checkpoint_type") if isinstance(raw.get("checkpoint_type"), dict) else {}
        checkpoint_tag = raw.get("checkpoint_tag") if isinstance(raw.get("checkpoint_tag"), dict) else {}
        query_sources = {
            str(item.get("query_source") or "").strip()
            for item in raw.get("sequence_links") or []
            if isinstance(item, dict) and str(item.get("query_source") or "").strip()
        }
        retrieval_sources = []
        if "model_selected_knowledge_focus" in query_sources:
            retrieval_sources.append("当前表达精确检索")
        if "model_selected_relevant_step" in query_sources:
            retrieval_sources.append("序列步骤检索")
        if "closing_catalog_node" in query_sources:
            retrieval_sources.append("逼单节点话术类型检索")
        lines.append(
            f"话术ID={script_id or '无'}｜内容ID=follow_script:{source_id}｜{raw.get('script_name') or raw.get('name') or ''}"
            f"｜卡点={checkpoint_type.get('name') or raw.get('checkpoint_name') or raw.get('checkpoint_code') or ''}"
            + (f"｜标签={checkpoint_tag.get('name')}" if checkpoint_tag.get("name") else "")
            + f"｜动作={raw.get('action_name') or raw.get('action_code') or ''}"
            + f"｜权限={raw.get('authority_scope') or raw.get('authority') or 'approved_sales_expression'}"
            + (
                f"｜匹配范围={raw.get('retrieval_match_scope')}"
                if raw.get("retrieval_match_scope")
                else ""
            )
            + (f"｜匹配依据={'、'.join(retrieval_sources)}" if retrieval_sources else "")
            + (f"｜来源={raw.get('source_ref')}" if raw.get("source_ref") else "")
        )
        paragraphs = [item for item in raw.get("paragraphs") or [] if isinstance(item, dict)]
        if paragraphs:
            for paragraph in paragraphs:
                number = int(paragraph.get("paragraph_no") or 1)
                paragraph_ref = paragraph.get("source_ref") or f"follow_script:{source_id}:p{number}"
                lines.append(f"  内容组 {paragraph_ref}（整组参考，可采用、组合或忽略）：")
                for message in paragraph.get("messages") or []:
                    if not isinstance(message, dict):
                        continue
                    if message.get("type") == "text" and message.get("content"):
                        lines.append("    文字：" + _dedupe_reference_text(message.get("content")))
                    elif message.get("type") in {"image", "video"} and message.get("url"):
                        lines.append(
                            f"    {message.get('type')}：{message.get('url')}"
                            + (f"｜备注={message.get('remark')}" if message.get("remark") else "")
                        )
        else:
            if text:
                lines.append("  参考表达：" + text)
            media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
            if media.get("url"):
                lines.append(f"  配套素材：{media.get('url')}")
    selector = value.get("selector") if isinstance(value.get("selector"), dict) else {}
    if selector:
        lines.append(
            "精选结果："
            + "；".join(
                part
                for part in (
                    f"状态={selector.get('status')}" if selector.get("status") else "",
                    f"话术={_join(selector.get('selected_script_ids') or [])}" if selector.get("selected_script_ids") else "",
                    f"原因={selector.get('reason')}" if selector.get("reason") else "",
                )
                if part
            )
        )
    lines.append(
        "表达权限：已发布话术中的一般客户经验、社会证明、价值类比和人际表达可以灵活使用；"
        "不要扩写出原话术没有的精确人物、人数、城市、车程或个体结果；原话术已有的精确数字只能原样引用或删去，不能改成另一个数字。交易、履约、门店、支付、活动权益、"
        "个体效果和个体安全仍以【本轮相关权威事实】与【当前工具权威事实：不得虚构或违背】为准。"
    )
    return "\n".join(lines) or "无"


def _render_delivery_assets(
    value: Any,
    *,
    json_dumps,
    relevant_fact_topic_ids: list[str] | None = None,
) -> str:
    assets = (
        [
            item
            for item in value
            if isinstance(item, dict)
            and str(item.get("delivery_status") or "").strip() != "completed"
        ]
        if isinstance(value, list)
        else []
    )
    relevant_topics = {
        str(item or "").strip()
        for item in relevant_fact_topic_ids or []
        if str(item or "").strip()
    }
    assets = sorted(
        enumerate(assets),
        key=lambda pair: (
            0 if str(pair[1].get("asset_role") or "").strip() in relevant_topics else 1,
            pair[0],
        ),
    )
    assets = [item for _, item in assets]
    lines: list[str] = []
    unsent_activity_offer = any(
        isinstance(item, dict)
        and str(item.get("asset_role") or "").strip() == "activity_offer"
        and not (
            item.get("delivery_observation")
            if isinstance(item.get("delivery_observation"), dict)
            else {}
        ).get("sent_count")
        for item in assets
    )
    for raw in assets:
        if not isinstance(raw, dict) or str(raw.get("asset_role") or "") == "sales_reference":
            continue
        observation = raw.get("delivery_observation") if isinstance(raw.get("delivery_observation"), dict) else {}
        content_id = str(raw.get("content_id") or "").strip()
        lines.append(
            f"素材 {raw.get('content_id') or ''}｜{raw.get('name') or ''}｜角色={raw.get('asset_role') or ''}"
            f"｜已发次数={observation.get('sent_count', 0)}"
            + (f"｜最近={observation.get('last_sent_at')}" if observation.get("last_sent_at") else "")
        )
        if str(raw.get("asset_role") or "").strip() in relevant_topics:
            lines.append("  相关性：与 Router 本轮选择的事实主题直接对应")
        messages = raw.get("messages") or raw.get("media") or []
        if content_id == "s10_activity_intro":
            lines.append("  用途：首次完整活动或价格介绍的配套凭证")
            message_types = {
                str(item.get("type") or "").strip()
                for item in messages
                if isinstance(item, dict)
            }
            content_parts = []
            if "text" in message_types:
                content_parts.append("活动文字")
            if "image" in message_types:
                content_parts.append("活动宣传图")
            lines.append(f"  内容：{' + '.join(content_parts) or '已配置活动内容'}")
            lines.append(
                "  发送状态："
                + ("当前销售接触尚未发送" if not observation.get("sent_count") else "当前销售接触已发送")
            )
            lines.append("  采用方式：选择该素材 ID 即会原样交付配置的活动图；活动文字按本轮权威事实自行组织")
        elif str(raw.get("asset_role") or "").strip() == "deposit_close":
            lines.append("  用途：成交基础成熟且客户当前明确报名、预约或付款时的预约金说明")
            lines.append("  边界：不是首次活动或价格介绍的配套图，不能替代 activity_offer")
        elif raw.get("purpose"):
            lines.append(f"  用途：{raw.get('purpose')}")
            if (
                str(raw.get("asset_role") or "").strip() == "objection_support"
                and unsent_activity_offer
            ):
                lines.append(
                    "  配套关系：本素材负责解释顾虑；若本轮同时完成首次活动或价格介绍，"
                    "还要采用未发送的 activity_offer，本素材不能替代活动凭证"
                )
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "").strip()
            content = message.get("content")
            if message_type == "text":
                # Static SOP bodies are not a runtime reply template. Authoritative
                # facts are rendered separately; only real deliverable media stays
                # in this compact asset directory.
                continue
            if isinstance(content, dict):
                content = content.get("text") or content.get("url") or json_dumps(content)
            lines.append(f"  {message_type or '内容'}：{content}")
    return "\n".join(lines) or "无可用素材"


def _render_payment_channel_availability(value: Any) -> str:
    data = value if isinstance(value, dict) else {}
    inbound = (
        data.get("current_inbound_payment_event")
        if isinstance(data.get("current_inbound_payment_event"), dict)
        else {}
    )
    if inbound:
        channel = str(inbound.get("payment_channel") or "").strip()
        channel_name = "红包" if channel == "red_packet" else "转账"
        return "\n".join(
            [
                f"客户本轮已经发出{channel_name}类平台消息，到账状态仍未核验。",
                "本轮不得再次要求客户发红包、转账、点击付款或重新付款，也不得发送任何付款入口；只承接已发送动作并核对权威支付结果。",
            ]
        )
    payment_card = data.get("payment_card") if isinstance(data.get("payment_card"), dict) else {}
    transfer = data.get("transfer") if isinstance(data.get("transfer"), dict) else {}
    red_packet = data.get("red_packet") if isinstance(data.get("red_packet"), dict) else {}
    account_or_qr = data.get("account_or_qr_facts") if isinstance(data.get("account_or_qr_facts"), list) else []
    return "\n".join(
        [
            f"小程序预约金卡：{'可用' if payment_card.get('available') else '不可用'}",
            f"人工转账：{'允许' if transfer.get('allowed') else '不允许'}；收款二维码：{'有权威事实' if transfer.get('qr_code_available') else '未提供'}",
            f"微信红包：{'允许' if red_packet.get('allowed') else '不允许'}",
            f"账户或二维码事实：{len(account_or_qr)} 条",
            "首次说明本轮选定的付款渠道时：同一轮完整覆盖【权威业务事实】中的预约金金额、到店抵扣、尾款和可退条件，四项不能省略",
        ]
    )


def _render_registration_fact_status(value: Any) -> str:
    data = value if isinstance(value, dict) else {}
    if not data.get("authoritative_paid"):
        return (
            "预约金：尚未权威核实为已付\n"
            "当前边界：客户口头说已付只能核对付款方式或凭证；不得按已付收姓名、电话、门店或到店意向"
        )
    collected = {str(item) for item in data.get("collected_fields") or []}
    missing = {str(item) for item in data.get("missing_fields") or []}
    store = data.get("confirmed_store") if isinstance(data.get("confirmed_store"), dict) else {}
    lines = [
        "预约金：已核实",
        f"姓名：{'已收到' if 'customer_name' in collected else '未收到'}",
        f"电话：{'已收到' if 'customer_mobile' in collected else '未收到'}",
    ]
    if store.get("store_name") or store.get("store_id"):
        lines.append(f"确认门店：{store.get('store_name') or store.get('store_id')}")
    if "arrival_intent" in collected:
        lines.append(f"到店意向：{data.get('arrival_intent') or '已收到'}")
    elif "arrival_intent" in missing:
        lines.append("仍缺：宽松到店意向")
    if store.get("store_name") or store.get("store_id"):
        lines.append("本轮登记承接：确认已收到姓名电话并带上确认门店，只询问仍缺的到店意向")
    return "\n".join(lines)


def _render_tool_facts(
    evidence: dict[str, Any],
    *,
    json_dumps,
    reference_aliases: dict[str, str] | None = None,
) -> str:
    normalized = evidence.get("normalized_tool_facts") if isinstance(evidence.get("normalized_tool_facts"), dict) else {}
    structured = normalized.get("structured_facts") if isinstance(normalized.get("structured_facts"), dict) else {}
    resolution = structured.get("store_resolution_fact") if isinstance(structured.get("store_resolution_fact"), dict) else {}
    final_store_ids = [
        str(item).strip()
        for item in resolution.get("delivery_store_ids") or []
        if str(item).strip()
    ]
    lines: list[str] = []
    store_conclusion = _render_store_resolution_conclusion(resolution)
    if store_conclusion:
        lines.append(store_conclusion)
    for label, key in (
        ("可用事实", "usable_facts"),
        ("缺失事实", "missing_facts"),
        ("风险事实", "risky_facts"),
        ("不支持声明", "unsupported_claims"),
    ):
        values = normalized.get(key) or []
        if key == "usable_facts" and resolution:
            values = [
                item
                for item in values
                if not str(item).startswith("customer_store_lookup: matched_stores=")
                and "tool_error=" not in str(item)
                and "status=no_candidate_stores" not in str(item)
            ]
        if values:
            lines.append(f"{label}：{_join(values)}")
    lookup = structured.get("store_lookup_status") if isinstance(structured.get("store_lookup_status"), dict) else {}
    if lookup and not resolution:
        lines.append("门店查询状态：" + "；".join(_flatten_pairs(_pick(lookup, "status", "raw_query", "query", "province", "city", "district", "township", "resolved_admin_level", "scope_match_level", "exact_scope_has_store", "same_city_has_store", "candidate_count"))))
    if resolution:
        compact_resolution = _pick(
            resolution,
            "status",
            "raw_place",
            "normalized_query",
            "resolution_status",
            "resolved_admin_level",
            "province",
            "city",
            "district",
            "township",
            "coverage_status",
            "clarification_required",
            "clarification_would_change_result",
            "recommendation_final_for_destination",
            "delivery_mode",
            "customer_claim_level",
            "candidate_store_ids",
            "delivery_store_ids",
            "requested_detail_kind",
            "requested_detail_available",
            "visible_candidate_count",
            "candidate_search_complete",
            "ranking_method",
            "route_ranking_complete",
            "route_shortlist_size",
            "customer_claim_guidance",
            "reason",
        )
        destination = resolution.get("destination_resolution") if isinstance(resolution.get("destination_resolution"), dict) else {}
        if destination:
            compact_resolution["destination"] = _pick(
                destination,
                "request_kind",
                "destination_query",
                "destination_precision",
                "evidence_refs",
                "needs_clarification",
                "confidence",
                "reason",
            )
            if compact_resolution["destination"].get("evidence_refs"):
                compact_resolution["destination"]["evidence_refs"] = _alias_reference_list(
                    compact_resolution["destination"].get("evidence_refs") or [],
                    reference_aliases,
                )
        location = resolution.get("location_evidence") if isinstance(resolution.get("location_evidence"), dict) else {}
        if location:
            compact_resolution["location"] = _pick(location, "source_message_refs", "longitude", "latitude", "confidence")
            if compact_resolution["location"].get("source_message_refs"):
                compact_resolution["location"]["source_message_refs"] = _alias_reference_list(
                    compact_resolution["location"].get("source_message_refs") or [],
                    reference_aliases,
                )
        lines.append("门店决议：" + "；".join(_flatten_pairs(compact_resolution)))
    stores = [item for item in structured.get("store_facts") or [] if isinstance(item, dict)]
    if final_store_ids:
        store_by_id = {
            str(item.get("store_id") or item.get("id") or "").strip(): item
            for item in stores
            if str(item.get("store_id") or item.get("id") or "").strip()
        }
        stores = [store_by_id[store_id] for store_id in final_store_ids if store_id in store_by_id]
    for store in stores:
        compact_store = _pick(
            store,
            "store_id",
            "store_name",
            "province",
            "city",
            "district",
            "store_address",
            "business_hours",
            "parking_name",
            "parking_address",
            "map_url",
            "floor",
            "room",
            "arrival_guidance",
            "reception",
            "distance_km",
            "duration_seconds",
            "scope_authorized",
        )
        lines.append("门店：" + "；".join(_flatten_pairs(compact_store)))
    for label, key in (
        ("价格事实", "price_facts"),
        ("案例事实", "case_facts"),
        ("知识事实", "knowledge_facts"),
        ("预约事实", "appointment_facts"),
        ("订单事实", "order_facts"),
        ("支付事实", "payment_facts"),
        ("登记事实", "registration_facts"),
    ):
        rows = structured.get(key) or []
        for row in rows if isinstance(rows, list) else [rows]:
            if isinstance(row, dict):
                lines.append(f"{label}：" + "；".join(_flatten_pairs(row)))
            elif row not in (None, ""):
                lines.append(f"{label}：{row}")
    for label, key in (("缺失事实", "missing_facts"),):
        values = evidence.get(key) or []
        if values:
            lines.append(f"{label}：{_join(values)}")
    if not lines:
        raw = evidence.get("tool_facts") if isinstance(evidence.get("tool_facts"), dict) else {}
        for tool_name, result in raw.items():
            if isinstance(result, dict):
                if result.get("error"):
                    lines.append(f"{tool_name}：查询未完整返回")
                else:
                    summary = _pick(result, "status", "source")
                    lines.append(
                        f"{tool_name}："
                        + ("；".join(_flatten_pairs(summary)) or "已调用，未返回可用结构事实")
                    )
    return "\n".join(lines) or "本轮没有工具事实"


def _render_store_resolution_conclusion(resolution: dict[str, Any]) -> str:
    if not isinstance(resolution, dict) or not resolution:
        return ""
    status = str(resolution.get("status") or "").strip()
    complete = bool(resolution.get("candidate_search_complete"))
    if status == "search_incomplete":
        return (
            "门店最终结论：查询未完整返回，不能判断当地有店、无店或可以安排到店，也不能发送门店卡。"
            "客户地点证据已经足够时，不得重复追问同一地址。"
        )
    if status in {"need_location", "need_location_confirmation", "ambiguous_location"}:
        return "门店最终结论：仍缺一个会改变查询结果的位置事实；只补问这一项，不发送门店卡。"
    if status == "no_valid_candidate" and complete:
        return (
            "门店最终结论：查询范围完整，该地点当前没有可发送的合法门店。"
            "本轮必须只用 text 明确说明当地没有可发送门店，并给一个安全下一步"
            "（例如询问客户是否有其他常去城市/区域，或先承接项目价值）；"
            "不得输出 store_address，不得挑选其他城市门店，不得回复为空。"
        )
    if status in {"send_single", "send_multiple", "reuse_confirmed_store"}:
        is_reuse = status == "reuse_confirmed_store"
        id_source = (
            resolution.get("already_delivered_store_ids")
            if is_reuse
            else resolution.get("delivery_store_ids")
        )
        store_ids = [str(item).strip() for item in id_source or [] if str(item).strip()]
        exact_scope_has_store = resolution.get("exact_scope_has_store")
        same_city_has_store = resolution.get("same_city_has_store")
        scope_match_level = str(resolution.get("scope_match_level") or "").strip()
        if exact_scope_has_store is False and same_city_has_store is True:
            conclusion = (
                "客户所述具体区县/乡镇本地没有门店；以下是同一城市其他区域的可发送候选，"
                "不得说成客户所述地点本地有店"
            )
        elif exact_scope_has_store is False:
            conclusion = (
                "客户所述地点本地没有门店；以下是查询后返回的跨区域可发送候选，"
                "不得说成客户当地有店"
            )
        elif exact_scope_has_store is True:
            conclusion = "客户所述范围内有可发送门店"
        else:
            conclusion = "已查到可发送门店"
        if is_reuse:
            return (
                f"门店最终结论：{conclusion}；匹配层级={scope_match_level or '未标注'}；"
                "同一目的地的最终门店结果此前已经真实发送，本轮不得重复发送 store_address；"
                "可以按上述范围结论简短承接，但不能改变本地有店/无店结论。此前门店ID="
                + _join(store_ids)
            )
        return (
            f"门店最终结论：{conclusion}；匹配层级={scope_match_level or '未标注'}；"
            "必须按 delivery_store_ids 原样交付；门店卡只能放在 reply_messages 的 store_address 中，"
            "不要把门店ID写入 selected_content_ids；store_address 前后必须有 text 承接。门店ID=" + _join(store_ids)
        )
    return ""


def _render_protocol_events(value: Any) -> str:
    events = value if isinstance(value, list) else []
    lines = []
    for item in events:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        payment_channel = str(item.get("payment_channel") or "").strip()
        delivery_status = str(item.get("delivery_status") or "").strip()
        payment_status = str(item.get("payment_status") or "").strip()
        source = str(item.get("source") or "").strip()
        if event_type not in {"external_redpacket", "external_transfer"}:
            continue
        channel_name = "红包" if payment_channel == "red_packet" else "转账"
        lines.append(
            f"客户本轮已发送{channel_name}类平台消息；方向=客户发给客服；"
            f"消息状态={delivery_status or 'received_unverified'}；"
            f"支付状态={payment_status or 'unknown'}。"
            "先承接客户已经发起的付款动作，不重复发送预约金图、付款卡或其他付款入口；"
            "不得再次要求客户发红包、转账、点击付款或重新付款；"
            "金额和到账仍未核验，只能核对凭证或等待权威支付结果。"
            + (f"｜来源={source}" if source else "")
        )
    return "\n".join(lines) or "无"


def _structured_options_for_topics(
    value: Any,
    *,
    relevant_fact_topic_ids: list[str],
) -> dict[str, Any]:
    """Keep side-effect payloads only when the model router requested that fact domain."""

    options = value if isinstance(value, dict) else {}
    relevant_topics = {
        str(item or "").strip()
        for item in relevant_fact_topic_ids
        if str(item or "").strip()
    }
    return {
        str(message_type): content
        for message_type, content in options.items()
        if message_type != "payment_collection" or "payment" in relevant_topics
    }


def _render_structured_options(value: Any, *, json_dumps) -> str:
    if not isinstance(value, dict) or not value:
        return "无"
    lines: list[str] = []
    for message_type, content in value.items():
        items = content if isinstance(content, list) else [content]
        for item in items:
            lines.append(f"{message_type}｜原样使用={json_dumps(item)}")
    return "\n".join(lines)


def _render_reference_contract(
    payload: dict[str, Any],
    *,
    json_dumps,
    reference_aliases: dict[str, str] | None = None,
) -> str:
    valid_message_refs = {
        str(item).strip()
        for item in payload.get("valid_message_refs") or []
        if str(item).strip()
    }
    extra_supporting_refs = [
        str(item).strip()
        for item in payload.get("valid_deposit_evidence_refs") or []
        if str(item).strip()
        and str(item).strip() not in valid_message_refs
    ]
    lines = [
        (
            "聊天每行开头的 now/mXX 只是本轮证据编号，不属于聊天内容。"
            "需要 evidence_refs 时直接引用对应行：客户证据只能引用标注为“客户”的行；"
            "历史交付证据可引用“小贝/人工”的对应行。编号只证明来源真实，不代表条件已经满足。"
        ),
        "可选内容 ID：" + _join(payload.get("allowed_selected_content_ids") or []),
        "实际采用候选内容时，在 selected_content_ids 记录对应 ID；候选图片和视频按 ID 原样交付，不需要复制 URL。",
    ]
    if extra_supporting_refs:
        lines.append(
            "聊天外可核对的结构来源："
            + _join(_alias_reference_list(extra_supporting_refs, reference_aliases))
        )
    sequence_options = payload.get("follow_sequence_reference_options") or []
    if sequence_options:
        rendered = []
        for raw in sequence_options:
            if isinstance(raw, dict):
                rendered.append(
                    f"{raw.get('sequence_id') or raw.get('id') or ''}"
                    + (f"(steps={_join(raw.get('valid_step_ids') or [])})" if raw.get("valid_step_ids") else "")
                )
            else:
                rendered.append(str(raw))
        lines.append("合法序列：" + "、".join(rendered))
    script_options = payload.get("follow_script_reference_options") or []
    if script_options:
        rendered_scripts = []
        for raw in script_options:
            if isinstance(raw, dict):
                rendered_scripts.append(str(raw.get("content_id") or raw.get("script_code") or ""))
            else:
                rendered_scripts.append(str(raw))
        lines.append("合法话术：" + "、".join(item for item in rendered_scripts if item))
    commit_refs = payload.get("valid_commit_evidence") or []
    if commit_refs:
        lines.append(
            "写操作证据 ref："
            + _join(
                _reply_reference_alias(item.get("ref"), reference_aliases)
                if isinstance(item, dict)
                else _reply_reference_alias(item, reference_aliases)
                for item in commit_refs
            )
        )
    constraints = payload.get("current_turn_structural_constraints") or []
    if constraints:
        lines.append("本轮结构约束：" + json_dumps(constraints))
    return "\n".join(line for line in lines if not line.endswith("："))


def build_reply_reference_aliases(payload: dict[str, Any]) -> dict[str, str]:
    """Create compact prompt-only aliases while preserving runtime provenance."""

    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    shared = evidence.get("shared_context") if isinstance(evidence.get("shared_context"), dict) else {}
    aliases: dict[str, str] = {"current_message": "now"}
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    current_ref = str(current.get("message_ref") or "").strip()
    if current_ref:
        aliases[current_ref] = "now"

    message_index = 1
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or "").strip()
        if not ref or ref in aliases:
            continue
        aliases[ref] = f"m{message_index:02d}"
        message_index += 1

    return aliases


def alias_reply_reference_fields(value: Any, payload: dict[str, Any]) -> Any:
    """Return a prompt-safe copy with aliases only in structured reference fields."""

    aliases = build_reply_reference_aliases(payload)

    def visit(item: Any, *, key: str = "") -> Any:
        if isinstance(item, dict):
            return {str(child_key): visit(child, key=str(child_key).strip().lower()) for child_key, child in item.items()}
        if isinstance(item, list):
            if key.endswith("refs"):
                return [_reply_reference_alias(child, aliases) for child in item]
            return [visit(child, key=key) for child in item]
        if isinstance(item, str) and (key == "ref" or key.endswith("_ref")):
            return _reply_reference_alias(item, aliases)
        return item

    return visit(value)


def restore_reply_output_references(value: Any, payload: dict[str, Any]) -> Any:
    """Restore prompt aliases before factual validation, without touching visible text."""

    reverse = {alias: ref for ref, alias in build_reply_reference_aliases(payload).items()}

    def visit(item: Any, *, key: str = "") -> Any:
        if isinstance(item, dict):
            for child_key, child in list(item.items()):
                normalized_key = str(child_key).strip().lower()
                item[child_key] = visit(child, key=normalized_key)
            return item
        if isinstance(item, list):
            if key.endswith("refs"):
                return [reverse.get(child, child) if isinstance(child, str) else child for child in item]
            return [visit(child, key=key) for child in item]
        if isinstance(item, str) and (key == "ref" or key.endswith("_ref")):
            return reverse.get(item, item)
        return item

    return visit(value)


def _reply_reference_alias(value: Any, aliases: dict[str, str] | None) -> str:
    ref = str(value or "").strip()
    return (aliases or {}).get(ref, ref)


def _alias_reference_list(values: Any, aliases: dict[str, str] | None) -> list[str]:
    return [_reply_reference_alias(item, aliases) for item in values if str(item or "").strip()]


def _dedupe_reference_text(value: Any) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for raw in str(value or "").replace("\r", "\n").split("\n"):
        text = " ".join(raw.split()).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return " ".join(output)


def _append_fact(lines: list[str], label: str, value: Any) -> None:
    if value not in (None, "", [], {}):
        lines.append(f"{label}：{_scalar(value)}")


def _flatten_pairs(value: Any, prefix: str = "") -> list[str]:
    output: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            output.extend(_flatten_pairs(item, child_prefix))
        return output
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            output.append(f"{prefix}={_join(value)}")
        else:
            for index, item in enumerate(value):
                output.extend(_flatten_pairs(item, f"{prefix}[{index}]"))
        return output
    output.append(f"{prefix}={_scalar(value)}" if prefix else _scalar(value))
    return output


def _join(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, (str, int, float, bool)):
        return _scalar(values)
    return "、".join(_scalar(item) for item in values if item not in (None, ""))


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return ""
    if isinstance(value, list):
        return _join(value)
    return str(value).strip()


def _pick(value: Any, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _drop_empty({key: value.get(key) for key in keys})


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {}, False, 0)
    }
