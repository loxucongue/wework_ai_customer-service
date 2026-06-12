from __future__ import annotations

import hashlib
import re
from typing import Any


DEFAULT_BUSINESS_SCENE_STATUS = "draft"
SHORT_WECHAT_STYLE = {
    "tone": "short_wechat_sales",
    "sentence_style": [
        "像微信短聊，先回答当前问题",
        "默认一条 text，必要时最多两条",
        "只带一个轻推进动作",
        "不机械照抄销冠话术原句",
    ],
    "avoid_style": [
        "不要长篇科普",
        "不要自我介绍",
        "不要使用小贝、AI、智能客服、机器人等身份表达",
        "不要复制最先进、最划算、保证效果、一次一定好等高风险销售话术",
    ],
}


FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "HUMAN_HANDOFF",
        (
            "投诉",
            "退款",
            "退钱",
            "退定金",
            "骗钱",
            "多收",
            "体检报告",
            "病历",
            "降压药",
            "降血糖",
            "高血压",
            "糖尿病",
            "怀孕",
            "孕妇",
            "哺乳",
            "未成年",
            "真人",
            "人工",
        ),
    ),
    (
        "SF5_COMPETITOR_COMPARE",
        (
            "别家",
            "其他家",
            "竞品",
            "同行",
            "截图",
            "对比",
            "同价",
            "比你们",
            "为什么一样",
        ),
    ),
    (
        "SF7_PRICE_ACTIVITY",
        (
            "价格",
            "多少钱",
            "费用",
            "报价",
            "活动价",
            "定金",
            "尾款",
            "付款",
            "付钱",
            "199",
            "268",
            "58",
            "308",
            "380",
            "乱收费",
            "隐形消费",
            "加价",
            "推销",
            "最低价",
        ),
    ),
    (
        "SF6_STORE_INQUIRY",
        (
            "门店",
            "地址",
            "位置",
            "导航",
            "停车",
            "营业",
            "店名",
            "附近",
            "城市",
            "西藏",
            "机场",
        ),
    ),
    (
        "SF9_APPOINTMENT",
        (
            "预约",
            "到店",
            "档期",
            "时间",
            "周六",
            "周日",
            "今天",
            "明天",
            "几点",
            "名额",
            "取消",
            "改约",
            "过来",
        ),
    ),
    (
        "CASE_EFFECT_REFERENCE",
        (
            "案例",
            "效果图",
            "对比图",
            "做完效果",
            "客户做了多少次",
            "几次的效果",
        ),
    ),
    (
        "SF12_AFTER_SALES",
        (
            "做完",
            "术后",
            "护理",
            "恢复",
            "红肿",
            "疼",
            "不见效果",
            "没效果",
            "更深",
            "反黑",
        ),
    ),
    (
        "SF10_TRUST_BUILD",
        (
            "资质",
            "真人",
            "机器人",
            "门店的人",
            "安全吗",
            "安全性",
            "伤害皮肤",
            "家属",
            "靠谱吗",
            "正规",
        ),
    ),
    (
        "SF3_PROJECT_CONSULT",
        (
            "祛斑",
            "淡斑",
            "斑点",
            "皱纹",
            "痘印",
            "毛孔",
            "去痣",
            "痣",
            "项目",
            "抗衰",
        ),
    ),
)

REQUIRED_TOOLS_BY_FAMILY = {
    "SF3_PROJECT_CONSULT": ["kb_search:project_qa"],
    "CASE_EFFECT_REFERENCE": ["kb_search:case_studies"],
    "SF5_COMPETITOR_COMPARE": ["kb_search:sales_talk_qa"],
    "SF6_STORE_INQUIRY": ["store_lookup"],
    "SF7_PRICE_ACTIVITY": ["pricing_rules"],
    "SF9_APPOINTMENT": ["store_lookup", "available_time"],
    "SF10_TRUST_BUILD": ["kb_search:sales_talk_qa"],
    "SF12_AFTER_SALES": ["kb_search:sales_talk_qa"],
    "HUMAN_HANDOFF": ["professional_assist"],
}

HARD_CONSTRAINTS_BY_FAMILY = {
    "SF7_PRICE_ACTIVITY": ["价格数字必须来自真实报价规则", "不得把客户说的价格当事实", "不得承诺最低价"],
    "SF6_STORE_INQUIRY": ["门店地址、营业时间、停车信息必须来自真实门店事实", "不得编造门店事实"],
    "SF9_APPOINTMENT": ["没有真实档期或预约结果，不能说预约成功", "不得承诺包接送或车费报销"],
    "SF5_COMPETITOR_COMPARE": ["不跟价", "不贬低同行", "只围绕配置、部位、次数、服务和费用透明解释"],
    "SF10_TRUST_BUILD": ["不得自称 AI、智能客服、机器人或小贝", "不得夸大资质或编造证照"],
    "SF12_AFTER_SALES": ["不直接判断正常或没事", "严重不适、退款、投诉需要专业同事协助"],
    "HUMAN_HANDOFF": ["先给客户可见说明，再追加 human_handoff", "不说转人工、转接、转人"],
}


def infer_policy_family(*, stage: str = "", scene_type: str = "", question: str = "", business_logic: str = "") -> str:
    text = _normalize_for_match(" ".join([stage, scene_type, question, business_logic]))
    for family, keywords in FAMILY_KEYWORDS:
        if any(_normalize_for_match(keyword) in text for keyword in keywords):
            return family
    if "破冰" in stage or "打招呼" in scene_type:
        return "S1_OPENING_GENERAL"
    return "GENERAL_DIRECT_REPLY"


def required_tools_for_family(family: str) -> list[str]:
    return list(REQUIRED_TOOLS_BY_FAMILY.get(family, []))


def hard_constraints_for_family(family: str) -> list[str]:
    common = [
        "业务逻辑优先于销冠话术风格",
        "销冠话术只能提炼风格，不得照抄原句",
        "不得输出根治、100%见效、绝对安全、保证效果、一次一定好、包接送、车费报销",
    ]
    return common + list(HARD_CONSTRAINTS_BY_FAMILY.get(family, []))


def generated_scene_id(*, row_number: int, stage: str, scene_type: str, question: str, family: str) -> str:
    digest = hashlib.sha1("|".join([stage, scene_type, question, family]).encode("utf-8")).hexdigest()[:8].upper()
    return f"BIZ_{family}_{row_number:03d}_{digest}"


def build_keywords(*, scene_type: str, question: str, business_logic: str, family: str) -> list[str]:
    text = " ".join([scene_type, question, business_logic])
    keywords: list[str] = []
    for candidate_family, candidates in FAMILY_KEYWORDS:
        if candidate_family != family and family != "GENERAL_DIRECT_REPLY":
            continue
        for keyword in candidates:
            if keyword in text and keyword not in keywords:
                keywords.append(keyword)
    for token in _extract_compact_tokens(question):
        if token not in keywords:
            keywords.append(token)
    return keywords[:12] or [question[:16]]


def business_logic_payload(*, family: str, stage: str, scene_type: str, standard: str) -> dict[str, Any]:
    standard = standard.strip()
    return {
        "stage": stage,
        "scene_type": scene_type,
        "standard": standard,
        "must_do": [standard] if standard else ["按该场景业务标准先回答客户当前问题"],
        "must_not_do": hard_constraints_for_family(family),
        "required_tools": required_tools_for_family(family),
        "handoff_required": family == "HUMAN_HANDOFF",
        "silence_required": "静默" in standard or "不回复" in standard,
    }


def style_reference_payload() -> dict[str, Any]:
    return dict(SHORT_WECHAT_STYLE)


def _extract_compact_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[\s，。！？、,.!?;；:：\"'（）()【】\[\]]+", " ", text).strip()
    tokens = [item for item in cleaned.split(" ") if len(item) >= 3]
    if not tokens and cleaned:
        tokens = [cleaned[:12]]
    return tokens[:4]


def _normalize_for_match(value: str) -> str:
    return "".join(str(value or "").lower().split())
