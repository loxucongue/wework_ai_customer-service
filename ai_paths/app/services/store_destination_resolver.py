from __future__ import annotations

import copy
from typing import Any

from app.graph.state import AgentState
from app.prompts.store_destination_resolver import STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
from app.services.model_client import ModelClient


_REQUEST_KINDS = {
    "match_location",
    "nearest",
    "list",
    "store_detail",
    "compare",
    "reuse_store",
    "clarify",
}
_PRECISIONS = {
    "coordinates",
    "exact_address",
    "poi",
    "village",
    "township",
    "district",
    "city",
    "province",
    "unknown",
}
_DETAIL_KINDS = {"address", "arrival_guidance", "navigation", "parking", "hours", "none"}
_CONFIDENCE = {"high", "medium", "low"}


async def resolve_active_store_destination(
    *,
    model_client: ModelClient | None,
    state: AgentState,
    tool: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the active location reference without making a store or sales decision."""

    payload, valid_refs, customer_refs = _destination_input(state, tool)
    fallback = _fallback_resolution(payload, tool)
    if model_client is None or not model_client.available:
        return {**fallback, "resolver_status": "model_unavailable"}
    try:
        raw = await model_client.chat_json(
            [
                {"role": "system", "content": STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "请根据以下事实解析当前门店查询目的地。输入中不含门店候选，"
                        "所以不要推荐或猜测门店。\n"
                        + _json_text(payload)
                    ),
                },
            ],
            tier="store_destination",
            temperature=0.0,
            max_parallel_candidates=3,
        )
    except Exception as exc:
        return {
            **fallback,
            "resolver_status": "model_failed",
            "resolver_error": f"{type(exc).__name__}: {exc}"[:500],
        }
    normalized, violations = _normalize_resolution(
        raw,
        valid_refs=valid_refs,
        customer_refs=customer_refs,
    )
    if violations:
        return {
            **fallback,
            "resolver_status": "invalid_model_output",
            "resolver_violations": violations,
        }
    return {
        **normalized,
        "source_query": _source_query_for_refs(payload, normalized.get("evidence_refs") or []),
        "resolver_status": "ok",
    }


def _destination_input(
    state: AgentState,
    tool: dict[str, Any],
) -> tuple[dict[str, Any], set[str], set[str]]:
    shared = state.get("shared_context") if isinstance(state.get("shared_context"), dict) else {}
    current = shared.get("current_message") if isinstance(shared.get("current_message"), dict) else {}
    current_content = str(
        current.get("content")
        or current.get("raw_content")
        or state.get("normalized_content")
        or state.get("content")
        or ""
    ).strip()
    conversation: list[dict[str, Any]] = []
    valid_refs = {"current_message"}
    customer_refs = {"current_message"}
    for index, item in enumerate(shared.get("conversation") if isinstance(shared.get("conversation"), list) else []):
        if not isinstance(item, dict):
            continue
        ref = str(item.get("message_ref") or f"conv_{index + 1:03d}").strip()
        content = str(item.get("content") or item.get("text") or "").strip()
        if not ref or not content or ref == "current_message":
            continue
        valid_refs.add(ref)
        role = str(item.get("role") or "").strip().lower()
        if role in {"customer", "user", "external", "inbound"}:
            customer_refs.add(ref)
        conversation.append(
            {
                "message_ref": ref,
                "role": role,
                "timestamp": str(item.get("timestamp") or item.get("time") or ""),
                "content": content,
            }
        )
    location_card = {}
    facts = shared.get("authoritative_facts") if isinstance(shared.get("authoritative_facts"), dict) else {}
    if isinstance(facts.get("location_card"), dict):
        location_card = copy.deepcopy(facts["location_card"])
    elif isinstance(state.get("location_card"), dict):
        location_card = copy.deepcopy(state["location_card"])
    return (
        {
            "current_message": {
                "message_ref": "current_message",
                "message_type": str(current.get("message_type") or state.get("message_type") or "text"),
                "content": current_content,
            },
            "conversation": conversation,
            "location_card": location_card,
            "planner_hint": {
                "destination_hint": str(
                    tool.get("destination_hint")
                    or tool.get("query")
                    or tool.get("origin")
                    or tool.get("address")
                    or ""
                ).strip(),
                "purpose": str(tool.get("purpose") or "").strip(),
                "evidence_refs": [str(item) for item in (tool.get("evidence_refs") or []) if str(item)],
            },
        },
        valid_refs,
        customer_refs,
    )


def _fallback_resolution(payload: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
    current = payload.get("current_message") if isinstance(payload.get("current_message"), dict) else {}
    location_card = payload.get("location_card") if isinstance(payload.get("location_card"), dict) else {}
    card_address = " ".join(
        dict.fromkeys(
            str(location_card.get(key) or "").strip()
            for key in ("address", "location_address", "title", "location_title")
            if str(location_card.get(key) or "").strip()
        )
    ).strip()
    coordinates = str(location_card.get("coordinates") or location_card.get("location") or "").strip()
    hint = str(
        tool.get("destination_hint")
        or tool.get("query")
        or tool.get("origin")
        or tool.get("address")
        or ""
    ).strip()
    # A generic customer utterance is not a location fact. When the resolver is
    # unavailable, only a structured location card or a router-grounded hint may
    # be geocoded; otherwise the workflow must request the missing location.
    destination = card_address or coordinates or hint
    precision = "coordinates" if coordinates else "unknown"
    return {
        "request_kind": "match_location",
        "destination_query": destination,
        "destination_precision": precision,
        "administrative_context": {},
        "destination_subject": "unknown",
        "named_store": "",
        "detail_kind": "none",
        "evidence_refs": ["current_message"] if destination else [],
        "superseded_location_refs": [],
        "confidence": "high" if coordinates else "low",
        "needs_clarification": not bool(destination),
        "geocode_before_clarification": bool(destination),
        "reason": "protocol_or_explicit_tool_hint_fallback",
        "source_query": str(current.get("content") or destination).strip(),
    }


def _normalize_resolution(
    raw: Any,
    *,
    valid_refs: set[str],
    customer_refs: set[str],
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, dict):
        return {}, ["resolver_output_not_object"]
    request_kind = str(raw.get("request_kind") or "").strip()
    precision = str(raw.get("destination_precision") or "").strip()
    detail_kind = str(raw.get("detail_kind") or "none").strip()
    confidence = str(raw.get("confidence") or "").strip()
    refs = [str(item).strip() for item in raw.get("evidence_refs") or [] if str(item).strip()]
    superseded = [
        str(item).strip()
        for item in raw.get("superseded_location_refs") or []
        if str(item).strip()
    ]
    violations: list[str] = []
    if request_kind not in _REQUEST_KINDS:
        violations.append("invalid_request_kind")
    if precision not in _PRECISIONS:
        violations.append("invalid_destination_precision")
    if detail_kind not in _DETAIL_KINDS:
        violations.append("invalid_detail_kind")
    if confidence not in _CONFIDENCE:
        violations.append("invalid_confidence")
    if not refs or any(ref not in valid_refs for ref in refs):
        violations.append("invalid_evidence_refs")
    elif not any(ref in customer_refs for ref in refs):
        violations.append("destination_not_grounded_in_customer_evidence")
    if any(ref not in valid_refs for ref in superseded):
        violations.append("invalid_superseded_location_refs")
    destination = str(raw.get("destination_query") or "").strip()
    named_store = str(raw.get("named_store") or "").strip()
    administrative_context = raw.get("administrative_context")
    if not isinstance(administrative_context, dict):
        administrative_context = {}
    administrative_context = {
        key: str(administrative_context.get(key) or "").strip()
        for key in ("province", "city", "district")
        if str(administrative_context.get(key) or "").strip()
    }
    needs_clarification = bool(raw.get("needs_clarification"))
    geocode_before_clarification = bool(raw.get("geocode_before_clarification", True))
    if not destination and not named_store and not needs_clarification:
        violations.append("missing_destination_without_clarification")
    return (
        {
            "request_kind": request_kind,
            "destination_query": destination,
            "destination_precision": precision,
            "administrative_context": administrative_context,
            "destination_subject": str(raw.get("destination_subject") or "unknown").strip(),
            "named_store": named_store,
            "detail_kind": detail_kind,
            "evidence_refs": list(dict.fromkeys(refs)),
            "superseded_location_refs": list(dict.fromkeys(superseded)),
            "confidence": confidence,
            "needs_clarification": needs_clarification,
            "geocode_before_clarification": geocode_before_clarification,
            "reason": str(raw.get("reason") or "").strip()[:500],
        },
        violations,
    )


def _json_text(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _source_query_for_refs(payload: dict[str, Any], refs: list[str]) -> str:
    ref_set = {str(item) for item in refs if str(item)}
    current = payload.get("current_message") if isinstance(payload.get("current_message"), dict) else {}
    current_text = str(current.get("content") or "").strip()
    if "current_message" in ref_set and current_text:
        return current_text
    conversation = payload.get("conversation") if isinstance(payload.get("conversation"), list) else []
    for item in reversed(conversation):
        if not isinstance(item, dict):
            continue
        if str(item.get("message_ref") or "") not in ref_set:
            continue
        if str(item.get("role") or "").lower() not in {"customer", "user", "external", "inbound"}:
            continue
        text = str(item.get("content") or "").strip()
        if text:
            return text
    return current_text
