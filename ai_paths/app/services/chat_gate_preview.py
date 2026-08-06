from __future__ import annotations

from typing import Any


def chat_gate_preview_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """Describe the current chat-gate result without changing runtime behavior."""

    send_sop = bool(result.get("send_sop"))
    need_ai_reply = bool(result.get("need_ai_reply"))
    mode = str(result.get("mode") or result.get("route") or "").strip()
    reply_messages = result.get("reply_messages") if isinstance(result.get("reply_messages"), list) else []
    has_content_candidate = bool(reply_messages or result.get("sop_pack_id"))

    route = "ai_reply"
    if send_sop and need_ai_reply:
        route = "content_and_ai_graph"
    elif send_sop:
        route = "direct_content"
    elif _is_terminal_no_reply(mode, result):
        route = "no_reply"
    elif need_ai_reply:
        route = "ai_reply"

    commit_policy = "none"
    if route == "direct_content":
        commit_policy = "already_committed_by_chat_gate"
    elif route == "content_and_ai_graph":
        commit_policy = "defer_sop_commit_until_ai_reply_is_usable"

    return _drop_empty(
        {
            "schema_version": "chat_gate_preview_v1",
            "route": route,
            "legacy_mode": mode,
            "has_content_candidate": has_content_candidate,
            "content_candidate": {
                "sop_pack_id": str(result.get("sop_pack_id") or ""),
                "message_count": len(reply_messages),
                "message_types": [
                    str(message.get("type") or "")
                    for message in reply_messages
                    if isinstance(message, dict) and str(message.get("type") or "")
                ],
            },
            "commit_policy": commit_policy,
            "reason": str(result.get("reason") or ""),
            "source": "current_sop_gate_result_shadow",
        }
    )


def _is_terminal_no_reply(mode: str, result: dict[str, Any]) -> bool:
    return (
        mode == "ignored_platform_auto_message"
        and not result.get("send_sop")
        and not result.get("need_ai_reply")
    )


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
