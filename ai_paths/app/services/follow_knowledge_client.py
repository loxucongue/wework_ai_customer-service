from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.config import Settings


CHECKPOINT_CODES = {
    "all",
    "distance",
    "price",
    "effect",
    "hesitation",
    "decision",
    "time_conflict",
    "alternative",
    "inquiry",
}
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
        checkpoint_code: str = "",
        action_code: str = "",
        script_name: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        checkpoint = _code(checkpoint_code)
        action = _code(action_code)
        if checkpoint and checkpoint not in CHECKPOINT_CODES - {"all"}:
            return _empty_result("follow_script_query_v1", "unknown_checkpoint_code")
        if action and action not in ACTION_CODES:
            return _empty_result("follow_script_query_v1", "unknown_action_code")
        payload = {
            "checkpointCode": checkpoint,
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
        if checkpoint and checkpoint not in CHECKPOINT_CODES:
            return _empty_result("follow_sequence_query_v1", "unknown_checkpoint_code")
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
        checkpoint_code: str,
        action_code: str,
    ) -> dict[str, Any]:
        return await self._query_all_pages(
            lambda page: self.query_scripts(
                checkpoint_code=checkpoint_code,
                action_code=action_code,
                page=page,
                page_size=100,
            ),
            schema_version="follow_script_index_v2",
        )

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
    checkpoint_code = _code(raw.get("checkpointCode"))
    action_code = _code(raw.get("actionCode"))
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    media_url = _text(media.get("url"))
    content_type = _code(raw.get("contentType")) or "text"
    quality_flags: list[str] = []
    if content_type != "text" and not media_url:
        quality_flags.append("declared_media_missing_url")
    elif media_url and not _is_http_url(media_url):
        quality_flags.append("media_value_is_not_http_url")
    return {
        "id": script_id,
        "script_code": script_code or f"script_{script_id}",
        "script_name": _text(raw.get("scriptName")),
        "body_text": _text(raw.get("bodyText")),
        "checkpoint_code": checkpoint_code,
        "checkpoint_name": _text(raw.get("checkpointName")),
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
        "status": int(raw.get("status") or 0),
        "status_text": _text(raw.get("statusText")),
        "create_time": _text(raw.get("createTime")),
        "update_time": _text(raw.get("updateTime")),
        "data_quality_flags": quality_flags,
    }


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


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
