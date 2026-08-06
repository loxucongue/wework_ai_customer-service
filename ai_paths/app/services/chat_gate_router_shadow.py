from __future__ import annotations

from typing import Any


STATIC_DIRECT_MESSAGE_TYPES = {"text", "image", "video"}
MAX_DIRECT_REPLY_CANDIDATE_MESSAGES = 1


def chat_gate_router_shadow_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """Map the legacy SOP Chat Gate result into the target router schema.

    The mapping is observational. It must not decide new business semantics or
    change the current runtime branch.
    """

    active_task = result.get("active_task") if isinstance(result.get("active_task"), dict) else {}
    required_tool = str(active_task.get("required_tool") or "").strip()
    send_sop = bool(result.get("send_sop"))
    need_ai_reply = bool(result.get("need_ai_reply"))
    sop_pack_id = str(result.get("sop_pack_id") or "").strip()
    priority_question_id = str(result.get("priority_question_id") or "").strip()
    reply_messages = result.get("reply_messages") if isinstance(result.get("reply_messages"), list) else []

    route_suggestion = _route_suggestion(
        send_sop=send_sop,
        need_ai_reply=need_ai_reply,
        required_tool=required_tool,
        mode=str(result.get("mode") or result.get("route") or "").strip(),
    )
    direct_candidate_audit = _direct_reply_candidate_audit(reply_messages)

    return _drop_empty(
        {
            "schema_version": "chat_gate_router_shadow_v1",
            "current_question": {
                "intent": str(active_task.get("type") or priority_question_id or result.get("mode") or "").strip(),
                "must_answer": route_suggestion != "no_reply",
                "evidence_refs": _evidence_refs(active_task),
            },
            "selected_content": {
                "sop_pack_ids": [sop_pack_id] if sop_pack_id else [],
                "precision_qa_ids": [priority_question_id] if priority_question_id else [],
                "simple_scene_id": _simple_scene_id(active_task, sop_pack_id, priority_question_id),
                "usage": "direct" if route_suggestion == "direct_text" else "reference",
                "message_count": len(reply_messages),
                "message_types": [
                    str(message.get("type") or "")
                    for message in reply_messages
                    if isinstance(message, dict) and str(message.get("type") or "")
                ],
            },
            "dynamic_fact_expectation": {
                "requirement": "required" if required_tool else "none",
                "capability_classes": [required_tool] if required_tool else [],
            },
            "route_suggestion": route_suggestion,
            "direct_reply_candidate": reply_messages if route_suggestion == "direct_text" else [],
            "direct_reply_candidate_audit": direct_candidate_audit,
            "commit_boundary": _commit_boundary(route_suggestion=route_suggestion),
            "handoff_notes": [
                "shadow_from_current_sop_gate_result",
                "no_runtime_behavior_change",
            ],
            "legacy": {
                "mode": str(result.get("mode") or result.get("route") or "").strip(),
                "coverage": str(result.get("coverage") or "").strip(),
                "reason": str(result.get("reason") or "").strip(),
            },
        }
    )


def _route_suggestion(*, send_sop: bool, need_ai_reply: bool, required_tool: str, mode: str) -> str:
    if _is_terminal_no_reply(mode=mode, send_sop=send_sop, need_ai_reply=need_ai_reply):
        return "no_reply"
    if send_sop and not need_ai_reply:
        return "direct_text"
    if send_sop and required_tool:
        return "content_and_tools"
    if send_sop and need_ai_reply:
        return "content_only_reply"
    if required_tool:
        return "tools_only"
    return "content_only_reply" if need_ai_reply else "no_reply"


def _is_terminal_no_reply(*, mode: str, send_sop: bool, need_ai_reply: bool) -> bool:
    return mode in {
        "ignored_platform_auto_message",
        "platform_auto_opening_duplicate",
        "platform_auto_opening_config_error",
    } and not send_sop and not need_ai_reply


def _evidence_refs(active_task: dict[str, Any]) -> list[str]:
    refs = [
        str(active_task.get("customer_evidence_ref") or "").strip(),
        str(active_task.get("assistant_evidence_ref") or "").strip(),
    ]
    return [ref for ref in refs if ref]


def _simple_scene_id(active_task: dict[str, Any], sop_pack_id: str, priority_question_id: str) -> str:
    if sop_pack_id or priority_question_id:
        return ""
    task_type = str(active_task.get("type") or "").strip()
    return task_type if task_type and task_type not in {"sop_delivery", "precision_answer"} else ""


def _commit_boundary(*, route_suggestion: str) -> dict[str, Any]:
    return {
        "schema_version": "chat_gate_commit_boundary_v1",
        "shadow_output_only": True,
        "this_shadow_creates_sop_task": False,
        "this_shadow_updates_send_once": False,
        "this_shadow_sends_customer_messages": False,
        "this_shadow_writes_database": False,
        "target_commit_owner": "reply_chain_commit_phase_after_reply_validation",
        "target_direct_route_requires_commit_phase": route_suggestion == "direct_text",
    }


def _direct_reply_candidate_audit(reply_messages: list[Any]) -> dict[str, Any]:
    message_types = [
        str(message.get("type") or "").strip()
        for message in reply_messages
        if isinstance(message, dict) and str(message.get("type") or "").strip()
    ]
    dynamic_types = [message_type for message_type in message_types if message_type not in STATIC_DIRECT_MESSAGE_TYPES]
    blockers: list[str] = []
    if reply_messages and not message_types:
        blockers.append("candidate_message_type_missing")
    if len(reply_messages) > MAX_DIRECT_REPLY_CANDIDATE_MESSAGES:
        blockers.append(
            "candidate_message_count_exceeds_direct_reply_limit:"
            f"{len(reply_messages)}>{MAX_DIRECT_REPLY_CANDIDATE_MESSAGES}"
        )
    blockers.extend(f"dynamic_structure_message_type:{message_type}" for message_type in dynamic_types)
    for index, message in enumerate(reply_messages):
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "").strip()
        if message_type in STATIC_DIRECT_MESSAGE_TYPES and not _static_message_content_present(message):
            blockers.append(f"candidate_message_content_empty:{index}:{message_type}")
    return {
        "schema_version": "chat_gate_direct_reply_candidate_audit_v1",
        "message_count": len(reply_messages),
        "max_direct_reply_candidate_messages": MAX_DIRECT_REPLY_CANDIDATE_MESSAGES,
        "message_types": message_types,
        "static_message_types_allowed": sorted(STATIC_DIRECT_MESSAGE_TYPES),
        "safe_for_direct_reply_static_candidate": bool(reply_messages) and not blockers,
        "blockers": blockers,
    }


def _static_message_content_present(message: dict[str, Any]) -> bool:
    message_type = str(message.get("type") or "").strip()
    content = message.get("content")
    if message_type == "text":
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, dict):
            return bool(str(content.get("text") or content.get("content") or "").strip())
        return False
    if message_type in {"image", "video"}:
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, dict):
            return bool(
                str(
                    content.get("url")
                    or content.get("image_url")
                    or content.get("video_url")
                    or content.get("media_url")
                    or content.get("content")
                    or ""
                ).strip()
            )
        return False
    return False


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in output.items() if item not in ("", None, {}, [])}
    if isinstance(value, list):
        output = [_drop_empty(item) for item in value]
        return [item for item in output if item not in ("", None, {}, [])]
    return value
