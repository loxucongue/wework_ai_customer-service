from __future__ import annotations

import base64
from hashlib import sha256
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5
import zlib

from app.services.first_day_outreach_log import redact_first_day_log_value


GENERATION_STATUS_GENERATING = "generating"
GENERATION_STATUS_COMPLETED = "completed"
GENERATION_STATUS_FALLBACK_PENDING = "fallback_pending"
GENERATION_STATUS_RECOVERY_CLAIMED = "recovery_claimed"
GENERATION_STATUS_RECOVERED = "recovered"
GENERATION_STATUS_RECOVERY_FAILED = "recovery_failed"
GENERATION_STATUS_MANUAL_REVIEW = "manual_review"


def v3_generation_key(
    *,
    corp_id: str,
    wechat: str,
    external_userid: str,
    msgid: str,
) -> str:
    """Return the opaque, tenant-scoped idempotency key for one platform message.

    Every identity component is required.  Falling back to ``customer_id`` here
    would merge contacts that the sales-contact contract intentionally keeps
    separate.
    """

    parts = (
        str(corp_id or "").strip(),
        str(wechat or "").strip().casefold(),
        str(external_userid or "").strip(),
        str(msgid or "").strip(),
    )
    if not all(parts):
        return ""
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def v3_response_id(generation_key: str) -> str:
    clean_key = str(generation_key or "").strip()
    if not clean_key:
        return ""
    return str(uuid5(NAMESPACE_URL, f"ai-paths:v3-response:{clean_key}"))


def v3_client_message_id(
    response_id: str,
    index: int,
    *,
    recovery_kind: str = "primary",
) -> str:
    clean_response_id = str(response_id or "").strip()
    if not clean_response_id:
        return ""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"ai-paths:v3-client-message:{clean_response_id}:{str(recovery_kind or 'primary')}:{max(0, int(index))}",
        )
    )


def stable_v3_reply_messages(
    reply_messages: list[dict[str, Any]],
    *,
    response_id: str,
    recovery_kind: str = "primary",
) -> list[dict[str, Any]]:
    """Attach deterministic public message IDs without mutating the caller list."""

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(reply_messages):
        if not isinstance(item, dict):
            continue
        message = dict(item)
        client_message_id = v3_client_message_id(
            response_id,
            index,
            recovery_kind=recovery_kind,
        )
        if client_message_id:
            message["client_message_id"] = client_message_id
        normalized.append(message)
    return normalized


def rebuildable_chat_request_snapshot(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project persisted input into the fields required to rebuild ChatRequest."""

    snapshot = input_snapshot if isinstance(input_snapshot, dict) else {}
    request_context = snapshot.get("request_context")
    return {
        "content": str(snapshot.get("content") or ""),
        "customer_id": str(snapshot.get("customer_id") or ""),
        "platform_customer_id": str(
            snapshot.get("platform_customer_id") or snapshot.get("customer_id") or ""
        ),
        "corp_id": str(snapshot.get("corp_id") or ""),
        "conversation_history": (
            list(snapshot.get("conversation_history") or [])
            if isinstance(snapshot.get("conversation_history"), list)
            else []
        ),
        "file_image": snapshot.get("file_image") or None,
        "user_id": snapshot.get("user_id"),
        "wechat": str(snapshot.get("wechat") or "") or None,
        "external_userid": str(snapshot.get("external_userid") or "") or None,
        "customer_add_wechat_id": snapshot.get("customer_add_wechat_id"),
        "confirmed_store_id": snapshot.get("confirmed_store_id"),
        "confirmed_store_name": snapshot.get("confirmed_store_name"),
        "store_id": snapshot.get("store_id"),
        "store_name": snapshot.get("store_name"),
        "appointment_id": snapshot.get("appointment_id"),
        "appointment_time": snapshot.get("appointment_time"),
        "request_context": dict(request_context) if isinstance(request_context, dict) else {},
    }


def chat_request_recovery_payload(request: Any) -> dict[str, Any]:
    """Build a short-lived, credential-scrubbed ChatRequest recovery payload."""

    request_context = getattr(request, "request_context", {})
    payload = {
        "content": str(getattr(request, "content", "") or ""),
        "customer_id": str(getattr(request, "customer_id", "") or ""),
        "platform_customer_id": str(getattr(request, "platform_customer_id", "") or ""),
        "corp_id": str(getattr(request, "corp_id", "") or ""),
        "conversation_history": list(getattr(request, "conversation_history", []) or []),
        "file_image": getattr(request, "file_image", None),
        "user_id": getattr(request, "user_id", None),
        "wechat": getattr(request, "wechat", None),
        "external_userid": getattr(request, "external_userid", None),
        "customer_add_wechat_id": getattr(request, "customer_add_wechat_id", None),
        "confirmed_store_id": getattr(request, "confirmed_store_id", None),
        "confirmed_store_name": getattr(request, "confirmed_store_name", None),
        "store_id": getattr(request, "store_id", None),
        "store_name": getattr(request, "store_name", None),
        "appointment_id": getattr(request, "appointment_id", None),
        "appointment_time": getattr(request, "appointment_time", None),
        "request_context": dict(request_context) if isinstance(request_context, dict) else {},
    }
    file_image = payload.get("file_image")
    if isinstance(file_image, str) and file_image.startswith("data:image/") and ";base64," in file_image:
        payload["file_image"] = f"[base64 image omitted: {len(file_image)} chars]"
    return encode_v3_recovery_payload(payload)


def encode_v3_recovery_payload(payload: dict[str, Any]) -> dict[str, Any]:
    scrubbed = redact_first_day_log_value(_scrub_credentials(payload if isinstance(payload, dict) else {}))
    raw = json.dumps(scrubbed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "encoding": "zlib+base64+json",
        "uncompressed_bytes": len(raw),
        "data": base64.b64encode(zlib.compress(raw, level=6)).decode("ascii"),
    }


def decode_v3_recovery_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if str(value.get("encoding") or "") != "zlib+base64+json":
        return dict(value)
    encoded = str(value.get("data") or "")
    if not encoded:
        return {}
    try:
        raw = zlib.decompress(base64.b64decode(encoded, validate=True)).decode("utf-8")
        decoded = json.loads(raw)
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _scrub_credentials(value: Any, key: str = "") -> Any:
    normalized_key = str(key or "").casefold().replace("-", "_")
    if any(
        fragment in normalized_key
        for fragment in ("token", "authorization", "api_key", "apikey", "password", "secret")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _scrub_credentials(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_credentials(item) for item in value]
    return value
