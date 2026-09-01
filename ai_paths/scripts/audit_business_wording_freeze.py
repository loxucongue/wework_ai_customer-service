from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROTECTED_BUSINESS_ASSET_PATHS: tuple[str, ...] = (
    "ai_paths/app/policies/business_rules.json",
    "ai_paths/app/policies/precision_qa_playbook.json",
    "config/sop_reply_packs.json",
    "ai_paths/app/prompts/global_contract.py",
    "ai_paths/app/prompts/reply_synthesizer.py",
    "ai_paths/app/prompts/sop_chat_gate.py",
    "ai_paths/app/services/payment_collection.py",
    "ai_paths/app/services/sop_reply_pack_service.py",
    "ai_paths/app/services/precision_qa_playbook_service.py",
)


def audit_business_wording_freeze(
    *,
    repo_root: Path,
    base_ref: str = "main",
    head_ref: str = "HEAD",
    protected_paths: tuple[str, ...] = PROTECTED_BUSINESS_ASSET_PATHS,
    include_worktree: bool = True,
) -> dict[str, Any]:
    """Audit whether a structural refactor changed customer-visible rule assets."""

    changed_paths = _changed_paths(
        repo_root=repo_root,
        base_ref=base_ref,
        head_ref=head_ref,
        include_worktree=include_worktree,
    )
    protected = sorted({_normalize_path(item) for item in protected_paths})
    changed_protected = sorted(path for path in changed_paths if path in set(protected))
    commit = _git_commit(repo_root, head_ref)
    return {
        "schema_version": "reply_chain_business_wording_freeze_audit_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit": commit,
        "git_commit_set": [commit] if commit else [],
        "base_ref": base_ref,
        "head_ref": head_ref,
        "include_worktree": include_worktree,
        "protected_paths": protected,
        "changed_paths": changed_paths,
        "changed_protected_paths": changed_protected,
        "customer_visible_business_assets_unchanged": not changed_protected,
        "review_required": bool(changed_protected),
        "safety": {
            "audit_only": True,
            "does_not_change_runtime_behavior": True,
            "does_not_send_customer_messages": True,
            "does_not_write_database": True,
            "does_not_call_models": True,
        },
        "source": "audit_business_wording_freeze",
    }


def _changed_paths(*, repo_root: Path, base_ref: str, head_ref: str, include_worktree: bool = True) -> list[str]:
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


def _git_commit(repo_root: Path, ref: str) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", ref], cwd=repo_root, text=True).strip()
    except Exception:
        return ""


def _normalize_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit whether reply-chain refactor commits changed protected business wording assets. "
            "This is read-only and does not call models, production APIs, or customer send paths."
        )
    )
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--committed-only", action="store_true")
    parser.add_argument("--report", type=Path, default=Path(".tmp_runtime/business_wording_freeze_audit.json"))
    return parser.parse_args()


def main() -> int:
    args = _args()
    repo_root = Path(__file__).resolve().parents[2]
    report = audit_business_wording_freeze(
        repo_root=repo_root,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        include_worktree=not args.committed_only,
    )
    output = args.report if args.report.is_absolute() else repo_root / args.report
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["customer_visible_business_assets_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
