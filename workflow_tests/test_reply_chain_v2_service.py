from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import Settings
from app.chat_request_context import is_isolated_v2_test_request
from app import main


def _request(client_host: str = "127.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/reply/workflow-compatible-v2",
            "headers": [],
            "client": (client_host, 12345),
            "server": ("127.0.0.1", 8001),
            "scheme": "http",
        }
    )


def test_refactor_service_configuration_defaults_are_safe_for_primary() -> None:
    settings = Settings(_env_file=None)

    assert settings.service_role == "primary"
    assert settings.background_workers_enabled is True


def test_health_exposes_traceable_release_identity(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "release_id", "v2-test-release")
    monkeypatch.setattr(main.settings, "build_git_commit", "abc123")
    monkeypatch.setattr(main.settings, "build_dirty", False)
    monkeypatch.setattr(main.settings, "build_config_revision", "rules-sha256")

    payload = asyncio.run(main.health())

    assert payload["release"] == {
        "release_id": "v2-test-release",
        "git_commit": "abc123",
        "dirty": False,
        "config_revision": "rules-sha256",
    }


def test_refactor_v2_route_is_explicit_and_does_not_replace_primary_route() -> None:
    paths = {route.path for route in main.app.routes}

    assert "/reply/workflow-compatible" in paths
    assert "/reply/workflow-compatible-v2" in paths
    assert "reply_chain_refactor" in inspect.getsource(main.reply_workflow_compatible_v2)
    assert 'interface_version="v2"' in inspect.getsource(main.reply_workflow_compatible_v2)


def test_v3_sidecar_route_is_explicit_and_does_not_replace_existing_routes() -> None:
    paths = {route.path for route in main.app.routes}

    assert "/reply/workflow-compatible" in paths
    assert "/reply/workflow-compatible-v2" in paths
    assert "/reply/workflow-compatible-v3" in paths
    source = inspect.getsource(main.reply_workflow_compatible_v3)
    assert "model_led_sales_brain_v3" in source
    assert 'interface_version="v3"' in source


def test_v3_takeover_guard_runs_before_media_preprocessing() -> None:
    source = inspect.getsource(main.workflow_compatible_reply)

    assert source.index("run_v3_takeover_guard") < source.index("platform_voice_batch_coordinator.prepare")


def test_workflow_interface_version_is_attached_to_request_context() -> None:
    request = main.ChatRequest(customer_id="c1", corp_id="corp")

    main._attach_request_interface_version(request, "v2")

    assert request.request_context["interface_version"] == "v2"
    assert request.request_context["api_version"] == "v2"


def test_v3_interface_version_adds_sidecar_markers() -> None:
    request = main.ChatRequest(customer_id="c1", corp_id="corp")

    main._attach_request_interface_version(request, "v3")

    assert request.request_context["interface_version"] == "v3"
    assert request.request_context["api_version"] == "v3"
    assert request.request_context["reply_chain_mode"] == "model_led_sales_brain_v3"
    assert request.request_context["v3_sidecar"] is True


def test_isolated_v2_smoke_requires_all_synthetic_contact_ids() -> None:
    context = {"test_isolated": True, "interface_version": "v2"}
    synthetic = main.ChatRequest(
        customer_id="sim_customer",
        corp_id="sim_corp",
        wechat="sim_wechat",
        external_userid="sim_external",
    )
    real_contact = synthetic.model_copy(update={"external_userid": "real_external"})

    assert is_isolated_v2_test_request(synthetic, context) is True
    assert is_isolated_v2_test_request(real_contact, context) is False
    assert is_isolated_v2_test_request(synthetic, {**context, "interface_version": "v1"}) is False
    assert is_isolated_v2_test_request(synthetic, {**context, "interface_version": "v3"}) is True


def test_background_workers_can_be_disabled_for_shared_data_sidecar() -> None:
    source = inspect.getsource(main.startup)

    assert "background_workers_enabled" in source
    assert source.index("background_workers_enabled") < source.index("backfill_first_day_outreach_runs")


@pytest.mark.parametrize("token", ["existing-v1-token", "v2-external-token"])
def test_v2_workflow_route_accepts_existing_v1_or_external_token(monkeypatch, token: str) -> None:
    monkeypatch.setattr(main.settings, "ai_paths_api_key", "existing-v1-token")
    monkeypatch.setattr(main.settings, "ai_external_api_key", "v2-external-token")

    asyncio.run(main.require_v2_workflow_api_key(_request("203.0.113.8"), f"Bearer {token}", None))


def test_v2_workflow_route_rejects_unknown_token(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ai_paths_api_key", "existing-v1-token")
    monkeypatch.setattr(main.settings, "ai_external_api_key", "v2-external-token")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.require_v2_workflow_api_key(_request("203.0.113.8"), "Bearer wrong-token", None))

    assert exc_info.value.status_code == 401


def test_v2_workflow_route_accepts_ip_restricted_nginx_proxy(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ai_paths_api_key", "existing-v1-token")
    monkeypatch.setattr(main.settings, "ai_external_api_key", "v2-external-token")

    asyncio.run(main.require_v2_workflow_api_key(_request(), None, "1"))


def test_v2_workflow_route_accepts_nginx_injected_proxy_header_with_forwarded_client(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ai_paths_api_key", "existing-v1-token")
    monkeypatch.setattr(main.settings, "ai_external_api_key", "v2-external-token")

    asyncio.run(main.require_v2_workflow_api_key(_request("120.26.43.96"), None, "1"))


def test_v2_workflow_route_rejects_local_request_without_proxy_header_or_token(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ai_paths_api_key", "existing-v1-token")
    monkeypatch.setattr(main.settings, "ai_external_api_key", "v2-external-token")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.require_v2_workflow_api_key(_request(), None, None))

    assert exc_info.value.status_code == 401


def test_v2_workflow_route_rejects_spoofed_proxy_header_from_remote_host(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ai_paths_api_key", "existing-v1-token")
    monkeypatch.setattr(main.settings, "ai_external_api_key", "v2-external-token")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.require_v2_workflow_api_key(_request("203.0.113.8"), None, "1"))

    assert exc_info.value.status_code == 401


def test_v3_workflow_route_accepts_own_proxy_header(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "ai_paths_api_key", "existing-v1-token")
    monkeypatch.setattr(main.settings, "ai_external_api_key", "v2-external-token")

    asyncio.run(
        main.require_v3_workflow_api_key(
            _request(),
            authorization=None,
            x_ai_paths_v2_trusted_proxy=None,
            x_ai_paths_v3_trusted_proxy="1",
        )
    )


def test_v3_route_is_hidden_unless_sidecar_role(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "service_role", "reply_chain_refactor")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            main.reply_workflow_compatible_v3(
                {"workflow_id": "xiaobei-default", "parameters": {}},
                background_tasks=None,
                _=None,
            )
        )

    assert exc_info.value.status_code == 404
