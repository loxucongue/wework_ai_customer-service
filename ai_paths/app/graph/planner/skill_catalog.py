from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    positive_examples: tuple[str, ...] = field(default_factory=tuple)
    negative_examples: tuple[str, ...] = field(default_factory=tuple)
    required_slots: tuple[str, ...] = field(default_factory=tuple)
    optional_slots: tuple[str, ...] = field(default_factory=tuple)
    must_call_tools: tuple[str, ...] = field(default_factory=tuple)
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)
    exit_conditions: tuple[str, ...] = field(default_factory=tuple)
    risk_notes: tuple[str, ...] = field(default_factory=tuple)


SKILL_CATALOG: tuple[SkillDefinition, ...] = (
    SkillDefinition(
        name="project_consult",
        description="客户咨询皮肤问题、改善方向、项目流程、项目适合性、恢复期或操作时长时使用。",
        positive_examples=(
            "我脸上这种斑能改善吗",
            "这个广告上说的项目具体怎么操作",
            "大概需要做多久",
            "我不懂项目，你先帮我判断方向",
        ),
        negative_examples=(
            "这个多少钱",
            "你们门店在哪里",
            "我约的是几点",
            "你们正规吗",
        ),
        optional_slots=("concern", "body_part", "image_info", "project_name"),
        must_call_tools=("sales_talk_qa", "project_qa"),
        exit_conditions=("已给出方向性判断", "客户转为价格、门店或预约问题"),
    ),
    SkillDefinition(
        name="price_consult",
        description="客户询问明确项目、改善方向、套餐、活动或预算价格时使用。",
        positive_examples=(
            "这个多少钱",
            "光子嫩肤一次什么价格",
            "预算太高了",
            "有没有活动价",
        ),
        negative_examples=(
            "广告上这个怎么操作",
            "客户做完效果我想看一下",
            "你们正规吗",
            "下午能约吗",
        ),
        optional_slots=("project_name", "price_type", "customer_type"),
        must_call_tools=("sales_talk_qa", "project_price"),
        forbidden_tools=("available_time_query",),
        risk_notes=("没有价格事实时不能编造金额或价格区间。",),
    ),
    SkillDefinition(
        name="store_inquiry",
        description="客户询问门店、地址、路线、导航、停车、营业时间、附近门店时使用。",
        positive_examples=(
            "你们厦门有门店吗",
            "机场附近哪家店近",
            "这家店地址发我",
            "有停车吗",
            "几点关门",
        ),
        negative_examples=(
            "下午能约吗",
            "这个多少钱",
            "你们正规吗",
            "做完有没有效果",
        ),
        optional_slots=("city", "location_hint", "store_name", "parking", "route"),
        must_call_tools=("store_search",),
        forbidden_tools=("available_time_query",),
        exit_conditions=("已回答门店事实", "客户转为预约意图"),
    ),
    SkillDefinition(
        name="appointment",
        description="客户明确想到店、预约、改约、取消预约或查询已有预约时使用。",
        positive_examples=(
            "下午能约吗",
            "帮我查一下我约的是几点",
            "我想改到明天",
            "取消预约",
        ),
        negative_examples=(
            "你们门店在哪里",
            "这家店几点关门",
            "这个项目怎么做",
            "会不会乱收费",
        ),
        optional_slots=("store_id", "store_name", "date", "time", "appointment_id"),
        must_call_tools=("appointment_query",),
        risk_notes=("没有真实可约时间时不能说可约或预约成功。",),
    ),
    SkillDefinition(
        name="trust_build",
        description="客户担心正规性、资质、真假、乱收费、隐形消费、安全或服务保障时使用。",
        positive_examples=(
            "你们正规吗",
            "会不会乱收费",
            "产品是真的吗",
            "靠谱吗",
        ),
        negative_examples=(
            "地址发我",
            "这个项目多少钱",
            "下午能约吗",
        ),
        optional_slots=("concern", "asset_type"),
        must_call_tools=("sales_talk_qa", "trust_assets"),
        forbidden_tools=("available_time_query",),
    ),
    SkillDefinition(
        name="case_reference",
        description="客户要求查看案例、做完效果、前后对比或同类改善参考时使用。",
        positive_examples=(
            "客户做完之后的效果我想看一下",
            "有没有案例",
            "发我看看前后对比",
        ),
        negative_examples=(
            "这个项目多少钱",
            "怎么操作多久",
            "你们门店在哪里",
        ),
        optional_slots=("concern", "project_name", "image_assets"),
        risk_notes=("没有真实图片资料时不能编造案例图片。",),
    ),
    SkillDefinition(
        name="competitor_response",
        description="客户提到别家、竞品报价、竞品方案、竞品承诺或要求同价时使用。",
        positive_examples=(
            "别家才199",
            "他们说一次就能看到明显变化",
            "这个报价靠谱吗",
        ),
        negative_examples=("普通问价", "普通问门店", "术后不适"),
        optional_slots=("competitor", "project_name", "price"),
        must_call_tools=("sales_talk_qa", "competitor_qa"),
    ),
    SkillDefinition(
        name="after_sales",
        description="客户已做项目后咨询护理、恢复、效果反馈或不适时使用。",
        positive_examples=(
            "做完后有点红",
            "现在结痂了怎么办",
            "做完没效果",
        ),
        negative_examples=("还没做想了解项目", "普通问价格", "普通问地址"),
        optional_slots=("project_name", "days_after", "symptom", "image_info"),
        must_call_tools=("sales_talk_qa", "after_sales_qa"),
        risk_notes=("严重不适、投诉、退款或效果纠纷应进入专业协助。",),
    ),
    SkillDefinition(
        name="human_assist",
        description="投诉、退款、维权、高风险医疗、真实订单付款争议、系统无法获取但必须真实核验的问题。",
        positive_examples=(
            "我要投诉",
            "把10元退给我",
            "我怀孕了能做吗",
            "我付款到账了吗",
        ),
        negative_examples=("普通担心正规", "普通价格咨询", "普通项目咨询"),
        risk_notes=("客户可见表达应说专业同事协助，不直接说系统转人工。",),
    ),
)


SKILL_BY_NAME = {skill.name: skill for skill in SKILL_CATALOG}
