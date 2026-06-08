from __future__ import annotations


PROJECT_TERM_REPLACEMENTS = {
    "祛斑": "淡斑",
    "净斑": "淡斑",
    "光子嫩肤": "肤色改善类项目",
    "光子": "肤色改善类项目",
    "皮秒": "淡斑改善类项目",
    "超皮秒": "淡斑改善类项目",
    "调Q激光": "淡斑改善类项目",
    "调Q": "淡斑改善类项目",
    "水光针": "深层补水管理",
    "热玛吉": "紧致项目",
    "超声刀": "面部提升项目",
    "超声炮": "面部提升项目",
    "线雕": "提升项目",
    "肉毒素": "除皱项目",
}

MEDICAL_TERM_REPLACEMENTS = {
    "黄褐斑": "面部色斑",
    "敏感肌": "敏感肌肤",
    "过敏": "肌肤不适",
    "注射水光针": "深层补水管理",
    "靶向": "精准",
    "纤维重组": "肌肤修复",
    "无创": "温和",
    "消炎": "舒缓",
    "抑菌": "护理",
    "杀菌": "清洁",
    "皮肤科": "皮肤管理中心",
    "患者": "顾客",
    "疗程": "方案",
    "诊断": "检测",
    "医生": "老师",
    "治疗": "护理",
}

PROMISE_TERM_REPLACEMENTS = {
    "根治": "从根本上改善",
    "当场看到效果": "可参考改善变化",
    "立竿见影": "可参考改善变化",
    "100%见效": "改善反馈较好",
    "一次见效": "可参考改善变化",
    "一次祛干净": "多数顾客反馈有改善",
    "做掉": "淡化",
    "不反弹": "做好护理可以维持很好的效果",
    "到店不满意退尾款": "到店满意再做，不满意不做定金可退",
    "不满意退款": "不满意不做定金可退",
    "无效退款": "不满意不做定金可退",
    "一口价": "活动价",
}


def compliance_prompt_section() -> str:
    return (
        "合规表达规则：客户可能会使用敏感词或项目俗称，你要理解含义，但客服回复尽量使用合规替代表达，"
        "不要主动直接说敏感项目名、医疗诊断词或绝对效果承诺。"
        "项目表达替换参考："
        f"{_format_pairs(PROJECT_TERM_REPLACEMENTS)}。"
        "医疗/诊断表达替换参考："
        f"{_format_pairs(MEDICAL_TERM_REPLACEMENTS)}。"
        "效果承诺表达替换参考："
        f"{_format_pairs(PROMISE_TERM_REPLACEMENTS)}。"
        "注意：这些替代表达只用于被动规避敏感词，不代表你应该主动制造效果承诺。"
        "能用更中性的说法时，优先用“有改善空间”“方向上更匹配”“可参考同类改善变化”这类表达，"
        "不要主动说“做完后会有明显变化”“效果反馈较好”“多数顾客反馈变化比较明显”。"
        "如果客户问到原词，可以先理解为对应替代表达，再用替代表达回答；"
        "例如客户问具体项目名时，可说“你说的这个方向/这类项目”，不要反复复述敏感词。"
    )


def sensitive_reply_terms() -> set[str]:
    return set(PROJECT_TERM_REPLACEMENTS) | set(PROMISE_TERM_REPLACEMENTS)


def replace_sensitive_terms(text: str) -> str:
    for mapping in (PROJECT_TERM_REPLACEMENTS, MEDICAL_TERM_REPLACEMENTS, PROMISE_TERM_REPLACEMENTS):
        for old, new in mapping.items():
            text = text.replace(old, new)
    return text


def _format_pairs(values: dict[str, str]) -> str:
    return "；".join(f"{key}->{value}" for key, value in values.items())
