from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings


ACTION_CODES = {
    "empathy",
    "resolve",
    "case",
    "campaign",
    "low_barrier",
    "value_add",
    "care",
    "appt_confirm",
}
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    value: dict[str, Any]


class FollowKnowledgeClient:
    """Read-only client for published follow scripts and enabled sequences."""

    def __init__(self, settings: Settings) -> None:
        self._enabled = bool(settings.follow_knowledge_enabled)
        self._base_url = str(settings.follow_knowledge_base_url or "").rstrip("/") + "/"
        self._token = str(settings.follow_knowledge_token or "").strip()
        self._timeout = float(settings.follow_knowledge_timeout_seconds)
        self._cache_ttl = max(0.0, float(settings.follow_knowledge_cache_ttl_seconds))
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None
        self._cache: dict[tuple[Any, ...], _CacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return bool(self._enabled and self._base_url.strip("/") and self._token)

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def query_scripts(
        self,
        *,
        checkpoint_type_id: int | None = None,
        checkpoint_tag_id: int | None = None,
        checkpoint_code: str = "",
        action_code: str = "",
        script_name: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        checkpoint = _code(checkpoint_code)
        action = _code(action_code)
        if action and action not in ACTION_CODES:
            return _empty_result("follow_script_query_v1", "unknown_action_code")
        type_id = _positive_int(checkpoint_type_id)
        tag_id = _positive_int(checkpoint_tag_id)
        payload = {
            "checkpointTypeId": type_id or 0,
            "checkpointTagId": tag_id or 0,
            "checkpointCode": "" if type_id else checkpoint,
            "actionCode": action,
            "scriptName": _text(script_name)[:120],
            "page": max(1, int(page or 1)),
            "pageSize": max(1, min(int(page_size or 10), 100)),
        }
        return await self._query(
            path="event/trigger/follow-script",
            payload=payload,
            schema_version="follow_script_query_v1",
            item_normalizer=_normalize_script,
        )

    async def query_sequences(
        self,
        *,
        checkpoint_code: str = "",
        sequence_name: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        checkpoint = _code(checkpoint_code)
        payload = {
            "checkpointCode": checkpoint,
            "sequenceName": _text(sequence_name)[:120],
            "page": max(1, int(page or 1)),
            "pageSize": max(1, min(int(page_size or 20), 100)),
        }
        return await self._query(
            path="event/trigger/follow-sequence",
            payload=payload,
            schema_version="follow_sequence_query_v1",
            item_normalizer=_normalize_sequence,
        )

    async def query_all_sequences(
        self,
        *,
        checkpoint_code: str = "",
        sequence_name: str = "",
    ) -> dict[str, Any]:
        return await self._query_all_pages(
            lambda page: self.query_sequences(
                checkpoint_code=checkpoint_code,
                sequence_name=sequence_name,
                page=page,
                page_size=100,
            ),
            schema_version="follow_sequence_index_v2",
        )

    async def query_all_scripts(
        self,
        *,
        checkpoint_type_id: int | None = None,
        checkpoint_tag_id: int | None = None,
        checkpoint_code: str = "",
        action_code: str = "",
    ) -> dict[str, Any]:
        return await self._query_all_pages(
            lambda page: self.query_scripts(
                checkpoint_type_id=checkpoint_type_id,
                checkpoint_tag_id=checkpoint_tag_id,
                checkpoint_code=checkpoint_code,
                action_code=action_code,
                page=page,
                page_size=100,
            ),
            schema_version="follow_script_index_v2",
        )

    async def query_script_taxonomy(self) -> dict[str, Any]:
        """Build the tenant-owned checkpoint type/tag directory from published scripts."""

        result = await self.query_all_scripts()
        if str(result.get("status") or "") != "ok":
            return {
                "schema_version": "follow_script_taxonomy_v2",
                "status": result.get("status") or "error",
                "reason": result.get("reason") or "script_index_failed",
                "types": [],
                "duration_ms": int(result.get("duration_ms") or 0),
            }
        types: dict[tuple[int, str], dict[str, Any]] = {}
        for script in result.get("items") or []:
            if not isinstance(script, dict):
                continue
            checkpoint_type = (
                script.get("checkpoint_type")
                if isinstance(script.get("checkpoint_type"), dict)
                else {}
            )
            type_id = _positive_int(checkpoint_type.get("id")) or 0
            code = _code(checkpoint_type.get("code") or script.get("checkpoint_code"))
            name = _text(checkpoint_type.get("name") or script.get("checkpoint_name"))
            if not type_id and not code:
                continue
            entry = types.setdefault(
                (type_id, code),
                {
                    "id": type_id,
                    "code": code,
                    "name": name,
                    "script_count": 0,
                    "action_counts": {},
                    "tags": [],
                },
            )
            entry["script_count"] += 1
            action_code = _code(script.get("action_code"))
            if action_code:
                entry["action_counts"][action_code] = int(entry["action_counts"].get(action_code) or 0) + 1
            tag = script.get("checkpoint_tag") if isinstance(script.get("checkpoint_tag"), dict) else {}
            tag_id = _positive_int(tag.get("id")) or 0
            tag_name = _text(tag.get("name"))
            if tag_id:
                tag_entry = next(
                    (item for item in entry["tags"] if int(item.get("id") or 0) == tag_id),
                    None,
                )
                if tag_entry is None:
                    tag_entry = {
                        "id": tag_id,
                        "name": tag_name,
                        "script_count": 0,
                        "action_counts": {},
                    }
                    entry["tags"].append(tag_entry)
                tag_entry["script_count"] += 1
                if action_code:
                    tag_entry["action_counts"][action_code] = (
                        int(tag_entry["action_counts"].get(action_code) or 0) + 1
                    )
        items = sorted(types.values(), key=lambda item: (int(item.get("id") or 0), str(item.get("code") or "")))
        for item in items:
            item["action_counts"] = dict(sorted(item["action_counts"].items()))
            item["tags"].sort(key=lambda tag: int(tag.get("id") or 0))
            for tag in item["tags"]:
                tag["action_counts"] = dict(sorted(tag["action_counts"].items()))
        return {
            "schema_version": "follow_script_taxonomy_v2",
            "status": "ok",
            "source": "follow_knowledge_api",
            "types": items,
            "script_total": int(result.get("total") or len(result.get("items") or [])),
            "duration_ms": int(result.get("duration_ms") or 0),
        }

    async def _query_all_pages(self, fetch_page, *, schema_version: str) -> dict[str, Any]:
        started = time.perf_counter()
        items: list[dict[str, Any]] = []
        page = 1
        total = 0
        cache_hits = 0
        page_results: list[dict[str, Any]] = []
        while True:
            result = await fetch_page(page)
            page_results.append(
                {
                    "page": page,
                    "status": result.get("status"),
                    "reason": result.get("reason", ""),
                    "count": len(result.get("items") or []),
                }
            )
            if str(result.get("status") or "") != "ok":
                return {
                    "schema_version": schema_version,
                    "status": result.get("status") or "error",
                    "reason": result.get("reason") or "page_query_failed",
                    "total": total,
                    "items": items,
                    "pages": page_results,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                }
            page_items = [copy.deepcopy(item) for item in result.get("items") or [] if isinstance(item, dict)]
            items.extend(page_items)
            total = max(total, int(result.get("total") or 0))
            cache_hits += int(bool(result.get("cache_hit")))
            if not page_items or len(items) >= total or len(page_items) < int(result.get("page_size") or 100):
                break
            page += 1
        return {
            "schema_version": schema_version,
            "status": "ok",
            "source": "follow_knowledge_api",
            "total": total,
            "items": items,
            "pages": page_results,
            "cache_hit_pages": cache_hits,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    async def _query(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        schema_version: str,
        item_normalizer,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if not self.available:
            return {
                **_empty_result(schema_version, "follow_knowledge_not_configured"),
                "status": "disabled",
                "duration_ms": 0,
            }
        cache_key = (path, *sorted(payload.items()))
        cached = await self._cached(cache_key)
        if cached is not None:
            cached["cache_hit"] = True
            cached["duration_ms"] = int((time.perf_counter() - started) * 1000)
            return cached
        try:
            response = await self._request_with_retry(path, payload)
            body = _response_body(response)
            if response.status_code >= 400:
                raise RuntimeError(f"http_status:{response.status_code}")
            if not isinstance(body, dict) or int(body.get("code") or 0) != 200:
                message = _text(body.get("message")) if isinstance(body, dict) else "invalid_response"
                raise RuntimeError(f"business_error:{message or 'unknown'}")
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            items = [
                normalized
                for raw in data.get("list") or []
                if isinstance(raw, dict)
                and (normalized := item_normalizer(raw)) is not None
            ]
            result = {
                "schema_version": schema_version,
                "status": "ok",
                "source": "follow_knowledge_api",
                "query": copy.deepcopy(payload),
                "total": max(0, int(data.get("total") or 0)),
                "page": max(1, int(data.get("page") or payload["page"])),
                "page_size": max(1, int(data.get("pageSize") or payload["pageSize"])),
                "items": items,
                "cache_hit": False,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
            await self._store_cache(cache_key, result)
            return result
        except Exception as exc:
            return {
                **_empty_result(schema_version, f"{type(exc).__name__}: {exc}"),
                "status": "error",
                "query": copy.deepcopy(payload),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }

    async def _cached(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        if self._cache_ttl <= 0:
            return None
        async with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None or entry.expires_at <= time.monotonic():
                self._cache.pop(key, None)
                return None
            return copy.deepcopy(entry.value)

    async def _store_cache(self, key: tuple[Any, ...], value: dict[str, Any]) -> None:
        if self._cache_ttl <= 0:
            return
        async with self._cache_lock:
            self._cache[key] = _CacheEntry(
                expires_at=time.monotonic() + self._cache_ttl,
                value=copy.deepcopy(value),
            )

    def _http_client(self) -> httpx.AsyncClient:
        loop_id = id(asyncio.get_running_loop())
        if self._client is None or self._client.is_closed or self._client_loop_id != loop_id:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
            self._client_loop_id = loop_id
        return self._client

    async def _request_with_retry(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        url = urljoin(self._base_url, path)
        headers = {"Content-Type": "application/json", "x-event-token": self._token}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._http_client().post(url, headers=headers, json=payload)
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                return response
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
            ) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("follow knowledge request failed without response")


def _normalize_script(raw: dict[str, Any]) -> dict[str, Any] | None:
    script_code = _text(raw.get("scriptCode"))
    script_id = _text(raw.get("id"))
    if not script_code and not script_id:
        return None
    source_ref = f"follow_script:{script_id or script_code}"
    checkpoint_code = _code(raw.get("checkpointCode"))
    action_code = _code(raw.get("actionCode"))
    checkpoint_type_id = _positive_int(raw.get("checkpointTypeId"))
    checkpoint_tag_id = _positive_int(raw.get("checkpointTagId"))
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    media_url = _text(media.get("url"))
    content_type = _code(raw.get("contentType")) or "text"
    paragraphs = _normalize_script_paragraphs(raw.get("paragraphs"))
    if not paragraphs:
        paragraphs = _legacy_script_paragraphs(raw, media=media)
    for paragraph in paragraphs:
        paragraph["source_ref"] = f"{source_ref}:p{paragraph.get('paragraph_no') or 1}"
    media_messages = [
        message
        for paragraph in paragraphs
        for message in paragraph.get("messages") or []
        if isinstance(message, dict) and message.get("type") in {"image", "video"}
    ]
    quality_flags: list[str] = []
    paragraph_media_urls = [
        str(message.get("url") or "").strip()
        for paragraph in paragraphs
        for message in paragraph.get("messages") or []
        if isinstance(message, dict) and message.get("type") in {"image", "video"}
    ]
    if content_type != "text" and not (media_url or paragraph_media_urls):
        quality_flags.append("declared_media_missing_url")
    elif any(url and not _is_http_url(url) for url in [media_url, *paragraph_media_urls]):
        quality_flags.append("media_value_is_not_http_url")
    return {
        "id": script_id,
        "script_code": script_code or f"script_{script_id}",
        "source_ref": source_ref,
        "authority_scope": "approved_sales_expression",
        "hard_fact_authority": False,
        "script_name": _text(raw.get("scriptName")),
        "body_text": _text(raw.get("bodyText")),
        "checkpoint_type": {
            "id": checkpoint_type_id or 0,
            "code": checkpoint_code,
            "name": _text(raw.get("checkpointTypeName") or raw.get("checkpointName")),
        },
        "checkpoint_tag": {
            "id": checkpoint_tag_id or 0,
            "name": _text(raw.get("checkpointTagName")),
        },
        "checkpoint_code": checkpoint_code,
        "checkpoint_name": _text(raw.get("checkpointTypeName") or raw.get("checkpointName")),
        "action_code": action_code,
        "action_name": _text(raw.get("actionName")),
        "style_code": _code(raw.get("styleCode")),
        "style_name": _text(raw.get("styleName")),
        "content_type": content_type,
        "content_type_name": _text(raw.get("contentTypeName")),
        "weight": int(raw.get("weight") or 0),
        "media": {
            "file_id": int(media.get("fileId") or 0),
            "url": media_url,
            "title": _text(media.get("title")),
            "app_id": _text(media.get("appId")),
            "page_path": _text(media.get("pagePath")),
        },
        "paragraphs": paragraphs,
        "media_messages": media_messages,
        "status": int(raw.get("status") or 0),
        "status_text": _text(raw.get("statusText")),
        "create_time": _text(raw.get("createTime")),
        "update_time": _text(raw.get("updateTime")),
        "data_quality_flags": quality_flags,
    }


def _normalize_script_paragraphs(raw: Any) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    for position, paragraph in enumerate(raw or [], start=1):
        if not isinstance(paragraph, dict):
            continue
        messages: list[dict[str, Any]] = []
        for message in paragraph.get("messages") or []:
            normalized = _normalize_script_message(message)
            if normalized:
                messages.append(normalized)
        if messages:
            paragraphs.append(
                {
                    "paragraph_no": max(1, int(paragraph.get("paragraphNo") or position)),
                    "messages": messages,
                }
            )
    paragraphs.sort(key=lambda item: item["paragraph_no"])
    return paragraphs


def _normalize_script_message(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    message_type = _code(raw.get("msgType"))
    if message_type == "text":
        content = _text(raw.get("contentText"))
        return {"type": "text", "content": content} if content else None
    if message_type not in {"image", "video"}:
        return None
    url = _text(raw.get("mediaUrl"))
    return {
        "type": message_type,
        "url": url,
        "raw_url": _text(raw.get("mediaUrlRaw")),
        "title": _text(raw.get("mediaTitle")),
        "remark": _text(raw.get("remark")),
        "file_id": int(raw.get("fileId") or 0),
        "url_is_http": _is_http_url(url),
    }


def _legacy_script_paragraphs(raw: dict[str, Any], *, media: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    body_text = _text(raw.get("bodyText"))
    if body_text:
        messages.append({"type": "text", "content": body_text})
    media_url = _text(media.get("url"))
    content_type = _code(raw.get("contentType"))
    media_type = "video" if content_type == "video" else "image"
    if media_url:
        messages.append(
            {
                "type": media_type,
                "url": media_url,
                "raw_url": media_url,
                "title": _text(media.get("title")),
                "remark": "",
                "file_id": int(media.get("fileId") or 0),
                "url_is_http": _is_http_url(media_url),
            }
        )
    return [{"paragraph_no": 1, "messages": messages}] if messages else []


def _normalize_sequence(raw: dict[str, Any]) -> dict[str, Any] | None:
    sequence_id = _text(raw.get("id"))
    name = _text(raw.get("sequenceName"))
    if not sequence_id and not name:
        return None
    steps: list[dict[str, Any]] = []
    for item in raw.get("steps") or []:
        if not isinstance(item, dict):
            continue
        action_code = _code(item.get("actionCode"))
        if action_code and action_code not in ACTION_CODES:
            continue
        steps.append(
            {
                "id": _text(item.get("id")),
                "sort_order": int(item.get("sortOrder") or 0),
                "action_code": action_code,
                "action_name": _text(item.get("actionName")),
                "trigger_base": _code(item.get("triggerBase")),
                "trigger_base_name": _text(item.get("triggerBaseName")),
                "relative_value": max(0, int(item.get("relativeValue") or 0)),
                "relative_unit": _code(item.get("relativeUnit")),
                "fixed_time": _text(item.get("fixedTime")),
                "remark": _text(item.get("remark")),
            }
        )
    steps.sort(key=lambda item: item["sort_order"])
    return {
        "id": sequence_id,
        "sequence_name": name,
        "checkpoint_code": _code(raw.get("checkpointCode")),
        "checkpoint_name": _text(raw.get("checkpointName")),
        "description": _text(raw.get("description")),
        "status": int(raw.get("status") or 0),
        "status_text": _text(raw.get("statusText")),
        "step_count": int(raw.get("stepCount") or len(steps)),
        "estimated_minutes": max(0, int(raw.get("estimatedMinutes") or 0)),
        "steps": steps,
        "create_time": _text(raw.get("createTime")),
        "update_time": _text(raw.get("updateTime")),
    }


def _empty_result(schema_version: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "status": "empty",
        "source": "follow_knowledge_api",
        "reason": reason,
        "total": 0,
        "items": [],
        "cache_hit": False,
    }


def _response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text[:2000]}


def _text(value: Any) -> str:
    return str(value or "").replace("\x00", "").strip()


def _code(value: Any) -> str:
    return _text(value).lower()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
