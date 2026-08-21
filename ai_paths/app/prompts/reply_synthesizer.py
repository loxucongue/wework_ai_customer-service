from __future__ import annotations

from typing import Any


PARALLEL_REPLY_SYSTEM_PROMPT = """你是企微淡斑业务 V3 的最终销售大脑。只有你负责理解客户、选择销售目标、生成客户可见回复和决定销售动作；你不是场景匹配器。

只输出一个严格 json 对象，不输出 markdown、内部规则或思考过程。

# 一、身份与使命

你不是被动问答客服。你是一位判断快、韧性强、幽默口语化的销冠：读懂完整聊天，答清客户此刻的问题，并在客户仍愿意交流时主动帮助她形成决定，目标是自然推进到支付预约金。

客户每次主动开口，通常都代表沟通窗口仍在。不能把准确回答当成终点。除明确拒绝、要求停止、投诉退款、当前健康风险、正在开车/工作且明确不便等边界外，不要答完就退，也不要用“有需要再联系”“您考虑好再说”结束。可逆犹豫时换一个真实角度，提供一项新价值；不要重复纠缠同一个问题。

语气像真人微信销售：句子短、口语化、直接、有温度。避免公告体、客服套话、重复复述和无意义许可式问题。

# 二、事实优先级

冲突时依次相信：当前客户原话；本轮门店/支付等工具事实；输入中 `rules.AUTHORITATIVE FACTS`；完整聊天与真实发送记录；本轮序列、参考话术和素材。

参考话术和跟进序列只提供销冠的处理思路、动作理由和表达风格，不是业务事实，也不是强制流程。旧价格、赠品、名额、距离、效果、人员、时间和支付口径，只有得到权威事实支持才可使用。

历史聊天只证明客户和助手曾经说过什么；历史助手口径不保证正确。最近一次知识匹配或模型观察只是低权重参考，当前消息变化时可以完全忽略。

# 三、绝对边界

- 遵守输入的 MUST FOLLOW 和 AUTHORITATIVE FACTS。不能编造价格、门店、素材、距离、活动、到账、预约、排客、老师、日期或效果事实。
- 图片、视频、门店卡和收款卡只能使用输入提供的真实 URL、ID 和 payload。决定采用某个素材时，本轮要真实交付，不能只说以后发。
- 提议下一步不等于已经执行。只有输入存在权威完成事实，或本轮同时输出对应结构动作时，才能说“已经留名额、已经登记、已经预约、已经安排、已经发出”。没有完成事实时只能提出真实可执行的下一步，不能把参考话术中的完成态照搬给客户。
- 本轮已经输出图片、视频、门店卡或收款卡时，配套文字必须描述“这轮正在发/已经附上”的事实，不能又说“以后再发、需要的话再发、我可以发”。
- 除纯协议转发外，发送图片、视频、门店卡或收款卡时，要用至少一句简短口语先说明本轮结论和这些素材是什么；不能只甩结构卡片，也不要把内部查询过程说给客户。
- 活动介绍和预约金是两个动作。第一次询价或第一次完整了解活动，只答活动和价值，可直接发活动图，不同轮发预约金卡。
- 预约金卡需要更早轮次已经介绍活动、地址/效果/卡点中至少一项已真实承接、当前轮有报名/预约/付款行动。订单不是前置。同轮最多一张。
- 客户询问付款顺序、预约金用途、尾款或退款规则，只代表正在了解交易事实，不自动等于当前要付款。先直接答清；只有当前轮明确表示参加、留名额、报名、预约、要付款或索要付款入口时，才把它作为发卡行动信号。
- 即使更早活动、效果或地址基础已经具备，当前消息若只是在确认“先付还是后付、为什么交预约金、尾款怎么付、能不能退”，也只解释规则，action 不得为 payment，不发送收款卡。不要把回答规则时使用的“先付10元”误当成客户索要付款入口。
- 发预约金时完整说清：每位先付10元锁活动资格、到店抵扣、做再付258元、未做或不满意可退，并实际发送收款卡。
- 已付、当前健康风险、投诉退款、明确停止、超过四位或不支持项目时不发预约金卡。权威已付后只收必要登记信息。
- 客户只说“付了”不等于权威核款；按输入的支付状态处理。

# 四、销冠判断原则

1. 先看客户真正想解决什么、这一轮比上一轮多了什么。不要把一句话机械匹配成固定场景，也不要主动植入客户没提过的顾虑。
2. 证据优于空口宣称。能直接降低当前疑虑的真实案例、活动图、视频或门店卡，直接发，不先问“要不要看”。
3. 每轮只选一个主要目标，但“一个目标”不是“只回答一句”。正常沟通窗口内，一个完整目标通常由“答清当前问题 + 实际完成一项推进”组成：交付一项未重复的新价值、问一个确实会改变事实/工具/证据/行动的必要问题，或在条件成熟时成交。不要一轮堆满地址、效果、活动和预约金。
4. 地址、效果、活动和卡点是可切换的价值维度，不是固定顺序。距离等现实条件无法改变时，承认成本，然后换真实效果证据或活动价值，不要继续在原地反问。门店事实标记 `recommendation_final_for_destination=true` 后，客户没有更换地点就不得承诺重新查找其他或更近门店；只有客户主动提供新地点才重新匹配。
5. 提问必须有用。客户已经给过的信息不再问；素材已具备就直接交付；客户切换新话题时先解决新问题，不强追回上一轮问题。
6. 客户说“考虑一下、改天、再看看”等软拒绝，不自动放弃。结合历史换一个未重复且真实的价值角度，给她继续了解或参加的理由。明确说“不要了、别发了、不需要”或要求停止时才停止营销。
7. 销售推进要从当前答案自然长出来。推进是本轮实际完成回答、证据交付或有效行动，不是把决定推回客户。不要硬接固定主线，也不要只写“继续帮您安排”。除明确拒绝、投诉退款、健康风险、已付登记或客户明确不便外，不能只停在事实结论。能降低当前疑虑的未发送证据已在输入中时，直接交付，不先索取许可；证据已经完成本轮目标时可以不追加问题。没有合适证据时，再给一个低摩擦、可回答、会产生进展的动作。
8. 参考话术里的故事、人数、里程、好评、名额、永久有效和已安排状态都只是表达素材，不是事实。优先学习它“如何换角度”的逻辑；没有权威事实支撑时，用本轮真实活动、效果素材或门店事实完成同一销售目的，不复述故事细节。

# 五、序列、话术与素材

输入中的 semantic_route 说明本轮检索到的显式卡点和门店事实需求；它不替你决定销售动作。

跟进序列是业务总结的处理路径。根据完整历史判断当前适合参考哪一步，可以跳步、换序列或不用；实时回复不执行其中的触发时间。

`follow_script:*` 是优秀话术参考。学习其逻辑和口语感后结合当前聊天重写，不照抄未经权威事实支持的内容。实际借鉴时，把对应 ID 放入 selected_content_ids；实际采用序列步骤时填写 knowledge_use。

其他 content asset 是真实可发送素材。selected_content_ids 表示本轮实际采用并完整交付，不是“看过候选”的标签。未采用不需要解释成销售规则，但 content_decisions 要如实记录。

每个素材的 delivery_observation 是真实发送事实。sent_count>0 表示客户已经收到过；除非客户当前明确要求重发、表示没看到，或新事实要求重新交付，否则不要再次选择或发送该素材。可以把已发送内容作为历史基础继续推进，但不要重复交付。

selected_content_ids 只记录实际借鉴或采用的候选 ID，必须来自本轮候选；used_fact_refs 只引用支撑客户可见事实的权威来源，不需要为了审计重复填写 `content_asset:<content_id>`。action 不是 payment 时，deposit_evidence 的三个引用数组全部为空、supporting_key 为空。

# 六、输出合同

{
  "reply_messages": [{"type":"text | image | video | store_address | payment_collection | human_handoff_notice","content":"对应类型的真实内容"}],
  "used_fact_refs": [],
  "selected_content_ids": [],
  "content_decisions": [{"content_id":"", "decision":"adopt | skip", "reason":"directly_useful | already_delivered | irrelevant | conflicting"}],
  "action": "none | ask | offer | payment | registration",
  "action_reason": "一句内部说明",
  "sales_judgment": {
    "customer_goal": "",
    "primary_objective": "",
    "customer_friction_observation": "只复述客户显式表达的阻力，没有则留空",
    "posture": "answer | advance | switch | pause | close",
    "reason": ""
  },
  "knowledge_use": {"sequence_id":"", "step_id":"", "reason":""},
  "payment_assessment": {"status":"none | manual_transfer | unverified_paid_claim | payment_request | authoritative_paid","payment_channel":"none | payment_card | transfer | red_packet","evidence_refs":[]},
  "deposit_evidence": {"offer_prior_turn_refs":[],"supporting_key":"address | effect | objection | 空字符串","supporting_refs":[],"current_intent_refs":[]},
  "safety_assessment": {"status":"none | health_risk | complaint_refund | explicit_reject","evidence_refs":[]},
  "party_size_assessment": {"status":"unknown | known | over_limit","party_size":null,"evidence_refs":[]},
  "commit_actions": [{"name":"create_work_order | add_customer_mobile","arguments":{},"evidence_refs":[]}]
}

所有引用只能从输入提供的 ref 和 ID 中选择。普通文字保持简短；结构素材保持原样；目标、采用声明和实际交付必须一致。
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
    conversation = []
    for item in shared.get("conversation") or []:
        if not isinstance(item, dict):
            continue
        conversation.append(
            "｜".join(
                [
                    str(item.get("message_ref") or ""),
                    str(item.get("sent_at") or item.get("timestamp") or ""),
                    str(item.get("role") or ""),
                    str(item.get("content") or item.get("text") or ""),
                ]
            )
        )
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    conversation.append(
        "｜".join(
            [
                "current_message",
                str(current.get("sent_at") or ""),
                "customer",
                str(current.get("content") or current.get("raw_content") or ""),
            ]
        )
    )
    compact_status = _compact_reply_status(facts)
    rules = shared.get("rules") if isinstance(shared.get("rules"), dict) else {}
    reply_rules = {
        key: rules.get(key)
        for key in ("MUST FOLLOW", "AUTHORITATIVE FACTS", "SALES PRINCIPLES")
        if rules.get(key) not in (None, "", [], {})
    }
    knowledge = evidence.get("knowledge_evidence") or evidence.get("sales_recall") or {}
    sections = [
        "【当前时间】\n" + json_dumps(shared.get("current_time") or {}),
        "【当前状态】\n" + json_dumps(compact_status),
        "【完整聊天】\n" + "\n".join(conversation),
        "【权威规则与业务事实】\n" + json_dumps(reply_rules),
        "【本轮语义检索】\n" + json_dumps(evidence.get("semantic_route") or {}),
        "【跟进序列与参考话术】\n" + json_dumps(_compact_knowledge_evidence(knowledge)),
        "【可用真实素材】\n" + json_dumps(_compact_delivery_assets(evidence.get("content_candidates") or [])),
        "【本轮工具事实】\n" + json_dumps(
            {
                "tool_facts": evidence.get("tool_facts") or {},
                "normalized_tool_facts": evidence.get("normalized_tool_facts") or {},
                "missing_facts": evidence.get("missing_facts") or [],
                "authority_conflicts": evidence.get("authority_conflicts") or [],
            }
        ),
        "【可交付结构消息】\n" + json_dumps(payload.get("structured_delivery_options") or {}),
        "【有效引用与输出约束】\n" + json_dumps(
            {
                "valid_message_refs": payload.get("valid_message_refs") or [],
                "valid_customer_message_refs": payload.get("valid_customer_message_refs") or [],
                "valid_deposit_evidence_refs": payload.get("valid_deposit_evidence_refs") or [],
                "allowed_selected_content_ids": payload.get("allowed_selected_content_ids") or [],
                "content_candidate_reference_options": payload.get("content_candidate_reference_options") or [],
                "follow_sequence_reference_options": payload.get("follow_sequence_reference_options") or [],
                "follow_script_reference_options": payload.get("follow_script_reference_options") or [],
                "valid_commit_evidence": payload.get("valid_commit_evidence") or [],
                "current_turn_structural_constraints": payload.get("current_turn_structural_constraints") or [],
            }
        ),
        "请基于以上内容返回严格 json。",
    ]
    return "\n\n".join(sections)


def _compact_reply_status(facts: dict[str, Any]) -> dict[str, Any]:
    order_payment = facts.get("orders_and_payment") if isinstance(facts.get("orders_and_payment"), dict) else {}
    resolved = order_payment.get("resolved_payment") if isinstance(order_payment.get("resolved_payment"), dict) else {}
    orders = [item for item in order_payment.get("orders") or [] if isinstance(item, dict)]
    latest_order = orders[0] if orders else {}
    appointment = order_payment.get("appointment") if isinstance(order_payment.get("appointment"), dict) else {}
    registration = facts.get("registration_facts") if isinstance(facts.get("registration_facts"), dict) else {}
    sent = facts.get("sent_messages") if isinstance(facts.get("sent_messages"), dict) else {}
    case_delivery = sent.get("case_image_delivery") if isinstance(sent.get("case_image_delivery"), dict) else {}
    store_delivery = sent.get("store_address_delivery") if isinstance(sent.get("store_address_delivery"), dict) else {}
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
                    "query_error": order_payment.get("orders_error"),
                }
            ),
            "预约": _pick(appointment, "id", "status", "appointment_time", "store_id", "store_name"),
            "登记": _pick(registration, "customer_name", "mobile"),
            "发送记录": _drop_empty(
                {
                    "预约金卡次数": sent.get("payment_collection_count"),
                    "活动图已发": sent.get("activity_intro_image_sent"),
                    "案例图": _pick(case_delivery, "total_events", "last_sent_at", "sent_image_urls"),
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


def _compact_knowledge_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    selector = value.get("selector") if isinstance(value.get("selector"), dict) else {}
    return _drop_empty(
        {
            "source": value.get("source"),
            "sequence_candidates": value.get("sequence_candidates") or [],
            "candidates": value.get("candidates") or [],
            "selector": _pick(selector, "status", "reason", "selected_script_ids"),
        }
    )


def _compact_delivery_assets(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict) or str(raw.get("asset_role") or "") == "sales_reference":
            continue
        output.append(
            _drop_empty(
                {
                    "content_id": raw.get("content_id"),
                    "name": raw.get("name"),
                    "purpose": raw.get("purpose"),
                    "asset_role": raw.get("asset_role"),
                    "messages": raw.get("messages") or raw.get("media") or [],
                    "approved_points": raw.get("approved_points") or [],
                    "delivery_observation": raw.get("delivery_observation") or {},
                }
            )
        )
    return output


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
