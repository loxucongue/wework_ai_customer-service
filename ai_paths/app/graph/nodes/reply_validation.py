from __future__ import annotations

import html
import re
from difflib import SequenceMatcher
from typing import Any

from app.graph.nodes.common import renumber_messages
from app.graph.nodes.contextual_short_message import is_contextual_short_message

VISIBLE_MESSAGE_TYPES = {"text", "image", "payment_collection", "store_address"}
ALLOWED_MESSAGE_TYPES = {"text", "image", "human_handoff", "payment_collection", "store_address"}


def validated_model_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("reply_messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Model JSON missing reply_messages")
    result: list[dict[str, Any]] = []
    visible_count = 0
    has_handoff = False
    for item in messages:
        if not isinstance(item, dict):
            continue
        msg_type = item.get("type") if item.get("type") in ALLOWED_MESSAGE_TYPES else "text"
        if msg_type == "human_handoff":
            if has_handoff:
                continue
            handoff_reason = message_content_text(item.get("content"))
            if not handoff_reason:
                continue
            result.append(
                {
                    "type": "human_handoff",
                    "order": len(result) + 1,
                    "content": {"handoff_reason": handoff_reason},
                }
            )
            has_handoff = True
            continue
        if msg_type == "payment_collection":
            if visible_count >= 3:
                continue
            result.append(
                {
                    "type": "payment_collection",
                    "order": len(result) + 1,
                    "content": message_content_payment_collection(item.get("content")),
                }
            )
            visible_count += 1
            continue
        if msg_type == "store_address":
            if visible_count >= 3:
                continue
            store_id = message_content_store_id(item.get("content"))
            if not store_id:
                continue
            result.append({"type": "store_address", "order": len(result) + 1, "content": {"store_id": store_id}})
            visible_count += 1
            continue
        if visible_count >= 3:
            continue
        content = message_content_text(item.get("content"))
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
    return renumber_messages(result)


def validate_reply_consistency(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    _validate_payment_collection_consistency(messages, state)
    _validate_store_address_message_facts(messages, state)
    _validate_appointment_time_facts(messages, state)
    _validate_two_text_rhythm(messages, state)
    _validate_repeat_similarity(messages, state)
    _validate_fact_boundaries(messages, state)


def debug_message_contents(messages: list[dict[str, Any]]) -> list[str]:
    return [message_content_text(message.get("content"))[:240] for message in messages[:4] if isinstance(message, dict)]


def _validate_payment_collection_consistency(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if str(state.get("planner_decision") or "") == "no_reply":
        return
    has_payment = any(str(item.get("type") or "") == "payment_collection" for item in messages if isinstance(item, dict))
    text = _combined_text(messages)
    needs_payment = False
    if not _explains_previous_payment_entry(text):
        needs_payment = (
            str(state.get("conversion_stage") or "") == "deposit_push"
            or str(state.get("next_step") or "") == "send_deposit"
            or _promises_payment_entry(text)
        )
    if needs_payment and not has_payment:
        raise ValueError("payment_collection_required_when_reply_promises_payment_entry")


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


def _validate_appointment_time_facts(messages: list[dict[str, Any]], state: dict[str, Any]) -> None:
    text = _combined_text(messages)
    if not text or not _asserts_time_available(text):
        return
    appointment_facts = _structured_facts(state).get("appointment_facts")
    if not isinstance(appointment_facts, list):
        return
    available_time_facts = [
        item for item in appointment_facts if isinstance(item, dict) and str(item.get("type") or "") == "available_time"
    ]
    if not available_time_facts:
        return
    if not any(_available_time_fact_supports_availability(item) for item in available_time_facts):
        raise ValueError("available_time_fact_required")


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
    has_distance = bool(recommended_store.get("distance_km") or recommended_store.get("distance_text")) or recommended_store.get("reason") == "distance_calculate_rank_1"
    if _asserts_parking(text) and not has_parking:
        raise ValueError("parking_fact_required")
    if _asserts_business_hours(text) and not has_hours:
        raise ValueError("business_hours_fact_required")
    if _asserts_address(text) and not has_store_detail:
        raise ValueError("store_address_fact_required")
    if _asserts_distance(text) and not has_distance:
        raise ValueError("distance_fact_required")


def _combined_text(messages: list[dict[str, Any]]) -> str:
    return " ".join(
        message_content_text(item.get("content"))
        for item in messages
        if isinstance(item, dict) and str(item.get("type") or "text") == "text"
    )


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
    return any(term in text for term in ("地址是", "地址在", "位于", "路", "号")) and any(term in text for term in ("门店", "店", "导航", "地址"))


def _asserts_distance(text: str) -> bool:
    return bool(re.search(r"\d+(?:\.\d+)?\s*(?:公里|km|KM|分钟)", text)) or any(term in text for term in ("最近的是", "离您最近", "距离最近"))


def _asserts_time_available(text: str) -> bool:
    return any(term in text for term in ("有空", "有时间", "可以约", "能约", "可以预约", "能预约", "有名额", "有位置"))


def _available_time_fact_supports_availability(item: dict[str, Any]) -> bool:
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


def _promises_payment_entry(text: str) -> bool:
    return any(term in text for term in ("发入口", "重新发", "付款入口", "收款入口", "支付入口", "现在为您发", "马上发您"))


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


def message_content_payment_collection(content: Any) -> dict[str, Any]:
    remark = ""
    if isinstance(content, dict):
        remark = str(content.get("remark") or "").strip()
    return {"amount": 10, "remark": remark}


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
