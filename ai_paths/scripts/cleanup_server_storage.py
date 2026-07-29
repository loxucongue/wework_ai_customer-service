from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".zip")


@dataclass(frozen=True)
class CleanupCandidate:
    category: str
    path: Path
    size_bytes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or apply bounded cleanup for an AI Paths server.",
    )
    parser.add_argument("--opt-root", type=Path, default=Path("/opt/ai-paths"))
    parser.add_argument("--tmp-root", type=Path, default=Path("/tmp"))
    parser.add_argument("--run-log-days", type=int, default=14)
    parser.add_argument("--tmp-archive-days", type=int, default=2)
    parser.add_argument("--keep-backend-releases", type=int, default=3)
    parser.add_argument("--keep-frontend-releases", type=int, default=2)
    parser.add_argument("--keep-frontend-backups", type=int, default=1)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _resolved(path: Path) -> Path | None:
    try:
        if path.exists() or path.is_symlink():
            return path.resolve(strict=False)
    except OSError:
        return None
    return None


def _path_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return int(path.lstat().st_size)
        if path.is_file():
            return int(path.stat().st_size)
    except OSError:
        return 0

    total = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            root_path = Path(root)
            for name in files:
                item = root_path / name
                try:
                    total += int(item.lstat().st_size)
                except OSError:
                    continue
            for name in dirs:
                item = root_path / name
                if item.is_symlink():
                    try:
                        total += int(item.lstat().st_size)
                    except OSError:
                        continue
    except OSError:
        return total
    return total


def _immediate_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for item in root.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                result.append(item)
        except OSError:
            continue
    return result


def _newest(paths: Iterable[Path]) -> list[Path]:
    def key(path: Path) -> tuple[float, str]:
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        return modified, path.name

    return sorted(paths, key=key, reverse=True)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _candidate_contains_protected(path: Path, protected: set[Path]) -> bool:
    candidate = path.resolve(strict=False)
    for target in protected:
        try:
            target.resolve(strict=False).relative_to(candidate)
            return True
        except (OSError, ValueError):
            continue
    return False


def _known_protected_paths(opt_root: Path) -> set[Path]:
    protected: set[Path] = {
        opt_root.resolve(strict=False),
        (opt_root / ".env").resolve(strict=False),
        (opt_root / "data").resolve(strict=False),
        (opt_root / "shared").resolve(strict=False),
    }
    for link in (opt_root / "current", opt_root / "projects"):
        target = _resolved(link)
        if target is not None:
            protected.add(target)

    node_modules = _resolved(opt_root / "projects" / "node_modules")
    if node_modules is not None:
        protected.add(node_modules)

    if opt_root.is_dir():
        for item in opt_root.iterdir():
            try:
                if item.is_symlink():
                    target = _resolved(item)
                    if target is not None:
                        protected.add(target)
            except OSError:
                continue
    return protected


def _old_files(root: Path, *, older_than_days: int, archives_only: bool = False) -> list[Path]:
    if not root.is_dir():
        return []
    cutoff = time.time() - max(1, older_than_days) * 24 * 60 * 60
    iterator = root.iterdir() if archives_only else root.rglob("*")
    result: list[Path] = []
    for item in iterator:
        try:
            if not item.is_file() or item.is_symlink() or item.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        if archives_only and not item.name.lower().endswith(ARCHIVE_SUFFIXES):
            continue
        result.append(item)
    return result


def _release_candidates(
    root: Path,
    *,
    active: Path | None,
    keep_recent: int,
    protected: set[Path],
) -> list[Path]:
    directories = _newest(_immediate_directories(root))
    keep: set[Path] = set()
    if active is not None:
        keep.add(active.resolve(strict=False))

    for item in directories:
        resolved = item.resolve(strict=False)
        if resolved in keep:
            continue
        if len([path for path in keep if _is_within(path, root)]) >= keep_recent + int(active is not None):
            break
        keep.add(resolved)

    return [
        item
        for item in directories
        if item.resolve(strict=False) not in keep
        and not _candidate_contains_protected(item, protected)
    ]


def build_cleanup_candidates(
    *,
    opt_root: Path,
    tmp_root: Path,
    run_log_days: int,
    tmp_archive_days: int,
    keep_backend_releases: int,
    keep_frontend_releases: int,
    keep_frontend_backups: int,
) -> tuple[list[CleanupCandidate], set[Path]]:
    opt_root = opt_root.resolve(strict=False)
    tmp_root = tmp_root.resolve(strict=False)
    protected = _known_protected_paths(opt_root)

    candidates: list[CleanupCandidate] = []
    for path in _old_files(opt_root / "logs" / "runs", older_than_days=run_log_days):
        candidates.append(CleanupCandidate("run_logs", path, _path_size(path)))
    for path in _old_files(
        tmp_root,
        older_than_days=tmp_archive_days,
        archives_only=True,
    ):
        candidates.append(CleanupCandidate("tmp_archives", path, _path_size(path)))

    active_backend = _resolved(opt_root / "current")
    for path in _release_candidates(
        opt_root / "releases",
        active=active_backend,
        keep_recent=max(0, keep_backend_releases),
        protected=protected,
    ):
        candidates.append(CleanupCandidate("backend_releases", path, _path_size(path)))

    active_frontend = _resolved(opt_root / "projects")
    for path in _release_candidates(
        opt_root / "frontend-releases",
        active=active_frontend,
        keep_recent=max(0, keep_frontend_releases),
        protected=protected,
    ):
        candidates.append(CleanupCandidate("frontend_releases", path, _path_size(path)))

    backups = _newest(_immediate_directories(opt_root / "frontend-backups"))
    keep_backups = {
        path.resolve(strict=False)
        for path in backups[: max(0, keep_frontend_backups)]
    }
    for path in backups:
        if path.resolve(strict=False) in keep_backups:
            continue
        if _candidate_contains_protected(path, protected):
            continue
        candidates.append(CleanupCandidate("frontend_backups", path, _path_size(path)))

    return candidates, protected


def _delete_candidate(candidate: CleanupCandidate, *, allowed_roots: tuple[Path, ...]) -> None:
    path = candidate.path
    if path.is_symlink():
        raise RuntimeError(f"refusing to delete symlink: {path}")
    if not any(_is_within(path, root) and path.resolve(strict=False) != root.resolve(strict=False) for root in allowed_roots):
        raise RuntimeError(f"path is outside cleanup roots: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def apply_cleanup(
    candidates: list[CleanupCandidate],
    *,
    opt_root: Path,
    tmp_root: Path,
    protected: set[Path],
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    allowed_roots = (
        opt_root / "logs" / "runs",
        opt_root / "releases",
        opt_root / "frontend-releases",
        opt_root / "frontend-backups",
        tmp_root,
    )
    for candidate in candidates:
        if _candidate_contains_protected(candidate.path, protected):
            failures.append(
                {
                    "path": str(candidate.path),
                    "error": "candidate contains a protected path",
                }
            )
            continue
        try:
            _delete_candidate(candidate, allowed_roots=allowed_roots)
        except Exception as exc:
            failures.append({"path": str(candidate.path), "error": str(exc)})
    return failures


def _summary(
    candidates: list[CleanupCandidate],
    *,
    protected: set[Path],
    applied: bool,
    failures: list[dict[str, str]],
) -> dict[str, object]:
    grouped: dict[str, dict[str, object]] = defaultdict(
        lambda: {"count": 0, "size_bytes": 0, "sample_paths": []}
    )
    for candidate in candidates:
        item = grouped[candidate.category]
        item["count"] = int(item["count"]) + 1
        item["size_bytes"] = int(item["size_bytes"]) + candidate.size_bytes
        sample_paths = item["sample_paths"]
        if isinstance(sample_paths, list) and len(sample_paths) < 10:
            sample_paths.append(str(candidate.path))
    return {
        "mode": "apply" if applied else "dry_run",
        "candidate_count": len(candidates),
        "candidate_size_bytes": sum(item.size_bytes for item in candidates),
        "categories": dict(grouped),
        "protected_paths": sorted(str(path) for path in protected),
        "failures": failures,
    }


def main() -> int:
    args = parse_args()
    candidates, protected = build_cleanup_candidates(
        opt_root=args.opt_root,
        tmp_root=args.tmp_root,
        run_log_days=args.run_log_days,
        tmp_archive_days=args.tmp_archive_days,
        keep_backend_releases=args.keep_backend_releases,
        keep_frontend_releases=args.keep_frontend_releases,
        keep_frontend_backups=args.keep_frontend_backups,
    )
    failures: list[dict[str, str]] = []
    if args.apply:
        failures = apply_cleanup(
            candidates,
            opt_root=args.opt_root.resolve(strict=False),
            tmp_root=args.tmp_root.resolve(strict=False),
            protected=protected,
        )
    summary = _summary(
        candidates,
        protected=protected,
        applied=args.apply,
        failures=failures,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"mode={summary['mode']}")
        print(f"candidate_count={summary['candidate_count']}")
        print(f"candidate_size_bytes={summary['candidate_size_bytes']}")
        for category, item in summary["categories"].items():
            print(
                f"{category}: count={item['count']} "
                f"size_bytes={item['size_bytes']}"
            )
        if failures:
            for failure in failures:
                print(f"ERROR {failure['path']}: {failure['error']}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
