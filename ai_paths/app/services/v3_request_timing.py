from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from app.services.storage.serialization import utc_now_iso


_BACKGROUND_FINALIZERS: set[asyncio.Task[None]] = set()


class V3RequestTimingMiddleware:
    """Measure the complete V3 HTTP lifecycle without affecting reply semantics."""

    def __init__(self, app: Any, *, repository: Any) -> None:
        self.app = app
        self.repository = repository

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not _is_v3_reply_request(scope):
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        ingress_id = str(uuid4())
        started_at = utc_now_iso()
        started_perf = time.perf_counter()
        state["v3_http_ingress_id"] = ingress_id
        state["v3_http_started_at"] = started_at
        response_finished = False

        async def timing_send(message: dict[str, Any]) -> None:
            nonlocal response_finished
            await send(message)
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                response_finished = True

        try:
            await self.app(scope, receive, timing_send)
        finally:
            request_id = str(state.get("v3_run_request_id") or "").strip()
            finalize = getattr(self.repository, "finalize_run_http_timing", None)
            if response_finished and request_id and callable(finalize):
                finished_at = utc_now_iso()
                duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))
                async def persist_timing() -> None:
                    try:
                        await asyncio.to_thread(
                            finalize,
                            request_id=request_id,
                            ingress_id=ingress_id,
                            started_at=started_at,
                            finished_at=finished_at,
                            duration_ms=duration_ms,
                        )
                    except Exception:
                        # Observability must never turn a completed customer reply into a 5xx.
                        pass

                if bool(state.get("v3_timing_finalize_background")):
                    task = asyncio.create_task(persist_timing())
                    _BACKGROUND_FINALIZERS.add(task)
                    task.add_done_callback(_BACKGROUND_FINALIZERS.discard)
                else:
                    await persist_timing()


def attach_v3_http_timing(http_request: Any, chat_request: Any) -> None:
    """Carry middleware timing into the run created for this exact HTTP request."""

    state = getattr(http_request, "state", None)
    ingress_id = str(getattr(state, "v3_http_ingress_id", "") or "").strip()
    started_at = str(getattr(state, "v3_http_started_at", "") or "").strip()
    if not ingress_id:
        ingress_id = str(uuid4())
        setattr(state, "v3_http_ingress_id", ingress_id)
    if not started_at:
        started_at = utc_now_iso()
        setattr(state, "v3_http_started_at", started_at)

    context = dict(chat_request.request_context or {})
    context["http_request_ingress_id"] = ingress_id
    context["http_request_started_at"] = started_at
    chat_request.request_context = context


def bind_v3_run_request_id(http_request: Any, request_id: str) -> None:
    state = getattr(http_request, "state", None)
    if state is not None and request_id:
        setattr(state, "v3_run_request_id", str(request_id))


def _is_v3_reply_request(scope: dict[str, Any]) -> bool:
    return (
        scope.get("type") == "http"
        and str(scope.get("method") or "").upper() == "POST"
        and str(scope.get("path") or "").rstrip("/") == "/reply/workflow-compatible-v3"
    )
