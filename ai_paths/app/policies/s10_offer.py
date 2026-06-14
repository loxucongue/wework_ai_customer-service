from __future__ import annotations

from copy import deepcopy
from typing import Any


ACTIVE_S10_OFFER_CONTEXT: dict[str, Any] = {
    "mode": "single_s10_front_reception",
    "project_code": "S10",
    "project_name": "S10 淡斑套餐",
    "customer_visible_project_name": "周年庆淡斑活动",
    "marketing_activity_name": "周年庆活动",
    "source_channel_note": "抖音/快手合作线上报名线索；客户可见活动名统一为周年庆活动。",
    "brand_scale": "全国连锁门店400多家；具体城市、地址、营业时间、停车和档期必须以 store_lookup / available_time 真实工具结果为准。",
    "method_summary": (
        "采用目前属于先进有效的第三代操作斑点技术，围绕淡斑、美白嫩肤、收缩毛孔、痘印、改善细纹等方向承接。"
        "可以提 ST 色素嫩肤从皮肤底层处理斑点问题，适用于老年斑、遗传斑等各类斑点方向。"
    ),
    "method_sales_reference": (
        "销售原话参考：目前我们家是国内最先进的技术，只针对黑色素，不伤皮肤，没有不良反应的。"
        "客户可见时需合规改写为：目前做的是先进的斑点改善技术，主要围绕黑色素、斑点和肤色不均方向，"
        "整体更偏温和；具体还是到店看皮肤状态后更准。"
    ),
    "new_customer_price": 268,
    "old_customer_price_rules": [
        {"condition": "上一次订单超过1000元", "price": 680},
        {"condition": "上一次订单不超过1000元", "price": 520},
    ],
    "reservation_deposit": 10,
    "tail_payment": 258,
    "original_price": 1980,
    "quota": 30,
    "package_items": ["淡斑", "检测皮肤", "基础清洁", "肌肤补水"],
    "campaign_package_name": "抖音合作线上秒杀活动（淡斑套餐）",
    "signup_rule": "仅限线上报名客户有效；线上预定10元并留电话姓名，到店抵扣10元，做付258元，不做退还10元。",
    "walk_in_rule": "线下客人未预定到店按原价1980元；预定后名额到店做付258元。",
    "hard_close_benefit": "客户实在拿不下时，可说帮客户申请280元小气泡管理，但不要说成默认活动内容。",
    "hard_constraints": [
        "当前阶段只推进S10淡斑套餐前端接待，不主动推荐S10N/K10/M10/其他品项。",
        "对客户不要说S10、项目编码、单品编号；客户侧只说周年庆活动、淡斑活动、斑点改善方向。",
        "对客户只说周年庆活动，不得编造焕新体验季、新客专属活动、老带新专属活动、内部活动、大型活动等名称。",
        "客户问活动/价格/项目内容时，直接按S10周年庆活动事实回答；即使调用工具，也只能使用当前S10周年庆事实。",
        "老客价必须结合系统里的客户类型和上一次订单金额事实；没有订单金额事实时，只能说老客价需要按上次订单记录核对，不能对客户讲超过/不超过1000元对应的价格规则。",
        "10元预约金规则：到店抵扣10，做付258，不做退还10；不要改写成全额退还。",
        "10元预约金只是到店抵扣，不是额外优惠、省钱、减免或立减；不要说节省10元。",
        "不要单独说10元预约金不退还；必须完整表达为到店抵扣10元、做付258元、不做退还10元。",
        "没有真实订单查询结果时，不要说正在查订单、稍等、稍后同步；只说老客价需要核对上次订单金额。",
        "“国内最先进、不伤皮肤、没有不良反应”只能作为销售原话参考，不得无条件原样承诺；客户可见时优先说先进、温和、到店检测后更准。",
        "不承诺一次一定好、根治、100%见效、绝对安全、包效果。",
    ],
}


def s10_offer_context() -> dict[str, Any]:
    return deepcopy(ACTIVE_S10_OFFER_CONTEXT)


def s10_price_facts() -> list[dict[str, Any]]:
    context = ACTIVE_S10_OFFER_CONTEXT
    common = {
        "project_code": "S10",
        "project_name": context["project_name"],
        "category": "S10单品前端接待",
        "body_scope": "单部位体验",
        "original_price": str(context["original_price"]),
        "description": context["method_summary"],
        "suitable_for": "老年斑、遗传斑、各类斑点、黑色素、肤色不均、痘印、毛孔、细纹等基础改善方向",
        "contraindications": "孕期、哺乳期、皮肤破损或明显不适期需谨慎",
        "effects": '["操作斑点","检测皮肤","基础清洁","肌肤补水"]',
        "duration": "到店整体约50-60分钟，具体以门店安排为准",
    }
    return [
        {
            **common,
            "rule_id": "S10_ANNIVERSARY_NEW_268",
            "quote_type": "周年庆活动价",
            "customer_segment": "线上报名客户",
            "prepay_amount": str(context["reservation_deposit"]),
            "tail_amount": str(context["tail_payment"]),
            "total_price": str(context["new_customer_price"]),
            "display_price": "周年庆活动价268元，线上预约金10元，到店抵扣10元，做付258元；不做退还10元",
            "min_quote": str(context["new_customer_price"]),
            "conditions": "抖音合作线上秒杀活动；仅限线上报名客户；限30名；套餐包括淡斑、检测皮肤、基础清洁、肌肤补水；线下客人未预定到店按原价1980元",
            "rule_note": "当前只有周年庆活动，不得生成其他活动名。",
        },
        {
            **common,
            "rule_id": "S10_OLD_GT_1000_680",
            "quote_type": "老客报价",
            "customer_segment": "老客且系统核对上一次订单超过1000元",
            "prepay_amount": "0",
            "tail_amount": "680",
            "total_price": "680",
            "display_price": "系统核对上一次订单金额后，符合老客档位时报价680元",
            "min_quote": "680",
            "conditions": "仅用于系统内部按客户历史订单金额确认，不对客户解释阈值规则",
            "rule_note": "没有订单金额事实时，不要直接判断客户老客档位；对客户不能讲超过/不超过1000元的价格区别。",
        },
        {
            **common,
            "rule_id": "S10_OLD_LE_1000_520",
            "quote_type": "老客报价",
            "customer_segment": "老客且系统核对上一次订单不超过1000元",
            "prepay_amount": "0",
            "tail_amount": "520",
            "total_price": "520",
            "display_price": "系统核对上一次订单金额后，符合老客档位时报价520元",
            "min_quote": "520",
            "conditions": "仅用于系统内部按客户历史订单金额确认，不对客户解释阈值规则",
            "rule_note": "没有订单金额事实时，不要直接判断客户老客档位；对客户不能讲超过/不超过1000元的价格区别。",
        },
    ]


def attach_s10_offer_facts(fact_envelope: dict[str, Any] | None) -> dict[str, Any]:
    envelope = deepcopy(fact_envelope or {})
    structured = envelope.setdefault("structured_facts", {})
    if not isinstance(structured, dict):
        structured = {}
        envelope["structured_facts"] = structured

    structured["active_offer_context"] = s10_offer_context()
    existing = structured.get("price_facts")
    existing_list = existing if isinstance(existing, list) else []
    seen_rule_ids = {str(item.get("rule_id") or "") for item in existing_list if isinstance(item, dict)}
    for fact in s10_price_facts():
        if fact["rule_id"] not in seen_rule_ids:
            existing_list.append(fact)
    structured["price_facts"] = existing_list

    usable_facts = envelope.setdefault("usable_facts", [])
    if isinstance(usable_facts, list) and not any("周年庆活动" in str(item) for item in usable_facts):
        usable_facts.append(
            "active_offer: S10周年庆活动/抖音合作线上秒杀淡斑套餐；公开活动价268元；线上预约金10元，到店抵扣10元，做付258元，不做退还10元；限30名；全国连锁门店400多家"
        )
    return envelope


def s10_offer_prompt_section() -> str:
    return """
# Current Active Offer
- 当前阶段只做 S10 淡斑套餐的前端接待和推进预约，不主动推荐其他品项。
- S10 只是内部项目编码，对客户不要说 S10、项目编码、单品编号；客户侧统一说“周年庆活动”“淡斑活动”“斑点改善方向”。
- 当前对外活动只有“周年庆活动”；客户问活动、优惠、广告、券、活动价时，都按周年庆活动口径表达。
- 不得编造“焕新体验季、新客专属活动、老带新专属活动、内部活动、大型活动、公司统一通知价”等活动名。
- 可说明全国连锁门店400多家；但具体城市、地址、营业时间、停车、档期必须以真实工具结果为准。
- S10 采用目前属于先进有效的第三代操作斑点技术，围绕淡斑、美白嫩肤、收缩毛孔、痘印、改善细纹等方向承接；可提 ST 色素嫩肤从皮肤底层处理斑点问题，但不要讲成复杂科普。
- “国内最先进、不伤皮肤、没有不良反应”只作为销售原话参考，不要无条件原样承诺；客户可见优先改成“先进的斑点改善技术、主要围绕黑色素和斑点方向、整体更偏温和、到店检测后更准”。
- 适用方向：老年斑、遗传斑、各类斑点、黑色素、肤色不均等。
- 周年庆公开活动价 268 元；线上报名交 10 元预约金并留电话姓名，到店抵扣 10 元，做付 258 元；不做退还 10 元。
- 10 元预约金只是到店抵扣，不是额外优惠、省钱、减免或立减；不要说“节省10元”。
- 抖音合作线上秒杀活动（淡斑套餐）包含：淡斑、检测皮肤、基础清洁、肌肤补水。
- 活动限 30 名；名额满恢复原价 1980 元；线下客人未预定到店按原价 1980 元；预定后名额到店做付 258 元。
- 客户类型只看系统客户信息 kind：kind=1 新客，kind=2 老客；查不到、接口错误或未知时按公开活动价 268。
- 老客报价是内部规则：系统确认 kind=2 且有上一次订单金额事实时，上一次订单金额超过 1000 元可报 680 元，不超过 1000 元可报 520 元；没有订单金额事实时，只能说“老客价要按您上次订单记录核对后才报得准”。不要对客户讲超过/不超过1000元对应的价格区别。
- 不允许问客户是新客还是老客，客户自称老客或自称上一次订单金额不能作为报价依据。
- 预约金口径固定说“不做退还10元”，不要写成“全额退还10元”；老客价没有真实订单查询结果时，不要说“我去查订单、稍等、稍后同步”。
- 实在拿不下且客户已明显价格犹豫时，可以说“亲给你的价格已经是最优惠的了，看你也是真诚信任我，我这边再帮你申请一个280元小气泡管理哈”；不要把小气泡说成默认套餐内容。
""".strip()
