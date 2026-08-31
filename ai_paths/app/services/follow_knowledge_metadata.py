from __future__ import annotations

from typing import Any


def adopted_follow_knowledge_metadata(state: dict[str, Any]) -> dict[str, Any]:
    """Build aggregation metadata from references actually adopted by Reply."""

    context = state.get("request_context") if isinstance(state.get("request_context"), dict) else {}
    if str(context.get("interface_version") or "").strip().lower() != "v3":
        return {}
    if not state.get("reply_messages"):
        return {}
    if "fallback" in _text(state.get("reply_source")).lower():
        return {}

    usage = state.get("reply_knowledge_use") if isinstance(state.get("reply_knowledge_use"), dict) else {}
    recall = state.get("sales_recall") if isinstance(state.get("sales_recall"), dict) else {}
    sequence = _selected_sequence(recall, usage)
    script = _selected_script(recall, usage)
    output: dict[str, Any] = {}
    if sequence:
        output["followSequence"] = sequence
    if script:
        output["followScript"] = script
    return output


def _selected_sequence(recall: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    sequence_id = _text(usage.get("sequence_id"))
    step_id = _text(usage.get("step_id"))
    if not sequence_id:
        return {}
    sequence = next(
        (
            item
            for item in recall.get("sequence_candidates") or []
            if isinstance(item, dict) and _text(item.get("sequence_id")) == sequence_id
        ),
        None,
    )
    if not isinstance(sequence, dict):
        return {}
    step = next(
        (
            item
            for item in sequence.get("steps") or []
            if isinstance(item, dict) and _text(item.get("step_id")) == step_id
        ),
        {},
    )
    if not step:
        return {}
    return {
        "id": _id_value(sequence_id),
        "sequenceName": _text(sequence.get("sequence_name")),
        "checkpointCode": _text(sequence.get("checkpoint_code")),
        "checkpointName": _text(sequence.get("checkpoint_name")),
        "sortOrder": _integer(step.get("sort_order")),
        "actionCode": _text(step.get("action_code")),
        "actionName": _text(step.get("action_name")),
    }


def _selected_script(recall: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    selected_ids = [
        _text(value)
        for value in usage.get("selected_script_ids") or []
        if _text(value)
    ]
    if not selected_ids:
        return {}
    primary_id = selected_ids[0]
    script = next(
        (
            item
            for item in recall.get("candidates") or []
            if isinstance(item, dict)
            and primary_id in {_text(item.get("source_id")), _text(item.get("script_code")), _text(item.get("script_id"))}
        ),
        None,
    )
    if not isinstance(script, dict):
        return {}
    checkpoint_type = script.get("checkpoint_type") if isinstance(script.get("checkpoint_type"), dict) else {}
    checkpoint_tag = script.get("checkpoint_tag") if isinstance(script.get("checkpoint_tag"), dict) else {}
    return {
        "id": _id_value(script.get("script_id")),
        "scriptCode": _text(script.get("source_id") or script.get("script_code")),
        "scriptName": _text(script.get("script_name")),
        "bodyText": _script_body_text(script),
        "checkpointCode": _text(script.get("checkpoint_code")),
        "checkpointTypeId": _integer(checkpoint_type.get("id")),
        "checkpointTypeName": _text(checkpoint_type.get("name") or script.get("checkpoint_name")),
        "checkpointTagId": _integer(checkpoint_tag.get("id")),
        "checkpointTagName": _text(checkpoint_tag.get("name")),
        "checkpointName": _text(script.get("checkpoint_name") or checkpoint_type.get("name")),
        "actionCode": _text(script.get("action_code")),
        "actionName": _text(script.get("action_name")),
    }


def _script_body_text(script: dict[str, Any]) -> str:
    body = _text(script.get("reference_text") or script.get("body_text"))
    if body:
        return body
    texts: list[str] = []
    for paragraph in script.get("paragraphs") or []:
        if not isinstance(paragraph, dict):
            continue
        for message in paragraph.get("messages") or []:
            if not isinstance(message, dict) or _text(message.get("type")) != "text":
                continue
            content = _text(message.get("content"))
            if content:
                texts.append(content)
    return "\n".join(texts)


def _id_value(value: Any) -> int | str:
    text = _text(value)
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()
