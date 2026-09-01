from __future__ import annotations

from fastapi import FastAPI

from app.runtime_roles import RuntimeRole


_FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/health"}


def route_visible_for_role(path: str, role: RuntimeRole) -> bool:
    if path in _FRAMEWORK_PATHS:
        return True
    if role is RuntimeRole.REPLY:
        return path == "/reply/workflow-compatible-v3"
    if role is RuntimeRole.CONTROL:
        return path.startswith("/admin/") or path.startswith("/callbacks/")
    return False


def apply_runtime_route_policy(app: FastAPI, role: RuntimeRole) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if route_visible_for_role(str(getattr(route, "path", "")), role)
    ]
