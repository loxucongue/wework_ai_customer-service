from __future__ import annotations

from pathlib import Path

from ai_paths.scripts.audit_business_wording_freeze import (
    PROTECTED_BUSINESS_ASSET_PATHS,
    _normalize_path,
    audit_business_wording_freeze,
)


def test_business_wording_freeze_audit_detects_protected_path_changes(monkeypatch) -> None:
    import ai_paths.scripts.audit_business_wording_freeze as audit

    monkeypatch.setattr(
        audit,
        "_changed_paths",
        lambda **kwargs: [
            "ai_paths/app/policies/business_rules.json",
            "ai_paths/app/services/model_client.py",
        ],
    )
    monkeypatch.setattr(audit, "_git_commit", lambda repo_root, ref: "abc123")

    report = audit_business_wording_freeze(repo_root=Path("."), base_ref="main", head_ref="HEAD")

    assert report["schema_version"] == "reply_chain_business_wording_freeze_audit_v1"
    assert report["git_commit"] == "abc123"
    assert report["git_commit_set"] == ["abc123"]
    assert report["include_worktree"] is True
    assert report["changed_protected_paths"] == ["ai_paths/app/policies/business_rules.json"]
    assert report["customer_visible_business_assets_unchanged"] is False
    assert report["review_required"] is True
    assert report["safety"]["does_not_call_models"] is True


def test_business_wording_freeze_audit_passes_structural_only_changes(monkeypatch) -> None:
    import ai_paths.scripts.audit_business_wording_freeze as audit

    monkeypatch.setattr(
        audit,
        "_changed_paths",
        lambda **kwargs: [
            "ai_paths/app/services/model_client.py",
            "workflow_tests/test_model_client_json_mode.py",
        ],
    )
    monkeypatch.setattr(audit, "_git_commit", lambda repo_root, ref: "abc123")

    report = audit_business_wording_freeze(repo_root=Path("."), base_ref="main", head_ref="HEAD")

    assert report["changed_protected_paths"] == []
    assert report["customer_visible_business_assets_unchanged"] is True
    assert report["review_required"] is False


def test_business_wording_freeze_protected_paths_cover_customer_visible_assets() -> None:
    protected = set(PROTECTED_BUSINESS_ASSET_PATHS)

    assert "ai_paths/app/policies/business_rules.json" in protected
    assert "ai_paths/app/policies/precision_qa_playbook.json" in protected
    assert "config/sop_reply_packs.json" in protected
    assert "ai_paths/app/services/payment_collection.py" in protected


def test_business_wording_freeze_normalizes_windows_paths() -> None:
    assert _normalize_path(r".\config\sop_reply_packs.json") == "config/sop_reply_packs.json"
