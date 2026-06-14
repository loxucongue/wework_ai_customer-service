from __future__ import annotations

import re
from typing import Any

_INTERNAL_REPLY_PREFIXES = (
    "判断依据",
    "内部分析",
    "系统分析",
    "工具结果",
    "工具返回",
    "知识库显示",
    "检索结果",
    "流程判断",
    "路由结果",
    "AI回复",
    "调试信息",
)

_INTERNAL_REPLY_MARKERS = (
    "判断依据：",
    "判断依据:",
    "内部分析：",
    "内部分析:",
    "系统查到",
    "工具返回",
    "知识库显示",
    "检索结果显示",
    "根据资料库",
    "根据知识库",
    "reply_brief",
    "module_outputs",
    "route_result",
    "subflow",
    "intent",
    "debug",
    "调试信息",
)


def sanitize_customer_visible_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = message.get("content")
        content_text = _message_text(content)
        if not content_text:
            continue
        if msg_type == "text":
            if is_internal_reply_message(content_text):
                continue
            content_text = strip_internal_reply_terms(content_text)
            content_text = sanitize_handoff_visible_phrasing(content_text)
            content_text = sanitize_health_assist_phrasing(content_text)
            content_text = sanitize_overpromising_phrasing(content_text)
            content_text = sanitize_activity_name_phrasing(content_text)
            content_text = dedupe_repeated_phrase_noise(content_text)
            if not content_text:
                continue
            visible.append({"type": "text", "order": len(visible) + 1, "content": {"text": content_text}})
            continue
        visible.append({"type": msg_type, "order": len(visible) + 1, "content": content})
    return renumber(visible)


def has_internal_reply_leak(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return bool(compact) and any(marker in compact for marker in _INTERNAL_REPLY_MARKERS)


def is_internal_reply_message(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True
    if any(compact.startswith(prefix) for prefix in _INTERNAL_REPLY_PREFIXES):
        return True
    return any(marker in compact for marker in _INTERNAL_REPLY_MARKERS)


def strip_internal_reply_terms(text: str) -> str:
    cleaned = str(text or "").strip()
    replacements = {
        "根据系统查到的：": "",
        "系统查到": "",
        "根据工具返回": "",
        "工具返回": "",
        "知识库显示：": "",
        "根据知识库：": "",
        "根据资料库：": "",
        "检索结果显示：": "",
        "AI回复": "",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(
        r"^\s*(判断依据|内部分析|系统分析|工具结果|流程判断|路由结果)\s*[:：]\s*",
        "",
        cleaned,
    )
    return cleaned.strip()


def sanitize_handoff_visible_phrasing(text: str) -> str:
    cleaned = str(text or "").strip()
    replacements = (
        ("转给专业同事核实处理", "让专业同事核实处理"),
        ("转给专业同事继续核对", "让专业同事继续核对"),
        ("转给专业同事协助处理", "让专业同事协助处理"),
        ("转接专业同事核实处理", "让专业同事核实处理"),
        ("转接专业同事继续核对", "让专业同事继续核对"),
        ("转接专业同事协助处理", "让专业同事协助处理"),
        ("转交专业同事核实处理", "让专业同事核实处理"),
        ("转交专业同事继续核对", "让专业同事继续核对"),
        ("转交专业同事协助处理", "让专业同事协助处理"),
        ("转给专业同事", "让专业同事"),
        ("转接专业同事", "让专业同事"),
        ("转交专业同事", "让专业同事"),
        ("转人工处理", "让专业同事协助处理"),
        ("转人工核实", "让专业同事核实"),
        ("转人工", "让专业同事继续核对"),
    )
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)
    cleaned = cleaned.replace("帮您登记并让专业同事帮您", "帮您登记，并让专业同事")
    cleaned = cleaned.replace("帮您记录并让专业同事帮您", "帮您记录，并让专业同事")
    cleaned = cleaned.replace("帮您登记并让专业同事", "帮您登记，并让专业同事")
    cleaned = cleaned.replace("帮您记录并让专业同事", "帮您记录，并让专业同事")
    cleaned = cleaned.replace("帮您让专业同事", "让专业同事")
    cleaned = cleaned.replace("为您让专业同事", "让专业同事")
    cleaned = cleaned.replace("为您让", "让")
    return cleaned.strip()


def sanitize_health_assist_phrasing(text: str) -> str:
    cleaned = str(text or "").strip()
    health_markers = ("降压药", "降糖", "高血压", "糖尿病", "血压", "血糖", "心脏病", "慢病", "用药", "体检报告", "病历")
    assist_markers = ("专业同事", "专业老师", "顾问", "协助", "对接")
    if not any(marker in cleaned for marker in health_markers):
        return cleaned
    if not any(marker in cleaned for marker in assist_markers):
        return cleaned
    return "您这个情况需要先让专业同事帮您核对是否适合，我先帮您记录清楚。"


def sanitize_overpromising_phrasing(text: str) -> str:
    cleaned = str(text or "").strip()
    replacements = (
        ("确保方案匹配", "确认方案是否适合"),
        ("确保适配", "确认是否适合"),
        ("确保安全", "尽量降低风险"),
        ("安全可控", "先检测评估后更稳妥"),
        ("绝不会强制消费", "不会强制消费"),
        ("绝不会临时加价", "不会临时加价"),
        ("绝不会", "不会随意"),
        ("一定不会", "不会随意"),
        ("不会越做越差", "会先评估适不适合再做"),
        ("所有操作都在安全阈值内", "操作前会先看皮肤状态"),
        ("专属优惠机制", "当前活动规则"),
        ("最优方案", "合适方案"),
    )
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)
    return cleaned


def sanitize_activity_name_phrasing(text: str) -> str:
    cleaned = str(text or "").strip()
    replacements = (
        ("S10 淡斑套餐", "周年庆淡斑活动"),
        ("S10淡斑套餐", "周年庆淡斑活动"),
        ("S10 周年庆活动", "周年庆活动"),
        ("S10周年庆活动", "周年庆活动"),
        ("S10 活动", "周年庆活动"),
        ("S10活动", "周年庆活动"),
        ("S10 单品", "周年庆活动"),
        ("S10单品", "周年庆活动"),
        ("焕新体验计划", "周年庆活动"),
        ("焕新体验季", "周年庆活动"),
        ("焕新季·限时轻颜礼", "周年庆活动"),
        ("焕新季限时活动", "周年庆活动"),
        ("限时焕新活动", "周年庆活动"),
        ("限时焕新", "周年庆活动"),
        ("焕新季", "周年庆活动"),
        ("体验季", "周年庆活动"),
        ("轻颜礼", "周年庆活动"),
        ("节日活动", "周年庆活动"),
        ("大型活动", "周年庆活动"),
        ("团购活动", "周年庆活动"),
        ("新客活动", "周年庆活动"),
        ("新客专属的周年庆活动价", "新客周年庆活动价"),
        ("新客专属的周年庆淡斑活动价", "新客周年庆淡斑活动价"),
        ("新客专属的周年庆", "新客周年庆"),
        ("新客专享价", "新客活动价"),
        ("指定项目首单可享立减", "现在参加的就是周年庆活动价"),
        ("指定项目享限时特惠价", "现在参加的就是周年庆活动价"),
        ("指定项目立减或加赠护理", "按周年庆活动规则参与"),
        ("享立减+赠护理", "按周年庆活动规则参与"),
        ("活动有效期到本月底", "名额满活动结束"),
        ("活动持续到本月底", "名额满活动结束"),
        ("本月底活动结束", "名额满活动结束"),
        ("如果最后没做，这10元会原路退还", "如果到店不做，退还10元"),
        ("如果最后没做，10元会原路退还", "如果到店不做，退还10元"),
        ("如果临时不来，10元会全额退还", "如果到店不做，退还10元"),
        ("如果临时不来，这10元会全额退还", "如果到店不做，退还10元"),
        ("如果不做会原路退还10元", "如果到店不做，退还10元"),
        ("如果不做会全额退还10元", "如果到店不做，退还10元"),
        ("10元预约金不退还", "到店抵扣10元，做付258元，不做退还10元"),
        ("预约金10元不退还", "到店抵扣10元，做付258元，不做退还10元"),
        ("10元不退还", "不做退还10元"),
    )
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\bS10\b", "周年庆活动", cleaned)
    cleaned = cleaned.replace("周年庆活动活动", "周年庆活动")
    cleaned = cleaned.replace("周年庆活动价价", "周年庆活动价")
    cleaned = cleaned.replace("这10元全额退还", "到店不做退还10元")
    cleaned = cleaned.replace("10元全额退还", "到店不做退还10元")
    cleaned = cleaned.replace("全额退还10元", "到店不做退还10元")
    cleaned = cleaned.replace("不做到店退还10元", "到店不做退还10元")
    cleaned = cleaned.replace("不做到店会退还10元", "到店不做退还10元")
    cleaned = cleaned.replace("不做的话10元退还", "到店不做退还10元")
    cleaned = cleaned.replace("不做的话，10元退还", "到店不做退还10元")
    cleaned = cleaned.replace("不做会退还10元", "到店不做退还10元")
    cleaned = cleaned.replace("不做就退10元", "到店不做退还10元")
    cleaned = cleaned.replace("不做就退还10元", "到店不做退还10元")
    cleaned = cleaned.replace("这10元会原路退还", "到店不做退还10元")
    cleaned = cleaned.replace("10元会原路退还", "到店不做退还10元")
    cleaned = cleaned.replace("这10元会原路退回", "到店不做退还10元")
    cleaned = cleaned.replace("10元会原路退回", "到店不做退还10元")
    cleaned = cleaned.replace("原路退还10元", "到店不做退还10元")
    cleaned = cleaned.replace("原路退回10元", "到店不做退还10元")
    cleaned = cleaned.replace("不做到店不做退还10元", "到店不做退还10元")
    cleaned = cleaned.replace("如果到店不做，这10元不退还", "如果到店不做，退还10元")
    cleaned = cleaned.replace("到店不做，这10元不退还", "到店不做退还10元")
    cleaned = cleaned.replace("不做不退还10元", "不做退还10元")
    cleaned = cleaned.replace("不做不退10元", "不做退还10元")
    return cleaned


def dedupe_repeated_phrase_noise(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = cleaned.replace("类项目类方向", "类项目方向")
    cleaned = re.sub(r"([^，。；、]{4,30})这类\1", r"\1", cleaned)
    cleaned = re.sub(r"(比如|例如)([^，。；]{2,24})\1", r"\1\2", cleaned)
    cleaned = re.sub(r"([^，。；]{2,24})\1", r"\1", cleaned)
    cleaned = re.sub(r"([。！？])\1+", r"\1", cleaned)
    return cleaned.strip(" ，。；")


def renumber(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        copied = dict(message)
        copied["order"] = index
        ordered.append(copied)
    return ordered


def _message_text(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("text") or content.get("handoff_reason") or "").strip()
    return str(content or "").strip()
