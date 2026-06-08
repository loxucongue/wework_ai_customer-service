from __future__ import annotations

from typing import Any

from app.schemas import ChatRequest

DEFAULT_PLATFORM_USER_ID = 7294


def build_request_context(request: ChatRequest) -> dict[str, Any]:
    context = dict(request.request_context or {})
    fields = {
        "user_id": request.user_id,
        "corp_id": request.corp_id,
        "wechat": request.wechat,
        "external_userid": request.external_userid,
        "customer_id": request.customer_id,
        "customer_add_wechat_id": request.customer_add_wechat_id,
        "confirmed_store_id": request.confirmed_store_id,
        "confirmed_store_name": request.confirmed_store_name,
        "store_id": request.store_id,
        "store_name": request.store_name,
        "appointment_id": request.appointment_id,
        "appointment_time": request.appointment_time,
    }
    for key, value in fields.items():
        if value not in (None, ""):
            context[key] = value
    if context.get("user_id") in (None, ""):
        context["user_id"] = DEFAULT_PLATFORM_USER_ID
    return context


def conversation_id_from_request(request: ChatRequest, request_context: dict[str, Any]) -> str:
    explicit = request_context.get("conversation_id") or request_context.get("session_id")
    return str(explicit or request.customer_id or request.external_userid or "unknown")


def conversation_title(content: str) -> str:
    title = (content or "").strip().replace("\n", " ")
    if not title:
        return "图片咨询"
    return title[:40]
