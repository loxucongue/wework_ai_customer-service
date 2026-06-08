from __future__ import annotations

import re
from typing import Any

from app.graph.reply_assets import attach_asset_images, first_asset_image_url
from app.graph.reply_compliance_filters import (
    allows_specific_project_names,
    has_sensitive_external_terms,
    is_license_doc_request,
    sanitize_license_promise,
    sanitize_unasked_project_names,
)
from app.graph.reply_internal_sanitizer import (
    has_internal_reply_leak as _has_internal_reply_leak,
    sanitize_customer_visible_messages as _sanitize_customer_visible_messages,
)


def sanitize_sensitive_reply_content(
    messages: list[dict[str, Any]],
    *,
    intents: set[str],
    normalized_content: str,
    conversation_history: list[Any],
    contextual_price_project: str,
) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    allow_project_names = allows_specific_project_names(
        normalized_content,
        conversation_history,
        intents=intents,
        contextual_price_project=contextual_price_project,
    )
    license_doc_request = is_license_doc_request(normalized_content)
    for message in messages:
        if (license_doc_request or "trust_issue" in intents) and isinstance(message, dict) and message.get("type") == "image":
            continue
        if not isinstance(message, dict) or message.get("type") != "text":
            sanitized.append(message)
            continue
        content = str(message.get("content") or "")
        content = sanitize_license_promise(content, strict=license_doc_request or "trust_issue" in intents)
        if has_sensitive_external_terms(content):
            content = sanitize_unasked_project_names(content)
        elif not allow_project_names:
            content = sanitize_unasked_project_names(content)
        if content.strip():
            sanitized.append({**message, "content": content})
    return sanitized


def has_internal_reply_leak(text: str) -> bool:
    return _has_internal_reply_leak(text)


def sanitize_customer_visible_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _sanitize_customer_visible_messages(messages)


def asks_for_duplicate_photo(text: str) -> bool:
    terms = [
        "发照片",
        "发张照片",
        "发一张",
        "照片发",
        "发我照片",
        "正脸自然光",
        "自然光下",
        "正脸照片",
        "拍张照片",
        "再发一张",
        "再发张",
        "看看皮肤状态",
        "帮你看看皮肤",
    ]
    return any(term in text for term in terms)


def is_vague_price_deferral(text: str) -> bool:
    no_price_phrases = ["暂时没查到", "暂时没有查到", "暂未查到", "没有查到明确", "没有明确价格", "不能拿别的项目价格代替"]
    if any(phrase in text for phrase in no_price_phrases) or re.search(r"\d+\s*元?", text):
        return False
    return any(term in text for term in ["具体价格要看", "价格要看", "准确价格", "需要看配置", "结合配置"])


def is_project_only_after_price_objection(text: str) -> bool:
    if has_budget_or_price_answer(text):
        return False
    return any(term in text for term in ["斑的深浅", "项目配置", "适合哪个方案", "看斑型", "范围判断", "皮肤耐受"])


def has_budget_or_price_answer(text: str) -> bool:
    return bool(re.search(r"\d+\s*元?", text)) or any(
        term in text for term in ["新客体验价", "活动价", "日常单次", "优惠价", "明确价格", "没查到", "没有查到", "暂未查到", "不能直接改价", "不能直接承诺", "不乱降价", "底价", "预约金", "定金", "订金", "尾款", "总价", "一次费用", "单次", "到店付"]
    )


def has_unsupported_no_price_commitment(text: str) -> bool:
    no_price_phrases = ["没查到", "没有查到", "暂时没查到", "暂时没有查到", "暂未查到", "没有明确价格", "没有查到明确"]
    if not any(phrase in text for phrase in no_price_phrases):
        return False
    unsupported_terms = [
        "体验/活动档位",
        "活动档位",
        "低配高配",
        "高配方案",
        "类似项目的预算范围",
        "类似项目预算范围",
        "预算参考",
        "做预算参考",
        "新客首次体验",
        "专项安排",
        "预算预期",
        "效果节奏",
        "护理强度",
        "配置对应",
        "档位",
        "费用明细",
    ]
    return any(term in text for term in unsupported_terms)


def asks_daily_single_price(content: str) -> bool:
    return any(term in content for term in ["普通一次", "日常单次", "单次多少钱", "一次多少钱", "普通单次"])


def repair_appointment_commitment(text: str) -> str:
    text = text.replace("预约成功", "继续确认")
    text = text.replace("已经预约成功", "继续确认")
    text = text.replace("预约已经帮您登记好了", "预约信息我继续帮您确认")
    text = text.replace("预约已经帮你登记好了", "预约信息我继续帮你确认")
    text = text.replace("已经帮您登记好了", "我继续帮您确认预约信息")
    text = text.replace("已经帮你登记好了", "我继续帮你确认预约信息")
    text = text.replace("帮您登记好了", "继续帮您确认预约信息")
    text = text.replace("帮你登记好了", "继续帮你确认预约信息")
    text = text.replace("登记好了", "继续确认")
    text = text.replace("预约已经确认", "这个时间目前有空位")
    text = text.replace("预约已确认", "这个时间目前有空位")
    text = text.replace("已经为您确认好了", "这个时间目前有空位")
    text = text.replace("已经为你确认好了", "这个时间目前有空位")
    text = text.replace("为您确认好了", "目前有空位")
    text = text.replace("为你确认好了", "目前有空位")
    text = text.replace("确认好了", "继续确认")
    text = text.replace("已经确认", "还在继续确认")
    text = text.replace("已确认", "继续确认")
    text = text.replace("已经约好了", "继续确认")
    text = text.replace("约好了", "继续确认")
    text = text.replace("已经预约了", "继续确认")
    text = text.replace("已预约了", "继续确认")
    text = re.sub(r"(?<!未)预约了([^，。！？!?]*)", r"继续确认\1", text)
    text = text.replace("已为您预留", "先按这个时间继续帮您确认")
    text = text.replace("为您预留", "按这个时间继续帮您确认")
    text = text.replace("为您预约", "按这个时间继续帮您确认")
    text = text.replace("帮您预约", "继续帮您确认")
    text = text.replace("帮你预约", "继续帮你确认")
    text = text.replace("可以预约", "目前有空位")
    text = text.replace("能预约", "目前有空位")
    text = text.replace("小贝马上帮你锁位", "我再继续帮你确认")
    text = text.replace("马上帮你锁位", "再继续帮你确认")
    text = text.replace("小贝马上为你锁定这个时段", "我按这个时段继续帮你确认")
    text = text.replace("马上为你锁定这个时段", "按这个时段继续帮你确认")
    text = text.replace("为你锁定这个时段", "按这个时段继续确认")
    text = text.replace("锁定这个时段", "继续确认这个时段")
    text = text.replace("先留着", "继续确认")
    text = text.replace("留着这个时段", "继续确认这个时段")
    text = text.replace("把这个时段留着", "继续确认这个时段")
    text = text.replace("帮你锁位", "帮你继续确认")
    text = text.replace("帮您锁位", "帮您继续确认")
    text = text.replace("锁位", "确认")
    text = text.replace("可以安排", "目前有可约时间")
    text = text.replace("我马上给您开预约入口和10元预约金小程序", "信息确认没问题后，我再给您开预约入口和10元预约金小程序")
    text = text.replace("我马上给你开预约入口和10元预约金小程序", "信息确认没问题后，我再给你开预约入口和10元预约金小程序")
    text = text.replace("马上给您开预约入口", "信息确认没问题后再给您开预约入口")
    text = text.replace("马上给你开预约入口", "信息确认没问题后再给你开预约入口")
    text = text.replace("我会把预约入口和10元预约金小程序发给您", "信息确认没问题后，我再给您开预约入口和10元预约金小程序")
    text = text.replace("我会把预约入口和10元预约金小程序发给你", "信息确认没问题后，我再给你开预约入口和10元预约金小程序")
    text = text.replace("接下来我会给您发一个预约入口和10元的预约金小程序，请按页面提示确认一下", "您看是否按这个信息开预约入口？10元预约金按页面确认就行")
    text = text.replace("接下来我会给你发一个预约入口和10元的预约金小程序，请按页面提示确认一下", "你看是否按这个信息开预约入口？10元预约金按页面确认就行")
    text = text.replace("接下来我会给您发一个预约入口和10元预约金小程序，请按页面提示确认一下", "您看是否按这个信息开预约入口？10元预约金按页面确认就行")
    text = text.replace("接下来我会给你发一个预约入口和10元预约金小程序，请按页面提示确认一下", "你看是否按这个信息开预约入口？10元预约金按页面确认就行")
    text = re.sub(
        r"(明天(?:上午|下午|晚上)?\d{1,2}:\d{2}在[^，。！？!?]{2,20})按这个时间继续帮[您你]确认[。；;，,]?\s*请提供一下[您你]的电话号码[，,]?\s*我继续帮[您你]确认",
        r"\1这个时间目前有空位。你把电话号码发我，我继续帮你登记",
        text,
    )
    text = text.replace("我继续帮您确认我继续帮您确认", "我继续帮您确认")
    text = text.replace("我继续帮你确认我继续帮你确认", "我继续帮你确认")
    return text
