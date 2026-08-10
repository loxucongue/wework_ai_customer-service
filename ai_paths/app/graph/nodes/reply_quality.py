from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.graph.nodes.contextual_short_message import is_contextual_short_message
from app.graph.nodes.reply_validation import (
    _case_image_urls,
    _combined_text,
    _has_visible_case_image_fact,
    _is_case_or_effect_turn,
    _is_generic_store_question_without_current_scope,
    _known_store_names_for_validation,
    _normalized_image_url,
    _structured_facts,
    message_content_text,
)


def collect_reply_soft_warnings(messages: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, str]]:
    checks = (
        _validate_case_image_required_for_effect_turn,
        _validate_effect_reply_confidence_order,
        _validate_generic_store_question_does_not_use_context_store,
        _validate_appointment_time_option_count,
        _validate_repeat_similarity,
        _validate_two_text_rhythm,
        _validate_precision_reply_active_mainline_closure,
        _validate_nearby_store_claim_has_fact,
    )
    warnings: list[dict[str, str]] = []
    for check in checks:
        try:
            check(messages, state)
        except ValueError as exc:
            warnings.append(
                {
                    "node": "synthesize_reply",
                    "message": "soft_reply_quality_warning",
                    "detail": str(exc),
                }
            )
    return warnings


def _validate_case_image_required_for_effect_turn(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if not _is_case_or_effect_turn(state) or not _has_visible_case_image_fact(state):
        return
    case_urls = _case_image_urls(state)
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "") != "image":
            continue
        if _normalized_image_url(message_content_text(item.get("content"))) in case_urls:
            return
    raise ValueError("case_image_required_for_effect_turn")


def _validate_effect_reply_confidence_order(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if not _is_case_or_effect_turn(state):
        return
    text = _first_text(messages)
    if not text:
        return
    prefix = re.sub(r"^[\s，。,.、~～哈呀亲您你好您好]+", "", text)[:40]
    cautious_terms = ("因人而异", "每个人不同", "每个人肤质不同", "不保证", "不能保证", "不好保证", "具体要看", "要看个人情况")
    confidence_terms = ("可以做", "可以先看", "能做", "能改善", "大多数", "反馈不错", "效果不错", "改善明显", "改善反馈")
    bad_indexes = [prefix.find(term) for term in cautious_terms if prefix.find(term) >= 0]
    if not bad_indexes:
        return
    first_bad = min(bad_indexes)
    good_indexes = [prefix.find(term) for term in confidence_terms if prefix.find(term) >= 0]
    first_good = min(good_indexes) if good_indexes else -1
    if first_good < 0 or first_bad < first_good:
        raise ValueError("effect_reply_confidence_order_required")


def _validate_generic_store_question_does_not_use_context_store(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if not _is_generic_store_question_without_current_scope(state):
        return
    structured = _structured_facts(state)
    if structured.get("store_facts") or structured.get("recommended_store"):
        return
    text = _combined_text(messages)
    if not text:
        return
    for name in _known_store_names_for_validation(state):
        if name and name in text:
            raise ValueError("store_context_over_anchor_for_generic_question")


def _validate_appointment_time_option_count(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    appointment_facts = _structured_facts(state).get("appointment_facts")
    if not isinstance(appointment_facts, list) or not any(
        isinstance(item, dict) and str(item.get("type") or "") == "available_time" for item in appointment_facts
    ):
        return
    text = _combined_text(messages)
    if not text:
        return
    times = list(dict.fromkeys(re.findall(r"\b\d{1,2}[:：]\d{2}\b", text)))
    if len(times) > 2:
        raise ValueError("too_many_appointment_time_options")


def _validate_repeat_similarity(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if any(str(item.get("type") or "") != "text" for item in messages if isinstance(item, dict)):
        return
    current_text = _combined_text(messages)
    if not current_text or _is_price_confirmation_turn(str(state.get("normalized_content") or state.get("content") or "")):
        return
    previous = _last_assistant_text(state)
    if not previous:
        return
    ratio = SequenceMatcher(None, previous, current_text).ratio()
    if ratio > 0.85:
        raise ValueError("reply_too_similar_to_previous_assistant_message")


def _validate_two_text_rhythm(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if str(state.get("planner_decision") or "") != "direct_reply":
        return
    if str(state.get("conversion_stage") or "") == "deposit_push" or str(state.get("next_step") or "") == "send_deposit":
        return
    if is_contextual_short_message(str(state.get("normalized_content") or state.get("content") or "")):
        return
    if any(str(item.get("type") or "") != "text" for item in messages if isinstance(item, dict)):
        return
    text_messages = [item for item in messages if isinstance(item, dict) and str(item.get("type") or "") == "text"]
    if len(text_messages) != 1:
        return
    if _looks_like_answer_with_next_step(message_content_text(text_messages[0].get("content"))):
        raise ValueError("two_text_required_for_answer_with_next_step")


def _validate_precision_reply_active_mainline_closure(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    precision = state.get("precision_qa_decision") if isinstance(state.get("precision_qa_decision"), dict) else {}
    question_id = str(precision.get("question_id") or "").strip()
    if not question_id or str(question_id).lower() in {"none", "unknown"}:
        return
    sales_progression = state.get("sales_progression") if isinstance(state.get("sales_progression"), dict) else {}
    if str(sales_progression.get("status") or "").strip().lower() == "terminal":
        return
    text = _combined_text(messages)
    if not text:
        return
    passive_phrases = (
        "如果您想",
        "如果你想",
        "如果您愿意",
        "如果你愿意",
        "我可以继续给您",
        "我可以继续给你",
        "要不要了解",
        "是否需要",
        "您看要不要",
        "你看要不要",
    )
    if any(phrase in text for phrase in passive_phrases):
        raise ValueError("precision_reply_passive_mainline_closure")
    if question_id == "one_session_effect" and any(
        phrase in text for phrase in ("不是那种做了完全没变化", "不是完全没变化")
    ):
        raise ValueError("precision_reply_weak_one_session_confidence")
    if not _precision_reply_has_mainline_action(messages, text):
        raise ValueError("precision_reply_missing_mainline_action")


def _validate_nearby_store_claim_has_fact(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    if "附近门店" not in text and "附近的门店" not in text:
        return
    if any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages):
        return
    structured = _structured_facts(state)
    if structured.get("store_facts") or structured.get("recommended_store"):
        return
    if str(state.get("normalized_content") or state.get("content") or "").strip().startswith("定位卡"):
        return
    raise ValueError("nearby_store_claim_without_location_fact")


def _first_text(messages: list[dict[str, Any]]) -> str:
    for item in messages:
        if isinstance(item, dict) and str(item.get("type") or "text") == "text":
            text = message_content_text(item.get("content"))
            if text:
                return text
    return ""


def _precision_reply_has_mainline_action(messages: list[dict[str, Any]], text: str) -> bool:
    for item in messages:
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "")
        if message_type in {"payment_collection", "store_address", "image"}:
            return True
    value = str(text or "")
    action_patterns = (
        r"您在哪个城市",
        r"你在哪个城市",
        r"哪个城市.*哪个区",
        r"在哪个区",
        r"我(?:先|这边)?给您(?:对|匹配|发|留|登记|接)",
        r"我(?:先|这边)?帮您(?:对|匹配|发|留|登记|接)",
        r"活动名额",
        r"活动价",
        r"名额.{0,8}(?:留|锁|占)",
        r"预约金",
        r"收款卡",
        r"截图发我",
        r"您是.{0,6}(?:两位|几位)",
        r"几位一起",
        r"到店时间",
        r"哪天",
        r"上午|下午|周末",
        r"同类.{0,8}(?:案例|效果|参考)",
        r"(?:案例|效果参考).{0,8}发您",
        r"满了我就",
        r"确定参加",
    )
    return any(re.search(pattern, value) for pattern in action_patterns)


def _last_assistant_text(state: dict[str, Any]) -> str:
    for item in reversed(state.get("conversation_history") or []):
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role and role not in {"assistant", "staff", "service", "bot"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if text:
                return text
            continue
        raw = str(item or "").strip()
        for prefix in ("小贝:", "小贝：", "客服:", "客服：", "助手:", "助手：", "AI回复:", "AI回复："):
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
    return ""


def _is_price_confirmation_turn(content: str) -> bool:
    return any(term in str(content or "") for term in ("多少钱", "价格", "到底", "是不是199", "268", "199"))


def _looks_like_answer_with_next_step(text: str) -> bool:
    value = str(text or "").strip()
    if len(value) < 18:
        return False
    return any(
        term in value
        for term in (
            "您方便",
            "哪个区",
            "哪天",
            "今天还是明天",
            "上午还是下午",
            "周六还是周日",
            "到店看看",
            "到店看",
            "帮您看名额",
            "帮您查",
            "帮您看看",
            "我帮您看",
        )
    )
