from __future__ import annotations

from typing import Any

from app.schemas import ChatRequest, ChatResponse


def normalize_workflow_request(payload: dict[str, Any]) -> ChatRequest:
    """Convert Coze-style workflow payloads into the native chat request."""
    parameters = _record(payload.get("parameters")) or payload
    content_value = parameters.get("content")
    content_object = _record(content_value)
    content = _string(content_object.get("content")) if content_object else _string(content_value)
    image = _string(parameters.get("image")) or _string(parameters.get("file_image"))

    request_context = dict(_record(parameters.get("request_context")) or {})
    workflow_id = _string(payload.get("workflow_id")) or _string(parameters.get("workflow_id"))
    content_meta = {
        "source_protocol": "workflow-compatible",
        "workflow_id": workflow_id,
        "category_id": _string(parameters.get("category_id")),
        "msgid": _string(content_object.get("msgid")) if content_object else "",
        "msgtime": _string(content_object.get("msgtime")) if content_object else "",
        "msgtype": (_string(content_object.get("msgtype")) if content_object else "") or _string(parameters.get("msgtype")),
        "location": _string(content_object.get("location")) if content_object else "",
    }
    request_context.update({key: value for key, value in content_meta.items() if value})

    messages = parameters.get("messages")
    raw_history = parameters.get("conversation_history")
    if isinstance(raw_history, list):
        conversation_history = [_string(item) for item in raw_history if _string(item)]
    elif isinstance(messages, list):
        conversation_history = _workflow_messages_to_history(messages)
    else:
        message_summary = _string(messages)
        conversation_history = [f"对话摘要: {message_summary}"] if message_summary else []

    customer_id = (
        _string(parameters.get("customer_id"))
        or _string(parameters.get("external_userid"))
        or _string(request_context.get("customer_id"))
    )
    if not customer_id:
        raise ValueError("missing required parameter: customer_id")

    return ChatRequest(
        content=content,
        customer_id=customer_id,
        corp_id=_string(parameters.get("corp_id")) or customer_id,
        conversation_history=conversation_history,
        file_image=image or None,
        user_id=_int_or_none(parameters.get("user_id")),
        wechat=_string(parameters.get("wechat")) or None,
        external_userid=_string(parameters.get("external_userid")) or None,
        customer_add_wechat_id=_string(parameters.get("customer_add_wechat_id")) or None,
        confirmed_store_id=_string(parameters.get("confirmed_store_id")) or None,
        confirmed_store_name=_string(parameters.get("confirmed_store_name")) or None,
        store_id=_string(parameters.get("store_id")) or None,
        store_name=_string(parameters.get("store_name")) or None,
        appointment_id=_string(parameters.get("appointment_id")) or None,
        appointment_time=_string(parameters.get("appointment_time")) or None,
        request_context=request_context,
    )


def workflow_response_from_chat(response: ChatResponse) -> dict[str, Any]:
    return {
        "code": 0,
        "msg": "success",
        "execute_id": response.request_id,
        "data": {
            "versions": "1",
            "reply_messages": [_workflow_reply_message(message.model_dump()) for message in response.reply_messages],
            "trace_id": response.request_id,
            "step": response.subflow or response.intent or response.scene,
            "has_knowledge": "true" if _has_knowledge(response.meta) else "",
            "error": "",
        },
        "detail": {"logid": response.request_id},
    }


def workflow_error_response(message: str, *, code: int = 400) -> dict[str, Any]:
    return {
        "code": code,
        "msg": message,
        "execute_id": "",
        "data": {
            "versions": "1",
            "reply_messages": [],
            "trace_id": "",
            "step": "validate_request",
            "has_knowledge": "",
            "error": message,
        },
        "detail": {"logid": ""},
    }


def _workflow_messages_to_history(messages: list[Any]) -> list[str]:
    history: list[str] = []
    for item in messages:
        message = _record(item)
        if not message:
            continue
        content = _string(message.get("content"))
        if not content:
            continue
        direction = _string(message.get("direction"))
        if direction in {"customer", "user", "external"}:
            role = "用户"
        elif direction in {"staff", "assistant", "service"}:
            role = "小贝"
        else:
            role = "对话"
        history.append(f"{role}: {content}")
    return history[-10:]


def _workflow_reply_message(message: dict[str, Any]) -> dict[str, Any]:
    message_type = _string(message.get("type")) or "text"
    content = _string(message.get("content"))
    order = int(message.get("order") or 1)
    if message_type == "image":
        return {"type": "image", "order": order, "content": {"url": content}}
    return {"type": "text", "order": order, "content": {"text": content}}


def _has_knowledge(meta: dict[str, Any]) -> bool:
    keys = meta.get("tool_result_keys") if isinstance(meta, dict) else None
    return isinstance(keys, list) and bool(keys)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
