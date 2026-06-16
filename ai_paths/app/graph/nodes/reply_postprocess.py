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

BOOK_ORDER_TRIGGER_TERMS = (
    "登记",
    "报名",
    "先交10",
    "交10",
    "付预约金",
    "预约金",
    "先约一下",
    "先帮我约",
    "帮我登记",
    "帮我安排",
    "先定一下",
)


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
            if text_count >= 2:
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
            if image_count >= 1:
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
        state["postprocess_reasons"] = ["all_messages_removed"] if original_messages else []
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
        return None
    reason = assist_reason or "当前问题需要专业同事继续协助核对"
    return {"handoff_reason": reason}


def _book_order_message_for_state(
    state: AgentState,
    model_messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not any(isinstance(message, dict) and message.get("type") == "book_order" for message in model_messages):
        if not _should_auto_append_book_order(state):
            return None
    if not _has_confirmed_store_for_booking(state):
        return None
    order_id = _trusted_book_order_id_from_state(state)
    if not order_id:
        return None
    return {"type": "book_order", "order": 0, "content": {"order_id": order_id}}


def _should_auto_append_book_order(state: AgentState) -> bool:
    content = str(state.get("normalized_content") or state.get("content") or "").strip()
    if not content:
        return False
    if not any(term in content for term in BOOK_ORDER_TRIGGER_TERMS):
        return False
    task_types = {
        str(view.get("type") or "").strip()
        for view in planner_task_views(state)
        if isinstance(view, dict) and str(view.get("type") or "").strip()
    }
    if task_types and not task_types.intersection({"appointment", "appointment_create", "signup_close", "price_close"}):
        return False
    return True


def _trusted_book_order_id_from_state(state: AgentState) -> str:
    tool_results = state.get("tool_results")
    opening = tool_results.get("appointment_opening") if isinstance(tool_results, dict) else {}
    if not isinstance(opening, dict) or opening.get("status") not in {"created", "dry_run_created"}:
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
    return {"type": "store_address", "order": 0, "content": {"store_id": store_id}}


def _real_store_ids_from_state(state: AgentState) -> list[str]:
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
    ids.extend(_recent_store_card_ids_from_history(state))
    return list(dict.fromkeys(ids))


def _preferred_store_id_from_state(state: AgentState) -> str:
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
    recent_ids = _recent_store_card_ids_from_history(state)
    if recent_ids:
        return recent_ids[-1]
    ids = _real_store_ids_from_state(state)
    return ids[0] if ids else ""


def _store_id_from_fact(value: dict[str, Any]) -> str:
    return str(value.get("store_id") or value.get("id") or "").strip()


def _should_auto_append_store_address(state: AgentState) -> bool:
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
    if _recent_store_card_ids_from_history(state) and _looks_like_store_card_turn(state):
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
    match = re.search(r'"store_id"\s*:\s*"?(?P<store_id>\d+)"?', text)
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
    return "".join(kept).strip()


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
    if (session.get("visit_date") or session.get("visit_time")) and any(
        term in content
        for term in ("哪天", "什么时候", "几点", "上午还是下午", "什么时间", "到店时间", "方便时间")
    ):
        return True
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
        if "case" in joined or "effect" in joined:
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
    if not any(term in content for term in ("发你看", "发您看", "发图", "效果图", "对比图")):
        return False
    return True


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
        if normalized == recent or normalized in recent or recent in normalized:
            return True
        overlap = len(set(normalized) & set(recent)) / max(1, len(set(normalized)))
        if overlap >= 0.88 and abs(len(normalized) - len(recent)) <= max(12, int(len(normalized) * 0.25)):
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


def _unsupported_store_names_from_text(state: AgentState, text: str) -> list[str]:
    real_names = _real_store_names_from_state(state)
    if not real_names:
        return []
    unsupported: list[str] = []
    for candidate in _candidate_store_names(text):
        if _is_generic_store_reference(candidate):
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
    return list(dict.fromkeys(names))


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
    for name in real_names:
        if not name:
            continue
        if value == name or value in name or name in value:
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
    for source in _professional_assist_sources(state):
        if not isinstance(source, dict) or str(source.get("status") or "").strip() != "requested":
            continue
        reason = str(source.get("reason") or source.get("required_internal_action") or "").strip()
        if reason:
            return reason[:180]
    return ""


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
