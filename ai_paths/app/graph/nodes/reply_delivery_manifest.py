from __future__ import annotations

import hashlib
import json
from typing import Any


EFFECT_TRUST_SCENE_IDS = {"effect_definition_trust", "one_session_effect"}


def build_sop_delivery_manifest(sop_gate: dict[str, Any]) -> dict[str, Any]:
    """Convert the Gate-selected SOP sequence into a stable delivery contract."""

    messages = sop_gate.get("reply_messages") if isinstance(sop_gate.get("reply_messages"), list) else []
    route = str(sop_gate.get("route") or sop_gate.get("mode") or "").strip()
    pack_id = str(sop_gate.get("sop_pack_id") or "").strip()
    gate_selected = bool(sop_gate.get("send_sop")) or route in {"sop_only", "ai_then_sop"}
    if not gate_selected or not messages:
        return {
            "active": False,
            "source": "chat_sop_gate",
            "sop_pack_id": pack_id,
            "route": route,
            "reason": "gate_did_not_select_sop_delivery",
            "messages": [],
        }

    manifest_messages: list[dict[str, Any]] = []
    for index, item in enumerate(messages, start=1):
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or "").strip()
        if not message_type:
            continue
        content = item.get("content")
        manifest_messages.append(
            {
                "source_order": _safe_order(item.get("order"), index),
                "message_type": message_type,
                "required": True,
                "source_pack_id": pack_id,
                "content": content,
                "content_fingerprint": _content_fingerprint(message_type, content),
                "authorization": "planner_payment" if message_type == "payment_collection" else "gate_selected",
            }
        )
    manifest_messages.sort(key=lambda item: int(item.get("source_order") or 0))
    return {
        "active": bool(manifest_messages),
        "source": "chat_sop_gate",
        "sop_pack_id": pack_id,
        "route": route,
        "mode": "preserve_required_messages",
        "core_fact_contract": "activity_intro_v1" if pack_id == "s10_activity_intro" else "",
        "messages": manifest_messages,
    }


def authorize_sop_delivery_manifest(
    manifest: Any,
    *,
    payment_decision: Any,
    precision_scene_id: str,
    delivery_decision: Any = None,
) -> dict[str, Any]:
    raw = dict(manifest) if isinstance(manifest, dict) else {}
    messages = raw.get("messages") if isinstance(raw.get("messages"), list) else []
    scene_id = str(precision_scene_id or "").strip()
    decision = normalize_sop_delivery_decision(delivery_decision, manifest=raw)
    if not raw.get("active") or not messages:
        return {**raw, "active": False, "messages": [], "delivery_decision": decision}
    if scene_id in EFFECT_TRUST_SCENE_IDS:
        return {
            **raw,
            "active": False,
            "messages": [],
            "reason": "effect_trust_scene_owns_current_turn",
            "delivery_decision": decision,
            "suppressed_message_count": len(messages),
        }

    if decision["action"] != "deliver_now":
        return {
            **raw,
            "active": False,
            "messages": [],
            "reason": f"planner_{decision['action']}",
            "delivery_decision": decision,
            "suppressed_message_count": len(messages),
        }

    payment = payment_decision if isinstance(payment_decision, dict) else {}
    payment_authorized = str(payment.get("action") or "").strip() in {"send_now", "resend"}
    authorized: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("message_type") or "") == "payment_collection" and not payment_authorized:
            suppressed.append({**item, "suppressed_reason": "planner_payment_not_authorized"})
            continue
        authorized.append(dict(item))
    return {
        **raw,
        "active": bool(authorized),
        "messages": authorized,
        "payment_authorized": payment_authorized,
        "delivery_decision": decision,
        "suppressed_messages": suppressed,
    }


def normalize_sop_delivery_decision(value: Any, *, manifest: Any) -> dict[str, str]:
    """Normalize the Planner-owned decision without inferring business semantics."""

    raw = value if isinstance(value, dict) else {}
    candidate = manifest if isinstance(manifest, dict) else {}
    route = str(candidate.get("route") or "").strip()
    candidate_pack_id = str(candidate.get("sop_pack_id") or "").strip()
    action = str(raw.get("action") or "").strip()
    if action not in {"deliver_now", "defer", "suppress"}:
        # sop_only is already a direct Gate delivery decision. ai_then_sop remains
        # a candidate until Planner explicitly accepts it for the current turn.
        action = "deliver_now" if route == "sop_only" else "defer"
    requested_pack_id = str(raw.get("sop_pack_id") or "").strip()
    if action == "deliver_now" and requested_pack_id and requested_pack_id != candidate_pack_id:
        action = "defer"
    return {
        "action": action,
        "sop_pack_id": candidate_pack_id,
        "reason": str(raw.get("reason") or "").strip()[:240],
    }


def merge_manifest_into_reply_contract(
    reply_contract: Any,
    manifest: Any,
) -> dict[str, Any]:
    contract = dict(reply_contract) if isinstance(reply_contract, dict) else {}
    required = [
        dict(item) if isinstance(item, dict) else {"message_type": str(item or "").strip()}
        for item in (contract.get("required_deliveries") or [])
        if (isinstance(item, dict) and str(item.get("message_type") or item.get("type") or "").strip())
        or (not isinstance(item, dict) and str(item or "").strip())
    ]
    raw = manifest if isinstance(manifest, dict) else {}
    if raw.get("active"):
        manifest_pack_id = str(raw.get("sop_pack_id") or "").strip()
        manifest_types = {
            str(item.get("message_type") or "").strip()
            for item in raw.get("messages") or []
            if isinstance(item, dict) and str(item.get("message_type") or "").strip()
        }
        required = [
            item
            for item in required
            if not (
                (
                    manifest_pack_id
                    and str(item.get("source_pack_id") or "").strip() == manifest_pack_id
                    and not str(item.get("delivery_role") or "").strip()
                )
                or (
                    str(item.get("message_type") or "") in manifest_types
                    and not str(item.get("source_pack_id") or "").strip()
                    and not str(item.get("asset_id") or "").strip()
                    and not str(item.get("delivery_role") or "").strip()
                )
            )
        ]
        for item in raw.get("messages") or []:
            if not isinstance(item, dict):
                continue
            required.append(
                {
                    "message_type": str(item.get("message_type") or "").strip(),
                    "asset_id": str(item.get("asset_id") or "").strip(),
                    "source_pack_id": str(item.get("source_pack_id") or "").strip(),
                    "source_order": int(item.get("source_order") or 0),
                    "required": True,
                    "content": item.get("content"),
                    "content_fingerprint": str(item.get("content_fingerprint") or ""),
                    "authorization": str(item.get("authorization") or ""),
                }
            )
    contract["required_deliveries"] = required
    contract["delivery_manifest_active"] = bool(raw.get("active"))
    contract["delivery_manifest_pack_id"] = str(raw.get("sop_pack_id") or "")
    contract["delivery_manifest_core_fact_contract"] = str(raw.get("core_fact_contract") or "")
    return contract


def manifest_image_urls(manifest: Any) -> set[str]:
    raw = manifest if isinstance(manifest, dict) else {}
    urls: set[str] = set()
    for item in raw.get("messages") or []:
        if not isinstance(item, dict) or str(item.get("message_type") or "") != "image":
            continue
        url = message_content_text(item.get("content"))
        if url:
            urls.add(url)
    return urls


def message_content_text(content: Any) -> str:
    if isinstance(content, dict):
        for key in ("text", "url", "image_url", "content"):
            value = content.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""
    return str(content or "").strip()


def _safe_order(value: Any, fallback: int) -> int:
    try:
        order = int(value)
    except (TypeError, ValueError):
        order = fallback
    return order if order > 0 else fallback


def _content_fingerprint(message_type: str, content: Any) -> str:
    encoded = json.dumps(
        {"type": message_type, "content": content},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]
