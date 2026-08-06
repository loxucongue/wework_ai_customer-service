from __future__ import annotations

import subprocess
from pathlib import Path

from ai_paths.scripts.audit_refactor_rollback_evidence import (
    audit_refactor_rollback_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


def test_refactor_rollback_evidence_audit_is_read_only_and_branch_scoped() -> None:
    report = audit_refactor_rollback_evidence(
        repo_root=ROOT,
        base_ref="main",
        head_ref="HEAD",
        expected_branch="codex/reply-chain-refactor",
        include_worktree=True,
    )

    assert report["schema_version"] == "reply_chain_refactor_rollback_evidence_v1"
    assert report["branch"] == "codex/reply-chain-refactor"
    assert report["branch_is_refactor"] is True
    assert report["main_branch_untouched"] is True
    assert report["safety"]["audit_only"] is True
    assert report["safety"]["does_not_deploy"] is True
    assert report["rollback_plan"]["revert_stage_commit"] is True
    assert report["rollback_plan"]["rollback_steps"]


def test_refactor_rollback_evidence_audit_reports_deployment_sensitive_paths(monkeypatch) -> None:
    def fake_check_output(command, cwd, text, stderr=None):  # noqa: ANN001
        if command[:3] == ["git", "branch", "--show-current"]:
            return "codex/reply-chain-refactor\n"
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return "abc123\n"
        if command[:3] == ["git", "diff", "--name-only"]:
            if command[-1] == "HEAD":
                return ""
            return ".github/workflows/deploy.yml\nai_paths/app/services/foo.py\n"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    report = audit_refactor_rollback_evidence(
        repo_root=ROOT,
        base_ref="main",
        head_ref="HEAD",
        include_worktree=True,
    )

    assert report["changed_deployment_sensitive_paths"] == [".github/workflows/deploy.yml"]
    assert report["deployment_sensitive_paths_unchanged"] is False
