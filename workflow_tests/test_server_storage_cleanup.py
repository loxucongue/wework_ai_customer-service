from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scripts.cleanup_server_storage import apply_cleanup, build_cleanup_candidates


def _age(path: Path, *, days: int) -> None:
    timestamp = time.time() - days * 24 * 60 * 60
    os.utime(path, (timestamp, timestamp))


def _create_release(root: Path, name: str, *, days: int) -> Path:
    release = root / name
    release.mkdir(parents=True)
    payload = release / "payload.bin"
    payload.write_bytes(b"x" * 10)
    _age(payload, days=days)
    _age(release, days=days)
    return release


def test_cleanup_dry_run_and_apply_are_bounded_and_idempotent(tmp_path: Path) -> None:
    opt_root = tmp_path / "opt" / "ai-paths"
    tmp_root = tmp_path / "tmp"
    run_logs = opt_root / "logs" / "runs"
    run_logs.mkdir(parents=True)
    tmp_root.mkdir(parents=True)

    old_log = run_logs / "old.json"
    old_log.write_text("old", encoding="utf-8")
    _age(old_log, days=20)
    recent_log = run_logs / "recent.json"
    recent_log.write_text("recent", encoding="utf-8")

    old_archive = tmp_root / "old.tar.gz"
    old_archive.write_bytes(b"archive")
    _age(old_archive, days=5)
    recent_archive = tmp_root / "recent.tar.gz"
    recent_archive.write_bytes(b"archive")
    unrelated = tmp_root / "old.txt"
    unrelated.write_text("keep", encoding="utf-8")
    _age(unrelated, days=5)

    candidates, protected = build_cleanup_candidates(
        opt_root=opt_root,
        tmp_root=tmp_root,
        run_log_days=14,
        tmp_archive_days=2,
        keep_backend_releases=3,
        keep_frontend_releases=2,
        keep_frontend_backups=1,
    )
    paths = {candidate.path for candidate in candidates}
    assert old_log in paths
    assert old_archive in paths
    assert recent_log not in paths
    assert recent_archive not in paths
    assert unrelated not in paths
    assert old_log.exists()
    assert old_archive.exists()

    failures = apply_cleanup(
        candidates,
        opt_root=opt_root,
        tmp_root=tmp_root,
        protected=protected,
    )
    assert failures == []
    assert not old_log.exists()
    assert not old_archive.exists()
    assert recent_log.exists()
    assert recent_archive.exists()
    assert unrelated.exists()

    second, _ = build_cleanup_candidates(
        opt_root=opt_root,
        tmp_root=tmp_root,
        run_log_days=14,
        tmp_archive_days=2,
        keep_backend_releases=3,
        keep_frontend_releases=2,
        keep_frontend_backups=1,
    )
    assert not any(item.category in {"run_logs", "tmp_archives"} for item in second)


def test_cleanup_keeps_release_counts_and_latest_frontend_backup(tmp_path: Path) -> None:
    opt_root = tmp_path / "opt" / "ai-paths"
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir(parents=True)
    backend = opt_root / "releases"
    frontend = opt_root / "frontend-releases"
    backups = opt_root / "frontend-backups"

    for index in range(6):
        _create_release(backend, f"backend-{index}", days=10 - index)
        _create_release(frontend, f"frontend-{index}", days=10 - index)
        _create_release(backups, f"backup-{index}", days=10 - index)

    candidates, protected = build_cleanup_candidates(
        opt_root=opt_root,
        tmp_root=tmp_root,
        run_log_days=14,
        tmp_archive_days=2,
        keep_backend_releases=3,
        keep_frontend_releases=2,
        keep_frontend_backups=1,
    )
    assert len([item for item in candidates if item.category == "backend_releases"]) == 3
    assert len([item for item in candidates if item.category == "frontend_releases"]) == 4
    assert len([item for item in candidates if item.category == "frontend_backups"]) == 5

    failures = apply_cleanup(
        candidates,
        opt_root=opt_root,
        tmp_root=tmp_root,
        protected=protected,
    )
    assert failures == []
    assert len(list(backend.iterdir())) == 3
    assert len(list(frontend.iterdir())) == 2
    assert [path.name for path in backups.iterdir()] == ["backup-5"]


def test_cleanup_protects_active_release_and_external_node_modules_target(
    tmp_path: Path,
) -> None:
    opt_root = tmp_path / "opt" / "ai-paths"
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir(parents=True)
    backend = opt_root / "releases"
    frontend = opt_root / "frontend-releases"
    active_backend = _create_release(backend, "active-old", days=30)
    _create_release(backend, "new-1", days=3)
    _create_release(backend, "new-2", days=2)
    _create_release(backend, "new-3", days=1)
    _create_release(backend, "new-4", days=0)

    active_frontend = _create_release(frontend, "active-frontend", days=1)
    dependency_frontend = _create_release(frontend, "dependency-frontend", days=30)
    (dependency_frontend / "node_modules").mkdir()
    _create_release(frontend, "new-frontend-1", days=2)
    _create_release(frontend, "new-frontend-2", days=0)

    try:
        (opt_root / "current").symlink_to(active_backend, target_is_directory=True)
        (opt_root / "projects").symlink_to(active_frontend, target_is_directory=True)
        (active_frontend / "node_modules").symlink_to(
            dependency_frontend / "node_modules",
            target_is_directory=True,
        )
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    candidates, _ = build_cleanup_candidates(
        opt_root=opt_root,
        tmp_root=tmp_root,
        run_log_days=14,
        tmp_archive_days=2,
        keep_backend_releases=3,
        keep_frontend_releases=1,
        keep_frontend_backups=1,
    )
    paths = {item.path.resolve(strict=False) for item in candidates}
    assert active_backend.resolve() not in paths
    assert active_frontend.resolve() not in paths
    assert dependency_frontend.resolve() not in paths
