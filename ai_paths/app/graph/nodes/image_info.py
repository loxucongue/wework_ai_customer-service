from __future__ import annotations

import json
from typing import Any

from app.graph.nodes.common import dedupe_strings, json_dumps
from app.graph.nodes.image_validation import validated_image_info


def build_vision_prompt(state: dict[str, Any]) -> str:
    context = {
        "content": state.get("normalized_content"),
        "conversation_history": state.get("conversation_history", [])[-4:],
    }
    return (
        "你是企业微信医美客服系统中的通用图片理解节点。"
        "你不回复客户，不诊断，不推荐项目，只输出结构化JSON。"
        "请判断图片类型、业务意图、可见表层问题、风险信号和关键文字。"
        "如果是面部皮肤图，可以记录点状斑点、片状色沉、肤色不均、泛红、痘印、痘坑、毛孔明显等可见事实。"
        "不能写黄褐斑、皮炎、感染等诊断词，除非客户文字明确说出。"
        "如果是截图、报价、海报、地图、付款、报告，请提取关键文字，不输出完整手机号、身份证、银行卡号。"
        "最终只输出合法JSON，格式："
        "{\"info\":{\"has_image\":true,\"image_desc\":\"\",\"image_type\":\"face_skin|eye_area|face_shape|body_skin|post_treatment|competitor_quote|chat_screenshot|product_package|payment_proof|store_location|document_report|campaign_poster|qr_code|unrelated|unclear\","
        "\"image_intent\":\"face_consult|after_sales|competitor_compare|price_inquiry|campaign_inquiry|store_inquiry|trust_issue|human_request|general_image|unrelated\","
        "\"body_part\":\"\",\"visible_concerns\":[],\"risk_signals\":[],\"extracted_text\":[],\"text_clues\":[],\"suggested_route\":\"SF4_face_consult|SF5_competitor_response|SF6_store_match|SF7_price_consult|SF8_campaign_push|SF10_trust_build|SF12_after_sales|HUMAN_HANDOFF|DIRECT_REPLY|UNKNOWN\",\"confidence\":0}}。"
        f"客户上下文：{json.dumps(context, ensure_ascii=False, default=str)}"
    )


def fallback_image_info(*, has_image: bool) -> dict[str, Any]:
    return {
        "has_image": has_image,
        "image_desc": "客户上传了图片，当前视觉模型未返回可用解析。" if has_image else "",
        "image_type": "unclear",
        "image_intent": "face_consult" if has_image else "unrelated",
        "body_part": "未知" if has_image else "无",
        "visible_concerns": [],
        "risk_signals": [],
        "extracted_text": [],
        "text_clues": [],
        "suggested_route": "SF4_face_consult" if has_image else "UNKNOWN",
        "confidence": 0.25 if has_image else 0,
    }


def has_image_concern(image_info: dict[str, Any], keywords: list[str]) -> bool:
    joined = " ".join(image_concern_terms(image_info))
    return any(keyword in joined for keyword in keywords)


def image_concern_terms(image_info: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ["visible_concerns", "text_clues", "extracted_text"]:
        for value in image_info.get(key, []) or []:
            text = str(value).strip()
            if text:
                terms.append(text)
    desc = str(image_info.get("image_desc") or "").strip()
    if desc:
        terms.append(desc)
    return terms


def has_known_image_context(state: dict[str, Any]) -> bool:
    image_info = state.get("image_info") or {}
    if isinstance(image_info, dict) and (
        image_info.get("has_image")
        or image_info.get("visible_concerns")
        or image_info.get("image_desc")
        or image_info.get("text_clues")
    ):
        return True
    return bool(known_visible_concerns_from_state(state))


def has_actual_image_context(state: dict[str, Any]) -> bool:
    file_image = str(state.get("file_image") or state.get("image_url") or "").strip()
    if file_image:
        return True
    image_info = state.get("image_info") or {}
    if isinstance(image_info, dict) and image_info.get("has_image"):
        return True
    for message in state.get("conversation_history", [])[-8:]:
        text = str(message)
        if "[图片]" in text or "file_image" in text or "<img" in text:
            return True
    return False


def known_visible_concerns_from_state(state: dict[str, Any]) -> list[str]:
    concerns: list[str] = []
    profile = state.get("customer_profile") or {}
    if isinstance(profile, dict):
        for key in ["pain_points", "concerns"]:
            for item in profile.get(key) or []:
                text = str(item).strip()
                if any(term in text for term in ["点状斑", "斑点", "色沉", "肤色不均", "泛红", "痘印", "痘坑", "毛孔"]):
                    concerns.append(text)
    for event in state.get("history_events", [])[-8:]:
        event_text = json_dumps(event) if isinstance(event, dict) else str(event)
        for term in ["点状斑点", "点状斑", "片状色沉", "面部色沉", "肤色不均", "泛红", "痘印", "痘坑", "毛孔"]:
            if term in event_text:
                concerns.append(term)
    for message in state.get("conversation_history", [])[-8:]:
        message_text = str(message)
        for term in ["点状斑点", "点状斑", "片状色沉", "面部色沉", "肤色不均", "泛红", "痘印", "痘坑", "毛孔"]:
            if term in message_text:
                concerns.append(term)
    concerns = dedupe_strings(concerns)
    if "点状斑点" in concerns:
        concerns = [item for item in concerns if item != "点状斑"]
    if "片状色沉" in concerns or "面部色沉" in concerns:
        concerns = [item for item in concerns if item != "色沉"]
    return concerns[:5]
