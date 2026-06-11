from __future__ import annotations

import re

from app.graph.reply_internal_sanitizer import dedupe_repeated_phrase_noise
from app.policies.compliance_terms import replace_sensitive_terms, sensitive_reply_terms


def is_license_doc_request(content: str) -> bool:
    asks_doc = any(term in content for term in ["营业执照", "执照", "证照", "许可证", "资质"])
    asks_send = any(term in content for term in ["发", "给我看", "看看", "看一下", "直接"])
    return asks_doc and asks_send


def sanitize_license_promise(text: str, *, strict: bool = False) -> str:
    replacements = {
        "我把营业执照发你": "资质类材料我可以先按合规性维度帮你说明，具体证照以门店或官方渠道核验为准",
        "把营业执照发你": "帮你说明资质核验方式",
        "发送营业执照": "说明资质核验方式",
        "发营业执照": "说明资质核验方式",
        "营业执照发你": "资质信息按门店或官方渠道核验为准",
        "直接发执照": "按官方渠道核验证照",
        "资质材料发你": "资质材料以门店或官方渠道核验为准",
        "发你核对": "通过门店或官方渠道核验",
    }
    cleaned = str(text or "")
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)

    unsupported_fact_replacements = {
        "所有门店都持有《皮肤管理机构执业许可证》": "门店资质可到店或通过官方渠道核验",
        "所有门店都持有《医疗机构执业许可证》": "门店资质可到店或通过官方渠道核验",
        "所有门店均具备医疗美容资质": "资质可到店或通过官方渠道核验",
        "所有门店均具备资质": "资质可到店或通过官方渠道核验",
        "所有门店均资质可核验运营": "资质可到店或通过官方渠道核验",
        "所有门店均资质可核验": "资质可到店或通过官方渠道核验",
        "我们所有门店资质合规、可查可验": "资质可到店或通过官方渠道核验",
        "所有门店资质合规、可查可验": "资质可到店或通过官方渠道核验",
        "所有门店资质合规": "资质可到店或通过官方渠道核验",
        "正规皮肤管理机构": "资质可核验的皮肤管理机构",
        "正规备案的": "资质可核验的",
        "正规备案": "资质可核验",
        "所有资质": "资质信息",
        "持有《皮肤管理机构执业许可证》": "资质可到店或通过官方渠道核验",
        "持有《医疗机构执业许可证》": "资质可到店或通过官方渠道核验",
        "医疗美容资质": "资质",
        "医疗美容": "皮肤管理",
        "皮肤管理机构执业许可证": "资质",
        "医疗机构执业许可证": "资质",
        "执业许可证": "资质",
        "操作由持证技师执行": "到店会由门店老师按规范安排",
        "操作老师持证上岗": "到店会由门店老师按规范安排",
        "老师持证上岗": "老师会按规范安排",
        "持证技师": "门店老师",
        "持证老师": "门店老师",
        "持证合规的": "资质可核验的",
        "持证合规": "资质可核验",
        "官网可验": "官方渠道可核验",
        "持证上岗": "按规范安排",
        "设备是CFDA认证的进口仪器": "设备和方案到店会提前说明",
        "CFDA认证的进口仪器": "设备信息以门店说明为准",
        "CFDA认证": "设备信息以门店说明为准",
        "进口仪器": "设备信息以门店说明为准",
        "临床上": "一般",
        "更安全": "更稳妥",
        "有接送服务": "可以帮您看路线",
        "提供接送服务": "可以帮您看路线",
        "可以接送服务": "可以帮您看路线",
        "有交通费用支持": "交通费用需自理",
        "提供交通费用支持": "交通费用需自理",
    }
    for old, new in unsupported_fact_replacements.items():
        cleaned = cleaned.replace(old, new)

    if strict:
        cleaned = re.sub(r"https?://\S+", "", cleaned).strip()
        cleaned = cleaned.replace("（附图）", "").replace("附图", "")
        cleaned = cleaned.replace("已上传", "可通过门店或官方渠道核验")
        cleaned = cleaned.replace("稍后发你样本图", "具体以门店或官方渠道核验为准")
        cleaned = cleaned.replace("马上帮你联系就近门店", "建议按门店或官方渠道核验")
        cleaned = cleaned.replace("让店长直接", "")
        cleaned = cleaned.replace("所有门店都是持证合规经营的", "门店资质建议以现场或官方渠道核验")
        cleaned = cleaned.replace("正规注册的机构", "资质建议以现场或官方渠道核验")
        cleaned = cleaned.replace("产品授权书", "产品来源信息")
        cleaned = cleaned.replace("器械备案信息", "器械备案信息可通过官方渠道核验")
        if any(term in cleaned for term in ["医疗机构执业许可证", "营业执照", "执业许可证"]):
            return (
                "你要核验证照这个诉求我理解，但这类材料我这边不直接发图片或截图。"
                "可以先帮你按资质核验、产品来源和服务保障这几块说明；具体证照建议以门店现场或官方渠道核验为准。"
            )
    return cleaned


def allows_specific_project_names(
    normalized_content: str,
    conversation_history: list[object],
    *,
    task_types: set[str],
    contextual_price_project: str,
) -> bool:
    history = " ".join(str(item) for item in conversation_history[-6:])
    text = f"{normalized_content} {history}"
    specific = ["光子嫩肤", "光子", "皮秒", "水光", "热玛吉", "超声炮", "水杨酸"]
    if any(name in text for name in specific):
        return True
    return bool(task_types & {"price_inquiry", "campaign_inquiry"} and contextual_price_project)


def sanitize_unasked_project_names(text: str) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.replace("皮秒/祛斑类", "针对性色素淡化类")
    cleaned = cleaned.replace("皮秒或者祛斑类", "针对性色素淡化类")
    cleaned = replace_sensitive_terms(cleaned)
    cleaned = re.sub(
        r"针对性色素淡化类项目[（(]\s*比如针对性色素淡化类项目\s*[）)]",
        "针对性色素淡化方向",
        cleaned,
    )
    cleaned = cleaned.replace("针对性色素淡化类项目，比如针对性色素淡化类项目", "针对性色素淡化方向")
    cleaned = cleaned.replace(
        "针对性色素淡化类项目、肤色改善类项目这类针对性色素淡化项目",
        "针对性色素淡化和肤色改善方向",
    )
    cleaned = cleaned.replace(
        "针对性色素淡化类项目、肤色改善类项目",
        "针对性色素淡化和肤色改善方向",
    )
    cleaned = cleaned.replace("针对性色素淡化类项目这类针对性色素淡化项目", "针对性色素淡化方向")
    cleaned = re.sub(r"比如\s*(针对性色素淡化方向和肤色改善类光电方向)\s*这类", "比如更偏淡斑的方向", cleaned)
    return dedupe_repeated_phrase_noise(cleaned)


def has_sensitive_external_terms(text: str) -> bool:
    return any(term in text for term in sensitive_reply_terms())
