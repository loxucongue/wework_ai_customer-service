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
        "有 canonical_sales_reply 时优先保持销冠话术骨架和节奏",
    ],
    "avoid_style": [
        "不要长篇科普",
        "不要自我介绍",
        "不要使用小贝、AI、智能客服、机器人等身份表达",
        "不要复制绝对化、贬低竞品、无事实报价等高风险销售话术",
    ],
}

SALES_RISK_REWRITES: dict[str, str] = {
    "目前国内最先进的肌源调肤": "目前做的是肌源调肤",
    "国内最先进的肌源调肤": "目前做的是肌源调肤",
    "国内最先进": "目前做的是",
    "最先进": "目前做的是",
    "最好": "比较适合",
    "最划算": "性价比比较合适",
    "保证效果": "到店先看同类改善参考",
    "效果有保障": "会按皮肤情况评估更稳妥",
    "一次一定好": "大部分客户做一次会有明显变化",
    "一次祛干净": "做完能看到明显变化",
    "不伤肤": "先检测评估，按皮肤状态操作更稳妥",
    "不会伤害皮肤": "先检测评估，按皮肤状态操作更稳妥",
    "不会伤皮肤": "先检测评估，按皮肤状态操作更稳妥",
    "包接送": "我可以帮您查近一点的门店和路线",
    "免费接送": "我可以帮您查近一点的门店和路线",
    "车费报销": "交通费用需要自理",
    "报销车费": "交通费用需要自理",
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
            "58元",
            "广告58",
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
            "孙子",
            "儿子",
            "女儿",
            "家里人",
            "别乱花钱",
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
    "SF3_PROJECT_CONSULT": ["kb_search:sales_talk_qa"],
    "CASE_EFFECT_REFERENCE": ["kb_search:case_studies"],
    "SF5_COMPETITOR_COMPARE": ["kb_search:sales_talk_qa"],
    "SF6_STORE_INQUIRY": ["store_lookup"],
    "SF7_PRICE_ACTIVITY": ["kb_search:sales_talk_qa"],
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
    stage_text = _normalize_for_match(stage)
    scene_text = _normalize_for_match(scene_type)
    question_text = _normalize_for_match(question)
    logic_text = _normalize_for_match(business_logic)
    text = " ".join([stage_text, scene_text, question_text])

    if _needs_handoff(question_text, logic_text):
        return "HUMAN_HANDOFF"
    if (
        _scene_contains(scene_text, ("人工接管", "静默"))
        or _scene_contains(logic_text, ("人工接管", "静默", "不回复"))
    ) and not _is_soft_price_handoff_scene(question_text, scene_text, logic_text):
        return "HUMAN_HANDOFF"
    if _scene_contains(scene_text, ("发图", "面诊")) or question_text in {"[图片]", "（发送斑点照片）你看看我这种能做吗"}:
        return "SF4_IMAGE_CONSULT"
    if _is_explicit_image_consult_question(question_text, scene_text):
        return "SF4_IMAGE_CONSULT"
    if _is_opening_scene(stage_text, scene_text, question_text):
        return "S1_OPENING_GENERAL"
    if _is_identity_or_trust_question(question_text, scene_text):
        return "SF10_TRUST_BUILD"
    if _is_explicit_competitor_question(question_text):
        return "SF5_COMPETITOR_COMPARE"
    if _is_explicit_case_effect_question(question_text):
        return "CASE_EFFECT_REFERENCE"
    if _is_explicit_price_question(question_text):
        return "SF7_PRICE_ACTIVITY"
    if _is_explicit_after_sales_question(question_text):
        return "SF12_AFTER_SALES"
    if _is_explicit_store_or_visit_info_question(question_text):
        return "SF6_STORE_INQUIRY"
    if _is_explicit_project_question(question_text):
        return "SF3_PROJECT_CONSULT"
    if _is_appointment_negotiation_scene(stage_text, scene_text, question_text, logic_text):
        return "SF9_APPOINTMENT"
    if _scene_contains(logic_text, ("进入sf10", "信任建立", "靠谱性顾虑")):
        return "SF10_TRUST_BUILD"
    if _scene_contains(scene_text, ("质疑负向情绪", "担心是骗子", "质疑反悔", "家人反对", "安全性", "资质", "信任")):
        return "SF10_TRUST_BUILD"
    if _scene_contains(scene_text, ("效果不佳", "质疑担心", "到店不满", "售后", "恢复期", "术后", "护理")):
        return "SF12_AFTER_SALES"
    if _scene_contains(scene_text, ("竞品", "价格对比", "发竞品", "竞品截图")):
        return "SF5_COMPETITOR_COMPARE"
    if _scene_contains(scene_text, ("直接问门店", "问门店", "门店", "地址", "停车", "营业")):
        return "SF6_STORE_INQUIRY"
    if _scene_contains(scene_text, ("直接问价格", "问价格", "报价", "价格", "费用", "活动", "定金", "尾款")):
        return "SF7_PRICE_ACTIVITY"
    if _scene_contains(scene_text, ("效果咨询", "案例", "效果图")):
        if _scene_contains(question_text, ("不见效果", "没效果", "没有效果", "做了", "已做")):
            return "SF12_AFTER_SALES"
        return "CASE_EFFECT_REFERENCE"
    if _scene_contains(scene_text, ("恢复期", "术后", "护理", "售后")):
        return "SF12_AFTER_SALES"
    if _scene_contains(scene_text, ("安全性", "资质", "信任")):
        return "SF10_TRUST_BUILD"
    if _scene_contains(scene_text, ("直接问项目", "项目细节", "问项目", "发图要面诊", "主动暴露画像-困扰", "次数咨询")):
        return "SF3_PROJECT_CONSULT"
    if _scene_contains(scene_text, ("预约", "到店", "档期", "改约", "取消", "收款", "通单")):
        return "SF9_APPOINTMENT"
    for family, keywords in FAMILY_KEYWORDS:
        if any(_normalize_for_match(keyword) in text for keyword in keywords):
            return family
    if "破冰" in stage_text or "打招呼" in scene_text or "闲聊" in scene_text or "来源识别" in scene_text:
        return "S1_OPENING_GENERAL"
    return "GENERAL_DIRECT_REPLY"


def _scene_contains(text: str, needles: tuple[str, ...]) -> bool:
    return any(_normalize_for_match(needle) in text for needle in needles)


def _needs_handoff(question_text: str, logic_text: str) -> bool:
    if _scene_contains(logic_text, ("强制转人工", "立即转人工")):
        return True
    if _scene_contains(question_text, ("体检报告", "病历", "降压药", "降血糖", "高血压", "糖尿病", "怀孕", "孕妇", "哺乳", "未成年")):
        return True
    if _scene_contains(question_text, ("退给我", "没有抵扣", "没抵扣")) and _scene_contains(
        question_text, ("定金", "订金", "预约金", "10元", "十元", "10块", "十块")
    ):
        return True
    if _scene_contains(question_text, ("投诉", "退款", "退钱", "退定金", "骗钱", "多收")):
        return True
    if re.search(r"(要|想|需要|找|换|转).{0,4}(真人|人工|人)", question_text):
        return True
    return False


def _is_soft_price_handoff_scene(question_text: str, scene_text: str, logic_text: str) -> bool:
    text = question_text + scene_text + logic_text
    if _scene_contains(text, ("投诉", "退款", "退钱", "骗钱", "多收", "不然")):
        return False
    return _scene_contains(
        text,
        (
            "底价",
            "最低价",
            "最低多少钱",
            "再便宜",
            "太贵",
            "预算不多",
            "退休金不多",
            "顾问报",
            "套餐没想好",
        ),
    )


def _is_opening_scene(stage_text: str, scene_text: str, question_text: str) -> bool:
    if _scene_contains(scene_text, ("打招呼", "闲聊", "来源识别")):
        return True
    return _scene_contains(question_text, ("介绍过来", "朋友介绍", "天气真好", "随便看看"))


def _is_appointment_negotiation_scene(stage_text: str, scene_text: str, question_text: str, logic_text: str) -> bool:
    appointment_stage = _scene_contains(stage_text, ("邀约协商", "已邀约待到店"))
    appointment_scene = _scene_contains(
        scene_text,
        ("确认时间门店", "犹豫", "再考虑", "逼单", "挽留", "反悔", "取消", "改约", "待到店", "收款", "通单"),
    )
    appointment_logic = _scene_contains(logic_text, ("进入SF9", "邀约确认", "确认门店时间", "给具体时间"))
    if not (appointment_stage or appointment_scene or appointment_logic):
        return False
    if _scene_contains(question_text + scene_text + logic_text, ("不靠谱", "风险", "担心", "安全", "资质", "家人反对")):
        return False
    return True


def _is_explicit_price_question(question_text: str) -> bool:
    if re.search(r"\d+\s*岁", question_text):
        return False
    return _scene_contains(
        question_text,
        (
            "多少钱",
            "价格",
            "费用",
            "收费",
            "尾款",
            "定金",
            "订金",
            "预约金",
            "活动",
            "优惠",
            "加钱",
            "乱收费",
            "隐形消费",
            "最低",
            "底价",
            "便宜",
            "太贵",
            "预算不多",
            "退休金不多",
            "顾问报",
            "套餐",
            "做完付款",
            "到店再付",
            "一只手",
            "两只手",
            "一只还是",
            "单只",
            "双手",
            "一双",
            "199",
            "268",
            "308",
            "380",
            "58元",
        ),
    )


def _is_explicit_competitor_question(question_text: str) -> bool:
    return _scene_contains(
        question_text,
        (
            "别家",
            "其他家",
            "竞品",
            "同行",
            "机构报价",
            "报价截图",
            "对比",
            "素颜家",
            "邻居说",
        ),
    )


def _is_explicit_after_sales_question(question_text: str) -> bool:
    if _scene_contains(question_text, ("做完付款", "到店再付", "尾款", "先付", "付款")):
        return False
    return _scene_contains(
        question_text,
        (
            "做完",
            "上次做",
            "已经做",
            "做了",
            "没做好",
            "不见效果",
            "没效果",
            "没有效果",
            "又深",
            "效果不好",
            "服务不好",
            "体验太差",
            "结痂",
            "红肿",
            "脸疼",
        ),
    )


def _is_explicit_case_effect_question(question_text: str) -> bool:
    if not _scene_contains(question_text, ("案例", "效果图", "对比图", "做完效果", "图片上的客户", "客户做了多少次", "几次的效果")):
        return False
    if _scene_contains(question_text, ("不见效果", "没效果", "没有效果", "效果不好", "上次做", "已经做")):
        return False
    return True


def _is_explicit_image_consult_question(question_text: str, scene_text: str) -> bool:
    text = question_text + scene_text
    if _scene_contains(text, ("报价截图", "活动截图", "竞品截图", "别家", "其他家", "效果图", "案例", "前后对比")):
        return False
    has_image_word = _scene_contains(text, ("照片", "图片", "发图", "拍的", "刚拍", "看图"))
    has_face_or_need = _scene_contains(text, ("脸", "脸颊", "斑", "痣", "痦子", "皮肤", "帮我看看", "这种能做", "适合做什么", "照片糊"))
    return has_image_word and has_face_or_need


def _is_explicit_store_or_visit_info_question(question_text: str) -> bool:
    return _scene_contains(
        question_text,
        (
            "门店",
            "地址",
            "导航",
            "停车",
            "地铁",
            "机场",
            "附近",
            "营业时间",
            "几点开",
            "几点关",
            "身份证",
            "带什么",
            "空腹",
            "车费",
            "接送",
            "楼下",
            "怎么走",
            "路线",
        ),
    )


def _is_explicit_project_question(question_text: str) -> bool:
    return _scene_contains(
        question_text,
        (
            "能做吗",
            "可以做吗",
            "做几次",
            "做两次",
            "做第二次",
            "痣",
            "痦子",
            "斑",
            "淡斑",
            "祛斑",
            "项目",
            "方案",
        ),
    )


def _is_identity_or_trust_question(question_text: str, scene_text: str) -> bool:
    if _scene_contains(scene_text, ("质疑真人", "信任", "资质")):
        return True
    return _scene_contains(
        question_text,
        (
            "机器人",
            "你是门店的人",
            "你们有资质",
            "医疗资质",
            "资质",
            "安全吗",
            "伤害皮肤",
            "留疤",
            "做坏了",
            "怕出问题",
            "万一",
            "靠谱吗",
            "正规",
        ),
    )


def required_tools_for_family(family: str) -> list[str]:
    return list(REQUIRED_TOOLS_BY_FAMILY.get(family, []))


def hard_constraints_for_family(family: str) -> list[str]:
    common = [
        "业务逻辑优先于销冠话术风格",
        "有 canonical_sales_reply 时优先高相似参考，只在风险词、事实词和当前上下文上做最小改写",
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


def risk_rewrite_payload(sales_talk: str) -> dict[str, str]:
    text = str(sales_talk or "")
    return {source: target for source, target in SALES_RISK_REWRITES.items() if source in text}


def canonical_sales_reply_payload(sales_talk: str) -> str:
    text = str(sales_talk or "").strip()
    if not text:
        return ""
    for source, target in SALES_RISK_REWRITES.items():
        text = text.replace(source, target)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def copy_strength_for_sales_talk(sales_talk: str) -> str:
    if not str(sales_talk or "").strip():
        return ""
    if risk_rewrite_payload(sales_talk):
        return "medium"
    return "high"


def _extract_compact_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[\s，。！？、,.!?;；:：\"'（）()【】\[\]]+", " ", text).strip()
    tokens = [item for item in cleaned.split(" ") if len(item) >= 3]
    if not tokens and cleaned:
        tokens = [cleaned[:12]]
    return tokens[:4]


def _normalize_for_match(value: str) -> str:
    return "".join(str(value or "").lower().split())
