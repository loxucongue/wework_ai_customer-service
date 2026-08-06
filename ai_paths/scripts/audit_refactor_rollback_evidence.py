from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


EXPECTED_REFACTOR_BRANCH = "codex/reply-chain-refactor"

DEPLOYMENT_SENSITIVE_PATH_PREFIXES: tuple[str, ...] = (
    ".github/workflows/",
    ".openai/",
    ".tmp_deploy/",
    "deploy_packages/",
    "deployment/",
    "infra/",
    "nginx/",
    "systemd/",
)

DEPLOYMENT_SENSITIVE_PATH_FRAGMENTS: tuple[str, ...] = (
    "/deploy",
    "deploy.",
    "deploy_",
    "release_bundle",
)


def audit_refactor_rollback_evidence(
    *,
    repo_root: Path,
    base_ref: str = "main",
    head_ref: str = "HEAD",
    expected_branch: str = EXPECTED_REFACTOR_BRANCH,
    include_worktree: bool = True,
) -> dict[str, Any]:
    """Audit rollback and no-deploy evidence for the reply-chain refactor branch."""

    branch = _git_output(repo_root, ["git", "branch", "--show-current"])
    changed_paths = _changed_paths(
        repo_root=repo_root,
        base_ref=base_ref,
        head_ref=head_ref,
        include_worktree=include_worktree,
    )
    deployment_sensitive = [
        path for path in changed_paths if _is_deployment_sensitive_path(path)
    ]
    commit = _git_output(repo_root, ["git", "rev-parse", head_ref])
    return {
        "schema_version": "reply_chain_refactor_rollback_evidence_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": commit,
        "git_commit_set": [commit] if commit else [],
        "base_ref": base_ref,
        "head_ref": head_ref,
        "branch": branch,
        "expected_branch": expected_branch,
        "include_worktree": include_worktree,
        "changed_paths": changed_paths,
        "changed_deployment_sensitive_paths": deployment_sensitive,
        "branch_is_refactor": branch == expected_branch,
        "main_branch_untouched": branch != "main",
        "deployment_sensitive_paths_unchanged": not deployment_sensitive,
        "rollback_plan": {
            "schema_version": "reply_chain_behavior_switch_rollback_plan_v1",
            "restore_flags_to_shadow_or_disabled": True,
            "revert_stage_commit": True,
            "rerun_diagnostics_before_reenable": True,
            "no_deployment_from_refactor_branch": True,
            "rollback_steps": [
                "keep behavior flags disabled or in shadow mode",
                "revert the reviewed refactor-stage commit if validation regresses",
                "rerun diagnostics, simulation, and model matrix evidence before another switch attempt",
            ],
        },
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
            "does_not_deploy": True,
        },
        "source": "audit_refactor_rollback_evidence",
    }


def _changed_paths(*, repo_root: Path, base_ref: str, head_ref: str, include_worktree: bool) -> list[str]:
    try:
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", base_ref, head_ref],
            cwd=repo_root,
            text=True,
        )
    paths = {_normalize_path(item) for item in raw.splitlines() if item.strip()}
    if include_worktree:
        worktree_raw = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repo_root,
            text=True,
        )
        paths.update(_normalize_path(item) for item in worktree_raw.splitlines() if item.strip())
    return sorted(paths)


def _is_deployment_sensitive_path(path: str) -> bool:
    normalized = _normalize_path(path)
    if normalized.startswith(DEPLOYMENT_SENSITIVE_PATH_PREFIXES):
        return True
    lower = normalized.lower()
    return any(fragment in lower for fragment in DEPLOYMENT_SENSITIVE_PATH_FRAGMENTS)


def _git_output(repo_root: Path, command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit reply-chain refactor rollback/no-deploy evidence. This is read-only and "
            "does not call models, production APIs, customer send paths, or deployment commands."
        )
    )
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--expected-branch", default=EXPECTED_REFACTOR_BRANCH)
    parser.add_argument("--committed-only", action="store_true")
    parser.add_argument("--report", type=Path, default=Path(".tmp_runtime/rollback_evidence_audit.json"))
    return parser.parse_args()


def main() -> int:
    args = _args()
    repo_root = Path(__file__).resolve().parents[2]
    report = audit_refactor_rollback_evidence(
        repo_root=repo_root,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        expected_branch=args.expected_branch,
        include_worktree=not args.committed_only,
    )
    output = args.report if args.report.is_absolute() else repo_root / args.report
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if _passes(report) else 1


def _passes(report: dict[str, Any]) -> bool:
    return (
        report.get("branch_is_refactor") is True
        and report.get("main_branch_untouched") is True
        and report.get("deployment_sensitive_paths_unchanged") is True
    )


if __name__ == "__main__":
    raise SystemExit(main())
