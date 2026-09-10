from __future__ import annotations

import copy
import hashlib
import html
from io import BytesIO
from typing import Any

from app.services.content_capabilities import content_only_candidates
from app.services.material_fingerprint import MAX_MEDIA_BYTES


IDENTITY_VERSION = "media-v1-rgb32-dhash4"
MAX_CATALOG_ITEMS = 10000
MAX_TURN_MEDIA = 128
MEDIA_FIELDS = ("messages", "media", "required_structured_media", "reference_messages", "reply_messages")


def media_url(message: dict[str, Any]) -> str:
    content = message.get("content")
    value = message.get("url") or (
        content.get("url") or content.get("image_url") or content.get("video_url")
        if isinstance(content, dict)
        else content
    )
    return html.unescape(str(value or "").strip())


def identity_key(kind: str, namespace: str, value: str) -> str:
    return hashlib.sha256(f"{kind}\0{namespace}\0{value}".encode()).hexdigest()


def media_aliases(message: dict[str, Any]) -> list[str]:
    kind = str(message.get("type") or "")
    if kind not in {"image", "video"}:
        return []
    result = []
    # Only the normalized, trusted Follow Knowledge file namespace is accepted.
    file_id = str(message.get("file_id") or "")
    if message.get("file_namespace") == "follow_knowledge" and file_id.isdecimal() and int(file_id) > 0:
        result.append(identity_key(kind, "follow_knowledge", file_id))
    url = media_url(message)
    if url.startswith(("https://", "http://")):
        result.append(identity_key(kind, "url", url))
    return result


def contact_key(state: dict[str, Any]) -> str:
    context = state.get("request_context") or {}
    values = [str(state.get(key) or context.get(key) or "").strip() for key in ("corp_id", "wechat", "external_userid")]
    if not all(values):
        return ""
    return identity_key("contact", "v3", "\0".join(values))


def fingerprint(payload: bytes, kind: str) -> dict[str, Any]:
    if not payload or len(payload) > MAX_MEDIA_BYTES:
        raise ValueError("media_size_invalid")
    result = {"sha256": hashlib.sha256(payload).hexdigest(), "version": IDENTITY_VERSION}
    if kind == "image":
        from PIL import Image, ImageOps

        try:
            with Image.open(BytesIO(payload)) as source:
                if source.width * source.height > 20000000 or getattr(source, "n_frames", 1) > 1:
                    raise ValueError("unsupported_format")
                oriented = ImageOps.exif_transpose(source).convert("RGBA")
                background = Image.new("RGBA", oriented.size, "white")
                rgb = Image.alpha_composite(background, oriented).convert("RGB")
                result["image_size"] = [rgb.width, rgb.height]
                result["pixel_sha256"] = hashlib.sha256(rgb.tobytes()).hexdigest()
                result["rgb32"] = list(rgb.resize((32, 32), Image.Resampling.LANCZOS).tobytes())
                pixels = list(rgb.convert("L").resize((9, 8)).getdata())
                bits = 0
                for y in range(8):
                    for x in range(8):
                        bits = (bits << 1) | int(pixels[y * 9 + x] > pixels[y * 9 + x + 1])
                result["perceptual_hash"] = f"{bits:016x}"
        except (OSError, SyntaxError) as exc:
            raise ValueError("unsupported_format") from exc
    elif kind != "video":
        raise ValueError("unsupported_format")
    return result


def same_media(left: dict[str, Any], right: dict[str, Any], kind: str) -> bool:
    if left.get("sha256") and left.get("sha256") == right.get("sha256"):
        return True
    if kind != "image" or left.get("version") != IDENTITY_VERSION or right.get("version") != IDENTITY_VERSION:
        return False
    if (
        left.get("pixel_sha256")
        and left.get("pixel_sha256") == right.get("pixel_sha256")
        and left.get("image_size") == right.get("image_size")
    ):
        return True
    try:
        lw, lh = left["image_size"]
        rw, rh = right["image_size"]
        if abs(lw / lh - rw / rh) > 0.005:
            return False
        if (int(left["perceptual_hash"], 16) ^ int(right["perceptual_hash"], 16)).bit_count() > 4:
            return False
        if not all(8 <= int(row["perceptual_hash"], 16).bit_count() <= 56 for row in (left, right)):
            return False
        a, b = left["rgb32"], right["rgb32"]
        if len(a) != 3072 or len(b) != 3072:
            return False
        delta = [abs(x - y) for x, y in zip(a, b)]
        return max(delta) <= 16 and sum(delta) / len(delta) <= 1.5
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def prepare_catalog(
    records: list[dict[str, Any]], existing: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Build a bounded append/update plan from local bytes, never from a network.

    Explicit canonical_id + override_reason are operator decisions; ambiguous
    automatic matches fail rather than silently joining two existing assets.
    """
    if len(records) + len(existing or []) > MAX_CATALOG_ITEMS:
        raise ValueError("catalog_limit_exceeded")
    known = copy.deepcopy(existing or [])
    output = []
    for record in records:
        kind = str(record.get("type") or "")
        aliases = media_aliases(record)
        if not aliases:
            raise ValueError("identity_unknown")
        payload = record.get("bytes")
        fp = fingerprint(payload, kind) if isinstance(payload, bytes) else {}
        file_id = str(record.get("file_id") or "")
        reliable_id = record.get("file_namespace") == "follow_knowledge" and file_id.isdecimal() and int(file_id) > 0
        override = str(record.get("canonical_id") or "").strip()
        reason = str(record.get("override_reason") or "").strip()
        if override and not reason:
            raise ValueError("override_reason_required")
        if override and (
            len(override) > 96
            or override != override.lower()
            or any(not (c.isascii() and (c.isalnum() or c in "-_:")) for c in override)
        ):
            # MySQL's default identifier-value collation is case insensitive;
            # one canonical spelling prevents SQLite/MySQL equality drift.
            raise ValueError("canonical_id_invalid")
        if override and any(row["canonical_id"] == override and row["media_type"] != kind for row in known):
            raise ValueError("canonical_type_conflict")
        alias_rows = [row for row in known if row["alias_key"] in aliases and row["media_type"] == kind]
        # A URL is a locator, not proof that newly supplied bytes are the same
        # asset. Never overwrite contradictory byte evidence under an old ID.
        # Video recoding may instead be vouched for by the same trusted file ID.
        trusted_video = kind == "video" and reliable_id and any(row["alias_key"] == aliases[0] for row in alias_rows)
        if (
            fp
            and not override
            and not trusted_video
            and any(row.get("fingerprint") and not same_media(fp, row["fingerprint"], kind) for row in alias_rows)
        ):
            raise ValueError("identity_conflict_requires_override")
        matches = {
            row["canonical_id"]
            for row in known
            if row["media_type"] == kind
            and (
                row["alias_key"] in aliases
                or (not row.get("override_reason") and fp and same_media(fp, row.get("fingerprint") or {}, kind))
            )
        }
        if len(matches) > 1 and not override:
            raise ValueError("identity_conflict_requires_override")
        if not fp and not reliable_id and not override and not matches:
            raise ValueError("identity_unknown")
        canonical = (
            override
            or next(iter(matches), "")
            or identity_key(
                kind,
                "file" if reliable_id else "sha256",
                aliases[0] if reliable_id else fp["sha256"],
            )
        )
        for alias in aliases:
            prior = next((row for row in alias_rows if row["alias_key"] == alias), {})
            row = {
                "alias_key": alias,
                "canonical_id": canonical,
                "media_type": kind,
                "fingerprint": fp or prior.get("fingerprint") or {},
                "override_reason": reason or prior.get("override_reason") or "",
                "provenance": {
                    "source": str(record.get("source") or "catalog"),
                    "source_id": str(record.get("source_id") or ""),
                    "version": IDENTITY_VERSION,
                },
            }
            output.append(row)
            known = [item for item in known if item["alias_key"] != alias]
            known.append(row)
    return output


def govern_candidates(candidates: list[dict[str, Any]], *, repository: Any, state: dict[str, Any]) -> dict[str, Any]:
    prepared = content_only_candidates(candidates[:MAX_TURN_MEDIA])
    for item in prepared:
        for field in MEDIA_FIELDS:
            if isinstance(item.get(field), list):
                item[field] = item[field][:MAX_TURN_MEDIA]
    messages = [
        message
        for item in prepared
        for field in MEDIA_FIELDS
        for message in item.get(field) or []
        if isinstance(message, dict) and message.get("type") in {"image", "video"}
    ]
    unique = {tuple(media_aliases(message)): message for message in messages}
    bounded = list(unique.values())[:MAX_TURN_MEDIA]
    registry_error = ""
    if state.get("test_isolated"):
        repository = None
    try:
        registry = repository.resolve_material_messages(bounded) if repository else {}
    except Exception:
        registry = {}
        registry_error = "identity_registry_unavailable"
    scope = contact_key(state)
    try:
        claims = (
            repository.material_claimed_ids(scope, list({row["canonical_id"] for row in registry.values()}))
            if repository and scope and registry
            else set()
        )
    except Exception:
        registry = {}
        claims = set()
        registry_error = "delivery_ledger_unavailable"
    entities: dict[str, dict[str, Any]] = {}
    bindings: dict[str, dict[str, Any]] = {}
    pending: dict[str, str] = {}
    output = []
    for item in prepared:
        source_id = str(item.get("content_id") or item.get("id") or "")
        role = str(item.get("asset_role") or "supporting_content")
        seen = set()
        for message in item.get("messages") or item.get("media") or []:
            if not isinstance(message, dict) or message.get("type") not in {"image", "video"}:
                continue
            aliases = media_aliases(message)
            row = next((registry[key] for key in aliases if key in registry), None)
            url_key = identity_key(str(message["type"]), "url", media_url(message))
            if not row or not scope:
                pending[url_key] = registry_error or ("identity_unknown" if not row else "contact_identity_missing")
                continue
            canonical = row["canonical_id"]
            if canonical in seen:
                continue
            seen.add(canonical)
            provenance = {
                "content_id": source_id,
                "asset_role": role,
                "source_script_id": str(item.get("source_script_id") or ""),
            }
            if canonical in entities:
                entity = entities[canonical]
                if provenance not in entity["material_provenance"]:
                    entity["material_provenance"].append(provenance)
                continue
            if canonical in claims:
                pending[url_key] = "delivery_already_reserved"
                continue
            entity = copy.deepcopy(item)
            entity["canonical_id"] = canonical
            entity["content_id"] = f"media:{canonical}"
            entity["material_provenance"] = [provenance]
            entity["delivery_status"] = "available"
            passive = {"type": message["type"], "content": media_url(message)}
            for field in MEDIA_FIELDS:
                if field in entity or field == "messages":
                    entity[field] = [copy.deepcopy(passive)]
            entities[canonical] = entity
            bindings[f"{message['type']}:{media_url(message)}"] = {
                "canonical_id": canonical,
                "alias_key": row["alias_key"],
                "asset_role": role,
                "content_id": entity["content_id"],
                "source_content_id": source_id,
            }
        # Keep textual knowledge independently of attachment eligibility.
        reference = copy.deepcopy(item)
        for field in MEDIA_FIELDS:
            if field in reference:
                reference[field] = [m for m in reference[field] if isinstance(m, dict) and m.get("type") == "text"]
        reference["delivery_status"] = "reference_only"
        if (
            reference.get("reference_text")
            or reference.get("approved_points")
            or any(reference.get(k) for k in MEDIA_FIELDS)
        ):
            output.append(reference)
    output.extend(entities.values())
    return {
        "candidates": output,
        "bindings": bindings,
        "audit": {
            "version": IDENTITY_VERSION,
            "pending": pending,
            "input_media_count": len(unique),
            "available_entity_count": len(entities),
            "over_budget_count": max(0, len(unique) - MAX_TURN_MEDIA),
        },
    }
