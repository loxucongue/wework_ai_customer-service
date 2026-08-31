from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_release_manifest import build_manifest


def test_release_manifest_refuses_dirty_worktree(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "scripts.build_release_manifest._git",
        lambda _root, *args: "abc123" if args == ("rev-parse", "HEAD") else " M changed.py",
    )

    with pytest.raises(RuntimeError, match="dirty worktree"):
        build_manifest(tmp_path, release_id="test")


def test_release_manifest_records_commit_and_config_revision(monkeypatch, tmp_path: Path) -> None:
    for relative in (
        "ai_paths/app/policies/business_rules.json",
        "config/sop_reply_packs.json",
        "config/v2_sop_asset_overlay.json",
        "config/v2_model_led_objection_playbook.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    monkeypatch.setattr(
        "scripts.build_release_manifest._git",
        lambda _root, *args: "abc123" if args == ("rev-parse", "HEAD") else "",
    )

    manifest = build_manifest(tmp_path, release_id="v2-test")

    assert manifest["release_id"] == "v2-test"
    assert manifest["git_commit"] == "abc123"
    assert manifest["dirty"] is False
    assert len(str(manifest["config_revision"])) == 64
    assert len(manifest["config_hashes"]) == 4
