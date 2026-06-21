from __future__ import annotations

import re
from typing import Any

from app.graph import reply_filters
from app.graph.nodes.common import looks_garbled_text, renumber_messages
from app.graph.nodes.memory_usage_policy import order_session_state
from app.graph.nodes.store_context import should_use_known_store_context, should_use_recent_store_fact_context
from app.graph.runtime_context import contextual_price_project
from app.graph.nodes.reply_validation import message_content_order_id, message_content_store_id, message_content_text
from app.graph.planner.runtime_plan import planner_handoff, planner_task_views
from app.graph.state import AgentState

MAX_TEXT_MESSAGES = 2
MAX_IMAGE_MESSAGES = 2

def postprocess_reply_messages(
    state: AgentState,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    state["postprocess_changed"] = False
    state["postprocess_reasons"] = []
    original_messages = [dict(message) for message in messages if isinstance(message, dict)]
    reasons: list[str] = []
    task_types = {
        str(view.get("type") or "").strip()
        for view in planner_task_views(state)
        if isinstance(view, dict) and str(view.get("type") or "").strip()
    }
    content_text = str(state.get("normalized_content") or "")
    conversation_history = state.get("conversation_history", [])
    recent_assistant = _recent_assistant_texts_for_dedupe(state)
    cleaned: list[dict[str, Any]] = []
    special_messages: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    text_count = 0
    image_count = 0

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "human_handoff":
            continue
        if message.get("type") == "book_order":
            order_id = message_content_order_id(message.get("content"))
            special_messages.append({"type": "book_order", "order": 0, "content": {"order_id": order_id}})
            continue
        if message.get("type") == "store_address":
            store_id = message_content_store_id(message.get("content"))
            special_messages.append({"type": "store_address", "order": 0, "content": {"store_id": store_id}})
            continue

        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = message_content_text(message.get("content"))
        if not content:
            continue
        if looks_garbled_text(content) or "\ufffd" in content:
            reasons.append("garbled_removed")
            continue

        if msg_type == "text":
            if text_count >= MAX_TEXT_MESSAGES:
                reasons.append("text_limit")
                continue
            content = content.strip()
            if _contains_blocked_placeholder_url(content):
                reasons.append("placeholder_url_removed")
                continue
            if _contains_fake_placeholder_text(content):
                reasons.append("placeholder_text_removed")
                continue
            if _has_unbacked_case_image_promise(state, content):
                reasons.append("unbacked_case_image_promise_blocked")
                continue
            if _has_unbacked_store_claim_text(state, content):
                reasons.append("unbacked_store_claim_blocked")
                continue
            fabricated_store_names = _unsupported_store_names_from_text(state, content)
            if fabricated_store_names:
                reasons.append("fabricated_store_name_blocked")
                continue
            sanitized_sales = _sanitize_sales_close_risk_terms(content)
            if sanitized_sales != content:
                content = sanitized_sales
                reasons.append("sales_close_terms_sanitized")
            without_known_slot_question = _remove_known_slot_requestion(state, content)
            if without_known_slot_question != content:
                content = without_known_slot_question
                reasons.append("known_slot_requestion_removed")
            normalized = re.sub(r"\s+", "", content)
            if not normalized or normalized in seen_text:
                reasons.append("dedupe_or_limit")
                continue
            if _too_similar_to_recent_reply(normalized, recent_assistant):
                reasons.append("recent_reply_dedupe")
                continue
            seen_text.add(normalized)
        elif msg_type == "image":
            if image_count >= MAX_IMAGE_MESSAGES:
                reasons.append("image_limit")
                continue
            if content not in _case_image_urls_from_state(state):
                reasons.append("invalid_image_url_removed")
                continue

        payload: Any = {"text": content} if msg_type == "text" else content
        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": payload})
        if msg_type == "text":
            text_count += 1
        elif msg_type == "image":
            image_count += 1

    if cleaned:
        before_sensitive = _message_fingerprint(cleaned)
        cleaned = reply_filters.sanitize_sensitive_reply_content(
            cleaned,
            task_types=task_types,
            normalized_content=content_text,
            conversation_history=conversation_history,
            contextual_price_project=contextual_price_project(state),
        )
        if _message_fingerprint(cleaned) != before_sensitive:
            reasons.append("sensitive_sanitized")

        before_visible = _message_fingerprint(cleaned)
        cleaned = reply_filters.sanitize_customer_visible_messages(cleaned)
        if _message_fingerprint(cleaned) != before_visible:
            reasons.append("customer_visible_sanitized")

        if not _has_visible_image(cleaned):
            case_image = _case_image_message_for_state(state, cleaned)
            if case_image:
                cleaned.append(case_image)
                reasons.append("case_image_appended")

        cleaned = renumber_messages(cleaned)

    handoff_message = _handoff_message_for_state(state)
    if handoff_message:
        cleaned.append({"type": "human_handoff", "order": len(cleaned) + 1, "content": handoff_message})
        reasons.append("handoff_appended")
    else:
        store_address_message = _store_address_message_for_state(state, special_messages)
        if store_address_message:
            cleaned.append(store_address_message)
            reasons.append("store_address_appended")
        book_order_message = _book_order_message_for_state(state, special_messages)
        if book_order_message:
            cleaned.append(book_order_message)
            reasons.append("book_order_appended")

    if not cleaned:
        state["postprocess_changed"] = bool(original_messages)
        if original_messages:
            state["postprocess_reasons"] = _unique_reasons(reasons + ["all_messages_removed"])
        else:
            state["postprocess_reasons"] = []
        return []

    cleaned = renumber_messages(cleaned)
    changed = _message_fingerprint(cleaned) != _message_fingerprint(original_messages)
    state["postprocess_changed"] = changed
    state["postprocess_reasons"] = _unique_reasons(reasons) if changed else []
    return cleaned


def _handoff_message_for_state(state: AgentState) -> dict[str, Any] | None:
    handoff = planner_handoff(state)
    if handoff.get("needed"):
        reason = str(handoff.get("reason") or "").strip() or "当前问题需要专业同事继续协助核对"
        return {"handoff_reason": reason}

    assist_reason = _professional_assist_reason(state)
    if not assist_reason:
        assist_reason = _available_time_failure_reason(state)
    if not assist_reason:
        return None
    reason = assist_reason or "当前问题需要专业同事继续协助核对"
    return {"handoff_reason": reason}


def _available_time_failure_reason(state: AgentState) -> str:
    structured = _structured_facts_from_state(state)
    facts = structured.get("appointment_facts")
    if not isinstance(facts, list):
        fact_envelope = state.get("fact_envelope")
        if isinstance(fact_envelope, dict):
            facts = fact_envelope.get("appointment_facts")
    if not isinstance(facts, list):
        return ""
    for item in facts:
        if not isinstance(item, dict) or item.get("type") != "available_time":
            continue
        status = str(item.get("status") or "").strip()
        missing = [str(value) for value in (item.get("missing") or [])]
        if status in {"error", "no_slots"}:
            return "档期查询暂时无法确认，需要专业同事继续核对可约时间"
        if status == "missing_info" and "store_id" not in missing and "date" not in missing:
            return "档期查询暂时无法确认，需要专业同事继续核对可约时间"
    return ""


def _book_order_message_for_state(
    state: AgentState,
    model_messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not _has_confirmed_store_for_booking(state):
        return None
    order_id = _trusted_book_order_id_from_state(state)
    if not order_id:
        return None
    return {"type": "book_order", "order": 0, "content": {"order_id": order_id}}


def _trusted_book_order_id_from_state(state: AgentState) -> str:
    tool_results = state.get("tool_results")
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if not isinstance(opening, dict) or opening.get("status") not in {"created", "dry_run_created", "reused_open_order"}:
        return ""
    order_id = str(opening.get("order_id") or "").strip()
    if not order_id:
        push = opening.get("appointment_push")
        if isinstance(push, dict):
            order_id = str(push.get("order_id") or "").strip()
    return order_id


def _store_address_message_for_state(
    state: AgentState,
    model_messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    intents = [
        message
        for message in model_messages
        if isinstance(message, dict) and message.get("type") == "store_address"
    ]
    if not intents and not _should_auto_append_store_address(state):
        return None
    real_store_ids = _real_store_ids_from_state(state)
    if not real_store_ids:
        return None
    requested_id = ""
    for message in intents:
        requested_id = message_content_store_id(message.get("content"))
        if requested_id:
            break
    store_id = requested_id if requested_id in real_store_ids else _preferred_store_id_from_state(state)
    if not store_id:
        store_id = real_store_ids[0]
    if not _should_send_store_address_now(state, store_id):
        return None
    return {"type": "store_address", "order": 0, "content": {"store_id": store_id}}


def _real_store_ids_from_state(state: AgentState) -> list[str]:
    if not _has_current_platform_store_facts(state):
        return []
    structured = _structured_facts_from_state(state)
    ids: list[str] = []
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        store_id = _store_id_from_fact(recommended)
        if store_id:
            ids.append(store_id)
    stores = structured.get("store_facts")
    if isinstance(stores, list):
        for item in stores:
            if not isinstance(item, dict):
                continue
            store_id = _store_id_from_fact(item)
            if store_id:
                ids.append(store_id)
    return list(dict.fromkeys(ids))


def _preferred_store_id_from_state(state: AgentState) -> str:
    if not _has_current_platform_store_facts(state):
        return ""
    structured = _structured_facts_from_state(state)
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        store_id = _store_id_from_fact(recommended)
        if store_id:
            return store_id
    stores = structured.get("store_facts")
    if isinstance(stores, list):
        for item in stores:
            if not isinstance(item, dict):
                continue
            store_id = _store_id_from_fact(item)
            if store_id:
                return store_id
    ids = _real_store_ids_from_state(state)
    return ids[0] if ids else ""


def _store_id_from_fact(value: dict[str, Any]) -> str:
    return str(value.get("store_id") or value.get("id") or "").strip()


def _should_auto_append_store_address(state: AgentState) -> bool:
    if _trusted_book_order_id_from_state(state) and not _explicit_store_address_request(state):
        return False
    structured = _structured_facts_from_state(state)
    status = structured.get("store_lookup_status") if isinstance(structured.get("store_lookup_status"), dict) else {}
    recommended = structured.get("recommended_store") if isinstance(structured.get("recommended_store"), dict) else {}
    if isinstance(status, dict) and isinstance(recommended, dict):
        if str(status.get("data_authority") or "").strip().lower() == "platform":
            if not bool(status.get("needs_area_or_landmark")) and not bool(status.get("no_store_match_confirmed")):
                if bool(recommended.get("has_detail")) and _store_id_from_fact(recommended):
                    granularity = str(status.get("location_granularity") or "").strip()
                    if granularity in {"area_or_landmark", "store_name"}:
                        return True
                    if _looks_like_store_card_turn(state):
                        return True
    return False


def _explicit_store_address_request(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or "").strip()
    if not content:
        return False
    explicit_terms = (
        "地址",
        "定位",
        "位置",
        "导航",
        "路线",
        "怎么去",
        "停车",
        "找不到",
        "再发",
        "重新发",
    )
    return any(term in content for term in explicit_terms)


def _should_send_store_address_now(state: AgentState, store_id: str) -> bool:
    if not store_id:
        return False
    recent_ids = _recent_store_card_ids_from_history(state)
    if store_id not in recent_ids:
        return True
    content = str(state.get("normalized_content") or "").strip()
    if not content:
        return False
    explicit_resend_terms = (
        "地址",
        "定位",
        "位置发",
        "发位置",
        "发定位",
        "发地址",
        "导航",
        "路线",
        "怎么去",
        "停车",
        "找不到",
        "忘了",
        "再发",
        "重新发",
    )
    if any(term in content for term in explicit_resend_terms):
        return True
    return False


def _looks_like_store_card_turn(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or "").strip()
    if not content:
        return False
    direct_terms = (
        "地址",
        "位置",
        "导航",
        "路线",
        "怎么去",
        "哪家",
        "最近",
        "离我近",
        "附近",
        "机场",
        "高铁",
        "地铁",
        "科技园",
        "停车",
        "营业时间",
        "发我",
        "发给我",
    )
    if any(term in content for term in direct_terms):
        return True
    return should_use_known_store_context(content) or should_use_recent_store_fact_context(content, state)


def _recent_store_card_ids_from_history(state: AgentState) -> list[str]:
    ids: list[str] = []
    for item in state.get("conversation_history") or []:
        if isinstance(item, str):
            store_id = _store_id_from_history_content(item)
            if store_id:
                ids.append(store_id)
            continue
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("direction") or "").lower()
        if role not in {"assistant", "staff", "bot"}:
            continue
        msg_type = str(item.get("type") or item.get("msgtype") or "").lower()
        if msg_type == "store_address":
            store_id = _store_id_from_history_content(item.get("content"))
            if store_id:
                ids.append(store_id)
                continue
        store_id = _store_id_from_history_content(item.get("content"))
        if store_id:
            ids.append(store_id)
    return list(dict.fromkeys(ids))


def _store_id_from_history_content(content: Any) -> str:
    if isinstance(content, dict):
        return str(content.get("store_id") or "").strip()
    text = str(content or "").strip()
    if not text:
        return ""
    match = re.search(r'["\']store_id["\']\s*:\s*["\']?(?P<store_id>\d+)["\']?', text)
    if match:
        return str(match.group("store_id") or "").strip()
    match = re.search(r"(?:store_address|门店卡片)\s*[:：]\s*(?P<store_id>\d+)", text, flags=re.IGNORECASE)
    if match:
        return str(match.group("store_id") or "").strip()
    return ""


def _has_confirmed_store_for_booking(state: AgentState) -> bool:
    for key in ("confirmed_store_id", "confirmed_store_name", "store_id", "store_name"):
        if str(state.get(key) or "").strip():
            return True
    session = order_session_state(state)
    if str(session.get("confirmed_store_id") or session.get("confirmed_store_name") or "").strip():
        return True
    appointment = state.get("appointment_cache")
    if isinstance(appointment, dict):
        if str(appointment.get("store_id") or appointment.get("store_name") or "").strip():
            return True
    tool_results = state.get("tool_results")
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if isinstance(opening, dict) and str(opening.get("store_id") or opening.get("store_name") or "").strip():
        return True
    structured = _structured_facts_from_state(state)
    latest = _first_dict(structured.get("appointment_facts"))
    if str(latest.get("store_id") or latest.get("store_name") or "").strip():
        return True
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict) and str(
        recommended.get("store_id") or recommended.get("id") or recommended.get("name") or ""
    ).strip():
        return True
    stores = structured.get("store_facts")
    if isinstance(stores, list):
        for item in stores:
            if not isinstance(item, dict):
                continue
            if str(item.get("store_id") or item.get("id") or item.get("name") or "").strip():
                return True
    return False


def _sanitize_sales_close_risk_terms(text: str) -> str:
    content = str(text or "")
    replacements = {
        "锁定死": "先保留",
        "绝对没有任何强制加价": "费用会提前说清楚",
        "绝对没有": "费用会提前说清楚",
        "无任何不良反应": "整体比较温和",
        "没有不良反应": "整体比较温和",
        "保证效果": "很多客户反馈不错",
        "包效果": "很多客户反馈不错",
        "广告是错的": "以现在周年庆活动为准",
        "直接输密码": "按页面提示操作",
        "无需授权": "按页面提示操作",
        "不用确认": "按页面提示操作",
        "自动扣款": "按页面提示操作",
        "老客价": "这次给您核到的价格",
        "老客": "您这边",
        "新客": "您这边",
        "订单记录": "系统信息",
        "上次订单": "系统信息",
    }
    for source, target in replacements.items():
        content = content.replace(source, target)
    content = re.sub(r"(仅剩|只剩|剩下|最后)\s*[0-9一二三四五六七八九十]+\s*个名额", "名额有限", content)
    return content


def _remove_known_slot_requestion(state: AgentState, text: str) -> str:
    session = order_session_state(state)
    if not session:
        return text
    parts = re.split(r"(?<=[。！？!?；;])", str(text or "").strip())
    if not parts:
        return text
    kept: list[str] = []
    changed = False
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        if _asks_known_order_slot(session, chunk):
            changed = True
            continue
        kept.append(chunk)
    if not changed:
        return text
    result = "".join(kept).strip()
    return result or text


def _asks_known_order_slot(session: dict[str, object], text: str) -> bool:
    content = re.sub(r"\s+", "", str(text or ""))
    if not content or ("？" not in content and "?" not in content and not _looks_like_slot_request(content)):
        return False
    if session.get("city") and any(term in content for term in ("哪个城市", "哪座城市", "所在城市", "您在哪个市", "你在哪个市", "在哪个城市")):
        return True
    if session.get("area_or_landmark") and any(
        term in content
        for term in ("哪个区", "哪一区", "附近什么地标", "附近地标", "所在区域", "您在哪个区", "你在哪个区", "具体位置")
    ):
        return True
    if (session.get("confirmed_store_name") or session.get("confirmed_store_id")) and any(
        term in content
        for term in ("哪家门店", "哪个门店", "哪一家", "选哪家", "确认哪家店", "方便哪家")
    ):
        return True
    if _has_precise_visit_time(session) and any(
        term in content
        for term in ("哪天", "什么时候", "几点", "上午还是下午", "什么时间", "到店时间", "方便时间")
    ):
        return True
    return False


def _has_precise_visit_time(session: dict[str, object]) -> bool:
    value = re.sub(r"\s+", "", str(session.get("visit_time") or session.get("appointment_time") or ""))
    if not value:
        return False
    if re.search(r"\d{1,2}[:：]\d{2}", value):
        return True
    if re.search(r"(上午|下午|中午|晚上)?\d{1,2}点(半|多|左右)?", value):
        return True
    # Coarse ranges like "明天上午" or "周六下午" still need a concrete arrival time.
    return False


def _looks_like_slot_request(text: str) -> bool:
    return any(
        term in text
        for term in (
            "告诉我您在哪",
            "发我位置",
            "发一下位置",
            "发一下附近地标",
            "告诉我附近地标",
            "说下附近地标",
            "附近地标",
            "说下位置",
            "告诉我时间",
            "发我时间",
            "确认一下门店",
        )
    )


def _has_visible_image(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(message, dict) and message.get("type") == "image" for message in messages)


def _case_image_message_for_state(state: AgentState, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not _looks_like_case_or_effect_turn(state):
        return None
    recent_urls = set(_recent_image_urls_from_state(state))
    for image_url in _case_image_urls_from_state(state):
        if image_url and image_url not in recent_urls:
            return {"type": "image", "order": len(messages) + 1, "content": image_url}
    return None


def _looks_like_case_or_effect_turn(state: AgentState) -> bool:
    text = str(state.get("normalized_content") or "")
    if any(
        term in text
        for term in (
            "效果图",
            "案例",
            "对比图",
            "做完效果",
            "恢复后",
            "客户做完",
            "图片上的客户",
            "客户做完后的效果",
            "看看效果",
            "发个效果",
            "效果对比",
        )
    ):
        return True
    for view in planner_task_views(state):
        if not isinstance(view, dict):
            continue
        joined = " ".join(str(view.get(key) or "").lower() for key in ("type", "subtype", "policy_hint", "scene", "subflow"))
        if (
            "case" in joined
            or "effect" in joined
            or "show_case" in joined
            or any(term in joined for term in ("案例", "效果", "对比", "方法确认"))
        ):
            return True
    return False


def _case_image_urls_from_state(state: AgentState) -> list[str]:
    urls: list[str] = []
    for case in _case_facts_from_state(state):
        if not isinstance(case, dict):
            continue
        image_url = str(case.get("image_url") or "").strip()
        if _is_usable_case_image_url(image_url):
            urls.append(image_url)
    return list(dict.fromkeys(urls))


def _is_usable_case_image_url(image_url: str) -> bool:
    if not image_url or not image_url.startswith(("http://", "https://")):
        return False
    lowered = image_url.lower()
    blocked_hosts = ("example.com", "example.cn", "localhost", "127.0.0.1", "picsum.photos", "placehold.co")
    if any(host in lowered for host in blocked_hosts):
        return False
    return True


def _contains_blocked_placeholder_url(text: str) -> bool:
    lowered = (text or "").lower()
    return any(host in lowered for host in ("example.com", "example.cn", "localhost", "127.0.0.1", "picsum.photos", "placehold.co"))


def _contains_fake_placeholder_text(text: str) -> bool:
    content = str(text or "")
    if re.search(r"[XxＸｘ]{2,}", content):
        return True
    return any(term in content for term in ("某某地址", "某某路", "某某大厦", "测试地址"))


def _has_unbacked_case_image_promise(state: AgentState, text: str) -> bool:
    if _case_image_urls_from_state(state):
        return False
    content = str(text or "")
    send_terms = (
        "发你看",
        "发您看",
        "发图",
        "发个图",
        "发案例",
        "发个案例",
        "案例给您",
        "案例给你",
        "给您参考",
        "给你参考",
        "参考下",
    )
    case_terms = ("案例", "效果", "图片", "图", "对比", "同类情况", "类似情况", "改善")
    if not any(term in content for term in send_terms):
        return False
    if not any(term in content for term in case_terms):
        return False
    return True


def _has_unbacked_store_claim_text(state: AgentState, text: str) -> bool:
    if _has_current_platform_store_facts(state) or _has_trusted_confirmed_store_state(state):
        return False
    content = str(text or "").strip()
    if not content:
        return False
    claim_terms = (
        "有门店",
        "有店",
        "附近有门店",
        "附近有店",
        "地址发您",
        "地址发你",
        "位置发您",
        "位置发你",
        "定位发您",
        "定位发你",
        "门店位置发",
        "门店地址发",
        "发您地址",
        "发你地址",
        "发您位置",
        "发你位置",
        "发您定位",
        "发你定位",
        "推荐最近",
        "最近门店",
        "最近的门店",
        "最近的是",
        "离您最近",
        "离你最近",
        "离您更方便",
        "离你更方便",
        "哪家离您更方便",
        "哪家离你更方便",
        "哪家更方便",
        "具体哪家",
    )
    if any(term in content for term in claim_terms):
        return True
    if re.search(r"(?:有|查到|推荐|匹配到).{0,12}(?:门店|店)", content):
        return True
    if re.search(r"(?:门店|店).{0,12}(?:地址|位置|定位).{0,8}(?:发|给)", content):
        return True
    return False


def _recent_assistant_texts_for_dedupe(state: AgentState) -> list[str]:
    texts: list[str] = []
    for item in (state.get("conversation_history") or [])[-8:]:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("direction") or "").lower()
            if role not in {"assistant", "staff", "bot"}:
                continue
            content = item.get("content")
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
        else:
            raw = str(item or "").strip()
            if not raw.startswith(("小贝：", "客服：", "AI回复：")):
                continue
            text = raw.split("：", 1)[-1].strip()
        normalized = re.sub(r"\s+", "", text)
        if normalized:
            texts.append(normalized)
    return texts[-4:]


def _too_similar_to_recent_reply(normalized: str, recent_replies: list[str]) -> bool:
    if len(normalized) < 18:
        return False
    for recent in recent_replies:
        if len(recent) < 18:
            continue
        # Only remove exact repeats here. Similar-but-not-identical wording can
        # be intentional sales follow-up, and broad containment/overlap checks
        # were deleting valid model replies after multi-turn context changed.
        if normalized == recent:
            return True
    return False


def _case_facts_from_state(state: AgentState) -> list[dict[str, Any]]:
    sources: list[Any] = []
    structured_facts = state.get("structured_facts")
    if isinstance(structured_facts, dict):
        sources.append(structured_facts.get("case_facts"))
    fact_envelope = state.get("fact_envelope")
    if isinstance(fact_envelope, dict):
        envelope_structured = fact_envelope.get("structured_facts")
        if isinstance(envelope_structured, dict):
            sources.append(envelope_structured.get("case_facts"))
    results: list[dict[str, Any]] = []
    for source in sources:
        if isinstance(source, list):
            results.extend(item for item in source if isinstance(item, dict))
    return results


def _structured_facts_from_state(state: AgentState) -> dict[str, Any]:
    structured = state.get("structured_facts")
    if isinstance(structured, dict):
        return structured
    fact_envelope = state.get("fact_envelope")
    if isinstance(fact_envelope, dict) and isinstance(fact_envelope.get("structured_facts"), dict):
        return fact_envelope["structured_facts"]
    return {}


def _has_current_platform_store_facts(state: AgentState) -> bool:
    structured = _structured_facts_from_state(state)
    status = structured.get("store_lookup_status")
    if not isinstance(status, dict):
        return False
    if str(status.get("data_authority") or "").strip().lower() != "platform":
        return False
    if bool(status.get("needs_area_or_landmark")) or bool(status.get("no_store_match_confirmed")):
        return False
    return bool(_current_store_facts_from_state(state))


def _current_store_facts_from_state(state: AgentState) -> list[dict[str, Any]]:
    structured = _structured_facts_from_state(state)
    facts: list[dict[str, Any]] = []
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        facts.append(recommended)
    stores = structured.get("store_facts")
    if isinstance(stores, list):
        facts.extend(item for item in stores if isinstance(item, dict))
    return facts


def _unsupported_store_names_from_text(state: AgentState, text: str) -> list[str]:
    real_names = _real_store_names_from_state(state)
    has_trusted_store = _has_current_platform_store_facts(state) or _has_trusted_confirmed_store_state(state)
    unsupported: list[str] = []
    for candidate in _candidate_store_names(text):
        if _is_generic_store_reference(candidate):
            continue
        if not real_names or not has_trusted_store:
            unsupported.append(candidate)
            continue
        if _store_name_is_supported(candidate, real_names):
            continue
        unsupported.append(candidate)
    return list(dict.fromkeys(unsupported))


def _real_store_names_from_state(state: AgentState) -> list[str]:
    structured = _structured_facts_from_state(state)
    names: list[str] = []
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        name = str(recommended.get("name") or "").strip()
        if name:
            names.append(name)
    stores = structured.get("store_facts")
    if isinstance(stores, list):
        for item in stores:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    session = order_session_state(state)
    confirmed_name = str(session.get("confirmed_store_name") or "").strip()
    confirmed_id = str(session.get("confirmed_store_id") or "").strip()
    if confirmed_name and confirmed_id:
        names.append(confirmed_name)
    return list(dict.fromkeys(names))


def _has_trusted_confirmed_store_state(state: AgentState) -> bool:
    session = order_session_state(state)
    if str(session.get("confirmed_store_id") or "").strip():
        return True
    appointment = state.get("appointment_cache") if isinstance(state.get("appointment_cache"), dict) else {}
    if str(appointment.get("store_id") or "").strip():
        return True
    current_store = _first_dict(_current_store_facts_from_state(state))
    return bool(_store_id_from_fact(current_store))


def _candidate_store_names(text: str) -> list[str]:
    content = str(text or "")
    candidates: list[str] = []
    for match in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}(?:店|二店))", content):
        value = match.group(1).strip("，。！？：:；;、（）()[]【】")
        if value:
            candidates.append(value)
    return candidates


def _is_generic_store_reference(candidate: str) -> bool:
    value = str(candidate or "").strip()
    if not value:
        return True
    generic = {
        "门店",
        "到店",
        "店里",
        "店内",
        "店铺",
        "本店",
        "这家店",
        "那家店",
        "哪家店",
        "附近店",
        "最近店",
        "意向店",
        "预约店",
        "活动店",
    }
    if value in generic or value.endswith(("到店", "门店", "店里", "店内")):
        return True
    return any(term in value for term in ("哪家店", "哪个店", "这家店", "那家店", "到店", "门店", "店里", "店内"))


def _store_name_is_supported(candidate: str, real_names: list[str]) -> bool:
    value = str(candidate or "").strip()
    if not value:
        return True
    for name in {str(name or "").strip() for name in real_names if str(name or "").strip()}:
        if value == name or value.endswith(name) or name.endswith(value):
            return True
    return False


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    if isinstance(value, dict):
        return value
    return {}


def _recent_image_urls_from_state(state: AgentState) -> list[str]:
    urls: list[str] = []
    for key in ("recent_image_urls",):
        value = state.get(key)
        if isinstance(value, list):
            urls.extend(str(item).strip() for item in value if str(item).strip())
    for message in state.get("conversation_history") or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("type") or message.get("msgtype") or "").lower() not in {"image", "图片"}:
            continue
        content = message.get("content")
        if isinstance(content, dict):
            url = str(content.get("url") or content.get("image_url") or "").strip()
        else:
            url = str(content or "").strip()
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def _professional_assist_reason(state: AgentState) -> str:
    content = str(state.get("normalized_content") or "")
    for source in _professional_assist_sources(state):
        if not isinstance(source, dict) or str(source.get("status") or "").strip() != "requested":
            continue
        reason = str(source.get("reason") or source.get("required_internal_action") or "").strip()
        if reason and _is_real_professional_assist_reason(reason, content):
            return reason[:180]
    return ""


def _is_real_professional_assist_reason(reason: str, content: str) -> bool:
    text = f"{reason} {content}"
    if any(
        term in text
        for term in (
            "普通售前",
            "售前顾虑",
            "销售顾虑",
            "家属反对",
            "老公说",
            "怕被骗",
            "靠不靠谱",
            "不应交给专业",
            "不需要专业",
        )
    ):
        return any(term in text for term in ("退款", "退钱", "投诉", "多收", "付款异常", "订单异常", "红肿", "疼痛", "渗液"))
    return any(
        term in text
        for term in (
            "退款",
            "退钱",
            "投诉",
            "维权",
            "骗钱",
            "多收",
            "乱扣",
            "付款异常",
            "订单异常",
            "退款状态",
            "订单状态",
            "严重不适",
            "流脓",
            "发热",
            "剧痛",
            "感染",
            "孕",
            "哺乳",
            "未成年",
            "人工",
            "真人",
            "换人",
            "专业同事",
        )
    )


def _professional_assist_sources(state: AgentState) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []

    structured_facts = state.get("structured_facts")
    if isinstance(structured_facts, dict) and isinstance(structured_facts.get("professional_assist"), dict):
        sources.append(structured_facts["professional_assist"])

    fact_envelope = state.get("fact_envelope")
    if isinstance(fact_envelope, dict):
        envelope_structured = fact_envelope.get("structured_facts")
        if isinstance(envelope_structured, dict) and isinstance(envelope_structured.get("professional_assist"), dict):
            sources.append(envelope_structured["professional_assist"])

    tool_results = state.get("tool_results")
    if isinstance(tool_results, dict) and isinstance(tool_results.get("professional_assist"), dict):
        sources.append(tool_results["professional_assist"])

    return sources


def _message_fingerprint(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    fingerprint: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "")
        content = message.get("content")
        if isinstance(content, dict):
            text = str(
                content.get("text")
                or content.get("url")
                or content.get("handoff_reason")
                or content.get("order_id")
                or content.get("store_id")
                or ""
            ).strip()
        else:
            text = str(content or "").strip()
        fingerprint.append((message_type, text))
    return fingerprint


def _unique_reasons(reasons: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        ordered.append(reason)
    return ordered
