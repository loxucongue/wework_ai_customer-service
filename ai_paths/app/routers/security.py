from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Awaitable

from fastapi import Header, HTTPException, Request, status

from app.config import Settings


def api_key_dependency(settings: Settings) -> Callable[..., Awaitable[None]]:
    async def require_api_key(authorization: str | None = Header(default=None)) -> None:
        if not settings.ai_paths_api_key:
            return
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or token != settings.ai_paths_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API token",
            )

    return require_api_key


def workflow_api_key_dependency(settings: Settings) -> Callable[..., Awaitable[None]]:
    async def require_workflow_api_key(
        request: Request,
        authorization: str | None = Header(default=None),
        x_ai_paths_v3_trusted_proxy: str | None = Header(default=None),
    ) -> None:
        client_host = str(request.client.host if request.client else "").strip()
        trusted_proxy_hosts = {"127.0.0.1", "::1", "120.26.43.96", "121.199.0.182"}
        if x_ai_paths_v3_trusted_proxy == "1" and client_host in trusted_proxy_hosts:
            return
        accepted_tokens = {
            token
            for token in (settings.ai_paths_api_key, settings.ai_external_api_key)
            if token
        }
        if not accepted_tokens:
            if not settings.allow_missing_external_api_key:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Workflow API token is not configured",
                )
            return
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or token not in accepted_tokens:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing workflow API token",
            )

    return require_workflow_api_key


def delivery_callback_dependency(settings: Settings) -> Callable[..., Awaitable[None]]:
    async def require_callback_token(
        x_callback_token: str | None = Header(default=None, alias="X-Callback-Token"),
    ) -> None:
        expected = str(settings.message_delivery_callback_token or "").strip()
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Message delivery callback token is not configured",
            )
        if not x_callback_token or not secrets.compare_digest(x_callback_token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing message delivery callback token",
            )

    return require_callback_token
