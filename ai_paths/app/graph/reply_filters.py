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
        term in text for term in ["新客体验价", "活动价", "日常单次", "优惠价", "明确价格", "没查到", "没有查到", "暂未查到", "不能直接改价", "不能直接承诺", "不乱降价", "底价"]
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
    ]
    return any(term in text for term in unsupported_terms)


def asks_daily_single_price(content: str) -> bool:
    return any(term in content for term in ["普通一次", "日常单次", "单次多少钱", "一次多少钱", "普通单次"])


def repair_appointment_commitment(text: str) -> str:
    text = text.replace("小贝马上帮你锁位", "小贝再继续帮你确认")
    text = text.replace("马上帮你锁位", "再继续帮你确认")
    text = text.replace("小贝马上为你锁定这个时段", "小贝按这个时段继续帮你确认")
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
    return text
