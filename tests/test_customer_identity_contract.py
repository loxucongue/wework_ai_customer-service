from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.chat_request_context import conversation_id_from_request  # noqa: E402
from app.customer_identity import (  # noqa: E402
    IdentityContractError,
    canonical_platform_customer_id,
    customer_identity_from_mapping,
    missing_managed_identity_fields,
)
from app.schemas import ChatRequest  # noqa: E402
from app.services.sop_platform_task_service import _task_identity  # noqa: E402
from app.services.customer_scope import build_customer_scope  # noqa: E402
from app.services.storage import AppRepository, SQLiteStore  # noqa: E402
from app.config import Settings  # noqa: E402
from app.services.workflow_compat import normalize_workflow_request  # noqa: E402


def _repository(tmp_path: Path) -> AppRepository:
    settings = Settings().model_copy(
        update={
            "aics_storage_backend": "sqlite",
            "db_path": tmp_path / "identity.db",
            "background_workers_enabled": False,
        }
    )
    store = SQLiteStore(settings)
    store.initialize()
    return AppRepository(store)


def test_external_userid_cannot_become_platform_customer_id() -> None:
    with pytest.raises(IdentityContractError, match="external_userid"):
        canonical_platform_customer_id(
            legacy_customer_id="wmanzqsqaazhreyfc0b31nfxj0xdoskq",
            external_userid="wmanzqsqaazhreyfc0b31nfxj0xdoskq",
        )
    with pytest.raises(ValueError, match="external_userid"):
        ChatRequest(
            content="hello",
            customer_id="wmanzqsqaazhreyfc0b31nfxj0xdoskq",
            corp_id="ww-corp",
            external_userid="wmanzqsqaazhreyfc0b31nfxj0xdoskq",
        )


def test_platform_task_aliases_map_to_distinct_identity_fields() -> None:
    task = {
        "customerId": 13574115,
        "customerWechatId": "wmanzqsqaazhreyfc0b31nfxj0xdoskq",
        "customerAddWechatId": 22741145,
        "corpId": "ww943af61cd5d2afe4",
        "userWechatId": 7294,
        "userWechat": "SL1580",
    }
    identity = customer_identity_from_mapping(task)
    assert identity.platform_customer_id == "13574115"
    assert identity.external_userid == "wmanzqsqaazhreyfc0b31nfxj0xdoskq"
    assert identity.customer_add_wechat_id == "22741145"
    assert identity.platform_user_id == "7294"
    assert identity.wechat == "SL1580"
    assert missing_managed_identity_fields(identity) == []


def test_workflow_normalization_requires_and_preserves_managed_identity() -> None:
    request = normalize_workflow_request(
        {
            "parameters": {
                "content": {"content": "hello", "msgid": "message-1"},
                "customerId": 13574115,
                "customerWechatId": "wmanzqsqaazhreyfc0b31nfxj0xdoskq",
                "customerAddWechatId": 22741145,
                "corpId": "ww943af61cd5d2afe4",
                "userWechatId": 7294,
                "userWechat": "SL1580",
            }
        }
    )
    assert request.customer_id == "13574115"
    assert request.platform_customer_id == "13574115"
    assert request.external_userid == "wmanzqsqaazhreyfc0b31nfxj0xdoskq"
    assert request.customer_add_wechat_id == "22741145"
    assert request.corp_id == "ww943af61cd5d2afe4"
    assert request.user_id == 7294
    assert request.wechat == "SL1580"

    with pytest.raises(ValueError, match="platform_customer_id"):
        normalize_workflow_request(
            {
                "parameters": {
                    "content": "hello",
                    "external_userid": "wmanzqsqaazhreyfc0b31nfxj0xdoskq",
                    "corp_id": "ww943af61cd5d2afe4",
                    "user_id": 7294,
                    "wechat": "SL1580",
                }
            }
        )


def test_invalid_platform_task_identity_is_missing_not_substituted() -> None:
    external_userid = "wmanzqsqaazhreyfc0b31nfxj0xdoskq"
    identity = _task_identity(
        {
            "customerId": external_userid,
            "customerWechatId": external_userid,
            "corpId": "ww943af61cd5d2afe4",
            "userWechatId": 7294,
            "userWechat": "SL1580",
        }
    )
    assert identity["customer_id"] == ""
    assert identity["platform_customer_id"] == ""
    assert identity["external_userid"] == external_userid
    assert "external_userid" in identity["identity_contract_error"]


def test_generated_conversation_id_is_stable_and_not_a_customer_id() -> None:
    request = ChatRequest(
        content="hello",
        customer_id="13574115",
        corp_id="ww943af61cd5d2afe4",
        external_userid="wmanzqsqaazhreyfc0b31nfxj0xdoskq",
        wechat="SL1580",
        user_id=7294,
    )
    first = conversation_id_from_request(request, {})
    second = conversation_id_from_request(request, {})
    assert first == second
    assert first.startswith("conversation:v3:")
    assert first not in {request.customer_id, request.external_userid}
    assert conversation_id_from_request(request, {"conversation_id": "upstream-conversation"}) == "upstream-conversation"


def test_sales_contact_scope_requires_external_userid() -> None:
    scope = build_customer_scope(
        corp_id="ww943af61cd5d2afe4",
        wechat="SL1580",
        customer_id="13574115",
    )
    assert scope.platform_customer_id == "13574115"
    assert scope.persistence_allowed is False
    assert scope.sales_contact_key == ""
    assert scope.missing == ("external_userid",)


def test_identity_registry_preserves_conflict_instead_of_overwriting(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    common = {
        "corp_id": "ww943af61cd5d2afe4",
        "wechat": "SL1580",
        "external_userid": "wmanzqsqaazhreyfc0b31nfxj0xdoskq",
        "user_id": "7294",
        "source": "test",
    }
    created = repository.observe_customer_identity(customer_id="13574115", **common)
    conflict = repository.observe_customer_identity(customer_id="99999999", **common)
    assert created["status"] == "created"
    assert conflict["status"] == "conflict"
    assert conflict["conflict"]["platform_customer_id_candidates"] == ["13574115", "99999999"]

    conflicts = repository.list_customer_identity_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["platform_customer_id"] == "13574115"
    assert repository.customer_identity_quality()["identity_links"] == {"conflict": 1}


def test_verified_platform_lookup_replaces_unverified_observation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    common = {
        "corp_id": "ww943af61cd5d2afe4",
        "wechat": "SL1580",
        "external_userid": "wmanzqsqaazhreyfc0b31nfxj0xdoskq",
        "user_id": "7294",
    }
    repository.observe_customer_identity(customer_id="13574115", source="request", **common)
    resolved = repository.observe_customer_identity(
        customer_id="13574116",
        source="platform_customer_lookup",
        verified=True,
        **common,
    )
    assert resolved["status"] == "resolved_by_verified_source"
    assert resolved["platform_customer_id"] == "13574116"
    assert repository.customer_identity_quality()["identity_links"] == {"verified": 1}


def test_unverified_request_cannot_downgrade_verified_identity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    common = {
        "corp_id": "ww943af61cd5d2afe4",
        "wechat": "SL1580",
        "external_userid": "wmanzqsqaazhreyfc0b31nfxj0xdoskq",
        "user_id": "7294",
    }
    repository.observe_customer_identity(
        customer_id="13574115",
        source="platform_customer_lookup",
        verified=True,
        **common,
    )
    same = repository.observe_customer_identity(
        customer_id="13574115",
        source="request",
        **common,
    )
    conflicting = repository.observe_customer_identity(
        customer_id="99999999",
        source="request",
        **common,
    )

    assert same["status"] == "updated"
    assert conflicting["status"] == "ignored_unverified_conflict"
    assert conflicting["platform_customer_id"] == "13574115"
    assert repository.customer_identity_quality()["identity_links"] == {"verified": 1}
    with repository.store.connect() as conn:
        row = conn.execute(
            "SELECT platform_customer_id_source FROM customer_identity_links"
        ).fetchone()
    assert row["platform_customer_id_source"] == "platform_customer_lookup"


def test_admin_scope_does_not_treat_external_id_as_customer_id(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    external_userid = "wmanzqsqaazhreyfc0b31nfxj0xdoskq"
    request = ChatRequest(
        content="hello",
        customer_id="13574115",
        corp_id="ww943af61cd5d2afe4",
        external_userid=external_userid,
        wechat="SL1580",
        user_id=7294,
    )
    repository.upsert_conversation(conversation_id="conversation-1", request=request, title="")

    mismatch = repository.resolve_customer_account_scope(external_userid, wechat="SL1580")
    resolved = repository.resolve_customer_account_scope(
        "13574115",
        wechat="SL1580",
        external_userid=external_userid,
    )
    assert mismatch["status"] == "identity_type_mismatch"
    assert resolved["status"] == "resolved"
    assert resolved["resolved_customer_id"] == "13574115"
