from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.schemas import ChatRequest


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
    _inject_debug_platform_context_if_needed(request, context)
    return context


def is_isolated_v2_test_request(request: ChatRequest, context: dict[str, Any]) -> bool:
    """Accept write-free synthetic V2/V3 smoke identities only."""
    if not bool(context.get("test_isolated")):
        return False
    version = str(context.get("interface_version") or context.get("api_version") or "").strip().lower()
    if version not in {"v2", "v3"}:
        return False
    identity_values = (
        request.customer_id,
        request.corp_id,
        request.wechat,
        request.external_userid,
    )
    return all(str(value or "").strip().lower().startswith("sim_") for value in identity_values)


def is_platform_recalled_message(content: str) -> bool:
    """Recognize WeCom's customer-message recall protocol sentinel."""
    return str(content or "").strip() in {"[消息已撤回]", "【消息已撤回】"}


def _inject_debug_platform_context_if_needed(request: ChatRequest, context: dict[str, Any]) -> None:
    """Allow the local debug chat UI to exercise real platform store APIs.

    The debug UI creates synthetic customer/corp ids. Production callers provide
    real corp/user/wechat/external ids, so this intentionally only applies when
    the request clearly looks like a local synthetic conversation.
    """
    settings = get_settings()
    if not settings.debug_platform_context_enabled:
        return
    debug_context = {
        "customer_id": settings.debug_platform_customer_id,
        "customer_add_wechat_id": settings.debug_platform_customer_add_wechat_id,
        "external_userid": settings.debug_platform_external_userid,
        "user_id": settings.debug_platform_user_id,
        "wechat": settings.debug_platform_wechat,
        "corp_id": settings.debug_platform_corp_id,
    }
    if not all(str(value or "").strip() for value in debug_context.values()):
        return
    synthetic_id = str(request.customer_id or "").strip()
    synthetic_corp = str(request.corp_id or "").strip()
    if not synthetic_id or synthetic_id != synthetic_corp:
        return
    if any(
        context.get(key)
        for key in ("user_id", "wechat", "external_userid", "customer_add_wechat_id", "platform_customer_id")
    ):
        return
    context.update(debug_context)
    context["debug_platform_context_injected"] = True


def conversation_id_from_request(request: ChatRequest, request_context: dict[str, Any]) -> str:
    explicit = request_context.get("conversation_id") or request_context.get("session_id")
    return str(explicit or request.customer_id or request.external_userid or "unknown")


def conversation_title(content: str) -> str:
    title = (content or "").strip().replace("\n", " ")
    if not title:
        return "图片咨询"
    return title[:40]
