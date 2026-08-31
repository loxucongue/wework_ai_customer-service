from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable


CONFIG_PATHS = (
    "ai_paths/app/policies/business_rules.json",
    "config/sop_reply_packs.json",
    "config/v2_sop_asset_overlay.json",
    "config/v2_model_led_objection_playbook.json",
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _file_hashes(root: Path, paths: Iterable[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for relative in paths:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"release config file is missing: {relative}")
        output[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return output


def build_manifest(root: Path, *, release_id: str, allow_dirty: bool = False) -> dict[str, object]:
    git_commit = _git(root, "rev-parse", "HEAD")
    dirty = bool(_git(root, "status", "--porcelain"))
    if dirty and not allow_dirty:
        raise RuntimeError("refusing to build a release manifest from a dirty worktree")
    config_hashes = _file_hashes(root, CONFIG_PATHS)
    revision_input = "\n".join(f"{key}:{config_hashes[key]}" for key in sorted(config_hashes))
    config_revision = hashlib.sha256(revision_input.encode("utf-8")).hexdigest()
    return {
        "schema_version": "ai_paths_release_manifest_v1",
        "release_id": release_id,
        "git_commit": git_commit,
        "dirty": dirty,
        "config_revision": config_revision,
        "config_hashes": config_hashes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a traceable AI Paths release manifest.")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    manifest = build_manifest(root, release_id=args.release_id, allow_dirty=args.allow_dirty)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
