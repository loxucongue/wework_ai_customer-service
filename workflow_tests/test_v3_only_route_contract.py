from __future__ import annotations

from pathlib import Path

from app import main


ROOT = Path(__file__).resolve().parents[1]


def test_retired_reply_routes_are_absent_from_application() -> None:
    paths = {route.path for route in main.app.routes}
    for path in (
        "/chat",
        "/reply",
        "/chat/workflow-compatible",
        "/reply/workflow-compatible",
        "/chat/workflow-compatible-v2",
        "/reply/workflow-compatible-v2",
    ):
        assert path not in paths
    assert "/reply/workflow-compatible-v3" in paths


def test_deploy_config_never_proxies_retired_reply_routes() -> None:
    config = (ROOT / "deploy" / "ai-paths.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:8000/reply;" not in config
    assert "proxy_pass http://127.0.0.1:8000/reply/workflow-compatible;" not in config
    for path in (
        "/api/ai-paths/chat",
        "/api/ai/chat",
        "/api/ai/chat/workflow-compatible",
        "/api/ai/reply",
        "/api/ai/reply/workflow-compatible",
        "/api/ai/reply/workflow-compatible-v2",
    ):
        assert f"location = {path}" in config
