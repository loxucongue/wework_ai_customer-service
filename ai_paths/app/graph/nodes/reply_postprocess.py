from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.graph.customer_need_questions import (
    customer_friendly_type_question,
    detect_customer_need_type_followup,
)
from app.graph import reply_assets, reply_filters, task_state
from app.graph.store_anchor import current_store_anchor_from_state
from app.graph.nodes.project_kb_context import case_request_lacks_specific_context
from app.graph.nodes.price_question_frames import (
    detect_price_question_frame,
    is_case_times_followup,
    is_generic_times_or_effect_question,
)
from app.graph.nodes.reply_validation import message_content_text
from app.graph.task_appointment_signals import is_appointment_resume_message, is_low_info_social_message
from app.graph.state import AgentState
from app.policies.constants import APPOINTMENT_KEYWORDS

OPENING_GUIDANCE_TEXT = "你好呀～这边主要看淡斑、提亮、毛孔、痘印、肤质管理和紧致提升这些，到店会先检测再匹配方向。你在哪个城市或附近哪一片？我先帮你看最近门店～"


@dataclass(frozen=True)
class ReplyPostprocessCallbacks:
    contextual_price_project: Callable[[AgentState], str]
    has_actual_image_context: Callable[[AgentState], bool]
    has_confirmed_spot_goal: Callable[[AgentState], bool]
    has_known_image_context: Callable[[AgentState], bool]
    has_price_objection: Callable[[str], bool]
    is_redundant_known_goal_question: Callable[[AgentState, str], bool]
    looks_like_store_list_message: Callable[[str], bool]
    renumber_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    should_show_appointment_context: Callable[[AgentState], bool]


def postprocess_reply_messages(
    state: AgentState,
    messages: list[dict[str, Any]],
    callbacks: ReplyPostprocessCallbacks,
) -> list[dict[str, Any]]:
    """Filter repeated or unsafe model messages before returning to customer."""
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    content_text = state.get("normalized_content") or ""
    price_frame_name = detect_price_question_frame(content_text)
    price_frame_only_answer = bool(price_frame_name) and (
        price_frame_name
        in {
            "deposit_question",
            "single_fee",
            "course_payment",
            "price_conflict",
            "hidden_fee_concern",
            "confirm_price",
        }
        or not any(term in content_text for term in APPOINTMENT_KEYWORDS)
    )
    appointment_intents = {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}
    has_appointment_context = bool(intents & appointment_intents or task_state.is_active_appointment_task(state))
    appointment_resume = is_appointment_resume_message(content_text) and has_appointment_context
    low_info_social = is_low_info_social_message(content_text) and not appointment_resume
    price_objection = callbacks.has_price_objection(content_text)
    has_available_time_result = bool(state.get("tool_results", {}).get("available_time"))
    sales_strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    sales_stage = str(sales_strategy.get("sales_stage") or "")
    ask_policy = str(sales_strategy.get("ask_policy") or "")
    max_text_messages = _max_text_messages_for_reply(state, sales_stage, ask_policy)
    cleaned: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    text_messages: list[str] = []
    handoff_message = _handoff_message_for_state(state)
    if handoff_message is None and _state_allows_model_handoff(state):
        handoff_message = _handoff_message_from_model(messages)
    if handoff_message is not None and _is_future_effect_worry(content_text):
        handoff_message = None

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "human_handoff":
            continue
        msg_type = message.get("type") if message.get("type") in {"text", "image"} else "text"
        content = message_content_text(message.get("content"))
        if not content:
            continue
        image_url = _extract_standalone_image_url(content)
        if msg_type == "text" and image_url:
            msg_type = "image"
            content = image_url
        if msg_type == "text" and callbacks.has_actual_image_context(state) and not intents & {"human_request", "complaint_refund", "after_sales"} and reply_filters.asks_for_duplicate_photo(content):
            continue
        if msg_type == "text" and "price_inquiry" in intents and reply_filters.is_vague_price_deferral(content):
            continue
        if msg_type == "text" and "price_inquiry" in intents and callbacks.has_confirmed_spot_goal(state):
            if any(
                term in content
                for term in [
                    "更关注效果、恢复期还是预算",
                    "更在意效果、恢复期还是预算",
                    "效果、恢复期还是预算",
                    "更关注哪方面",
                    "更关注哪一方面",
                    "斑点本身还是整体肤色",
                ]
            ):
                continue
        if msg_type == "text" and price_objection and reply_filters.is_project_only_after_price_objection(content):
            continue
        if msg_type == "text" and callbacks.is_redundant_known_goal_question(state, content):
            continue
        if msg_type == "text" and not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"} and not task_state.is_active_appointment_task(state):
            if any(term in content for term in ["哪天方便到店", "方便到店", "到店面诊", "约个面诊", "约个时间到店", "面诊下皮肤状态"]):
                continue
        if msg_type == "text" and not task_state.is_active_appointment_task(state) and not any(term in content_text for term in APPOINTMENT_KEYWORDS):
            if any(term in content for term in ["留个名额", "留一个名额", "想约哪一天", "查当天可预约", "可预约的时间段", "近期面诊时段", "近期可约"]):
                continue
        if msg_type == "text" and not callbacks.should_show_appointment_context(state):
            if any(term in content for term in ["已有预约", "已有预约记录", "预约记录：", "你这边已有预约"]):
                continue
        if msg_type == "text" and has_available_time_result and callbacks.looks_like_store_list_message(content):
            if any(re.search(r"\d{1,2}:\d{2}", str(item.get("content") or "")) for item in cleaned):
                continue
        if msg_type == "text":
            content = reply_filters.repair_appointment_commitment(content)
            content = _rewrite_negated_lock_wording(content)
            content = _soften_unverified_store_rank_claims(content, content_text)
            if is_generic_times_or_effect_question(content_text):
                content = _strip_unneeded_store_followup(content)
            if price_frame_only_answer:
                content = _strip_price_frame_followup(content)
            if low_info_social:
                content = _strip_low_info_social_followup(content)
            if appointment_resume:
                content = _sanitize_low_info_appointment_resume(state, content)
            if "case_request" in intents or is_case_times_followup(content_text):
                content = _sanitize_case_reference_reply(content, content_text)
            normalized = re.sub(r"\s+", "", content)
            if normalized in seen_text:
                continue
            if _is_semantically_redundant(content, text_messages):
                continue
            seen_text.add(normalized)

        cleaned.append({"type": msg_type, "order": len(cleaned) + 1, "content": content})
        if msg_type == "text":
            text_messages.append(content)
        if msg_type == "text" and price_objection and reply_filters.has_budget_or_price_answer(content):
            break
        if msg_type == "text" and "price_inquiry" in intents and reply_filters.asks_daily_single_price(content_text) and re.search(r"\d+\s*元?", content):
            break
        if msg_type == "text" and is_generic_times_or_effect_question(content_text):
            break
        if msg_type == "text" and price_frame_only_answer:
            break
        if msg_type == "text" and low_info_social:
            break
        if msg_type == "text" and appointment_resume:
            break
        # Keep ordinary customer-facing replies compact; handoff is appended later.
        if msg_type == "text" and len(text_messages) >= max_text_messages:
            break

    if not cleaned and handoff_message:
        fallback_text = _handoff_text_fallback_for_state(state)
        if fallback_text:
            cleaned.append({"type": "text", "order": 1, "content": fallback_text})

    if not cleaned:
        return []

    cleaned = reply_filters.sanitize_sensitive_reply_content(
        cleaned,
        intents=intents,
        normalized_content=content_text,
        conversation_history=state.get("conversation_history", []),
        contextual_price_project=callbacks.contextual_price_project(state),
    )
    cleaned = reply_filters.sanitize_customer_visible_messages(cleaned)
    result = reply_filters.attach_asset_images(
        cleaned,
        intents=intents,
        tool_results=state.get("tool_results", {}) or {},
        conversation_history=state.get("conversation_history", []),
        normalized_content=content_text,
        allow_case_study_image=not case_request_lacks_specific_context(state)
        and not is_generic_times_or_effect_question(content_text),
    )
    result = _ensure_case_asset_image_message(state, result)
    result = _rewrite_repeated_case_image_reference(state, result)
    result = _enforce_verified_store_facts_only(state, result)
    result = _strip_unasked_unverified_store_details(state, result)
    result = _merge_compact_text_messages(result)
    result = [_normalize_output_message(message) for message in result]
    result = callbacks.renumber_messages(result)
    if handoff_message:
        result.append({"type": "human_handoff", "order": len(result) + 1, "content": handoff_message["content"]})
    return callbacks.renumber_messages(result)


def _ensure_case_asset_image_message(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if not (intents & {"case_request", "project_inquiry", "image_inquiry"}):
        return messages
    if any(isinstance(item, dict) and item.get("type") == "image" for item in messages):
        return messages
    content = str(state.get("normalized_content") or "")
    if case_request_lacks_specific_context(state):
        return messages
    should_attach = (
        "case_request" in intents
        or detect_customer_need_type_followup(content) is not None
        or reply_assets._should_attach_case_study_image(content)
    )
    if not should_attach:
        return messages
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    image_url = reply_assets.select_asset_image_url(
        tool_results,
        "case_studies",
        conversation_history=state.get("conversation_history", []) or [],
    )
    if not image_url:
        return messages
    updated = list(messages)
    insert_at = 1 if updated else 0
    updated.insert(insert_at, {"type": "image", "order": insert_at + 1, "content": image_url})
    return updated


def _enforce_verified_store_facts_only(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if "store_inquiry" not in intents:
        return messages
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    lookup = tool_results.get("store_lookup") if isinstance(tool_results, dict) else {}
    if not isinstance(lookup, dict):
        return messages
    stores = lookup.get("stores")
    if isinstance(stores, list) and stores:
        return messages
    city = str(lookup.get("city") or "").strip()
    safe_text = (
        f"我这边暂时没拉到{city}的实时门店信息，先不乱给你报门店。"
        if city
        else "我这边暂时没拉到实时门店信息，先不乱给你报门店。"
    )
    normalized_content = str(state.get("normalized_content") or "")
    if city and any(term in normalized_content for term in ["在哪", "哪些位置", "门店", "店吧"]):
        safe_text += " 你把区域或附近地标发我，我按实时门店信息再帮你看一遍。"
    elif city and any(term in normalized_content for term in ["最近", "近一点", "推荐"]):
        safe_text += " 你把附近地标发我，我按实时门店信息再帮你缩到最近的一家。"
    else:
        safe_text += " 你把城市、区域或附近地标发我，我按实时门店信息再帮你看。"
    return [{"type": "text", "order": 1, "content": safe_text}]


def _rewrite_repeated_case_image_reference(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if not (intents & {"case_request", "project_inquiry", "image_inquiry"}):
        return messages
    if any(isinstance(item, dict) and item.get("type") == "image" for item in messages):
        return messages
    history_urls = reply_assets.recent_assistant_image_urls(state.get("conversation_history", []), limit=3)
    if not history_urls:
        return messages
    if not _is_case_image_followup_request(content):
        return messages
    history_text = "\n".join(str(item) for item in (state.get("conversation_history") or [])[-8:])
    reference_label = _case_reference_label(f"{history_text}\n{content}")
    updated: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content")).strip()
        if not text:
            updated.append(item)
            continue
        if _is_case_times_question(content):
            text = f"你上面看到的这张{reference_label}主要是同类改善参考，单张图一般看不出准确做了几次，具体还是会因人而异。"
        else:
            text = re.sub(
                r"(我先给你看个同类(?:改善)?参考|我先给你看个同类案例|先发你看一张同类(?:实操)?参考|先给你看个同类(?:改善)?参考)",
                f"就是我上面发您的这个{reference_label}",
                text,
            )
            text = text.replace("这类大多数都可以先看改善参考，", "")
            text = text.replace("这类可以先看同类改善参考，", "")
            text = text.replace("这类有同类改善参考，", "")
            text = re.sub(r"[，,]\s*[，,]", "，", text)
            text = text.strip("，, ")
            if not any(term in text for term in ["上面发您的", "上面那张", "刚刚发您的"]):
                text = f"就是我上面发您的这个{reference_label}。{text}".strip("。")
        updated.append({**item, "content": text})
    return updated


def _ensure_age_suitability_next_step(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not _is_age_suitability_question(content):
        return messages
    known_city = _known_city_for_reply(state)
    known_store = current_store_anchor_from_state(state)
    updated: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content")).strip()
        if not text:
            updated.append(item)
            continue
        text = re.sub(r"同类参考[—\-－]+[，,]\s*", "同类参考，", text)
        text = re.sub(r"参考[—\-－]+[，,]\s*", "参考，", text)
        if not any(term in text for term in ["可以做", "能做", "成熟", "年龄段", "年纪"]):
            text = "可以做的，我们主要服务的就是偏成熟、斑点和肤色问题更明显的客群。"
        if not any(term in text for term in ["身体情况", "皮肤状态", "禁忌", "检测"]):
            text = text.rstrip("。！？!?~～") + "，到店会先看身体情况、皮肤状态和有没有明显禁忌。"
        if known_store:
            if known_city and known_city not in text and known_store not in text and "检测" in text:
                text = text.rstrip("。！？!?~～") + f"，你在{known_city}的话，前面聊到的{known_store}就可以先过去做个检测看看～"
            elif known_store not in text and "检测" in text:
                text = text.rstrip("。！？!?~～") + f"，前面聊到的{known_store}就可以先过去做个检测看看～"
        elif known_city:
            if known_city not in text and "检测" in text:
                text = text.rstrip("。！？!?~～") + f"，你在{known_city}这边的话，可以先找近一点的门店做个检测确认～"
        elif not any(term in text for term in ["哪个城市", "附近", "门店", "到店", "检测"]):
            text = text.rstrip("。！？!?~～") + "，你在哪个城市或附近哪一片？我先帮你看近一点的门店做检测确认～"
        elif "检测" in text and not any(term in text for term in ["哪个城市", "附近哪一片", "近一点的门店"]):
            text = text.rstrip("。！？!?~～") + "，你在哪个城市或附近哪一片？我先帮你看近一点的门店～"
        updated.append({**item, "content": text})
    return updated


def _known_city_for_reply(state: AgentState) -> str:
    basic = state.get("customer_basic_info") if isinstance(state.get("customer_basic_info"), dict) else {}
    city = str(basic.get("city") or "").strip()
    if city:
        return city
    strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    known_slots = strategy.get("known_slots") if isinstance(strategy.get("known_slots"), dict) else {}
    city = str(known_slots.get("city") or "").strip()
    if city:
        return city
    for item in reversed((state.get("history_events") or [])[-6:]):
        if not isinstance(item, dict):
            continue
        facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
        city = str(facts.get("city") or "").strip()
        if city:
            return city
    return ""


def _rewrite_store_confirmation_burden(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content"))
        if not text:
            updated.append(item)
            continue
        text = re.sub(
            r"(去之前)?(?:你)?可以?先确认一下当天(?:营业|接待)安排[～~。]*",
            "过去前我帮你再看一下当天接待安排～",
            text,
        )
        text = re.sub(
            r"(去之前)?(?:你)?先确认一下当天(?:营业|接待)安排[～~。]*",
            "过去前我帮你再看一下当天接待安排～",
            text,
        )
        updated.append({**item, "content": text})
    return updated


def _ensure_store_address_answer(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not _asks_store_address(content):
        return messages
    store = _first_confirmed_store_from_results(state)
    if not store:
        return messages
    name = str(store.get("name") or "").strip()
    address = str(store.get("address") or "").strip()
    if not address:
        return messages
    address_sentence = f"{name}在{address}。" if name else f"门店地址在{address}。"
    existing_text = " ".join(message_content_text(item.get("content")) for item in messages if isinstance(item, dict))
    if address in existing_text:
        return _dedupe_available_time_text(messages)
    updated = list(messages)
    for idx, item in enumerate(updated):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = message_content_text(item.get("content")).strip()
        if not text:
            continue
        text = _dedupe_available_time_text_value(text)
        updated[idx] = {**item, "content": f"{address_sentence}{text}"}
        return updated
    return [{"type": "text", "order": 1, "content": address_sentence}, *updated]


def _strip_unasked_unverified_store_details(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if intents & {"project_process", "case_request"}:
        return messages
    if _asks_store_address(content) or "store_inquiry" in intents or _first_confirmed_store_from_results(state):
        return messages
    if any(term in content for term in ["门店", "地址", "哪里", "附近", "停车", "导航", "到店", "预约"]):
        return messages
    updated: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content"))
        stripped = _strip_store_detail_sentences(text).strip(" ，,。")
        if stripped:
            updated.append({**item, "content": stripped})
    if updated:
        return updated
    if _is_passive_opening_content(str(state.get("normalized_content") or "")):
        return [_opening_guidance_message()]
    return [{"type": "text", "order": 1, "content": "在的，这边可以帮你看皮肤改善、活动和附近门店。你在哪个城市或附近哪一片？"}]


def _rewrite_passive_opening_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _is_passive_opening_content(str(state.get("normalized_content") or "")):
        return messages
    text_messages = [
        message_content_text(item.get("content")).strip()
        for item in messages
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    if not text_messages:
        return messages
    text = " ".join(item for item in text_messages if item).strip()
    compact = re.sub(r"\s+", "", text).strip("。！？!?~～")
    if any(term in text for term in ["城市", "附近区域", "哪个城市", "最近的门店", "最近门店"]):
        return [_opening_guidance_message()]
    if not any(term in text for term in ["城市", "附近区域", "哪个城市", "最近的门店", "最近门店", "门店安排"]):
        return [_opening_guidance_message()]
    if compact in {"您好", "你好", "你好呀", "小贝在的", "你好呀小贝在的"} or len(compact) <= 8:
        return [_opening_guidance_message()]
    return messages


def _rewrite_bare_greeting_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "").strip()
    compact_content = re.sub(r"\s+", "", content).lower().strip("，。！？!?~～")
    if compact_content not in {"你好", "您好", "你好呀", "在吗", "hello", "hi"}:
        return messages
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if intents and intents != {"emotion_chat"}:
        return messages
    if task_state.is_active_appointment_task(state):
        return messages
    if len(messages) != 1 or not isinstance(messages[0], dict) or messages[0].get("type") != "text":
        return messages
    text = message_content_text(messages[0].get("content")).strip()
    compact_reply = re.sub(r"\s+", "", text).strip("，。！？!?~～")
    if len(compact_reply) > 10 and any(term in text for term in ["城市", "附近区域", "哪个城市", "最近的门店", "最近门店", "门店安排"]):
        return messages
    if len(compact_reply) <= 12 or compact_reply in {"你好", "您好", "你好呀", "小贝在的", "你好呀小贝在的"}:
        return [_opening_guidance_message()]
    return messages


def _rewrite_low_info_business_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "").strip()
    compact_content = re.sub(r"\s+", "", content).strip("，。！？!?~～")
    if compact_content not in {"你好", "您好", "你好呀", "在吗", "可以", "行", "好的", "好", "嗯", "来了"}:
        return messages
    if task_state.is_active_appointment_task(state):
        return messages
    history_text = "\n".join(str(item) for item in (state.get("conversation_history") or [])[-8:])
    if not history_text:
        return messages
    text = _combined_text(messages)
    if not _looks_like_bare_reply(text):
        return messages
    if any(term in history_text for term in ["预约", "到店", "几点", "明天", "今天", "门店", "地址"]):
        return [
            {
                "type": "text",
                "order": 1,
                "content": "在的，前面到店和门店安排我接着跟进着，你现在想先确认时间，还是我把门店位置再发你一遍？",
            }
        ]
    if any(term in history_text for term in ["祛斑", "淡斑", "黑色素", "斑点", "效果", "案例"]):
        return [
            {
                "type": "text",
                "order": 1,
                "content": "在的，前面你是想看淡斑改善方向对吧？这类可以先看同类参考，再帮你按最近门店安排到店检测。",
            }
        ]
    if any(term in history_text for term in ["价格", "多少钱", "活动", "优惠", "预约金", "定金"]):
        return [
            {
                "type": "text",
                "order": 1,
                "content": "在的，前面价格和活动口径我接着帮你核。你要是方便，也可以先告诉我在哪个城市，我按最近门店帮你看。",
            }
        ]
    return messages


def _ensure_signup_progression(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not any(term in content for term in ["报名", "先报名", "帮我登记", "登记一下", "留个名额", "留名额", "保留优惠", "先保留"]):
        return messages
    combined = _combined_text(messages)
    if any(term in combined for term in ["登记", "名额", "门店", "时间", "预约", "城市", "电话", "姓名"]):
        return messages
    store_name = current_store_anchor_from_state(state)
    city = _known_city_for_reply(state)
    if store_name:
        text = f"可以的，我先按{store_name}这家继续帮你登记活动名额。你看今天还是明天方便到店？"
    elif city:
        text = f"可以的，我先帮你登记活动名额。你在{city}这边的话，我先按近一点的门店帮你安排。"
    else:
        text = "可以的，我先帮你登记活动名额。你在哪个城市或附近哪一片？我按最近门店继续帮你安排。"
    return [{"type": "text", "order": 1, "content": text}]


def _sanitize_unverified_signup_store_reference(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not any(term in content for term in ["报名", "先报名", "帮我登记", "登记一下", "留个名额", "留名额", "保留优惠", "先保留"]):
        return messages
    verified_store = _current_turn_store_anchor(state)
    if verified_store:
        return messages
    updated: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content")).strip()
        store_match = re.search(r"(?:你之前提到的是|预约记录是)([^，。！？!?]+店)", text)
        if (
            store_match
            and store_match.group(1) not in _conversation_text(state)
            and store_match.group(1) != _current_turn_store_anchor(state)
        ):
            text = "可以的，我先帮你把活动名额登记上。你在哪个城市或附近哪一片？我按最近门店继续帮你安排。"
        elif not _current_turn_store_anchor(state) and "这次还是去这家店" in text:
            text = "可以的，我先帮你把活动名额登记上。你在哪个城市或附近哪一片？我按最近门店继续帮你安排。"
        updated.append({**item, "content": text})
    return updated


def _rewrite_visit_arrangement_bare_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "").strip()
    if not (
        any(term in content for term in ["到店", "去店里", "过去看看", "去看看", "可以去"])
        and any(term in content for term in ["怎么安排", "怎么去", "怎么弄", "要怎么", "安排"])
    ):
        return messages
    if len(messages) != 1 or not isinstance(messages[0], dict) or messages[0].get("type") != "text":
        return messages
    text = message_content_text(messages[0].get("content")).strip()
    compact = re.sub(r"\s+", "", text).strip("，。！？!?~～")
    has_actionable_next = any(term in text for term in ["城市", "位置", "附近", "门店", "推荐", "时间", "安排", "地址"])
    is_echo_only = (
        any(term in text for term in ["想先去店里看看", "想去店里看看", "先去店里看看", "去店里看看"])
        and not has_actionable_next
    )
    if is_echo_only:
        return [
            {
                "type": "text",
                "order": 1,
                "content": "可以的，你告诉我在哪个城市或哪个位置，我先帮你推荐最近门店，再看合适的到店时间。",
            }
        ]
    if any(term in text for term in ["哪天", "什么时候", "几点", "时间"]) and not any(
        term in content for term in ["厦门", "上海", "重庆", "杭州", "广州", "深圳", "南京", "成都", "武汉", "长沙", "福州", "泉州", "北京", "西安", "附近", "机场", "浦东", "湖里", "思明"]
    ):
        return [
            {
                "type": "text",
                "order": 1,
                "content": "可以的，你告诉我在哪个城市或哪个位置，我先帮你推荐最近门店，再看合适的到店时间。",
            }
        ]
    if compact not in {"你好", "您好", "你好呀", "小贝在的", "你好呀小贝在的"}:
        return messages
    return [
        {
            "type": "text",
            "order": 1,
            "content": "可以的，你告诉我在哪个城市或哪个位置，我先帮你推荐最近门店，再看合适的到店时间。",
        }
    ]


def _combined_text(messages: list[dict[str, Any]]) -> str:
    return " ".join(message_content_text(item.get("content")) for item in messages if isinstance(item, dict)).strip()


def _looks_like_bare_reply(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).strip("，。！？!?~～")
    if compact in {"您好", "你好", "你好呀", "小贝在的", "你好呀小贝在的"}:
        return True
    return len(compact) <= 10 and not any(
        term in compact
        for term in ["城市", "门店", "价格", "效果", "预约", "登记", "地址", "检测", "案例", "到店"]
    )


def _strip_case_times_extra_followup(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    combined = " ".join(message_content_text(item.get("content")) for item in messages if isinstance(item, dict))
    if not is_case_times_followup(content) and not (
        any(term in combined for term in ["效果图", "单张图", "案例图"]) and any(term in combined for term in ["做了几次", "具体做了几次", "几次"])
    ):
        return messages
    updated: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content")).strip()
        text = re.split(r"你方便说下在哪个城市或附近区域", text, maxsplit=1)[0]
        text = re.split(r"(?:我|小贝)(?:先)?帮你找最近的店", text, maxsplit=1)[0]
        text = re.split(r"你更想看哪类问题的改善参考", text, maxsplit=1)[0]
        text = re.split(r"你主要想看哪类问题的改善参考", text, maxsplit=1)[0]
        text = re.split(r"你还想看哪类问题的改善参考", text, maxsplit=1)[0]
        text = re.split(r"你更想先看[^。！？!?]*?(效果|参考)", text, maxsplit=1)[0]
        text = re.split(r"你更想看[^。！？!?]*?(效果|参考)", text, maxsplit=1)[0]
        text = re.sub(r"[～~，,；;\s]+$", "", text).strip()
        if not text:
            text = "这类效果图主要作阶段性改善参考，单张图通常看不出准确做了几次；如果案例库有原始记录，我再按记录补充。"
        updated.append({**item, "content": text})
    return updated


def _ensure_sales_hook_after_effect_or_no_price(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not messages:
        return messages
    should_hook = _is_effect_confirmation_question(content) or _is_no_price_progression_moment(state, content, messages)
    if not should_hook:
        return messages
    combined = " ".join(message_content_text(item.get("content")) for item in messages if isinstance(item, dict))
    has_known_location = bool(_current_turn_store_anchor(state))
    if not has_known_location and any(term in combined for term in ["帮你看看店里时间", "帮你看下店里时间", "帮你看店里时间"]):
        updated_location: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, dict) or item.get("type") != "text":
                updated_location.append(item)
                continue
            text = message_content_text(item.get("content"))
            text = re.sub(r"我先帮你看看店里时间[～~。]*", "你在哪个城市或附近哪一片？我先帮你看最近门店和时间～", text)
            text = re.sub(r"小贝先帮你看下店里时间[～~。]*", "你在哪个城市或附近哪一片？我先帮你看最近门店和时间～", text)
            text = re.sub(r"我先帮你看店里时间[～~。]*", "你在哪个城市或附近哪一片？我先帮你看最近门店和时间～", text)
            updated_location.append({**item, "content": text})
        return updated_location
    if any(term in combined for term in ["什么时候方便", "哪天方便", "今天", "明天", "到店", "过来", "店里时间", "帮你看时间"]):
        return messages
    updated = list(messages)
    for idx, item in enumerate(updated):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = message_content_text(item.get("content")).rstrip("。！？!?~～ ")
        if not text:
            continue
        hook = (
            "你看今天还是明天方便到店，我先帮你看下店里时间～"
            if _current_turn_store_anchor(state)
            else "你在哪个城市或附近哪一片？我先帮你看最近门店和时间～"
        )
        updated[idx] = {**item, "content": f"{text}。{hook}"}
        return updated
    if _is_effect_confirmation_question(content):
        return [
            {
                "type": "text",
                "order": 1,
                "content": "这种零散小点可以先看淡斑改善方向，我先给你看个同类参考。你在哪个城市或附近哪一片？我先帮你看最近门店和时间～",
            },
            *messages,
        ]
    return messages


def _conversation_text(state: AgentState) -> str:
    return "\n".join(str(item) for item in (state.get("conversation_history") or []))


def _current_turn_store_anchor(state: AgentState) -> str:
    anchor = current_store_anchor_from_state(state)
    if not anchor:
        return ""
    history_text = _conversation_text(state)
    current = str(state.get("normalized_content") or "")
    if anchor in history_text or anchor in current:
        return anchor
    tool_results = state.get("tool_results") if isinstance(state.get("tool_results"), dict) else {}
    lookup = tool_results.get("store_lookup") if isinstance(tool_results, dict) else {}
    stores = lookup.get("stores") if isinstance(lookup, dict) else []
    if isinstance(stores, list):
        for store in stores:
            if not isinstance(store, dict):
                continue
            name = str(store.get("name") or store.get("store_name") or "").strip()
            if name and name == anchor:
                return anchor
    return ""


def _is_effect_confirmation_question(content: str) -> bool:
    text = str(content or "")
    type_terms = ["零散小点", "零散的小点", "小点", "斑点", "黑色素", "浅斑", "淡斑", "色沉"]
    effect_terms = ["这种效果", "这样效果", "能有效果", "能不能有", "能不能改善", "有没有效果", "能达到", "能做到"]
    action_terms = ["去做", "我做", "我去", "做了", "能有"]
    return any(term in text for term in type_terms) and any(term in text for term in effect_terms) and any(
        term in text for term in action_terms
    )


def _is_case_image_followup_request(content: str) -> bool:
    text = str(content or "")
    case_terms = ["案例", "对比", "图片", "照片", "效果图", "发我", "看看", "看下", "同类参考", "改善参考"]
    return any(term in text for term in case_terms) or _is_case_times_question(text)


def _is_case_times_question(content: str) -> bool:
    text = str(content or "")
    return is_case_times_followup(text) or (
        any(term in text for term in ["几次", "多少次"])
        and any(term in text for term in ["图片", "图上", "这种", "这种效果", "对比"])
    )


def _case_reference_label(content: str) -> str:
    text = str(content or "")
    if any(term in text for term in ["黑色素", "色素", "色沉"]):
        return "祛黑色素的对比"
    if any(term in text for term in ["斑", "淡斑", "祛斑", "小点"]):
        return "淡斑的对比"
    if any(term in text for term in ["毛孔", "出油"]):
        return "毛孔肤质的对比"
    if any(term in text for term in ["暗沉", "肤色不均", "提亮"]):
        return "提亮肤色的对比"
    if any(term in text for term in ["抗衰", "松", "下垂", "细纹", "皱纹"]):
        return "紧致提升的对比"
    return "同类对比"


def _is_age_suitability_question(content: str) -> bool:
    text = str(content or "")
    age_terms = ["年纪", "年龄", "岁数", "年纪大", "年龄大", "岁数大", "老了", "比较大了", "年纪比较大"]
    suitability_terms = ["能做", "能不能做", "可以做", "可不可以做", "适合", "还能做", "也能做", "还能不能"]
    return any(term in text for term in age_terms) and any(term in text for term in suitability_terms)


def _is_no_price_progression_moment(state: AgentState, content: str, messages: list[dict[str, Any]]) -> bool:
    if not any(term in content for term in ["多少钱", "什么价格", "价格", "费用", "要多少"]):
        return False
    combined = " ".join(message_content_text(item.get("content")) for item in messages if isinstance(item, dict))
    history = "\n".join(str(item) for item in state.get("conversation_history", [])[-6:])
    has_context = any(term in history for term in ["我在", "机场", "附近", "门店", "黑色素", "斑", "淡斑", "祛斑"])
    narrow_price_check = any(term in content for term in ["一次费用", "是一次", "确定", "为什么", "怎么不一样", "一只", "一双"])
    if re.search(r"\d+\s*(元|块)?", combined):
        return has_context and not narrow_price_check
    if any(term in combined for term in ["暂未查到", "没查到", "没有明确价格", "核清楚", "不乱报", "项目、包含项"]):
        return True
    return has_context


def _rewrite_too_far_overpush_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not any(term in content for term in ["太远", "远了", "没时间", "没有时间", "不方便去"]):
        return messages
    if len(messages) != 1 or not isinstance(messages[0], dict) or messages[0].get("type") != "text":
        return messages
    text = message_content_text(messages[0].get("content")).strip()
    compact = re.sub(r"\s+", "", text).strip("，。！？!?~～")
    if not any(term in text for term in ["哪天方便", "什么时候方便", "方便来", "方便到店"]) and compact not in {
        "你好",
        "您好",
        "你好呀",
        "小贝在的",
        "你好呀小贝在的",
    }:
        return messages
    return [
        {
            "type": "text",
            "order": 1,
            "content": "理解的，不方便跑太远就别勉强。你告诉我现在大概在哪个位置，我帮你看看有没有更近的门店或更省时间的安排。",
        }
    ]


def _is_passive_opening_content(content: str) -> bool:
    text = re.sub(r"\s+", "", str(content or "").strip())
    if not text:
        return False
    return any(term in text for term in ["我已经添加了你", "已经添加了你", "现在我们可以开始聊天了", "可以开始聊天了", "开始聊天"])


def _asks_store_address(content: str) -> bool:
    text = str(content or "")
    return any(term in text for term in ["门店在哪里", "门店在哪", "这个门店在哪里", "这个门店在哪", "地址", "在哪里", "在哪儿", "位置", "怎么过去", "导航", "发给我", "发我", "把这家店发给我", "把这家发给我"])


def _ensure_key_business_answer(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    text_messages = [message_content_text(item.get("content")) for item in messages if isinstance(item, dict) and item.get("type") == "text"]
    joined = " ".join(text_messages).strip()
    compact = re.sub(r"\s+", "", joined).strip("，。！？!?~～")
    greeting_like = compact in {"你好", "您好", "你好呀", "小贝在的", "你好呀小贝在的"} or len(compact) <= 8

    if "project_process" in intents and (greeting_like or not any(term in joined for term in ["流程", "操作", "分钟", "时长", "清洁", "护理"])):
        return [{"type": "text", "order": 1, "content": "这类项目一般先做皮肤状态确认，再清洁、操作和护理提醒，到店整体约40-60分钟，操作本身多在20-30分钟左右。"}]

    if any(term in content for term in ["几点上班", "几点开门", "营业时间", "几点开", "几点关"]):
        store = _first_confirmed_store_from_results(state)
        hours = str(store.get("business_hours") or "").strip() if isinstance(store, dict) else ""
        name = str(store.get("name") or "").strip() if isinstance(store, dict) else ""
        if hours:
            return [{"type": "text", "order": 1, "content": f"{name}营业时间一般是{hours}，你要是想过去，我再按这家帮你接着安排。".strip("，")}]
        if greeting_like or not joined:
            return [{"type": "text", "order": 1, "content": "我们门店一般是早上9点开始接待，具体哪家店定下来后，我再把那家营业时间一起发你。"}]

    if _asks_store_address(content):
        store = _first_confirmed_store_from_results(state)
        if isinstance(store, dict):
            name = str(store.get("name") or "").strip()
            address = str(store.get("address") or "").strip()
            map_url = str(store.get("map_url") or "").strip()
            if address and (greeting_like or address not in joined):
                text = f"{name}地址：{address}"
                if map_url:
                    text += f"，导航点这里→ {map_url}"
                return [{"type": "text", "order": 1, "content": text}]

    if any(term in content for term in ["会不会很快又回来", "很快又回来", "会不会又回来", "维持多久", "能维持多久", "保持多久"]):
        if greeting_like or "不会" not in joined or not any(term in joined for term in ["维持", "护理", "防晒", "稳定"]):
            return [{"type": "text", "order": 1, "content": "不会说刚做完很快就回到原来那样，基础改善和后续跟进这块是有的；后面防晒、护理和作息，也会影响维持时间和稳定度。"}]

    if any(term in content for term in ["平时需要注意什么", "需要注意什么", "平时注意什么", "平时要注意什么"]):
        if greeting_like or not any(term in joined for term in ["防晒", "补水", "修复", "作息", "刺激"]):
            return [{"type": "text", "order": 1, "content": "平时主要注意防晒、别频繁去角质或乱刷酸，基础补水修复做好，作息尽量规律一点，这样维持会更稳。"}]

    if any(term in content for term in ["券", "优惠券", "活动券", "代金券"]):
        if greeting_like or "券" not in joined:
            return [{"type": "text", "order": 1, "content": "这个券能不能用，要看你看到的是哪张活动图或对应哪个项目；你发我看一眼，我就按这条活动帮你核。"}]

    return messages


def _first_confirmed_store_from_results(state: AgentState) -> dict[str, Any] | None:
    lookup = state.get("tool_results", {}).get("store_lookup") if isinstance(state.get("tool_results"), dict) else None
    if not isinstance(lookup, dict):
        return None
    recommended = lookup.get("recommended_store")
    if isinstance(recommended, dict) and recommended.get("address"):
        return recommended
    stores = lookup.get("stores")
    if isinstance(stores, list):
        for store in stores:
            if isinstance(store, dict) and store.get("address"):
                return store
    return None


def _dedupe_available_time_text(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for item in messages:
        if isinstance(item, dict) and item.get("type") == "text":
            updated.append({**item, "content": _dedupe_available_time_text_value(message_content_text(item.get("content")))})
        else:
            updated.append(item)
    return updated


def _dedupe_available_time_text_value(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"(有空位[。；;，, ]*)这个时间目前有空位[。；;，, ]*", r"\1", value)
    value = re.sub(r"这个时间目前有空位[。；;，, ]*这个时间目前有空位", "这个时间目前有空位", value)
    return value


def _strip_store_detail_sentences(text: str) -> str:
    sentences = re.split(r"(?<=[。！？!?])", str(text or ""))
    kept: list[str] = []
    for sentence in sentences:
        compact = sentence.strip()
        if not compact:
            continue
        if re.search(r"(最近的门店|门店是|地址在|在[\u4e00-\u9fa5]{2,8}(?:区|路|街|号)|方便过来)", compact):
            continue
        kept.append(compact)
    return "".join(kept)


def lacks_price_answer_for_price_question(state: AgentState, text: str) -> bool:
    content = state.get("normalized_content") or ""
    if not any(term in content for term in ["多少钱", "多少", "价格", "费用", "预算", "贵不贵"]):
        return False
    if reply_filters.has_budget_or_price_answer(text):
        return False
    if has_no_price_fact_phrase(text):
        return False
    return True


def has_no_price_fact_phrase(text: str) -> bool:
    return any(
        term in text
        for term in [
            "没查到",
            "没有查到",
            "暂时没查到",
            "暂时没有查到",
            "暂未查到",
            "没有明确价格",
            "没有查到明确",
            "不乱报",
            "价格表没看到",
            "不能拿别的项目价格代替",
        ]
    )


def _handoff_message_from_model(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "human_handoff":
            continue
        reason = message_content_text(message.get("content"))
        if reason:
            return {"type": "human_handoff", "content": {"handoff_reason": reason}}
    return None


def _state_allows_model_handoff(state: AgentState) -> bool:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    skills = {item.get("skill") for item in state.get("intents", []) if isinstance(item, dict)}
    route_result = state.get("route_result") or {}
    return bool(
        intents & {"human_request", "complaint_refund"}
        or "handoff" in skills
        or route_result.get("need_human") is True
        or route_result.get("subflow") == "HUMAN_HANDOFF"
    )


def _handoff_message_for_state(state: AgentState) -> dict[str, Any] | None:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    skills = {item.get("skill") for item in state.get("intents", []) if isinstance(item, dict)}
    sales_strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    sales_stage = str(sales_strategy.get("sales_stage") or "")
    if not (intents & {"human_request", "complaint_refund"} or "handoff" in skills or sales_stage == "handoff_at_store"):
        return None
    if sales_stage == "handoff_at_store":
        reason = "客户已到店或在门店附近，需要门店同事现场接待/指引。"
    elif "complaint_refund" in intents:
        reason = "客户涉及投诉、退款、费用争议或效果不满，需要专业同事协助核对处理。"
    else:
        reason = "客户当前问题需要专业同事协助确认。"
    return {"type": "human_handoff", "content": {"handoff_reason": reason}}


def _handoff_text_fallback_for_state(state: AgentState) -> str:
    sales_strategy = state.get("sales_strategy") if isinstance(state.get("sales_strategy"), dict) else {}
    sales_stage = str(sales_strategy.get("sales_stage") or "")
    if sales_stage == "handoff_at_store":
        return "你先别着急，我马上帮你同步门店同事现场接待和指引，你在原地等一下就好～"
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if "complaint_refund" in intents:
        return "这个涉及具体处理，我先帮你同步专业同事核对一下。"
    if "human_request" in intents:
        return "这个我让专业同事接着协助你确认。"
    return ""


def _max_text_messages_for_reply(state: AgentState, sales_stage: str, ask_policy: str) -> int:
    content = str(state.get("normalized_content") or "").strip()
    if ask_policy == "no_ask":
        return 1
    if sales_stage in {"collect_info", "service_recovery"}:
        return 2
    if sales_stage in {"store_paving", "quote", "close_order"}:
        if any(term in content for term in ["地址", "导航", "停车", "营业时间", "几点", "怎么去", "路线"]):
            return 2
        return 2
    return 2


def _is_semantically_redundant(content: str, existing: list[str]) -> bool:
    candidate = _normalize_semantic_text(content)
    if not candidate:
        return True
    for item in existing:
        previous = _normalize_semantic_text(item)
        if not previous:
            continue
        if candidate == previous:
            return True
        if candidate in previous and len(candidate) >= max(12, int(len(previous) * 0.65)):
            return True
        if previous in candidate and len(previous) >= max(12, int(len(candidate) * 0.65)):
            return True
    return False


def _normalize_semantic_text(text: str) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"[，。！？、,.!\?\s]", "", normalized)
    for filler in ["小贝", "这边", "可以的", "按你这个情况看", "如果方便的话", "我这边", "给你说一下", "换个说法哈", "换个说法"]:
        normalized = normalized.replace(filler, "")
    return normalized


def _compact_trailing_question(
    state: AgentState,
    messages: list[dict[str, Any]],
    *,
    ask_policy: str,
) -> list[dict[str, Any]]:
    if ask_policy != "no_ask":
        return messages
    text_messages = [item for item in messages if item.get("type") == "text"]
    if len(text_messages) <= 1:
        return messages
    kept: list[dict[str, Any]] = []
    dropped_question = False
    for item in messages:
        if item.get("type") != "text":
            kept.append(item)
            continue
        text = message_content_text(item.get("content"))
        if not dropped_question and _looks_like_followup_question(text):
            dropped_question = True
            continue
        kept.append(item)
    return kept


def _sanitize_case_reference_reply(text: str, customer_content: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.replace("同类顾客的改善参考", "同类改善参考")
    cleaned = cleaned.replace("顾客的改善参考", "改善参考")
    cleaned = cleaned.replace("大多数顾客反馈是有改善的", "这类可以先看同类改善参考")
    cleaned = cleaned.replace("大多数顾客反馈是有基础改善的", "这类可以先看同类改善参考")
    if is_case_times_followup(customer_content):
        cleaned = re.sub(
            r"[。！？!?～~]?\s*你主要想看哪类问题的效果[^。！？!?]*[。！？!?]?",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"[。！？!?～~]?\s*比如[^。！？!?]*[。！？!?]?",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"[。！？!?～~]?\s*你想看[^。！？!?]*[。！？!?]?",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"[。！？!?～~]?\s*你方便说下在哪个城市或附近区域[^。！？!?]*[。！？!?]?",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"[。！？!?～~]?\s*小贝帮你找最近的店[^。！？!?]*[。！？!?]?",
            "",
            cleaned,
        )
        if "做了几次" not in cleaned and "几次" not in cleaned:
            cleaned = "这类效果图主要作阶段性改善参考，单张图通常看不出准确做了几次；如果案例库有原始记录，我再按记录补充。"
    cleaned = cleaned.replace("～按你前面说的淡斑需求", "～")
    cleaned = cleaned.replace("，按你前面说的淡斑需求", "")
    cleaned = re.sub(r"我们[^，。！？]*店有同类改善参考", "这类有同类改善参考", cleaned)
    store_terms = ["门店", "地址", "导航", "停车", "哪家", "附近", "到店", "过去", "预约"]
    if not any(term in str(customer_content or "") for term in store_terms):
        cleaned = re.sub(r"[，,～]\s*按你[^，。！？]*?(到店|安排|门店)[^。！？]*[。！？]?$", "", cleaned)
        cleaned = re.sub(r"[，,～]\s*[^，。！？]*(到店也方便|方便安排|就近安排|就近看|后面到店也方便)[^。！？]*[。！？]?$", "", cleaned)
        cleaned = re.sub(r"[，,～]\s*(我帮你顺一下方向|先帮你顺一下方向)[。！？]?$", "", cleaned)
        cleaned = re.sub(r"[，,～]\s*按你前面说的[^，。！？]*([。！？]?$|$)", "", cleaned)
        cleaned = re.sub(r"[，,～]\s*(我帮你顺一下更适合的方向|再帮你顺一下更适合的方向)[。！？]?$", "", cleaned)
        cleaned = re.sub(r"[，,～]\s*[^，。！？]*店[^，。！？]*(离你最近|更近|更方便|顺路|到店确认细节)[^。！？]*[。！？]?$", "", cleaned)
        cleaned = re.sub(r"(。){2,}$", "。", cleaned).strip(" ，,；;")
    return cleaned


def _merge_compact_text_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text_indexes = [idx for idx, item in enumerate(messages) if isinstance(item, dict) and item.get("type") == "text"]
    if len(text_indexes) != 2 or len(messages) != 2:
        return messages
    first_idx, second_idx = text_indexes
    first_text = message_content_text(messages[first_idx].get("content")).strip()
    second_text = message_content_text(messages[second_idx].get("content")).strip()
    if not first_text or not second_text:
        return messages
    if len(first_text) + len(second_text) > 88:
        return messages
    if any(mark in first_text for mark in ["\n", "1.", "2.", "①", "②"]) or any(mark in second_text for mark in ["\n", "1.", "2.", "①", "②"]):
        return messages
    separator = " " if first_text.endswith(("？", "?", "！", "!", "。")) else "。"
    merged = f"{first_text}{separator}{second_text}"
    return [{"type": "text", "order": 1, "content": merged}]


def _soften_unverified_store_rank_claims(text: str, customer_content: str) -> str:
    cleaned = str(text or "").strip()
    if any(term in str(customer_content or "") for term in ["机场", "附近", "商圈", "火车站", "高铁站", "地铁", "路", "区"]):
        return cleaned
    cleaned = cleaned.replace("离你最近", "会更方便")
    cleaned = cleaned.replace("最近，方便安排", "这边先看会更方便")
    cleaned = cleaned.replace("最近，也方便", "会更方便")
    return cleaned


def _strip_unneeded_store_followup(text: str) -> str:
    cleaned = str(text or "").strip()
    patterns = [
        r"[。！？!?～~]?\s*你目前在哪个城市或附近区域[^。！？!?]*[。！？!?]?",
        r"[。！？!?～~]?\s*你现在在哪个城市或附近区域[^。！？!?]*[。！？!?]?",
        r"[。！？!?～~]?\s*你是在哪个城市或附近区域[^。！？!?]*[。！？!?]?",
        r"[。！？!?～~]?\s*你在哪个城市或附近区域[^。！？!?]*[。！？!?]?",
        r"[。！？!?～~]?\s*我帮你找最近的店[。！？!?]?",
        r"[。！？!?～~]?\s*我帮你找最近的门店[。！？!?～~]?",
        r"[。！？!?～~]?\s*我帮你推荐最近的门店[。！？!?]?",
        r"[。！？!?～~]?\s*咱们先确认下你所在的位置[^。！？!?]*[。！？!?]?",
        r"[。！？!?～~]?\s*[^。！？!?]*方便推荐最近的门店[^。！？!?]*[。！？!?]?",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned).strip()
    return cleaned.strip(" ，,；;")


def _strip_price_frame_followup(text: str) -> str:
    cleaned = str(text or "").strip()
    patterns = [
        r"[。！？!?～~]?\s*你方便告诉我[^。！？!?]*(门店|登记|预约)[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*方便告诉我[^。！？!?]*(门店|登记|预约)[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*请问您?想预约哪个城市的门店[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*你想预约哪个城市的门店[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*你方便哪个时间段[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*方便哪个时间段[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*[^。！？!?]*(今天|明天|后天|上午|下午|晚上)[^。！？!?]*这些时间段可约[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*[^。！？!?]*可约时间[^。！？!?]*\d{1,2}:\d{2}[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*你想约哪家门店[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*想约哪家门店[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*我帮你登记[^。！？!?]*[。！？!?～~]?",
        r"[。！？!?～~]?\s*我帮您登记[^。！？!?]*[。！？!?～~]?",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned).strip()
    return cleaned.strip(" ，,；;")


def _rewrite_negated_lock_wording(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"帮你先锁定[^，。！？!?]*名额", "用于确认你的到店信息", cleaned)
    cleaned = re.sub(r"先锁定[^，。！？!?]*名额", "先确认到店信息", cleaned)
    cleaned = re.sub(r"锁定[^，。！？!?]*名额", "确认到店信息", cleaned)
    replacements = {
        "不是锁定项目效果或最终费用": "不代表项目效果或最终费用已经确定",
        "不等于锁定项目效果或最终费用": "不代表项目效果或最终费用已经确定",
        "不代表锁定项目效果或最终费用": "不代表项目效果或最终费用已经确定",
        "不是已经锁定项目效果或最终费用": "不代表项目效果或最终费用已经确定",
        "不是锁定效果或最终费用": "不代表效果或最终费用已经确定",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"[—\-－]+[，,]", "，", cleaned)
    if cleaned and cleaned[-1] not in "。！？!?~～）)】]\"'":
        cleaned += "。"
    return cleaned


def _strip_low_info_social_followup(text: str) -> str:
    cleaned = str(text or "").strip()
    patterns = [
        r"[，,。！？!?～~]?\s*方便说一下[^。！？!?]*(城市|附近区域|门店)[^。！？!?]*[。！？!?]?",
        r"[，,。！？!?～~]?\s*你现在[^。！？!?]*(城市|附近区域|门店)[^。！？!?]*[。！？!?]?",
        r"[，,。！？!?～~]?\s*这样我可以帮你推荐最近的门店[。！？!?]?",
        r"[，,。！？!?～~]?\s*我可以帮你推荐最近的门店[。！？!?]?",
        r"[，,。！？!?～~]?\s*我帮你推荐最近的门店[。！？!?]?",
        r"[，,。！？!?～~]?\s*我帮你找最近的门店[。！？!?～~]?",
        r"[，,。！？!?～~]?\s*[^。！？!?]*(明天|后天|哪天|什么时候)[^。！？!?]*(来店|到店|过来|看看)[^。！？!?]*[。！？!?]?",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned).strip()
    cleaned = cleaned.strip(" ，,；;")
    if any(term in cleaned for term in ["门店", "城市", "附近区域", "最近", "来店", "到店", "过来看看"]):
        return "在的，这边可以帮你看皮肤改善、活动和附近门店。你在哪个城市或附近哪一片？"
    if cleaned in {"你好呀", "你好", "您好", "嗨"}:
        return "在的，这边可以帮你看皮肤改善、活动和附近门店。你在哪个城市或附近哪一片？"
    return cleaned or "在的，这边可以帮你看皮肤改善、活动和附近门店。你在哪个城市或附近哪一片？"


def _sanitize_low_info_appointment_resume(state: AgentState, text: str) -> str:
    cleaned = str(text or "").strip()
    history_text = "\n".join(str(item or "") for item in (state.get("conversation_history") or [])[-6:])
    if any(term in history_text for term in ["小程序", "预约金", "按页面提示"]):
        return "在的，咱们继续刚刚的预约确认。刚才那个预约入口你按页面提示确认就可以，没收到的话我再帮你看～"
    if "门店" in cleaned and "城市" in cleaned and "预约" not in cleaned:
        return "在的，咱们继续刚刚的预约确认～"
    return cleaned or "在的，咱们继续刚刚的预约确认～"


def _looks_like_followup_question(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    if any(term in content for term in ["？", "?"]):
        return True
    return any(
        term in content
        for term in [
            "方便",
            "哪天",
            "哪家",
            "要不要",
            "想不想",
            "可以吗",
            "要吗",
            "吗",
        ]
    )


def _normalize_output_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        return message
    msg_type = str(message.get("type") or "text")
    content = message.get("content")
    if msg_type == "text":
        text = message_content_text(content)
        text = _normalize_punctuation(text)
        text = _normalize_visible_service_identity(text)
        image_url = _extract_standalone_image_url(text)
        if image_url:
            return {**message, "type": "image", "content": {"url": image_url}}
        return {**message, "content": {"text": text}}
    if msg_type == "image":
        url = _extract_standalone_image_url(message_content_text(content)) or message_content_text(content)
        return {**message, "content": {"url": url}}
    return message


def _normalize_punctuation(text: str) -> str:
    cleaned = str(text or "")
    replacements = {
        "。，": "，",
        "，。": "。",
        "。。": "。",
        "？？": "？",
        "！！": "！",
        "～。": "～",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    return cleaned


def _normalize_visible_service_identity(text: str) -> str:
    cleaned = str(text or "")
    replacements = {
        "小贝在的": "在的",
        "你好呀，小贝在的": "在的",
        "你好呀～小贝在的": "在的",
        "我是小贝": "我在",
        "客服小贝": "这边",
        "小贝这边": "这边",
        "小贝先": "我先",
        "小贝帮": "我帮",
        "小贝再": "我再",
        "小贝马上": "我马上",
        "告诉小贝": "告诉我",
        "找小贝": "找我",
        "来找小贝": "来找我",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"(?<![A-Za-z0-9])小贝(?![A-Za-z0-9])", "我", cleaned)
    cleaned = re.sub(r"(?<![\u4e00-\u9fa5])[\u4e00-\u9fa5]{1,3}(女士|先生|小姐)", "您", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_standalone_image_url(text: str) -> str:
    content = str(text or "").strip()
    if not content:
        return ""
    if _is_standalone_image_url(content):
        return content
    if content.startswith("{") and content.endswith("}"):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            for key in ("url", "image_url", "src"):
                value = str(payload.get(key) or "").strip()
                if _is_standalone_image_url(value):
                    return value
    match = re.fullmatch(r"\s*['\"]?(https?://[^'\"\s<>]+)['\"]?\s*", content)
    if match and _is_standalone_image_url(match.group(1)):
        return match.group(1)
    return ""


def _is_standalone_image_url(text: str) -> bool:
    content = str(text or "").strip()
    if not (content.startswith("http://") or content.startswith("https://")):
        return False
    if "\n" in content or " " in content:
        return False
    return any(marker in content.lower() for marker in [".png", ".jpg", ".jpeg", ".webp", "filebiztype"])


def _ensure_case_followup_question(
    state: AgentState,
    messages: list[dict[str, Any]],
    *,
    ask_policy: str,
) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not any(term in content for term in ["祛斑", "淡斑", "黑色素", "抗衰", "补水", "毛孔", "暗沉", "色沉"]):
        return messages
    has_image = any(item.get("type") == "image" for item in messages if isinstance(item, dict))
    if not has_image:
        return messages
    text_indexes = [idx for idx, item in enumerate(messages) if isinstance(item, dict) and item.get("type") == "text"]
    if not text_indexes:
        return messages
    if any("？" in message_content_text(messages[idx].get("content")) or "?" in message_content_text(messages[idx].get("content")) for idx in text_indexes):
        return messages
    image_info = state.get("image_info") if isinstance(state.get("image_info"), dict) else {}
    question = customer_friendly_type_question(
        content,
        visible_concerns=image_info.get("visible_concerns") if isinstance(image_info.get("visible_concerns"), list) else [],
    )
    if not question:
        return messages
    first_idx = text_indexes[0]
    first = dict(messages[first_idx])
    base_text = message_content_text(first.get("content")).rstrip("。！？!? ")
    first["content"] = f"{base_text}。{question}"
    updated = list(messages)
    updated[first_idx] = first
    return updated


def _rewrite_city_only_store_prompt(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if intents != {"store_inquiry"}:
        return messages
    lookup = state.get("tool_results", {}).get("store_lookup") or {}
    if not isinstance(lookup, dict):
        return messages
    city = str(lookup.get("city") or "").strip()
    stores = lookup.get("stores")
    if not city or not isinstance(stores, list) or len(stores) <= 1:
        return messages
    if lookup.get("recommended_store") or lookup.get("location_preference"):
        return messages
    if lookup.get("wants_route") or lookup.get("wants_parking") or lookup.get("wants_status"):
        return messages
    content = str(state.get("normalized_content") or "").strip()
    if not _is_city_only_store_text(content, city):
        return messages
    hints = _store_area_hints_from_lookup(stores)
    if len(hints) >= 2:
        text = f"{city}这边有几家店，你是在{hints[0]}还是{hints[1]}这边呢？我直接帮你缩到近一点的一家。"
    elif hints:
        text = f"{city}这边有几家店，你更方便在{hints[0]}附近，还是其他片区呢？我直接帮你推近一点的一家。"
    else:
        text = f"{city}这边有几家店，你是在市区哪一片更方便呢？我直接帮你推近一点的一家。"
    return [{"type": "text", "order": 1, "content": text}]


def _is_city_only_store_text(content: str, city: str) -> bool:
    text = str(content or "").strip()
    for prefix in ["我在", "人在", "目前在", "现在在", "住在"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    for suffix in ["这边", "这儿", "附近"]:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    text = text.strip(" ，。！？?~～")
    return text in {city, f"{city}市"}


def _store_area_hints_from_lookup(stores: list[dict[str, Any]]) -> list[str]:
    hints: list[str] = []
    for store in stores[:5]:
        if not isinstance(store, dict):
            continue
        for value in [str(store.get("address") or "").strip(), str(store.get("name") or "").strip()]:
            hint = _extract_area_hint(value)
            if hint and hint not in hints:
                hints.append(hint)
        if len(hints) >= 3:
            break
    return hints


def _extract_area_hint(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    matched = re.search(r"(?:[\u4e00-\u9fa5]{2,8}市)?([\u4e00-\u9fa5]{2,8}区)", value)
    if matched:
        return matched.group(1)
    matched = re.search(r"(?:[\u4e00-\u9fa5]{2,8}市)?([\u4e00-\u9fa5]{2,8}(?:机场|火车站|高铁站|商圈))", value)
    if matched:
        return matched.group(1)
    return ""


def _rewrite_opening_city_collection(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    content = str(state.get("normalized_content") or "").strip()
    history = state.get("conversation_history") or []
    if intents != {"emotion_chat"}:
        return messages
    if history:
        return messages
    if content not in {"你好", "您好", "在吗", "哈喽", "hello", "hi"}:
        return messages
    return [_opening_guidance_message()]


def _opening_guidance_message() -> dict[str, Any]:
    return {"type": "text", "order": 1, "content": OPENING_GUIDANCE_TEXT}


def _normalize_need_intro_wording(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intents = {item.get("intent") for item in state.get("intents", []) if isinstance(item, dict)}
    if "project_inquiry" not in intents and "case_request" not in intents:
        return messages
    customer_content = str(state.get("normalized_content") or "")
    store_terms = ["门店", "地址", "导航", "停车", "哪家", "附近", "过去", "怎么去", "路线", "预约", "到店"]
    has_image_message = any(isinstance(item, dict) and item.get("type") == "image" for item in messages)
    updated: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content"))
        text = re.sub(r"这类大多数(?:我们)?都可以先看", "这类大多数都可以做", text)
        if has_image_message:
            text = text.replace("小贝先给你看下更偏哪种情况。", "我先给你看个同类参考。")
            text = text.replace("先给你看下更偏哪种情况。", "先给你看个同类参考。")
        else:
            text = re.sub(r"小贝先给你看个同类(?:改善)?参考。?\s*", "我先帮你判断更偏哪种情况。", text)
            text = re.sub(r"先给你看个同类(?:改善)?参考。?\s*", "先帮你判断更偏哪种情况。", text)
        if "判断更偏哪种情况" in text and not re.search(r"[？?]", text):
            question = customer_friendly_type_question(customer_content) or "是零散小点多，还是成片颜色重一点？"
            text = text.rstrip("。！？!?~～ ") + f"，{question}"
        text = text.replace("淡斑改善+整体提亮方向", "淡斑改善和整体提亮方向")
        text = re.sub(r"([\u4e00-\u9fa5])\+([\u4e00-\u9fa5])", r"\1和\2", text)
        if not any(term in customer_content for term in store_terms):
            text = re.sub(r"[，,～]\s*按[^，。！？]*?(到店也方便|后面到店也方便|到店方便|安排也方便)[^。！？]*[。！？]?$", "", text)
            text = re.sub(r"[，,～]\s*[^，。！？]*(到店也方便|后面到店也方便|到店方便|安排也方便)[^。！？]*[。！？]?$", "", text)
            text = text.strip(" ，,；;")
        updated.append({**item, "content": text})
    return updated


def _ensure_deposit_refusal_answer(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not (
        any(term in content for term in ["不交预约金", "不交定金", "不要预约金", "不要定金", "不想先交预约金", "不想先交定金", "不先交预约金", "不先交定金"])
        and any(term in content for term in ["到店付", "到店再付", "付全款", "现场付"])
    ):
        return messages
    return [
        {
            "type": "text",
            "order": 1,
            "content": "可以，到店再了解、满意再做也行。活动价和到店安排我先按门店实际口径帮你确认清楚，不会因为你不先交预约金就拦着你。",
        }
    ]


def _sanitize_unverified_store_time_hook(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _current_turn_store_anchor(state):
        return messages
    updated: list[dict[str, Any]] = []
    changed = False
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content"))
        new_text = re.sub(
            r"[。！？!?]?\s*我先帮你看(?:看|下)?店里时间[～~。]*",
            "。你在哪个城市或附近哪一片？我先帮你看最近门店和时间～",
            text,
        )
        new_text = re.sub(
            r"[。！？!?]?\s*你看今天还是明天方便到店，我先帮你看(?:看|下)?店里时间[～~。]*",
            "。你在哪个城市或附近哪一片？我先帮你看最近门店和时间～",
            new_text,
        )
        changed = changed or new_text != text
        updated.append({**item, "content": new_text})
    return updated if changed else messages


def _is_future_effect_worry(content: str) -> bool:
    text = str(content or "")
    if any(term in text for term in ["已经做", "做了2次", "做了两次", "一点效果都没有", "没有效果", "没效果", "退款", "投诉"]):
        return False
    return any(term in text for term in ["会不会没变化", "会不会没有变化", "怕没效果", "担心没效果", "真的有效果吗", "有没有效果"])


def _rewrite_type_followup_reply(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "").strip()
    followup_type = detect_customer_need_type_followup(content)
    if not followup_type:
        return messages
    has_image_message = any(isinstance(item, dict) and item.get("type") == "image" for item in messages)
    template_map = {
        "spot_sparse": "零散小点这类，一般会先往淡斑改善方向看。",
        "spot_patchy": "成片颜色重这类，一般会先往淡斑改善和整体提亮方向看。",
        "tone_dull": "整体暗沉不均这类，一般会先往整体提亮方向看。",
        "lift_loose": "脸有点松这类，一般会先往紧致提升方向看。",
        "wrinkle_lines": "纹路更明显这类，一般会先往紧致提升方向看。",
        "hydrate_dry": "干燥缺水这类，一般会先往补水修护方向看。",
        "hydrate_dull": "发闷没光泽这类，一般会先往补水提亮方向看。",
        "pore_oily": "毛孔出油这类，一般会先往毛孔肤质管理方向看。",
        "acne_marks": "痘印痘坑这类，一般会先往肤质修护和痘印改善方向看。",
    }
    prefix = template_map.get(followup_type)
    if not prefix:
        return messages
    updated: list[dict[str, Any]] = []
    rewritten = False
    for item in messages:
        if not isinstance(item, dict) or item.get("type") != "text":
            updated.append(item)
            continue
        text = message_content_text(item.get("content")).strip()
        if rewritten:
            updated.append(item)
            continue
        if has_image_message:
            new_text = f"{prefix}我先给你看个同类参考。"
        else:
            new_text = f"{prefix}具体改善程度会因深浅、范围和皮肤状态不同。"
        updated.append({**item, "content": new_text})
        rewritten = True
    return updated


def _ensure_ad_multi_intent_answer(state: AgentState, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = str(state.get("normalized_content") or "")
    if not (
        any(term in content for term in ["广告", "活动", "直播"])
        and any(term in content for term in ["价格", "多少钱", "费用"])
        and any(term in content for term in ["效果", "案例", "做完"])
        and any(term in content for term in ["到店", "门店", "安排", "预约"])
    ):
        return messages
    joined = " ".join(message_content_text(item.get("content")) for item in messages if isinstance(item, dict))
    has_price = any(term in joined for term in ["价格", "活动", "广告", "费用", "口径", "尾款", "包含"])
    has_store = any(term in joined for term in ["到店", "门店", "城市", "位置", "最近"])
    if has_price and has_store:
        return messages
    addition = "价格我会按你看到的广告/活动口径核清楚，到店这块你告诉我城市或位置，我直接帮你推荐最近门店。"
    updated = list(messages)
    for idx, item in enumerate(updated):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = message_content_text(item.get("content")).strip()
        if not text:
            continue
        if addition in text:
            return messages
        separator = "" if text.endswith(("。", "！", "？", "～", "~")) else "。"
        updated[idx] = {**item, "content": f"{text}{separator}{addition}"}
        return updated
    return [{"type": "text", "order": 1, "content": addition}, *updated]
