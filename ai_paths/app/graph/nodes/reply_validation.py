from __future__ import annotations

import html
import re
from difflib import SequenceMatcher
from typing import Any

from app.graph.nodes.common import renumber_messages
from app.graph.nodes.contextual_short_message import is_contextual_short_message
from app.policies.constants import KNOWN_STORE_NAMES
from app.services.payment_collection import (
    has_forbidden_deposit_refund_policy_text,
    normalize_deposit_refund_policy_text,
    payment_amount_matches_text,
    payment_collection_content,
    payment_collection_context,
)
from app.services.risk_hold import health_risk_hold, is_hard_health_risk_hold

VISIBLE_MESSAGE_TYPES = {"text", "image", "video", "payment_collection", "store_address"}
ALLOWED_MESSAGE_TYPES = {"text", "image", "video", "human_handoff", "human_handoff_notice", "payment_collection", "store_address"}
MAX_VISIBLE_MESSAGES = 4
MAX_SOP_SEQUENCE_VISIBLE_MESSAGES = 8


def validated_model_messages(payload: dict[str, Any], state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    messages = payload.get("reply_messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Model JSON missing reply_messages")
    max_visible_messages = _max_visible_messages(state or {})
    result: list[dict[str, Any]] = []
    visible_count = 0
    has_handoff = False
    for item in messages:
        if not isinstance(item, dict):
            continue
        msg_type = item.get("type") if item.get("type") in ALLOWED_MESSAGE_TYPES else "text"
        if msg_type in {"human_handoff", "human_handoff_notice"}:
            if has_handoff:
                continue
            handoff_reason = message_content_text(item.get("content"))
            if not handoff_reason:
                continue
            result.append(
                {
                    "type": "human_handoff_notice",
                    "order": len(result) + 1,
                    "content": {"handoff_reason": handoff_reason},
                }
            )
            has_handoff = True
            continue
        if msg_type == "payment_collection":
            if visible_count >= max_visible_messages:
                continue
            result.append(
                {
                    "type": "payment_collection",
                    "order": len(result) + 1,
                    "content": message_content_payment_collection(item.get("content"), state=state or {}, messages=messages),
                }
            )
            visible_count += 1
            continue
        if msg_type == "store_address":
            if visible_count >= max_visible_messages:
                continue
            store_id = message_content_store_id(item.get("content"))
            if not store_id:
                continue
            result.append({"type": "store_address", "order": len(result) + 1, "content": {"store_id": store_id}})
            visible_count += 1
            continue
        if visible_count >= max_visible_messages:
            continue
        content = normalize_deposit_refund_policy_text(message_content_text(item.get("content")))
        if not content:
            continue
        if msg_type == "text":
            image_url = extract_image_url_from_text(content)
            if image_url:
                text_without_url = strip_image_url_from_text(content, image_url)
                if text_without_url:
                    result.append({"type": "text", "order": len(result) + 1, "content": text_without_url})
                    visible_count += 1
                result.append({"type": "image", "order": len(result) + 1, "content": image_url})
                visible_count += 1
                continue
        result.append({"type": msg_type, "order": len(result) + 1, "content": content})
        visible_count += 1
    if not result:
        raise ValueError("Model reply_messages are empty")
    result = _move_handoff_notices_after_visible(result)
    return renumber_messages(result)


def _max_visible_messages(state: dict[str, Any]) -> int:
    return MAX_SOP_SEQUENCE_VISIBLE_MESSAGES if _is_sop_sequence_state(state) else MAX_VISIBLE_MESSAGES


def _is_sop_sequence_state(state: dict[str, Any]) -> bool:
    explicit = str(state.get("reply_mode") or "").strip()
    if explicit == "sop_sequence":
        return True
    if explicit == "normal_answer":
        return False
    if str(state.get("planner_stage") or "").upper() == "S4":
        return False
    sub_rule = str(state.get("planner_sub_rule_id") or "").lower()
    if any(marker in sub_rule for marker in ("parking", "business_hours", "appointment_change", "appointment_cancel", "after_sales")):
        return False
    conversion_stage = str(state.get("conversion_stage") or "").lower()
    if conversion_stage in {"interest_capture", "objection_resolution", "store_match", "deposit_push"}:
        return True
    structured = _structured_facts(state)
    store_facts = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    case_facts = structured.get("case_facts") if isinstance(structured.get("case_facts"), list) else []
    return bool(store_facts or case_facts)


def _move_handoff_notices_after_visible(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notices = [item for item in messages if str(item.get("type") or "") == "human_handoff_notice"]
    if not notices:
        return messages
    visible = [item for item in messages if str(item.get("type") or "") != "human_handoff_notice"]
    return [*visible, *notices]


def validate_reply_consistency(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    _validate_handoff_notice_text(messages)
    _validate_payment_not_during_health_risk_hold(messages, state)
    _validate_payment_collection_consistency(messages, state)
    _validate_payment_collection_amount_text(messages, state)
    _validate_deposit_refund_wording(messages, state)
    _validate_case_image_priority(messages, state)
    _validate_case_image_required_for_effect_turn(messages, state)
    _validate_effect_reply_confidence_order(messages, state)
    _validate_effect_absolute_safety_claims(messages, state)
    _validate_store_address_message_facts(messages, state)
    _validate_store_address_card_consistency(messages, state)
    _validate_generic_store_question_does_not_use_context_store(messages, state)
    _validate_appointment_lookup_promise(messages, state)
    _validate_appointment_time_facts(messages, state)
    _validate_appointment_time_option_count(messages, state)
    _validate_appointment_confirmation_facts(messages, state)
    _validate_finished_tool_turn_does_not_promise_pending_work(messages, state)
    _validate_repeat_similarity(messages, state)
    _validate_fact_boundaries(messages, state)


def _validate_handoff_notice_text(messages: list[dict[str, Any]]) -> None:
    has_notice = any(
        str(item.get("type") or "") in {"human_handoff", "human_handoff_notice"}
        for item in messages
        if isinstance(item, dict)
    )
    if not has_notice:
        return
    text = _combined_text(messages)
    if not text:
        raise ValueError("human_handoff_notice_requires_visible_answer")
    banned = (
        "转人工",
        "转接",
        "转同事",
        "专业同事",
        "专业顾问",
        "同事沟通",
        "同事协助",
        "同步给同事",
        "同步给专业",
        "我帮您同步处理",
        "同步处理",
        "同步反馈处理",
        "反馈处理",
        "专人联系",
        "稍后会有专人",
        "马上帮您核对",
        "马上核对",
        "马上帮您对接",
        "稍等一下哈",
        "稍等哈",
        "我先帮您看一下",
    )
    if any(term in text for term in banned):
        raise ValueError("human_handoff_notice_customer_text_not_resolved")


def debug_message_contents(messages: list[dict[str, Any]]) -> list[str]:
    return [message_content_text(message.get("content"))[:240] for message in messages[:4] if isinstance(message, dict)]


def _validate_payment_collection_consistency(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if str(state.get("planner_decision") or "") == "no_reply":
        return
    has_payment = any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict))
    if is_hard_health_risk_hold(health_risk_hold(state)):
        return
    payment_action = str(state.get("payment_action") or "")
    if payment_action in {"none", "offer_resend", "explain_existing", "confirm_next_step"}:
        if has_payment:
            raise ValueError("payment_collection_blocked_by_payment_action")
        if _promises_payment_entry(_combined_text(messages)):
            raise ValueError("payment_collection_blocked_by_payment_action")
        return
    if _paid_deposit_context(state):
        if has_payment:
            raise ValueError("payment_collection_blocked_by_paid_deposit_context")
        return
    text = _combined_text(messages)
    payment_context = payment_collection_context(state=state, messages=messages)
    needs_payment = False
    if not _explains_previous_payment_entry(text):
        needs_payment = (
            payment_action == "send_now"
            or str(state.get("conversion_stage") or "") == "deposit_push"
            or str(state.get("next_step") or "") == "send_deposit"
            or _promises_payment_entry(text)
        )
    if needs_payment and payment_context.get("over_limit") and not has_payment:
        if (
            _promises_payment_entry(text)
            or _mentions_over_limit_payment_amount(text)
            or not _asks_over_limit_participant_confirmation(text)
        ):
            raise ValueError("payment_participant_count_confirm_required")
        return
    if needs_payment and not has_payment:
        raise ValueError("payment_collection_required_when_reply_promises_payment_entry")


def _paid_deposit_context(state: dict[str, Any]) -> bool:
    if str(state.get("payment_state") or "") == "customer_claimed_paid":
        return True
    turn_context = state.get("current_turn_context")
    if not isinstance(turn_context, dict):
        return False
    return str(turn_context.get("deposit_state") or "") == "deposit_paid"


def _validate_payment_not_during_health_risk_hold(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if not is_hard_health_risk_hold(health_risk_hold(state)):
        return
    if any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict)):
        raise ValueError("payment_collection_blocked_by_health_risk_hold")
    text = _combined_text(messages)
    if any(term in text for term in ("预约金", "付款入口", "收款入口", "报名入口", "支付入口", "线上10元", "锁名额")):
        raise ValueError("payment_collection_blocked_by_health_risk_hold")


def _validate_payment_collection_amount_text(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if not any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict)):
        return
    if payment_collection_context(state=state, messages=messages).get("over_limit"):
        raise ValueError("payment_participant_count_confirm_required")
    if not payment_amount_matches_text(messages):
        raise ValueError("payment_collection_amount_text_mismatch")


def _validate_deposit_refund_wording(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    if not text:
        return
    if "全额退还" in text or "全额退款" in text or has_forbidden_deposit_refund_policy_text(text):
        raise ValueError("ambiguous_deposit_refund_wording")


def _validate_case_image_priority(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if not _is_case_or_effect_turn(state) or not _has_visible_case_image_fact(state):
        return
    activity_url = _activity_intro_image_url(state)
    if not activity_url:
        return
    activity_marker = _normalized_image_url(activity_url)
    for item in messages:
        if not isinstance(item, dict) or str(item.get("type") or "") != "image":
            continue
        if _normalized_image_url(message_content_text(item.get("content"))) == activity_marker:
            raise ValueError("case_context_must_not_use_activity_intro_image")


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


def _validate_effect_absolute_safety_claims(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = re.sub(r"\s+", "", _combined_text(messages))
    if not text:
        return
    banned = (
        "绝不会反黑",
        "不会做坏",
        "不会越做越差",
        "一般不会",
        "通常不会",
        "基本不会",
        "一定有效",
        "一次一定",
        "保证效果",
        "包效果",
    )
    risk_terms = ("反黑", "留疤", "留痕", "伤肤", "伤皮肤", "做坏", "越做越差")
    state_text = re.sub(r"\s+", "", str(state.get("normalized_content") or state.get("content") or ""))
    risk_scope = f"{text}{state_text}"
    if any(term in text for term in banned) and any(term in risk_scope for term in risk_terms):
        raise ValueError("effect_absolute_safety_claim")
    if re.search(r"不会[^，。！？,.!?]{0,8}(反黑|留疤|留痕|伤肤|伤皮肤|做坏|越做越差)", text):
        raise ValueError("effect_absolute_safety_claim")


def _validate_store_address_message_facts(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    store_ids = [
        message_content_store_id(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "") == "store_address"
    ]
    store_ids = [item for item in store_ids if item]
    if not store_ids:
        return
    allowed_ids = _allowed_store_address_ids(state)
    missing_ids = [store_id for store_id in store_ids if store_id not in allowed_ids]
    if missing_ids:
        raise ValueError("unsupported_store_address_message")


def _validate_store_address_card_consistency(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    current_content = str(state.get("normalized_content") or state.get("content") or "")
    if not _promises_store_address_card(text) and not _current_message_requests_store_address_card(current_content):
        return
    if any(isinstance(item, dict) and str(item.get("type") or "") == "store_address" for item in messages):
        return
    allowed_ids = _allowed_store_address_ids(state)
    if allowed_ids:
        raise ValueError("store_address_message_required_when_reply_promises_location_card")


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


def _validate_appointment_time_facts(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    if not text or not _asserts_time_available(text):
        return
    if _has_appointment_confirmation_fact(state):
        return
    appointment_facts = _structured_facts(state).get("appointment_facts")
    if not isinstance(appointment_facts, list):
        raise ValueError("available_time_fact_required")
    available_time_facts = [
        item for item in appointment_facts if isinstance(item, dict) and str(item.get("type") or "") == "available_time"
    ]
    if not available_time_facts:
        raise ValueError("available_time_fact_required")
    if not any(_available_time_fact_supports_availability(item) for item in available_time_facts):
        raise ValueError("available_time_fact_required")


def _validate_appointment_lookup_promise(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    if not text or not _promises_appointment_lookup(text):
        return
    appointment_facts = _structured_facts(state).get("appointment_facts")
    if not isinstance(appointment_facts, list) or not appointment_facts:
        raise ValueError("unfinished_appointment_lookup_promise")
    if not any(
        isinstance(item, dict)
        and str(item.get("type") or "") == "available_time"
        and _available_time_fact_supports_availability(item)
        for item in appointment_facts
    ):
        raise ValueError("unfinished_appointment_lookup_promise")


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


def _validate_appointment_confirmation_facts(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    if not text or not _asserts_appointment_confirmed(text):
        return
    if _has_appointment_confirmation_fact(state):
        return
    raise ValueError("appointment_confirmation_fact_required")


def _validate_finished_tool_turn_does_not_promise_pending_work(
    messages: list[dict[str, Any]], state: dict[str, Any]
) -> None:
    if str(state.get("planner_decision") or "") != "need_tools":
        return
    text = _combined_text(messages)
    if not text:
        return
    if _promises_unfinished_lookup(text):
        raise ValueError("unfinished_tool_promise_after_tool_execution")


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


def _validate_fact_boundaries(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    if not text:
        return
    structured = _structured_facts(state)
    store_facts = structured.get("store_facts") if isinstance(structured.get("store_facts"), list) else []
    recommended_store = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    has_store_detail = bool(store_facts or recommended_store)
    has_parking = any(
        isinstance(item, dict) and any(str(item.get(key) or "").strip() for key in ("parking_name", "parking_address", "parking_url", "parking"))
        for item in store_facts
    )
    has_hours = any(
        isinstance(item, dict) and str(item.get("business_hours") or item.get("hours") or "").strip()
        for item in store_facts
    )
    has_distance = _has_distance_ranking_fact(structured)
    if _asserts_parking(text) and not has_parking:
        raise ValueError("parking_fact_required")
    if _asserts_business_hours(text) and not has_hours:
        raise ValueError("business_hours_fact_required")
    if _asserts_address(text) and not has_store_detail:
        raise ValueError("store_address_fact_required")
    if _asserts_customer_visible_distance_value(text, state):
        raise ValueError("distance_value_not_customer_visible")
    if _asserts_distance_ranking(text) and not has_distance:
        raise ValueError("distance_fact_required")


def _combined_text(messages: list[dict[str, Any]]) -> str:
    return " ".join(
        message_content_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )


def _is_case_or_effect_turn(state: dict[str, Any]) -> bool:
    values = " ".join(
        str(state.get(key) or "")
        for key in ("planner_sub_rule_id", "customer_type", "main_blocker", "next_step")
    ).lower()
    if any(marker in values for marker in ("case", "effect")):
        return True
    return _is_effect_question_text(str(state.get("normalized_content") or state.get("content") or ""))


def _is_effect_question_text(content: str) -> bool:
    text = str(content or "")
    if not text:
        return False
    direct_effect = any(
        term in text
        for term in (
            "效果怎么样",
            "有没有效果",
            "有效果吗",
            "没效果",
            "会不会没效果",
            "没效果怎么办",
            "一次有没有效果",
            "一次效果",
            "做完明显",
            "能不能淡",
            "能淡吗",
            "可以淡吗",
            "怕反黑",
            "会不会反黑",
            "怕做坏",
            "效果图",
            "案例",
        )
    )
    concern_terms = ("斑", "黑色素", "色沉", "痘印")
    can_do_terms = ("能不能做", "能做吗", "可以做吗", "可以改善吗", "能改善吗", "能不能改善")
    return direct_effect or (any(term in text for term in concern_terms) and any(term in text for term in can_do_terms))


def _has_visible_case_image_fact(state: dict[str, Any]) -> bool:
    return bool(_case_image_urls(state))


def _case_image_urls(state: dict[str, Any]) -> set[str]:
    structured = _structured_facts(state)
    case_facts = structured.get("case_facts") if isinstance(structured.get("case_facts"), list) else []
    return {
        _normalized_image_url(str(item.get("image_url") or ""))
        for item in case_facts
        if isinstance(item, dict) and str(item.get("image_url") or "").strip()
    }


def _first_text(messages: list[dict[str, Any]]) -> str:
    for item in messages:
        if isinstance(item, dict) and str(item.get("type") or "text") == "text":
            text = message_content_text(item.get("content"))
            if text:
                return text
    return ""


def _activity_intro_image_url(state: dict[str, Any]) -> str:
    rules = state.get("business_rules") if isinstance(state.get("business_rules"), dict) else {}
    offer = rules.get("offer") if isinstance(rules.get("offer"), dict) else {}
    return str(offer.get("activity_intro_image_url") or "").strip()


def _normalized_image_url(value: str) -> str:
    return html.unescape(str(value or "").strip())


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


def _structured_facts(state: dict[str, Any]) -> dict[str, Any]:
    fact_envelope = state.get("fact_envelope") if isinstance(state.get("fact_envelope"), dict) else {}
    structured = fact_envelope.get("structured_facts")
    return structured if isinstance(structured, dict) else {}


def _has_distance_ranking_fact(structured: dict[str, Any]) -> bool:
    recommended_store = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    store_lookup_status = structured.get("store_lookup_status") if isinstance(structured.get("store_lookup_status"), dict) else {}
    recommendation_status = str(store_lookup_status.get("recommendation_status") or store_lookup_status.get("status") or "")
    try:
        candidate_count = int(store_lookup_status.get("candidate_count") or 0)
    except (TypeError, ValueError):
        candidate_count = 0
    return (
        recommended_store.get("reason") == "distance_calculate_rank_1"
        or (
            store_lookup_status.get("source") == "distance_calculate"
            and candidate_count > 0
            and recommendation_status not in {"distance_tool_unavailable", "error", "failed"}
        )
    )


def _allowed_store_address_ids(state: dict[str, Any]) -> set[str]:
    structured = _structured_facts(state)
    allowed: set[str] = set()
    for item in structured.get("store_facts") or []:
        if isinstance(item, dict):
            _add_store_id(allowed, item)
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        _add_store_id(allowed, recommended)
    for item in structured.get("appointment_facts") or []:
        if isinstance(item, dict):
            _add_store_id(allowed, item)
    for key in ("confirmed_store_id", "store_id"):
        value = str(state.get(key) or "").strip()
        if value and value != "0":
            allowed.add(value)
    return allowed


def _add_store_id(target: set[str], item: dict[str, Any]) -> None:
    for key in ("store_id", "id"):
        value = str(item.get(key) or "").strip()
        if value and value != "0":
            target.add(value)


def _asserts_parking(text: str) -> bool:
    return any(term in text for term in ("有停车", "可以停车", "能停车", "楼下可停", "停车场"))


def _asserts_business_hours(text: str) -> bool:
    return any(term in text for term in ("营业时间是", "营业时间为", "营业到")) or bool(
        re.search(r"\d{1,2}[:：]\d{2}\s*[-~到至]\s*\d{1,2}[:：]\d{2}", text)
    )


def _asserts_address(text: str) -> bool:
    if any(term in text for term in ("地址是", "地址在", "位于")) and any(term in text for term in ("门店", "店", "导航", "地址")):
        return True
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]{1,30}(?:路|街|大道|巷)\s*\d+\s*号", text))


def _asserts_distance_ranking(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if _asks_location_before_distance_matching(compact):
        return False
    if any(term in compact for term in ("最近的是", "离您最近", "离你最近", "距离最近", "就近门店", "就近的门店")):
        return True
    return any(term in compact for term in ("更近", "近一些", "近一点", "较近")) and any(
        term in compact for term in ("门店", "店", "地址", "导航", "位置", "离您", "离你")
    )


def _asks_location_before_distance_matching(compact: str) -> bool:
    if not compact:
        return False
    asks_location = any(term in compact for term in ("告诉我您所在的城市", "告诉我所在城市", "您所在的城市", "您在哪个城市", "哪个城市", "哪个区", "城市或区域", "城市或者区域", "发我定位", "发个定位"))
    future_match = any(term in compact for term in ("帮您匹配", "帮你匹配", "给您匹配", "给你匹配", "匹配附近", "匹配就近", "查附近", "推荐附近"))
    return asks_location and future_match


def _asserts_customer_visible_distance_value(text: str, state: dict[str, Any]) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not _is_store_distance_context(compact, state):
        return False
    if re.search(r"\d+(?:\.\d+)?(?:公里|千米|km|KM)", compact):
        return True
    route_terms = "车程|步行|打车|开车|公交|地铁|骑车|过去|过来|到店|路程|导航|路上|交通"
    if re.search(rf"(?:{route_terms})[^，。！？；,.!?;]{{0,12}}\d+(?:-\d+)?分钟", compact):
        return True
    return bool(re.search(rf"\d+(?:-\d+)?分钟[^，。！？；,.!?;]{{0,8}}(?:{route_terms}|到)", compact))


def _is_store_distance_context(text: str, state: dict[str, Any]) -> bool:
    structured = _structured_facts(state)
    if _has_distance_ranking_fact(structured):
        return True
    state_markers = " ".join(
        str(state.get(key) or "")
        for key in ("conversion_stage", "customer_type", "main_blocker", "next_step", "planner_sub_rule_id")
    ).lower()
    if "distance" in state_markers or "store" in state_markers:
        return True
    return any(term in text for term in ("门店", "店", "地址", "导航", "位置", "距离", "离您", "离你", "车程", "步行"))


def _promises_store_address_card(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return any(
        term in compact
        for term in (
            "地址我发",
            "地址发您",
            "地址发你",
            "位置我发",
            "位置发您",
            "位置发你",
            "点开导航",
            "直接导航过去",
            "门店卡片",
            "位置卡",
            "定位卡",
        )
    )


def _current_message_requests_store_address_card(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if any(
        term in compact
        for term in (
            "发个位置",
            "发位置",
            "位置发我",
            "位置给我",
            "发个地址",
            "发地址",
            "地址发我",
            "地址给我",
            "发导航",
            "导航发我",
            "发定位",
            "定位发我",
            "门店位置",
        )
    ):
        return True
    return bool(re.search(r"发.{0,8}(地址|位置|定位|导航)", compact)) or bool(
        re.search(r"(地址|位置|定位|导航).{0,8}(发|给)", compact)
    )


def _is_generic_store_question_without_current_scope(state: dict[str, Any]) -> bool:
    text = str(state.get("normalized_content") or state.get("content") or "")
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    if any(term in compact for term in ("这家", "那家", "这个店", "刚刚", "刚才", "上面那家", "前面那家")):
        return False
    generic_terms = (
        "门店在哪里",
        "门店在哪",
        "哪里有门店",
        "有哪些门店",
        "有门店吗",
        "门店地址",
        "门店位置",
        "你们店在哪里",
        "你们店在哪",
        "店在哪里",
        "店在哪",
    )
    if not any(term in compact for term in generic_terms):
        return False
    if any(name and name in text for name in _known_store_names_for_validation(state)):
        return False
    return not bool(re.search(r"[\u4e00-\u9fff]{2,}(省|市|区|县|镇|乡|旗|州|盟|新区|机场|高铁站|火车站)", compact))


def _known_store_names_for_validation(state: dict[str, Any]) -> list[str]:
    names: list[str] = []
    knowledge = state.get("customer_store_knowledge") if isinstance(state.get("customer_store_knowledge"), dict) else {}
    stores = knowledge.get("stores") if isinstance(knowledge.get("stores"), list) else []
    for store in stores:
        if not isinstance(store, dict):
            continue
        name = str(store.get("store_name") or store.get("name") or "").strip()
        if name:
            names.append(name)
    names.extend(name for name in KNOWN_STORE_NAMES if name)
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    for item in history[-12:]:
        names.extend(_store_like_names_from_text(str(item or "")))
    output: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name and name not in seen:
            seen.add(name)
            output.append(name)
    return output


def _store_like_names_from_text(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"[\u4e00-\u9fff]{2,}(?:一店|二店|三店|四店|五店|六店|七店|八店|九店|十店|旗舰店|中心店|分店)", text):
        name = match.group(0).strip()
        if not name:
            continue
        names.append(name)
        max_len = min(12, len(name))
        for size in range(4, max_len + 1):
            suffix = name[-size:]
            if suffix not in {"这个店", "那个店"}:
                names.append(suffix)
    return names


def _asserts_time_available(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if any(term in compact for term in ("可以约", "能约", "可以预约", "能预约", "有空档", "有档期", "有空位")):
        return True
    return bool(
        re.search(r"(?:今天|明天|后天|上午|下午|晚上|\d{1,2}点(?:半|左右)?).{0,8}(?:可以|有空|有时间|有名额|有位置|能约|可约|安排)", compact)
        or re.search(r"(?:有空|有时间|有名额|有位置|能约|可约|安排).{0,8}(?:今天|明天|后天|上午|下午|晚上|\d{1,2}点(?:半|左右)?)", compact)
    )


def _available_time_fact_supports_availability(item: dict[str, Any]) -> bool:
    if str(item.get("recommended_slot") or "").strip():
        return True
    backup_slots = item.get("backup_slots")
    if isinstance(backup_slots, list) and any(str(slot or "").strip() for slot in backup_slots):
        return True
    if item.get("target_time_available") is True:
        return True
    nearby = item.get("nearby_times")
    if isinstance(nearby, list) and nearby:
        return True
    slots = item.get("slots")
    if isinstance(slots, dict):
        return any(bool(value) for value in slots.values())
    if isinstance(slots, list):
        return bool(slots)
    return False


def _asserts_appointment_confirmed(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    matched = any(
        term in compact
        for term in (
            "已为您锁定",
            "已经为您锁定",
            "已锁定",
            "已安排好",
            "安排好了",
            "预约好了",
            "准时等您",
            "先留着",
            "帮你留着",
            "帮您留着",
            "给你留着",
            "给您留着",
            "留好",
            "预留",
            "帮你记上",
            "帮您记上",
            "先记上",
            "记着",
        )
    )
    if matched:
        return True
    if "安排" in compact and not any(term in compact for term in ("适合再安排", "确认适合再安排", "检测评估", "皮肤状态")):
        if re.search(r"按.{0,12}安排", compact) or re.search(r"帮[你您]按.{0,12}安排", compact):
            return True
    time_token = r"(?:今天|明天|后天|上午|下午|晚上|\d{1,2}(?:[:：]\d{2}|点(?:半)?))"
    if re.search(rf"(?:能帮[你您]?|可以帮[你您]?|帮[你您]?|给[你您]?).{{0,4}}留(?:下|住)?{time_token}", compact):
        return True
    return bool(re.search(r"锁.{0,8}(?:时段|时间|今天|明天|后天|上午|下午|晚上|\d{1,2}点)", compact))


def _has_appointment_confirmation_fact(state: dict[str, Any]) -> bool:
    if str(state.get("appointment_id") or "").strip() or str(state.get("appointment_time") or "").strip():
        return True
    request_context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    if str(request_context.get("appointment_id") or "").strip() or str(request_context.get("appointment_time") or "").strip():
        return True
    structured = _structured_facts(state)
    for item in structured.get("appointment_facts") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") in {"appointment_created", "appointment_confirmed", "appointment_record"}:
            if str(item.get("appointment_id") or item.get("appointment_time") or item.get("time") or "").strip():
                return True
    return False


def _promises_payment_entry(text: str) -> bool:
    compact = "".join(str(text or "").split())
    return any(
        term in compact
        for term in (
            "发入口",
            "发送入口",
            "重新发",
            "付款入口",
            "收款入口",
            "支付入口",
            "预约金入口",
            "报名入口",
            "入口还在",
            "入口还有效",
            "重发",
            "再发您",
            "再发你",
            "发报名入口",
            "发送报名入口",
            "现在为您发",
            "马上发您",
        )
    )


def _asks_over_limit_participant_confirmation(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact:
        return False
    if re.search(r"(?:一共|总共|共)?\d+人吗", compact):
        return True
    has_count_word = any(term in compact for term in ("几位", "几个人", "多少人", "人数", "一共几", "总共几", "共几", "实际到店"))
    has_confirmation_word = any(term in compact for term in ("确认", "核对", "先问", "先看", "先帮您看", "先帮您确认"))
    return has_count_word or (has_confirmation_word and "人" in compact)


def _mentions_over_limit_payment_amount(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if not compact:
        return False
    high_amount = r"(?:[5-9]\d|[1-9]\d{2,})"
    payment_terms = r"(?:预约金|定金|订金|锁名额|锁活动名额)"
    return bool(
        re.search(payment_terms + r".{0,10}" + high_amount + r"元", compact)
        or re.search(high_amount + r"元.{0,10}" + payment_terms, compact)
    )


def _promises_unfinished_lookup(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if re.search(r"(查|核对|找).{0,12}(档期|案例|参考)", compact):
        return True
    if any(term in compact for term in _unicode_unfinished_lookup_terms()):
        return True
    return any(
        term in compact
        for term in (
            "马上查",
            "马上核",
            "稍后给",
            "等下给",
            "待会给",
            "一会给",
            "帮您查一下",
            "帮您查",
            "帮您看一下",
            "帮您看档期",
            "帮您找一下",
            "帮您找同类",
            "帮您找案例",
            "帮您找参考",
        )
    )


def _unicode_unfinished_lookup_terms() -> tuple[str, ...]:
    return (
        "\u9a6c\u4e0a\u540c\u6b65",
        "\u7a0d\u540e\u540c\u6b65",
        "\u665a\u70b9\u540c\u6b65",
        "\u6838\u5bf9\u4e2d",
        "\u786e\u8ba4\u597d\u9a6c\u4e0a",
        "\u5e2e\u60a8\u786e\u8ba4\u597d",
        "\u5e2e\u60a8\u67e5\u4e00\u4e0b",
        "\u6211\u5e2e\u60a8\u67e5",
        "\u67e5\u5176\u4ed6\u65f6\u6bb5",
        "\u6838\u5bf9\u6863\u671f",
        "\u67e5\u6863\u671f",
        "\u770b\u6863\u671f",
        "\u53ef\u7ea6\u65f6\u95f4",
    )


def _promises_appointment_lookup(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if any(term in compact for term in _unicode_appointment_lookup_terms()):
        return True
    return any(
        term in compact
        for term in (
            "查档期",
            "查下档期",
            "核对档期",
            "看档期",
            "可约时间",
            "可预约时间",
            "可约名额",
            "名额时间",
        )
    ) or bool(
        re.search(r"(查|核对|看).{0,12}档期", compact)
    )


def _unicode_appointment_lookup_terms() -> tuple[str, ...]:
    return (
        "\u67e5\u6863\u671f",
        "\u67e5\u4e0b\u6863\u671f",
        "\u6838\u5bf9\u6863\u671f",
        "\u770b\u6863\u671f",
        "\u53ef\u7ea6\u65f6\u95f4",
        "\u53ef\u9884\u7ea6\u65f6\u95f4",
        "\u53ef\u7ea6\u540d\u989d",
        "\u540d\u989d\u65f6\u95f4",
    )


def _explains_previous_payment_entry(text: str) -> bool:
    return any(term in text for term in ("刚刚发的是", "刚才发的是", "前面发的是", "之前发的是"))


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


def message_content_text(content: Any) -> str:
    if isinstance(content, dict):
        if "amount" in content and set(content).issubset({"amount", "remark"}):
            amount = str(content.get("amount") or "10").strip() or "10"
            remark = str(content.get("remark") or "").strip()
            return f"payment_collection:{amount}{':' + remark if remark else ''}"
        for key in ("handoff_reason", "text", "url"):
            value = content.get(key)
            text = message_content_text(value)
            if text:
                return text
        return ""
    return str(content or "").strip()


def message_content_payment_collection(
    content: Any,
    *,
    state: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return payment_collection_content(content, state=state, messages=messages)


def message_content_store_id(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("store_id") or content.get("id") or "").strip()
    return str(content or "").strip()


def looks_like_image_url(content: str) -> bool:
    return bool(extract_image_url_from_text(content))


def extract_image_url_from_text(content: str) -> str:
    text = html.unescape(content.strip())
    img_match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
    if img_match:
        return html.unescape(img_match.group(1)).strip()
    markdown_match = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", text)
    if markdown_match:
        url = html.unescape(markdown_match.group(1)).strip()
        if is_image_url(url):
            return url
    url_match = re.search(r"https?://[^\s<>'\")]+", text)
    if url_match:
        url = html.unescape(url_match.group(0)).strip()
        if is_image_url(url):
            return url
    return ""


def strip_image_url_from_text(content: str, image_url: str) -> str:
    text = html.unescape(content.strip())
    text = re.sub(r"<img\s+[^>]*>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"!\[[^\]]*\]\(" + re.escape(image_url) + r"\)", "", text).strip()
    text = text.replace(image_url, "").strip()
    text = re.sub(r"\s+", " ", text).strip(" ，,。；;")
    return text


def is_image_url(text: str) -> bool:
    if not (text.startswith("http://") or text.startswith("https://")):
        return False
    if "\n" in text or " " in text:
        return False
    lower = text.lower()
    return any(
        marker in lower
        for marker in [".png", ".jpg", ".jpeg", ".webp", "filebiztype.biz_bot_dataset", "ocean-cloud-tos"]
    )
