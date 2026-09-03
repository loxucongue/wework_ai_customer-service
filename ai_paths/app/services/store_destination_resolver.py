from __future__ import annotations

import copy
import re
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
    if fallback.get("destination_query") and str(fallback.get("destination_source") or "") in {
        "location_card_address",
        "location_card_coordinates",
    }:
        return {**fallback, "resolver_status": "deterministic_destination_evidence"}
    if model_client is None or not model_client.available:
        return {**fallback, "resolver_status": "model_unavailable"}
    messages = _resolver_messages(payload)
    try:
        raw = await model_client.chat_json(
            messages,
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
        fallback_client = _fallback_only_model_client(model_client)
        if fallback_client is not None:
            try:
                fallback_raw = await fallback_client.chat_json(
                    messages,
                    tier="store_destination",
                    temperature=0.0,
                    max_parallel_candidates=1,
                )
                fallback_normalized, fallback_violations = _normalize_resolution(
                    fallback_raw,
                    valid_refs=valid_refs,
                    customer_refs=customer_refs,
                )
                if not fallback_violations:
                    return {
                        **fallback_normalized,
                        "source_query": _source_query_for_refs(
                            payload,
                            fallback_normalized.get("evidence_refs") or [],
                        ),
                        "resolver_status": "ok_fallback_model",
                    }
                violations = [*violations, *[f"fallback:{item}" for item in fallback_violations]]
            except Exception as exc:
                violations.append(f"fallback:{type(exc).__name__}")
            finally:
                await fallback_client.aclose()
        return {**fallback, "resolver_status": "invalid_model_output", "resolver_violations": violations}
    if (
        normalized.get("needs_clarification")
        and fallback.get("destination_query")
        and fallback.get("reason")
        in {"structured_current_location_fallback", "recent_assistant_store_reference_fallback"}
    ):
        return {**fallback, "resolver_status": "model_missed_available_store_location"}
    return {
        **normalized,
        "source_query": _source_query_for_refs(payload, normalized.get("evidence_refs") or []),
        "resolver_status": "ok",
    }


def _resolver_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "请根据以下事实解析当前门店查询目的地。输入中不含门店候选，"
                "所以不要推荐或猜测门店。\n" + _json_text(payload)
            ),
        },
    ]


def _fallback_only_model_client(model_client: ModelClient) -> ModelClient | None:
    fallback_models = [
        item.strip()
        for item in str(model_client.settings.model_store_destination_fallbacks or "").split(",")
        if item.strip()
    ]
    if not fallback_models:
        return None
    return ModelClient(
        model_client.settings.model_copy(
            update={
                "model_store_destination": fallback_models[0],
                "model_store_destination_fallbacks": ",".join(fallback_models[1:]),
                "model_hedge_max_parallel": 1,
            }
        )
    )


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
    if not conversation:
        for index, raw in enumerate(state.get("conversation_history") if isinstance(state.get("conversation_history"), list) else []):
            content = str(raw or "").strip()
            if not content:
                continue
            role = "assistant" if re.match(r"^\s*(小贝|助手|客服)\s*[:：]", content) else "customer"
            ref = f"history_{index + 1:03d}"
            valid_refs.add(ref)
            if role == "customer":
                customer_refs.add(ref)
            conversation.append(
                {
                    "message_ref": ref,
                    "role": role,
                    "timestamp": "",
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
    hint = ""
    hint_source = ""
    for key in ("destination_hint", "origin", "address", "query"):
        value = str(tool.get(key) or "").strip()
        if value:
            hint = value
            hint_source = f"tool.{key}"
            break
    structured_current = _structured_current_location_query(str(current.get("content") or ""))
    recent_assistant_reference = _recent_assistant_store_reference(payload)
    generic_detail_hint = _is_generic_store_detail_hint(hint)
    # A generic customer utterance is not a location fact. When the resolver is
    # unavailable, only a structured location card or a router-grounded hint may
    # be geocoded; otherwise the workflow must request the missing location.
    destination = (
        card_address
        or coordinates
        or ("" if generic_detail_hint else hint)
        or structured_current
        or recent_assistant_reference.get("query", "")
    )
    precision = "coordinates" if coordinates else "unknown"
    reason = "protocol_or_explicit_tool_hint_fallback"
    destination_source = ""
    if card_address:
        destination_source = "location_card_address"
    elif coordinates:
        destination_source = "location_card_coordinates"
    elif hint and not generic_detail_hint:
        destination_source = hint_source
    elif structured_current:
        destination_source = "structured_current_location"
    elif recent_assistant_reference.get("query"):
        destination_source = "recent_assistant_store_reference"
    if structured_current and not (card_address or coordinates or hint):
        reason = "structured_current_location_fallback"
    elif recent_assistant_reference.get("query") and not (card_address or coordinates or hint or structured_current):
        reason = "recent_assistant_store_reference_fallback"
    elif recent_assistant_reference.get("query") and generic_detail_hint and not (card_address or coordinates or structured_current):
        reason = "recent_assistant_store_reference_fallback"
    source_query = (
        str(current.get("content") or "").strip()
        if reason != "recent_assistant_store_reference_fallback"
        else str(recent_assistant_reference.get("query") or "").strip()
    )
    return {
        "request_kind": "match_location",
        "destination_query": destination,
        "destination_precision": precision,
        "administrative_context": {},
        "destination_subject": "unknown",
        "named_store": "",
        "detail_kind": "none",
        "evidence_refs": [
            ref
            for ref in ("current_message", recent_assistant_reference.get("message_ref", ""))
            if destination and ref
        ],
        "superseded_location_refs": [],
        "confidence": "high" if coordinates else "low",
        "needs_clarification": not bool(destination),
        "geocode_before_clarification": bool(destination),
        "reason": reason,
        "destination_source": destination_source,
        "source_query": source_query or str(destination).strip(),
    }


def _structured_current_location_query(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(?:小贝|助手|客服|AI回复)\s*[:：]\s*", "", text)
    match = re.match(r"^\s*([\u4e00-\u9fffA-Za-z0-9_ /-]{1,16})\s*[:：]\s*(.+?)\s*$", text)
    if not match:
        return ""
    label = re.sub(r"\s+", "", match.group(1)).lower()
    if not any(marker in label for marker in ("门店", "位置", "地址", "定位", "区域", "商圈", "地标")):
        return ""
    value = match.group(2).strip()
    if len(value) < 2:
        return ""
    return value


def _recent_assistant_store_reference(payload: dict[str, Any]) -> dict[str, str]:
    current = payload.get("current_message") if isinstance(payload.get("current_message"), dict) else {}
    current_text = re.sub(r"\s+", "", str(current.get("content") or "")).lower()
    if not current_text:
        return {}
    asks_resend_or_detail = any(
        marker in current_text
        for marker in (
            "导航",
            "路线",
            "怎么去",
            "重新发",
            "再发",
            "发我",
            "地址",
            "位置",
            "地图",
            "停车",
            "营业",
            "几点",
            "下班",
            "上班",
            "开门",
            "关门",
            "营业时间",
        )
    )
    if not asks_resend_or_detail:
        return {}
    conversation = payload.get("conversation") if isinstance(payload.get("conversation"), list) else []
    for item in reversed(conversation[-8:]):
        if not isinstance(item, dict) or str(item.get("role") or "").strip().lower() != "assistant":
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        structured = _structured_current_location_query(content)
        if structured:
            return {"query": structured, "message_ref": str(item.get("message_ref") or "")}
        compact = re.sub(r"\s+", "", content).lower()
        if "店" not in compact:
            continue
        if not any(marker in compact for marker in ("门店", "位置", "地址", "导航", "发")):
            continue
        return {"query": content, "message_ref": str(item.get("message_ref") or "")}
    return {}


def _is_generic_store_detail_hint(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).lower()
    if not text:
        return False
    remainder = re.sub(
        r"(重新发|再发|发我|发一下|麻烦发|给我|地图|导航|路线|怎么过去|怎么去|"
        r"停车方便吗|有停车场吗|停车场|停车|营业时间|几点下班|几点开门|几点关门|"
        r"营业|地址|位置)",
        "",
        text,
    )
    remainder = re.sub(r"(和|及|以及|还有|都|也|呢|吗|嘛|么|一下|看看|查下|查一下)", "", remainder)
    remainder = re.sub(r"[?？。！!,，、；;：:\-_/\\()\[\]{}]+", "", remainder)
    return not remainder


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
    precision = {
        "address": "exact_address",
        "full_address": "exact_address",
        "road": "poi",
        "street": "poi",
        "landmark": "poi",
        "county": "district",
        "county_level_city": "district",
    }.get(precision, precision)
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
        for key in ("province", "city", "district", "county_level_city", "township")
        if str(administrative_context.get(key) or "").strip()
    }
    candidate_interpretations: list[dict[str, Any]] = []
    for item in raw.get("candidate_interpretations") or []:
        if not isinstance(item, dict):
            continue
        candidate_refs = [
            str(ref).strip()
            for ref in item.get("evidence_refs") or []
            if str(ref).strip() in valid_refs
        ]
        candidate_admin = item.get("administrative_context")
        candidate_admin = candidate_admin if isinstance(candidate_admin, dict) else {}
        candidate_query = str(item.get("destination_query") or "").strip()
        if not candidate_query or not candidate_refs or not any(ref in customer_refs for ref in candidate_refs):
            continue
        candidate_interpretations.append(
            {
                "destination_query": candidate_query,
                "administrative_context": {
                    key: str(candidate_admin.get(key) or "").strip()
                    for key in ("province", "city", "district", "county_level_city", "township")
                    if str(candidate_admin.get(key) or "").strip()
                },
                "poi_query": str(item.get("poi_query") or "").strip(),
                "confidence": str(item.get("confidence") or "low").strip()
                if str(item.get("confidence") or "low").strip() in _CONFIDENCE
                else "low",
                "evidence_refs": list(dict.fromkeys(candidate_refs)),
            }
        )
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
            "poi_query": str(raw.get("poi_query") or "").strip(),
            "candidate_interpretations": candidate_interpretations,
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
