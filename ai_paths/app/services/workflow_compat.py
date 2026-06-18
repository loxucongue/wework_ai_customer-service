from __future__ import annotations

from typing import Any

from app.schemas import ChatRequest, ChatResponse


def normalize_workflow_request(payload: dict[str, Any]) -> ChatRequest:
    """Convert Coze-style workflow payloads into the native chat request."""
    root = _record(payload)
    parameters = _record(payload.get("parameters")) or payload
    content_value = parameters.get("content")
    content_object = _record(content_value)
    content = _string(content_object.get("content")) if content_object else _string(content_value)
    if not content:
        content = _latest_turn_text(parameters.get("turns")) or _latest_turn_text(root.get("turns"))
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
    root_history = root.get("history")
    root_conversation_history = root.get("conversation_history")
    root_messages = root.get("messages")
    if isinstance(raw_history, list):
        conversation_history = [_string(item) for item in raw_history if _string(item)]
    elif isinstance(messages, list):
        conversation_history = _workflow_messages_to_history(messages)
    elif isinstance(root_history, list):
        conversation_history = _workflow_messages_to_history(root_history)
    elif isinstance(root_conversation_history, list):
        conversation_history = [_string(item) for item in root_conversation_history if _string(item)]
    elif isinstance(root_messages, list):
        conversation_history = _workflow_messages_to_history(root_messages)
    else:
        message_summary = _string(messages) or _string(root.get("messages"))
        conversation_history = [f"对话摘要: {message_summary}"] if message_summary else []
    conversation_history = _append_prior_turns(conversation_history, parameters.get("turns"), content)
    conversation_history = _append_prior_turns(conversation_history, root.get("turns"), content)

    customer_id = (
        _string(parameters.get("customer_id"))
        or _string(parameters.get("external_userid"))
        or _string(request_context.get("customer_id"))
    )
    if not customer_id:
        raise ValueError("missing required parameter: customer_id")

    external_userid = _string(parameters.get("external_userid")) or None
    customer_add_wechat_id = (
        _string(parameters.get("customer_add_wechat_id"))
        or _string(parameters.get("customer_add_wechat_userid"))
        or _string(parameters.get("customer_add_wechat_id_str"))
        or _string(request_context.get("customer_add_wechat_id"))
        or (external_userid or "")
        or customer_id
    )

    return ChatRequest(
        content=content,
        customer_id=customer_id,
        corp_id=_string(parameters.get("corp_id")) or customer_id,
        conversation_history=conversation_history,
        file_image=image or None,
        user_id=_int_or_none(parameters.get("user_id")),
        wechat=_string(parameters.get("wechat")) or None,
        external_userid=external_userid,
        customer_add_wechat_id=customer_add_wechat_id or None,
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
            role = "销售"
        else:
            role = "对话"
        history.append(f"{role}: {content}")
    return history[-10:]


def _latest_turn_text(turns: Any) -> str:
    if not isinstance(turns, list):
        return ""
    for item in reversed(turns):
        text = _string(item)
        if text:
            return text
    return ""


def _append_prior_turns(history: list[str], turns: Any, current_content: str) -> list[str]:
    if not isinstance(turns, list) or not turns:
        return history[-10:]
    current = _string(current_content)
    extra: list[str] = []
    for item in turns:
        text = _string(item)
        if not text or text == current:
            continue
        extra.append(f"用户: {text}")
    if not extra:
        return history[-10:]
    return [*history, *extra][-10:]


def _workflow_reply_message(message: dict[str, Any]) -> dict[str, Any]:
    message_type = _string(message.get("type")) or "text"
    raw_content = message.get("content")
    order = int(message.get("order") or 1)
    if message_type == "human_handoff":
        return {
            "type": "human_handoff",
            "order": order,
            "content": {"handoff_reason": _message_content_value(raw_content, "handoff_reason")},
        }
    if message_type == "image":
        content = _message_content_value(raw_content, "url")
        return {"type": "image", "order": order, "content": {"url": content}}
    if message_type == "appointment_push":
        content = raw_content if isinstance(raw_content, dict) else {"text": _message_content_value(raw_content, "text")}
        return {"type": "appointment_push", "order": order, "content": content}
    if message_type == "book_order":
        content = raw_content if isinstance(raw_content, dict) else {"order_id": _message_content_value(raw_content, "order_id")}
        return {"type": "book_order", "order": order, "content": {"order_id": _message_content_value(content, "order_id")}}
    if message_type == "store_address":
        content = raw_content if isinstance(raw_content, dict) else {"store_id": _message_content_value(raw_content, "store_id")}
        return {"type": "store_address", "order": order, "content": {"store_id": _message_content_value(content, "store_id")}}
    content = _message_content_value(raw_content, "text")
    return {"type": "text", "order": order, "content": {"text": content}}


def _has_knowledge(meta: dict[str, Any]) -> bool:
    keys = meta.get("tool_result_keys") if isinstance(meta, dict) else None
    return isinstance(keys, list) and bool(keys)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _message_content_value(value: Any, preferred_key: str) -> str:
    if isinstance(value, dict):
        for key in [preferred_key, "handoff_reason", "text", "url", "order_id", "store_id"]:
            text = _message_content_value(value.get(key), preferred_key)
            if text:
                return text
        return ""
    return _string(value)


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
