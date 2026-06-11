from __future__ import annotations


PROJECT_TERM_REPLACEMENTS = {
    "祛斑": "淡斑",
    "净斑": "淡斑",
    "光子嫩肤": "肤色改善类项目",
    "光子": "肤色改善类项目",
    "皮秒": "针对性色素淡化类项目",
    "超皮秒": "针对性色素淡化类项目",
    "注射水光针": "深层补水管理",
    "热玛吉": "紧致项目",
    "超声炮": "面部提升项目",
    "线雕": "提升项目",
    "肉毒素": "除皱项目",
}

MEDICAL_TERM_REPLACEMENTS = {
    "医美机构": "皮肤管理机构",
    "医美": "皮肤管理",
    "医疗美容资质": "资质",
    "医疗美容": "皮肤管理",
    "医疗机构": "皮肤管理机构",
    "医疗资质": "资质",
    "医师": "老师",
    "持证技师": "门店老师",
    "持证老师": "门店老师",
    "持证合规": "资质可核验",
    "临床": "一般",
    "更安全": "更稳妥",
    "CFDA认证": "设备信息以门店说明为准",
    "进口仪器": "设备信息以门店说明为准",
    "执业许可证": "资质",
    "持证上岗": "按规范安排",
    "黄褐斑": "面部色斑",
    "敏感肌": "敏感肌肤",
    "过敏": "肌肤不适",
    "纤维重组": "肌肤修护",
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
    "可以包接送": "可以帮您看路线",
    "能包接送": "可以帮您看路线",
    "不包接送": "没有接送服务",
    "不能包接送": "没有接送服务",
    "没有包接送": "没有接送服务",
    "包接送": "接送服务",
    "车费报销": "交通费用支持",
    "根治": "从根本上改善",
    "当场看到效果": "做完就能看到明显变化",
    "立竿见影": "做完就能看到明显变化",
    "100%见效": "效果反馈较好",
    "一次见效": "做完就能看到明显变化",
    "一次祛干净": "大部分顾客一次效果就很理想",
    "不反弹": "做好护理可以维持较好的改善状态",
    "不满意退款": "不满意不做定金可退",
    "无效退款": "不满意不做定金可退",
    "一口价": "活动价",
}


def compliance_prompt_section() -> str:
    return (
        "合规表达规则：客户可能会主动提到敏感项目词、医疗诊断词或效果承诺词，你要理解含义，"
        "但客户可见回复尽量使用更克制、更合规的替代表达。"
        "不要主动制造医疗诊断，不要主动使用绝对效果承诺。"
        "项目替换参考："
        f"{_format_pairs(PROJECT_TERM_REPLACEMENTS)}。"
        "医疗词替换参考："
        f"{_format_pairs(MEDICAL_TERM_REPLACEMENTS)}。"
        "效果承诺替换参考："
        f"{_format_pairs(PROMISE_TERM_REPLACEMENTS)}。"
        "能用更中性的说法时，优先用“有改善空间”“方向上更匹配”“可以看同类改善参考”“到店检测后再细化”这类表达。"
    )


def replace_sensitive_terms(text: str) -> str:
    value = str(text or "")
    for mapping in (PROJECT_TERM_REPLACEMENTS, MEDICAL_TERM_REPLACEMENTS, PROMISE_TERM_REPLACEMENTS):
        for old, new in mapping.items():
            value = value.replace(old, new)
    return value


def sensitive_reply_terms() -> set[str]:
    return set(PROJECT_TERM_REPLACEMENTS) | set(PROMISE_TERM_REPLACEMENTS) | set(MEDICAL_TERM_REPLACEMENTS)


def _format_pairs(values: dict[str, str]) -> str:
    return "；".join(f"{key}->{value}" for key, value in values.items())
