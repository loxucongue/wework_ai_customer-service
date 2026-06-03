from __future__ import annotations

from typing import Any


def validated_image_info(payload: dict[str, Any], *, has_image: bool) -> dict[str, Any]:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    if not isinstance(info, dict):
        raise ValueError("Vision JSON missing info")
    allowed_types = {
        "face_skin",
        "eye_area",
        "face_shape",
        "body_skin",
        "post_treatment",
        "competitor_quote",
        "chat_screenshot",
        "product_package",
        "payment_proof",
        "store_location",
        "document_report",
        "campaign_poster",
        "qr_code",
        "unrelated",
        "unclear",
    }
    allowed_intents = {
        "face_consult",
        "after_sales",
        "competitor_compare",
        "price_inquiry",
        "campaign_inquiry",
        "store_inquiry",
        "trust_issue",
        "human_request",
        "general_image",
        "unrelated",
    }
    image_type = str(info.get("image_type") or "unclear")
    image_intent = str(info.get("image_intent") or "general_image")
    confidence = info.get("confidence", 0.5)
    try:
        confidence_float = float(confidence)
    except (TypeError, ValueError):
        confidence_float = 0.5
    return {
        "has_image": has_image,
        "image_desc": str(info.get("image_desc") or "")[:500],
        "image_type": image_type if image_type in allowed_types else "unclear",
        "image_intent": image_intent if image_intent in allowed_intents else "general_image",
        "body_part": str(info.get("body_part") or "未知"),
        "visible_concerns": list_of_strings(info.get("visible_concerns")),
        "risk_signals": list_of_strings(info.get("risk_signals")),
        "extracted_text": list_of_strings(info.get("extracted_text")),
        "text_clues": list_of_strings(info.get("text_clues")),
        "suggested_route": str(info.get("suggested_route") or "UNKNOWN"),
        "confidence": max(0.0, min(1.0, confidence_float)),
    }


def list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:200] for item in value[:10] if str(item).strip()]
