from __future__ import annotations

import re
from typing import Any

from app.graph.reply_internal_sanitizer import dedupe_repeated_phrase_noise
from app.policies.compliance_terms import replace_sensitive_terms, sensitive_reply_terms


def is_license_doc_request(content: str) -> bool:
    return any(term in content for term in ["营业执照", "执照", "证照", "许可证", "资质"]) and any(
        term in content for term in ["发", "给我看", "看看", "看一下", "直接"]
    )


def sanitize_license_promise(text: str, *, strict: bool = False) -> str:
    replacements = {
        "我把营业执照发你": "资质类材料我可以帮你按正规性维度说明，具体证照以门店/官方渠道核验为准",
        "把营业执照发你": "帮你说明资质核验方式",
        "发送营业执照": "说明资质核验方式",
        "发营业执照": "说明资质核验方式",
        "营业执照发你": "资质信息按官方渠道核验",
        "直接发执照": "按官方渠道核验证照",
        "资质材料发你": "资质材料以门店/官方渠道核验为准",
        "发你核对": "通过门店/官方渠道核验",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if strict:
        text = re.sub(r"https?://\S+", "", text).strip()
        text = text.replace("（📎附图）", "").replace("📎附图", "")
        text = text.replace("已上传", "可通过门店/官方渠道核验")
        text = text.replace("稍后发你样本图", "具体以门店/官方渠道核验为准")
        text = text.replace("马上帮你联系就近门店", "建议按门店/官方渠道")
        text = text.replace("让店长直接", "")
        text = text.replace("所有门店都是持证合规经营的", "门店资质建议以现场或官方渠道核验")
        text = text.replace("正规注册的医美机构", "资质建议以现场或官方渠道核验")
        text = text.replace("产品授权书", "产品来源信息")
        text = text.replace("器械备案信息", "器械备案信息可通过官方渠道核验")
        if any(term in text for term in ["医疗机构执业许可证", "营业执照", "执业许可证"]):
            return "你要核验证照这个诉求我理解，但这类材料我这边不直接发图片或截图。可以先帮你按资质核验、产品来源和服务保障这几块说明；具体证照建议以门店现场或官方渠道核验为准。"
    return text


def allows_specific_project_names(
    normalized_content: str,
    conversation_history: list[Any],
    *,
    intents: set[str],
    contextual_price_project: str,
) -> bool:
    history = " ".join(str(item) for item in conversation_history[-6:])
    text = f"{normalized_content} {history}"
    specific = ["光子嫩肤", "光子", "皮秒", "水光", "热玛吉", "超声炮", "水杨酸"]
    if any(name in text for name in specific):
        return True
    if intents & {"price_inquiry", "campaign_inquiry"} and contextual_price_project:
        return True
    return False


def sanitize_unasked_project_names(text: str) -> str:
    text = text.replace("皮秒/祛斑类", "淡斑改善类")
    text = text.replace("皮秒或祛斑类", "淡斑改善类")
    text = text.replace("大多数顾客反馈是有改善的", "这类一般都可以先看同类改善参考")
    text = text.replace("大多数顾客反馈是有基础改善的", "这类一般都可以先看同类改善参考")
    text = text.replace("顾客反馈是有改善的", "一般都可以先看同类改善参考")
    text = replace_sensitive_terms(text)
    text = re.sub(r"比如\s*(淡斑改善方向|肤色改善类光电方向|针对性色素淡化方向)这类", "比如更偏淡斑的方向", text)
    return dedupe_repeated_phrase_noise(text)


def has_sensitive_external_terms(text: str) -> bool:
    return any(term in text for term in sensitive_reply_terms())
