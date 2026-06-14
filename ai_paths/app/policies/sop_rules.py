from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_SOP_STAGE = "S1_GREETING_INTRO"


SOP_STAGE_RULES: dict[str, dict[str, Any]] = {
    "S1_GREETING_INTRO": {
        "title": "第一阶段：打招呼 / 介绍 / 疑问解答",
        "goal": "激活客户，承接需求，介绍淡斑方向和技术，不急着报价。",
        "applies_to": [
            "新加微信、只打招呼、问在不在、没有明确需求",
            "客户问能不能淡斑、黑色素、老年斑、晒斑、痣、皱纹等改善方向",
            "客户发图片、问方法、问会不会伤肤、问是不是门店的人或是否靠谱",
        ],
        "steps": {
            "打招呼": {
                "goal": "询问激活客户。",
                "rules": [
                    "短句承接，不长篇自我介绍。",
                    "客户没有明确需求时，可以问是否咨询淡斑/斑点。",
                    "不要一上来报价。",
                ],
                "tools": ["kb_search(sales_talk_qa)"],
                "reply_focus": "轻问需求或城市，只问一个问题。",
                "sales_style": ["亲您好呀，请问是咨询我们淡斑吗？", "在的哈亲，您是想看斑点改善吗？"],
            },
            "介绍": {
                "goal": "仪器和效果铺垫。",
                "rules": [
                    "可介绍第三代操作斑点技术、ST色素嫩肤、淡斑、美白嫩肤、收缩毛孔、痘印、细纹等方向。",
                    "可说适用于老年斑、遗传斑、各种斑点、黑色素、肤色不均等。",
                    "销冠原话中的绝对词只作参考，客户可见时改成先进、温和、到店检测更准。",
                    "不要扩写成复杂科普。",
                ],
                "tools": ["kb_search(sales_talk_qa)", "image_understanding", "kb_search(case_studies)"],
                "reply_focus": "先给可以先看的确定感，再轻推照片、城市或到店检测。",
                "sales_style": ["斑点这类可以先看改善方向哈。", "目前做的是肌源调肤哈，到店看下斑点情况更准。"],
            },
            "疑问解答": {
                "goal": "解决客户疑虑，为发地址和到店检测做铺垫。",
                "rules": [
                    "客户问项目细节时先直接回答，不要只说到店检测。",
                    "客户不懂项目名时，从需求解释到改善方向，不要求客户说项目名。",
                    "痣、痦子可以先看大小和位置，但是否适合处理要到店确认。",
                    "病历、体检报告、慢病、用药、孕期等高风险不判断，交专业同事协助。",
                ],
                "tools": ["kb_search(sales_talk_qa)", "image_understanding", "professional_assist"],
                "reply_focus": "回答当前问题，不诊断，不承诺，带到店检测或问城市。",
                "sales_style": ["可以先看，具体要看位置和皮肤状态哈。"],
            },
        },
        "must_not": [
            "不要让客户先自己说项目名。",
            "不要承诺根治、100%见效、绝对安全、保证效果、一次一定好。",
            "不要对医疗文件、慢病、用药做判断。",
            "不要因为普通担心被坑、家人不同意就直接专业协助。",
        ],
    },
    "S2_STORE_ADDRESS": {
        "title": "第二阶段：门店 / 地址铺垫",
        "goal": "拿到城市/区域，查询真实门店，推荐近一点或更方便的门店，降低到店不确定性。",
        "applies_to": [
            "客户主动说城市、区、地铁站、机场、商圈",
            "客户问哪里有店、门店在哪里、地址、营业时间、停车、路线",
            "客户质疑为什么不发详细地址或门店是否真实",
        ],
        "steps": {
            "询问地址": {
                "goal": "问城市或区域。",
                "rules": [
                    "客户没给城市时，先问城市/市区。",
                    "客户只给城市时，可以说帮他查，再追问哪个区或附近地标。",
                    "客户给了区、机场、地铁站、商圈时，不要继续问城市，直接调用门店工具。",
                ],
                "tools": ["store_lookup"],
                "reply_focus": "承接能查或有门店，再问一个位置字段，或直接给推荐门店。",
                "sales_style": ["亲，您是在哪个市区呢？我给您发附近的门店位置。"],
            },
            "地址发送": {
                "goal": "发送门店位置并铺垫到店服务。",
                "rules": [
                    "有真实门店事实时，给清晰地址。",
                    "客户问最近哪家，应根据工具结果推荐更方便的一家，不只罗列。",
                    "营业时间、停车、导航必须来自工具事实。",
                    "客户明确要详细地址时，不要模糊化。",
                ],
                "tools": ["store_lookup", "available_time"],
                "reply_focus": "给真实门店或推荐门店，顺带帮看时间/档期。",
                "sales_style": ["门店地址在这边，到店一对一服务，全程大概50分钟左右。"],
            },
        },
        "must_not": [
            "没有工具事实不能编门店、地址、营业时间、停车。",
            "客户已经给了地标不要反复问在哪个城市。",
            "不要主动说包接送、车费报销。",
            "不说门店门头名称，客户问门店名字时优先给导航地址/登记地址。",
        ],
    },
    "S3_PRICE_CLOSE": {
        "title": "第三阶段：报价 / 收单",
        "goal": "报价、解释活动、定金、尾款、名额和效果预期，推动客户线上报名10元预约金或确认到店时间。",
        "applies_to": [
            "客户问价格、活动、优惠、券、名额、截止、广告价格",
            "客户问是不是一次费用、要做多少次、一次能不能做好",
            "客户担心隐形消费、推销、到店加价",
            "客户比价、发竞品截图、已高意向适合逼单/收单",
        ],
        "steps": {
            "报价铺垫": {
                "goal": "铺垫价格和效果预期。",
                "rules": [
                    "报价前可先铺垫效果、案例、温和、到店检测。",
                    "客户问效果图或案例时查 case_studies。",
                    "不说案例次数和保证同样结果。",
                ],
                "tools": ["kb_search(case_studies)", "kb_search(sales_talk_qa)"],
                "reply_focus": "用效果增强信任，轻推活动价/预约金。",
                "sales_style": ["您看一下，这是参加活动顾客做完后的同类参考。"],
            },
            "报价": {
                "goal": "对客户进行报价，让客户有价格预期。",
                "rules": [
                    "当前公开活动价是周年庆活动268元。",
                    "线上预约金10元，到店抵扣10元，做付258元，不做退还10元。",
                    "套餐包括淡斑、检测皮肤、基础清洁、肌肤补水。",
                    "限30名，名额满恢复原价1980。",
                    "线下未预约到店按原价1980，线上预定后到店做付258。",
                    "老客价格只能根据系统客户类型和真实订单事实核对，不向客户解释内部阈值。",
                    "查不到客户类型或接口报错时，按新客公开活动价承接。",
                ],
                "tools": ["customer_context", "kb_search(sales_talk_qa)"],
                "reply_focus": "价格直接，不绕；不主动提隐形消费；结尾轻推登记名额。",
                "sales_style": ["现在参加周年庆活动就是268哈亲。", "线上预约10，到店再付258就可以了哈。"],
            },
            "逼单": {
                "goal": "对高意向客户推动预约。",
                "rules": [
                    "客户高意向或犹豫时，强调名额、活动价、预约金低门槛、到店认可再做。",
                    "实在拿不下可申请280元小气泡管理，但不能说成默认活动内容。",
                    "不强迫，不制造过度恐吓。",
                ],
                "tools": ["kb_search(sales_talk_qa)", "available_time"],
                "reply_focus": "只带一个动作：登记名额/问时间/留电话姓名。",
                "sales_style": ["这边给您预约登记一个优惠名额吗？"],
            },
            "收单": {
                "goal": "客户付款、预约完成；未付款进入后续回访。",
                "rules": [
                    "客户明确预约一个、可以、帮我登记、现在过去时，进入预约收单。",
                    "收单前需要姓名、电话、门店、时间等必要信息。",
                    "信息齐全且客户明确确认，才调用 appointment_create。",
                    "预约成功必须来自工具结果，不能模型口头说成功。",
                ],
                "tools": ["available_time", "appointment_create", "appointment_record_query"],
                "reply_focus": "明确下一步资料，不编预约成功。",
                "sales_style": ["亲，那您这边名字电话发我一下，我给您录系统后排操作档期哦。"],
            },
        },
        "must_not": [
            "不得编造其他活动名。",
            "不得主动说老客/新客区别和内部报价阈值。",
            "不得没有事实时报价格之外的数字。",
            "不得主动说隐形消费，除非客户主动问费用透明。",
            "不得承诺包接送、车费报销。",
        ],
    },
    "S4_FOLLOWUP_REACTIVATE": {
        "title": "第四阶段：回访 / 逼单 / 已邀约 / 到店后 / 售后",
        "goal": "承接犹豫、改约、爽约、到店反馈、售后、沉默回流，继续推进预约/复购；真实纠纷或风险问题交专业同事。",
        "applies_to": [
            "已邀约待到店，客户问地址、时间、流程、停车、携带物品",
            "客户要改时间、取消、不想去了、家人反对、太远、没时间",
            "客户到店后未成交，反馈一般、不满、价格异常、等待太久",
            "客户已成交，问护理、红肿、结痂、洗脸、化妆、效果",
            "投诉、退款、付款异常、真实订单状态",
        ],
        "steps": {
            "效果铺垫": {
                "goal": "持续效果铺垫吸引客户付款/到店。",
                "rules": [
                    "未付款、犹豫、沉默时，可发效果参考或案例。",
                    "案例必须来自 case_studies。",
                    "不说包效果，只说同类改善参考、很多客户反馈不错。",
                ],
                "tools": ["kb_search(case_studies)", "kb_search(sales_talk_qa)"],
                "reply_focus": "用效果增强信任，轻推预约金/到店看。",
                "sales_style": ["您看看我们的效果，也是非常好的。"],
            },
            "回访收单/逼单": {
                "goal": "最大化把握住高意向客户。",
                "rules": [
                    "客户犹豫先问顾虑，再针对顾虑回应。",
                    "家人反对可建议一起到店看。",
                    "太远/没时间强调到店整体约50-60分钟，抽空来，不耽误太久。",
                    "不想去了先问原因，不能只强推。",
                    "改约给新时间选项，爽约先理解再重新安排。",
                ],
                "tools": ["available_time", "appointment_record_query", "store_lookup"],
                "reply_focus": "根据顾虑走：问原因/给时间/重约。",
                "sales_style": ["您这边主要担心什么呢？"],
            },
            "涨价铺垫": {
                "goal": "做活动最后收单提醒。",
                "rules": [
                    "用于未付款、隔天回访、名额稀缺提醒。",
                    "只能说当前周年庆活动、限30名、名额满恢复1980。",
                    "不编最后一天，除非业务明确当天截止。",
                ],
                "tools": ["kb_search(sales_talk_qa)"],
                "reply_focus": "稀缺感 + 保留名额，不恐吓、不编截止。",
                "sales_style": ["现在活动名额不多了，要先帮您登记一个吗？"],
            },
            "已邀约待到店": {
                "goal": "确认到店事实和准备事项。",
                "rules": [
                    "客户问地址/时间，查预约或门店事实后回复。",
                    "营业时间、停车路线必须基于门店事实。",
                    "客户说到楼下/正在路上，识别到店，必要时让专业同事或门店接待协助。",
                ],
                "tools": ["appointment_record_query", "store_lookup", "available_time", "professional_assist"],
                "reply_focus": "直接确认事实，不重新回到新客介绍。",
                "sales_style": ["出发了跟我说一下哈。"],
            },
            "到店后未成交": {
                "goal": "承接现场反馈和未成交原因。",
                "rules": [
                    "感受好：感谢认可，顺势做效果期待/复购/转介绍。",
                    "感受一般：问哪里不满意，不急推。",
                    "价格/套餐疑问：解释活动和方案，不乱报。",
                    "服务不满、等待太久：先问具体原因，严重时专业同事协助。",
                ],
                "tools": ["kb_search(sales_talk_qa)", "store_lookup", "professional_assist"],
                "reply_focus": "先承接情绪，再问原因或给处理路径。",
                "sales_style": ["具体是哪方面让您不满意呢，和我说一下。"],
            },
            "已成交售后": {
                "goal": "护理建议和风险升级。",
                "rules": [
                    "轻微护理问题给基础护理建议，避免诊断。",
                    "严重红肿、渗液、剧痛、发热、感染风险：专业同事协助。",
                    "效果不佳：先问项目、时间、门店、照片，不直接说正常/没事。",
                    "投诉退款：专业同事协助。",
                ],
                "tools": ["kb_search(sales_talk_qa)", "professional_assist"],
                "reply_focus": "简短护理，风险及时升级。",
                "sales_style": ["先注意补水，有不舒服及时和我说。"],
            },
            "沉默/流失客回归": {
                "goal": "回流承接和重新激活。",
                "rules": [
                    "热情欢迎回归，不生硬翻旧账。",
                    "有历史画像时自然衔接；没有历史时按新客承接。",
                    "问活动/价格直接按周年庆活动。",
                    "直接表达到店意向，进入邀约流程，不重做大量画像收集。",
                ],
                "tools": ["kb_search(sales_talk_qa)", "store_lookup", "available_time"],
                "reply_focus": "回归承接，快速推进活动/门店/预约。",
                "sales_style": ["当然在呢，随时在。"],
            },
        },
        "must_not": [
            "不要把所有不满都交给专业同事；普通不满先承接和收集原因。",
            "不要对严重不适自行判断。",
            "不要编预约、门店、档期。",
            "不要对已预约客户重复新客介绍。",
            "不要承诺保证效果、一定满意。",
            "不要包接送、报销车费。",
        ],
    },
}


SOP_STAGE_ALIASES = {
    "S1": "S1_GREETING_INTRO",
    "S1-破冰中": "S1_GREETING_INTRO",
    "S2": "S2_STORE_ADDRESS",
    "S2-画像收集中": "S2_STORE_ADDRESS",
    "S3": "S3_PRICE_CLOSE",
    "S3-深度咨询中": "S3_PRICE_CLOSE",
    "S4": "S4_FOLLOWUP_REACTIVATE",
    "S4-邀约协商中": "S4_FOLLOWUP_REACTIVATE",
    "S5": "S4_FOLLOWUP_REACTIVATE",
    "S5-已邀约待到店": "S4_FOLLOWUP_REACTIVATE",
    "S6": "S4_FOLLOWUP_REACTIVATE",
    "S6-已到店未成交": "S4_FOLLOWUP_REACTIVATE",
    "S7": "S4_FOLLOWUP_REACTIVATE",
    "S7-已成交活跃客": "S4_FOLLOWUP_REACTIVATE",
    "S8": "S4_FOLLOWUP_REACTIVATE",
    "S8-沉默/流失客": "S4_FOLLOWUP_REACTIVATE",
}


TASK_TYPE_DEFAULT_STAGE = {
    "opening": "S1_GREETING_INTRO",
    "general_chat": "S1_GREETING_INTRO",
    "general_consult": "S1_GREETING_INTRO",
    "project_consult": "S1_GREETING_INTRO",
    "image_consult": "S1_GREETING_INTRO",
    "trust_issue": "S1_GREETING_INTRO",
    "store_inquiry": "S2_STORE_ADDRESS",
    "price_inquiry": "S3_PRICE_CLOSE",
    "activity_inquiry": "S3_PRICE_CLOSE",
    "competitor_compare": "S3_PRICE_CLOSE",
    "case_request": "S3_PRICE_CLOSE",
    "appointment": "S3_PRICE_CLOSE",
    "appointment_status": "S4_FOLLOWUP_REACTIVATE",
    "appointment_change": "S4_FOLLOWUP_REACTIVATE",
    "appointment_cancel": "S4_FOLLOWUP_REACTIVATE",
    "after_sales": "S4_FOLLOWUP_REACTIVATE",
    "complaint_refund": "S4_FOLLOWUP_REACTIVATE",
    "human_request": "S4_FOLLOWUP_REACTIVATE",
}


def normalize_sop_stage(value: Any, *, task_type: str = "", request_stage: str = "") -> str:
    text = str(value or "").strip()
    if text in SOP_STAGE_RULES:
        return text
    if text in SOP_STAGE_ALIASES:
        return SOP_STAGE_ALIASES[text]
    upper = text.upper()
    if upper in SOP_STAGE_RULES:
        return upper
    for prefix, stage_id in (
        ("S1", "S1_GREETING_INTRO"),
        ("S2", "S2_STORE_ADDRESS"),
        ("S3", "S3_PRICE_CLOSE"),
        ("S4", "S4_FOLLOWUP_REACTIVATE"),
        ("S5", "S4_FOLLOWUP_REACTIVATE"),
        ("S6", "S4_FOLLOWUP_REACTIVATE"),
        ("S7", "S4_FOLLOWUP_REACTIVATE"),
        ("S8", "S4_FOLLOWUP_REACTIVATE"),
    ):
        if text.startswith(prefix):
            return stage_id

    request_text = str(request_stage or "").strip()
    if request_text in SOP_STAGE_ALIASES:
        return SOP_STAGE_ALIASES[request_text]
    for prefix, stage_id in (
        ("S1", "S1_GREETING_INTRO"),
        ("S2", "S2_STORE_ADDRESS"),
        ("S3", "S3_PRICE_CLOSE"),
        ("S4", "S4_FOLLOWUP_REACTIVATE"),
        ("S5", "S4_FOLLOWUP_REACTIVATE"),
        ("S6", "S4_FOLLOWUP_REACTIVATE"),
        ("S7", "S4_FOLLOWUP_REACTIVATE"),
        ("S8", "S4_FOLLOWUP_REACTIVATE"),
    ):
        if request_text.startswith(prefix):
            return stage_id

    return TASK_TYPE_DEFAULT_STAGE.get(str(task_type or "").strip(), DEFAULT_SOP_STAGE)


def normalize_sop_step(stage_id: str, value: Any = "") -> str:
    stage = SOP_STAGE_RULES.get(normalize_sop_stage(stage_id), SOP_STAGE_RULES[DEFAULT_SOP_STAGE])
    steps = stage.get("steps", {})
    text = str(value or "").strip()
    if text in steps:
        return text
    normalized_text = text.replace(" ", "")
    for step_name in steps:
        if normalized_text and normalized_text == step_name.replace(" ", ""):
            return step_name
    for step_name in steps:
        if step_name and step_name in text:
            return step_name
    for step_name in steps:
        if normalized_text and normalized_text in step_name.replace(" ", ""):
            return step_name
    return next(iter(steps), "")


def sop_stage_rules_for(stage_id: str) -> dict[str, Any]:
    return deepcopy(SOP_STAGE_RULES.get(normalize_sop_stage(stage_id), SOP_STAGE_RULES[DEFAULT_SOP_STAGE]))


def compact_sop_stage_rules_for_reply(stage_id: str, step: str = "") -> dict[str, Any]:
    stage_id = normalize_sop_stage(stage_id)
    stage = sop_stage_rules_for(stage_id)
    step_name = normalize_sop_step(stage_id, step)
    current_step = stage.get("steps", {}).get(step_name, {})
    return {
        "stage_id": stage_id,
        "stage_title": stage.get("title", ""),
        "stage_goal": stage.get("goal", ""),
        "current_step": step_name,
        "step_goal": current_step.get("goal", ""),
        "step_rules": current_step.get("rules", []),
        "step_tools": current_step.get("tools", []),
        "reply_focus": current_step.get("reply_focus", ""),
        "sales_style": current_step.get("sales_style", []),
        "stage_must_not": stage.get("must_not", []),
        "available_steps": list((stage.get("steps") or {}).keys()),
    }


def sop_planner_prompt_section() -> str:
    lines = [
        "# SOP Stage Planning",
        "You must choose exactly one current SOP stage and one current step. The SOP stage controls business rhythm; fine policy ids are only logging/compatibility.",
        "Output primary_task.sop_stage and primary_task.sop_step.",
        "Planner decides tools and tool parameters. Final Reply will receive the full current stage rules and decide wording.",
        "",
    ]
    for stage_id, rule in SOP_STAGE_RULES.items():
        lines.append(f"## {stage_id}: {rule['title']}")
        lines.append(f"- Goal: {rule['goal']}")
        lines.append("- Applies to: " + "；".join(rule.get("applies_to", [])[:3]))
        lines.append("- Steps: " + " / ".join((rule.get("steps") or {}).keys()))
        lines.append("- Must not: " + "；".join(rule.get("must_not", [])[:4]))
        lines.append("")
    lines.append(
        "Tool rule: store/address/hours/parking must call store_lookup; appointment time/status must call available_time or appointment_record_query; effect/case requests must call case_studies; sales wording should call sales_talk_qa with the original customer wording."
    )
    return "\n".join(lines).strip()
