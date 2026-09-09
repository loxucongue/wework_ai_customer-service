from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import re
import socket
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from io import BytesIO
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

try:  # Pillow is optional in production; SHA-256 remains available without it.
    from PIL import Image
except Exception:  # pragma: no cover - exercised only by minimal deployments
    Image = None  # type: ignore[assignment]


MAX_MEDIA_BYTES = 6 * 1024 * 1024
FINGERPRINT_CACHE_TTL_SECONDS = 6 * 60 * 60
FINGERPRINT_CACHE_MAX_ITEMS = 512
FINGERPRINT_FAILURE_TTL_SECONDS = 60
PERCEPTUAL_DUPLICATE_DISTANCE = 4

MediaFetcher = Callable[[str], Awaitable[bytes | None]]

_FINGERPRINT_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_FINGERPRINT_FAILURE_CACHE: dict[str, float] = {}
_FINGERPRINT_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}


def fingerprint_media_bytes(payload: bytes) -> dict[str, Any]:
    """Build stable byte and visual fingerprints without requiring Pillow."""

    sha256 = hashlib.sha256(payload).hexdigest()
    perceptual_hash = ""
    mode = "sha256_only"
    if Image is not None:
        try:
            with Image.open(BytesIO(payload)) as source:
                rgb = source.convert("RGB")
                image_size = [int(rgb.width), int(rgb.height)]
                visual_mean = list(rgb.resize((1, 1)).getpixel((0, 0)))
                image = rgb.convert("L").resize((9, 8))
                pixels = list(image.getdata())
            bits = 0
            for row in range(8):
                offset = row * 9
                for column in range(8):
                    bits = (bits << 1) | int(
                        pixels[offset + column] > pixels[offset + column + 1]
                    )
            perceptual_hash = f"{bits:016x}"
            mode = "sha256_and_dhash"
        except Exception:
            # Non-image payloads and unsupported encodings must never break Reply.
            perceptual_hash = ""
            image_size = []
            visual_mean = []
    else:
        image_size = []
        visual_mean = []
    return {
        "sha256": sha256,
        "perceptual_hash": perceptual_hash,
        "image_size": image_size,
        "visual_mean": visual_mean,
        "mode": mode,
    }


def media_fingerprints_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_sha = str(left.get("sha256") or "")
    right_sha = str(right.get("sha256") or "")
    if left_sha and right_sha and left_sha == right_sha:
        return True
    left_hash = str(left.get("perceptual_hash") or "")
    right_hash = str(right.get("perceptual_hash") or "")
    if not left_hash or not right_hash:
        return False
    try:
        if (int(left_hash, 16) ^ int(right_hash, 16)).bit_count() > PERCEPTUAL_DUPLICATE_DISTANCE:
            return False
    except ValueError:
        return False
    left_mean = left.get("visual_mean") if isinstance(left.get("visual_mean"), list) else []
    right_mean = right.get("visual_mean") if isinstance(right.get("visual_mean"), list) else []
    if len(left_mean) == 3 and len(right_mean) == 3:
        if max(abs(int(a) - int(b)) for a, b in zip(left_mean, right_mean)) > 28:
            return False
    return True


async def diversify_material_candidates(
    candidates: list[dict[str, Any]],
    *,
    sent_image_urls: list[str] | set[str] | tuple[str, ...] | None = None,
    recent_material_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    recent_assistant_texts: list[str] | tuple[str, ...] | None = None,
    fetcher: MediaFetcher | None = None,
    total_budget_seconds: float = 0.45,
) -> dict[str, Any]:
    """De-duplicate media and prefer fresh expressions inside semantic tiers.

    This is deliberately not an intent selector.  The Router/Gate relevance tier
    stays first in the sort key; recency and similarity only break ties between
    already relevant candidates.
    """

    prepared = [copy.deepcopy(item) for item in candidates if isinstance(item, dict)]
    sent_url_order = list(
        dict.fromkeys(
            _normalized_url(item)
            for item in sent_image_urls or []
            if _normalized_url(item)
        )
    )
    sent_urls = {
        _normalized_url(item)
        for item in sent_url_order
        if _normalized_url(item)
    }
    recent_ids = {str(item).strip() for item in recent_material_ids or [] if str(item).strip()}
    recent_texts = [str(item).strip() for item in recent_assistant_texts or [] if str(item).strip()]
    image_urls = list(
        dict.fromkeys(
            url
            for item in prepared
            for url in _candidate_image_urls(item)
            if url
        )
    )
    fingerprint_urls = list(dict.fromkeys([*sent_url_order[-24:], *image_urls]))
    fingerprints: dict[str, dict[str, Any]] = {}
    if fingerprint_urls and total_budget_seconds > 0:
        try:
            fingerprints = await asyncio.wait_for(
                _resolve_fingerprints(fingerprint_urls, fetcher=fetcher),
                timeout=max(0.05, float(total_budget_seconds)),
            )
        except (asyncio.TimeoutError, TimeoutError):
            # URL equality still gives a safe fallback when CDN access is slow.
            fingerprints = {}

    sent_fingerprints = [fingerprints[url] for url in sent_urls if url in fingerprints]
    scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for index, item in enumerate(prepared):
        candidate_id = str(item.get("content_id") or item.get("id") or "").strip()
        group_id = str(
            item.get("material_group")
            or item.get("category")
            or item.get("source_script_code")
            or candidate_id
        ).strip()
        candidate_text = _candidate_text(item)
        similarity = max(
            (_text_similarity(candidate_text, recent) for recent in recent_texts),
            default=0.0,
        )
        recent_penalty = int(candidate_id in recent_ids or group_id in recent_ids)
        item["material_group"] = group_id
        item["selection_observation"] = {
            "semantic_relevance_preserved": True,
            "recent_material": bool(recent_penalty),
            "recent_expression_similarity": round(similarity, 3),
            "media_fingerprint_mode": (
                "sha256_and_dhash"
                if any(
                    fingerprints.get(url, {}).get("perceptual_hash")
                    for url in _candidate_image_urls(item)
                )
                else "url_or_sha256_fallback"
            ),
        }
        scored.append(
            (
                (
                    _relevance_tier(item),
                    int(str(item.get("delivery_status") or "") == "completed"),
                    recent_penalty,
                    round(similarity, 4),
                    index,
                ),
                item,
            )
        )
    scored.sort(key=lambda value: value[0])

    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set(sent_urls)
    seen_fingerprints = list(sent_fingerprints)
    duplicate_media_count = 0
    for _, item in scored:
        original_images = _candidate_image_urls(item)
        blocked: set[str] = set()
        for url in original_images:
            fingerprint = fingerprints.get(url) or {}
            if url in seen_urls or any(
                fingerprint and media_fingerprints_match(fingerprint, prior)
                for prior in seen_fingerprints
            ):
                blocked.add(url)
                continue
            seen_urls.add(url)
            if fingerprint:
                seen_fingerprints.append(fingerprint)
        if blocked:
            duplicate_media_count += len(blocked)
            _remove_image_urls(item, blocked)
            observation = item.setdefault("selection_observation", {})
            observation["duplicate_media_removed"] = len(blocked)
        remaining_images = _candidate_image_urls(item)
        if original_images and not remaining_images and not _candidate_has_reference_value(item):
            continue
        if original_images and not remaining_images:
            item["delivery_status"] = "reference_only"
        selected.append(item)

    return {
        "candidates": selected,
        "audit": {
            "input_candidate_count": len(prepared),
            "output_candidate_count": len(selected),
            "image_url_count": len(image_urls),
            "fingerprinted_url_count": len(fingerprints),
            "duplicate_media_removed": duplicate_media_count,
            "recent_material_count": sum(
                1
                for item in selected
                if bool((item.get("selection_observation") or {}).get("recent_material"))
            ),
            "fallback_used": len(fingerprints) < len(fingerprint_urls),
        },
    }


async def _resolve_fingerprints(
    urls: list[str],
    *,
    fetcher: MediaFetcher | None,
) -> dict[str, dict[str, Any]]:
    if fetcher is not None:
        results = await asyncio.gather(
            *(_fingerprint_url(url, fetcher=fetcher) for url in urls),
        )
    else:
        timeout = httpx.Timeout(0.35, connect=0.25, read=0.3, write=0.3, pool=0.2)
        limits = httpx.Limits(max_connections=12, max_keepalive_connections=6)
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
        ) as client:
            results = await asyncio.gather(
                *(_fingerprint_url(url, fetcher=lambda value: _fetch_public_media(client, value)) for url in urls),
            )
    return {
        url: result
        for url, result in zip(urls, results)
        if isinstance(result, dict) and str(result.get("sha256") or "")
    }


async def _fingerprint_url(url: str, *, fetcher: MediaFetcher) -> dict[str, Any]:
    now = time.monotonic()
    cached = _FINGERPRINT_CACHE.get(url)
    if cached and now - cached[0] <= FINGERPRINT_CACHE_TTL_SECONDS:
        _FINGERPRINT_CACHE.move_to_end(url)
        return copy.deepcopy(cached[1])
    failed_at = _FINGERPRINT_FAILURE_CACHE.get(url)
    if failed_at is not None and now - failed_at <= FINGERPRINT_FAILURE_TTL_SECONDS:
        return {}
    task = _FINGERPRINT_INFLIGHT.get(url)
    if task is None:
        task = asyncio.create_task(_fetch_and_fingerprint(url, fetcher=fetcher))
        _FINGERPRINT_INFLIGHT[url] = task
        task.add_done_callback(
            lambda completed, *, cache_key=url: _finalize_fingerprint_task(
                cache_key,
                completed,
            )
        )
    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _FINGERPRINT_INFLIGHT.get(url) is task:
            _FINGERPRINT_INFLIGHT.pop(url, None)
    _remember_fingerprint_result(url, result)
    return result


def _finalize_fingerprint_task(
    url: str,
    task: asyncio.Task[dict[str, Any]],
) -> None:
    """Release single-flight ownership even when the original waiter timed out."""

    if _FINGERPRINT_INFLIGHT.get(url) is task:
        _FINGERPRINT_INFLIGHT.pop(url, None)
    if task.cancelled():
        _remember_fingerprint_result(url, {})
        return
    try:
        result = task.result()
    except BaseException:
        result = {}
    _remember_fingerprint_result(url, result)


def _remember_fingerprint_result(url: str, result: dict[str, Any]) -> None:
    if result:
        _FINGERPRINT_FAILURE_CACHE.pop(url, None)
        _FINGERPRINT_CACHE[url] = (time.monotonic(), copy.deepcopy(result))
        _FINGERPRINT_CACHE.move_to_end(url)
        while len(_FINGERPRINT_CACHE) > FINGERPRINT_CACHE_MAX_ITEMS:
            _FINGERPRINT_CACHE.popitem(last=False)
        return
    _FINGERPRINT_FAILURE_CACHE[url] = time.monotonic()
    if len(_FINGERPRINT_FAILURE_CACHE) > FINGERPRINT_CACHE_MAX_ITEMS:
        oldest = min(_FINGERPRINT_FAILURE_CACHE.items(), key=lambda item: item[1])[0]
        _FINGERPRINT_FAILURE_CACHE.pop(oldest, None)


async def _fetch_and_fingerprint(url: str, *, fetcher: MediaFetcher) -> dict[str, Any]:
    try:
        payload = await fetcher(url)
    except Exception:
        return {}
    if not payload or len(payload) > MAX_MEDIA_BYTES:
        return {}
    return fingerprint_media_bytes(payload)


async def _fetch_public_media(client: httpx.AsyncClient, url: str) -> bytes | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if not await _public_hostname(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
        return None
    try:
        async with client.stream("GET", url, headers={"Accept": "image/*"}) as response:
            if response.status_code != 200:
                return None
            content_type = str(response.headers.get("content-type") or "").lower()
            if content_type and not content_type.startswith("image/"):
                return None
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_MEDIA_BYTES:
                    return None
            return bytes(payload)
    except (httpx.HTTPError, TimeoutError):
        return None


async def _public_hostname(hostname: str, port: int) -> bool:
    compact = hostname.strip().lower().rstrip(".")
    if compact in {"localhost", "localhost.localdomain"} or compact.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(compact)
        return address.is_global
    except ValueError:
        pass
    try:
        loop = asyncio.get_running_loop()
        records = await asyncio.wait_for(
            loop.getaddrinfo(compact, port, type=socket.SOCK_STREAM),
            timeout=0.2,
        )
    except (OSError, asyncio.TimeoutError):
        return False
    addresses = {
        item[4][0]
        for item in records
        if item and len(item) > 4 and item[4]
    }
    return bool(addresses) and all(ipaddress.ip_address(item).is_global for item in addresses)


def _candidate_image_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("messages", "media", "required_structured_media", "reference_messages"):
        for message in item.get(key) or []:
            if not isinstance(message, dict) or str(message.get("type") or "").strip().lower() != "image":
                continue
            url = _media_url(message)
            if url and url not in urls:
                urls.append(url)
    return urls


def _remove_image_urls(item: dict[str, Any], blocked: set[str]) -> None:
    for key in ("messages", "media", "required_structured_media", "reference_messages"):
        values = item.get(key)
        if not isinstance(values, list):
            continue
        item[key] = [
            message
            for message in values
            if not (
                isinstance(message, dict)
                and str(message.get("type") or "").strip().lower() == "image"
                and _media_url(message) in blocked
            )
        ]


def _media_url(message: dict[str, Any]) -> str:
    direct = message.get("url")
    if direct:
        return _normalized_url(direct)
    content = message.get("content")
    if isinstance(content, dict):
        return _normalized_url(content.get("url") or content.get("image_url"))
    return _normalized_url(content)


def _normalized_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _candidate_text(item: dict[str, Any]) -> str:
    values = [
        item.get("name"),
        item.get("purpose"),
        item.get("reference_text"),
        *(item.get("approved_points") or []),
    ]
    for key in ("messages", "reference_messages"):
        values.extend(
            message.get("content")
            for message in item.get(key) or []
            if isinstance(message, dict) and str(message.get("type") or "") == "text"
        )
    return " ".join(str(value or "") for value in values if str(value or "").strip())


def _candidate_has_reference_value(item: dict[str, Any]) -> bool:
    reference_values: list[Any] = [
        item.get("reference_text"),
        *(item.get("approved_points") or []),
    ]
    for key in ("messages", "reference_messages"):
        reference_values.extend(
            message.get("content")
            for message in item.get(key) or []
            if isinstance(message, dict) and str(message.get("type") or "") == "text"
        )
    if any(str(value or "").strip() for value in reference_values):
        return True
    return any(
        isinstance(message, dict)
        and str(message.get("type") or "").strip().lower() in {"video", "payment_collection", "store_address"}
        for key in ("messages", "media", "required_structured_media", "reference_messages")
        for message in item.get(key) or []
    )


def _relevance_tier(item: dict[str, Any]) -> int:
    return {
        "direct": 0,
        "required": 0,
        "supporting": 1,
        "available": 2,
    }.get(str(item.get("relevance") or "available").strip().lower(), 2)


def _text_similarity(left: str, right: str) -> float:
    left_compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", left.lower())
    right_compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", right.lower())
    if not left_compact or not right_compact:
        return 0.0
    sequence = SequenceMatcher(None, left_compact, right_compact).ratio()
    left_grams = {left_compact[index : index + 3] for index in range(max(1, len(left_compact) - 2))}
    right_grams = {right_compact[index : index + 3] for index in range(max(1, len(right_compact) - 2))}
    overlap = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    return max(sequence, overlap)
