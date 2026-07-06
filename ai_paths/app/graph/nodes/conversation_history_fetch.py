from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from app.graph.state import AgentState


ConversationFetcher = Callable[..., Awaitable[dict[str, Any]]]


async def fetch_platform_conversation_history(
    state: AgentState,
    conversation_fetcher: ConversationFetcher | None,
    *,
    limit: int,
    fallback_limit: int,
    request_context: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    fallback = request_conversation_history(state, limit=fallback_limit)
    if not conversation_fetcher:
        return fallback, {
            "status": "skipped",
            "reason": "conversation_fetcher_unavailable",
            "used_message_count": len(fallback),
            "limit": limit,
        }
    params = conversation_fetch_params(state, request_context=request_context, limit=limit)
    missing = [key for key, value in params.items() if key != "limit" and not str(value or "").strip()]
    if missing:
        return fallback, {
            "status": "skipped",
            "reason": "missing_required_fields",
            "missing": missing,
            "used_message_count": len(fallback),
            "limit": limit,
        }
    try:
        result = await conversation_fetcher(**params)
    except Exception as exc:
        return fallback, {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "used_message_count": len(fallback),
            "limit": limit,
        }

    messages = result.get("messages") if isinstance(result, dict) and isinstance(result.get("messages"), list) else []
    history = platform_messages_to_history(messages, limit=limit)
    if not history:
        return fallback, {
            "status": str(result.get("status") or "empty") if isinstance(result, dict) else "empty",
            "reason": str(result.get("reason") or "") if isinstance(result, dict) else "",
            "error": str(result.get("error") or "") if isinstance(result, dict) else "",
            "message_count": len(messages),
            "used_message_count": len(fallback),
            "limit": limit,
        }
    used = history[-limit:]
    return used, {
        "status": str(result.get("status") or "ok") if isinstance(result, dict) else "ok",
        "message_count": len(messages),
        "used_message_count": len(used),
        "limit": limit,
    }


def request_conversation_history(state: AgentState, *, limit: int) -> list[str]:
    history = state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []
    return [str(item)[:240] for item in history[-limit:] if str(item or "").strip()]


def conversation_fetch_params(
    state: AgentState,
    *,
    request_context: dict[str, Any] | None = None,
    limit: int,
) -> dict[str, Any]:
    merged_context = dict(state.get("request_context") or {})
    if request_context:
        merged_context.update({key: value for key, value in request_context.items() if value is not None})
    external_userid = str(merged_context.get("external_userid") or state.get("external_userid") or "").strip()
    customer_id = str(external_userid or merged_context.get("customer_id") or state.get("customer_id") or "").strip()
    return {
        "corp_id": str(merged_context.get("corp_id") or state.get("corp_id") or "").strip(),
        "customer_id": customer_id,
        "external_userid": str(external_userid or customer_id).strip(),
        "user_id": str(merged_context.get("user_id") or state.get("user_id") or "").strip(),
        "wechat": str(merged_context.get("wechat") or state.get("wechat") or "").strip(),
        "limit": max(1, min(int(limit or 30), 50)),
    }


def platform_messages_to_history(messages: list[dict[str, Any]], *, limit: int) -> list[str]:
    output: list[str] = []
    ordered_messages = _ordered_platform_messages(messages)
    for item in ordered_messages[-limit:]:
        if not isinstance(item, dict):
            continue
        text = message_text(item.get("content"))
        if not text:
            text = message_text(item.get("text") or item.get("message") or item.get("body"))
        if not text:
            continue
        output.append(f"{message_role_label(item)}: {text[:220]}")
    return output


def _ordered_platform_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    typed = [item for item in messages if isinstance(item, dict)]
    if not typed:
        return []
    indexed: list[tuple[float, int, dict[str, Any]]] = []
    for index, item in enumerate(typed):
        timestamp = _message_timestamp(item)
        if timestamp is None:
            return typed
        indexed.append((timestamp, index, item))
    return [item for _, _, item in sorted(indexed, key=lambda part: (part[0], part[1]))]


def _message_timestamp(item: dict[str, Any]) -> float | None:
    for key in (
        "created_at",
        "create_time",
        "created_time",
        "timestamp",
        "msgtime",
        "msg_time",
        "send_time",
        "time",
    ):
        if key not in item:
            continue
        parsed = _parse_timestamp(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000.0
        return number
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        number = float(raw)
        if number > 10_000_000_000:
            number = number / 1000.0
        return number
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def message_role_label(item: dict[str, Any]) -> str:
    raw = str(item.get("direction") or item.get("role") or item.get("sender_type") or item.get("from") or "").lower()
    if raw in {"customer", "user", "external", "incoming", "in", "received", "receive", "contact"}:
        return "用户"
    if raw in {"assistant", "staff", "service", "cs", "internal", "outgoing", "out", "sent", "send", "bot", "agent"}:
        return "小贝"
    if item.get("is_from_customer") is True:
        return "用户"
    if item.get("is_from_customer") is False:
        return "小贝"
    sender = str(item.get("sender_type") or item.get("sender_role") or "").lower()
    if "external" in sender or "customer" in sender:
        return "用户"
    return "小贝"


def message_text(content: Any) -> str:
    if isinstance(content, dict):
        if "amount" in content:
            return f"预约金收款:{content.get('amount')}"
        for key in ("text", "content", "url", "handoff_reason", "store_id"):
            text = message_text(content.get(key))
            if text:
                return text
        return ""
    return str(content or "").strip()
