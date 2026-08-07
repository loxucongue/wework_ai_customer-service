from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse


ALLOWED_OUTREACH_ASSET_TYPES = {"image", "video"}


def build_outreach_asset_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    configured_assets = config.get("assets") if isinstance(config.get("assets"), list) else []
    assets: list[dict[str, Any]] = []
    for configured in configured_assets:
        if not isinstance(configured, dict) or not bool(configured.get("enabled")):
            continue
        asset_id = _string(configured.get("id"))
        asset_type = _string(configured.get("type"))
        url = _string(configured.get("url"))
        if not asset_id or asset_type not in ALLOWED_OUTREACH_ASSET_TYPES or not _is_http_url(url):
            continue
        assets.append(
            {
                "asset_id": asset_id,
                "type": asset_type,
                "url": url,
                "source": "outreach_asset_library",
                "name": _string(configured.get("name")),
                "annotation": _string(configured.get("annotation")),
                "use_cases": _string_list(configured.get("use_cases")),
                "avoid_when": _string_list(configured.get("avoid_when")),
                "tags": _string_list(configured.get("tags")),
            }
        )
    return assets


def recent_outreach_media(
    messages: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    hours: int = 72,
) -> dict[str, list[str]]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=max(1, hours))
    urls: list[str] = []
    document_ids: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or not _is_recent(message, cutoff):
            continue
        for item in _message_items(message):
            url = _message_url(item)
            if url and url not in urls:
                urls.append(url)
            document_id = _string(item.get("document_id") or item.get("case_document_id"))
            if document_id and document_id not in document_ids:
                document_ids.append(document_id)
    return {"urls": urls, "document_ids": document_ids}


def enrich_recent_outreach_media(
    delivery: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach configured asset facts to sent URLs without inferring sales intent."""
    output = {
        "urls": list(delivery.get("urls") or []),
        "document_ids": list(delivery.get("document_ids") or []),
        "items": [],
        "configured_deliveries": [],
    }
    assets_by_url: dict[str, list[dict[str, Any]]] = {}
    for asset in catalog:
        if not isinstance(asset, dict):
            continue
        url = _string(asset.get("url"))
        if url:
            assets_by_url.setdefault(url, []).append(asset)
    for url in output["urls"]:
        matches = assets_by_url.get(_string(url), [])
        output["items"].append(
            {
                "url": url,
                "configured_matches": [
                    {
                        key: asset.get(key)
                        for key in (
                            "asset_id",
                            "type",
                            "source",
                            "name",
                            "annotation",
                            "use_cases",
                            "tags",
                        )
                        if asset.get(key)
                    }
                    for asset in matches
                ],
            }
        )
        for asset in matches:
            fact = {
                key: asset.get(key)
                for key in (
                    "asset_id",
                    "type",
                    "source",
                    "name",
                    "annotation",
                    "use_cases",
                    "tags",
                )
                if asset.get(key)
            }
            if fact not in output["configured_deliveries"]:
                output["configured_deliveries"].append(fact)
    return output


def resolve_configured_asset(
    catalog: list[dict[str, Any]],
    asset_id: str,
    *,
    sent_urls: set[str] | None = None,
    expected_type: str = "",
) -> dict[str, Any]:
    sent = sent_urls or set()
    for asset in catalog:
        if _string(asset.get("asset_id")) != _string(asset_id):
            continue
        asset_type = _string(asset.get("type"))
        url = _string(asset.get("url"))
        if expected_type and asset_type != expected_type:
            return {}
        if asset_type not in ALLOWED_OUTREACH_ASSET_TYPES or not _is_http_url(url) or url in sent:
            return {}
        return dict(asset)
    return {}


def resolve_case_asset(
    result: Any,
    *,
    sent_urls: set[str] | None = None,
    sent_document_ids: set[str] | None = None,
) -> dict[str, Any]:
    sent_url_values = sent_urls or set()
    sent_document_values = sent_document_ids or set()
    items = getattr(result, "items", None)
    if not isinstance(items, list):
        return {}
    for item in items:
        content = _string(getattr(item, "content", ""))
        document_id = _string(getattr(item, "document_id", ""))
        url = image_url_from_content(content)
        if not url or url in sent_url_values:
            continue
        if document_id and document_id in sent_document_values:
            continue
        return {
            "asset_id": f"case_studies:{document_id or url}",
            "type": "image",
            "url": url,
            "source": "case_studies",
            "document_id": document_id,
            "description": case_description_from_content(content),
        }
    return {}


def asset_reply_message(asset: dict[str, Any], *, order: int) -> dict[str, Any]:
    asset_type = _string(asset.get("type"))
    url = _string(asset.get("url"))
    if asset_type not in ALLOWED_OUTREACH_ASSET_TYPES or not _is_http_url(url):
        return {}
    return {"type": asset_type, "order": order, "content": {"url": url}}


def image_url_from_content(content: str) -> str:
    if not content:
        return ""
    match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content, flags=re.IGNORECASE)
    if match:
        return html.unescape(match.group(1)).strip()
    stripped = content.strip()
    if stripped.startswith(("http://", "https://")):
        return html.unescape(stripped.split()[0]).strip()
    match = re.search(r"https?://[^\s<>'\")]+", content)
    return html.unescape(match.group(0)).strip() if match else ""


def case_description_from_content(content: str) -> str:
    text = re.sub(r"<img\s+[^>]*>", "", content or "", flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    if text.lower().startswith("description:"):
        text = text.split(":", 1)[1].strip()
    return text[:300]


def _message_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    items = [message]
    replies = message.get("reply_messages")
    if isinstance(replies, list):
        items.extend(item for item in replies if isinstance(item, dict))
    return items


def _message_url(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        value = _string(content.get("url"))
        if _is_http_url(value):
            return value
    value = _string(message.get("url"))
    if _is_http_url(value):
        return value
    if isinstance(content, str):
        value = image_url_from_content(content)
        if _is_http_url(value):
            return value
    return ""


def _is_recent(message: dict[str, Any], cutoff: datetime) -> bool:
    raw = _string(message.get("created_at") or message.get("sent_at") or message.get("msgtime"))
    if not raw:
        return False
    try:
        if raw.isdigit():
            timestamp = int(raw)
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OSError):
        return False
    return parsed >= cutoff


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _string(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _string(item))]
