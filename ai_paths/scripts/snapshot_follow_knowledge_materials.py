"""Create an ignored, byte-verified Follow Knowledge media snapshot.

The command is read-only against Follow Knowledge. It validates every redirect,
bounds downloads, writes URLs and bytes only below the requested artifact
directory, and prints a URL-free summary suitable for an operator checklist.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import socket
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.services.follow_knowledge_client import FollowKnowledgeClient  # noqa: E402
from app.services.material_identity import MAX_MEDIA_BYTES, fingerprint  # noqa: E402

SNAPSHOT_VERSION = "follow_knowledge_material_snapshot_v1"
MAX_REDIRECTS = 3


def _digest(value: str | bytes) -> str:
    payload = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


async def _public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.strip().lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
        return address.is_global
    except ValueError:
        pass
    try:
        loop = asyncio.get_running_loop()
        records = await asyncio.wait_for(
            loop.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM),
            timeout=1.0,
        )
    except (OSError, asyncio.TimeoutError):
        return False
    addresses = {item[4][0] for item in records if item and len(item) > 4 and item[4]}
    return bool(addresses) and all(ipaddress.ip_address(item).is_global for item in addresses)


async def _download(client: httpx.AsyncClient, url: str, media_type: str) -> tuple[bytes | None, str]:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not await _public_url(current):
            return None, "non_public_url"
        try:
            async with client.stream("GET", current, headers={"Accept": f"{media_type}/*"}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return None, "redirect_without_location"
                    current = urljoin(current, location)
                    continue
                if response.status_code != 200:
                    return None, f"http_{response.status_code}"
                content_type = str(response.headers.get("content-type") or "").lower()
                if content_type and not (
                    content_type.startswith(f"{media_type}/") or content_type.startswith("application/octet-stream")
                ):
                    return None, "content_type_mismatch"
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_MEDIA_BYTES:
                        return None, "media_size_invalid"
                return (bytes(payload), "ok") if payload else (None, "empty_media")
        except (httpx.HTTPError, TimeoutError):
            return None, "download_failed"
    return None, "too_many_redirects"


def _catalog_media(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for script in items:
        source_id = str(script.get("source_ref") or script.get("id") or "")
        role = str(script.get("checkpoint_code") or "unknown")
        for message in script.get("media_messages") or []:
            if not isinstance(message, dict) or message.get("type") not in {"image", "video"}:
                continue
            output.append(
                {
                    "type": message["type"],
                    "url": str(message.get("url") or "").strip(),
                    "file_id": int(message.get("file_id") or 0),
                    "source_id": source_id,
                    "role": role,
                }
            )
    return output


async def snapshot(
    env_file: Path | None,
    catalog_json: Path | None,
    output_dir: Path,
    concurrency: int,
) -> dict[str, Any]:
    if catalog_json:
        response = json.loads(catalog_json.read_text(encoding="utf-8"))
    else:
        settings = Settings(
            _env_file=env_file,
            AI_PATHS_SERVICE_ROLE="reply",
            AI_PATHS_BACKGROUND_WORKERS_ENABLED=False,
            SOP_PLATFORM_PULL_ENABLED=False,
        )
        catalog = FollowKnowledgeClient(settings)
        try:
            response = await catalog.query_all_scripts()
        finally:
            await catalog.aclose()
    if response.get("status") != "ok":
        raise RuntimeError(f"catalog_unavailable:{response.get('reason') or 'unknown'}")
    media = _catalog_media(response.get("items") or [])
    directory_rows = sorted(
        (
            {"type": row["type"], "url_hash": _digest(row["url"]), "source_id": row["source_id"], "role": row["role"]}
            for row in media
        ),
        key=lambda row: (row["type"], row["url_hash"], row["source_id"]),
    )
    directory_checksum = _digest(json.dumps(directory_rows, sort_keys=True, separators=(",", ":")))
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 16)))
    unique = {(row["type"], row["url"]): row for row in media if row["url"]}
    results: dict[tuple[str, str], tuple[bytes | None, str]] = {}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False, trust_env=False
    ) as http:

        async def fetch(key: tuple[str, str]) -> None:
            async with semaphore:
                results[key] = await _download(http, key[1], key[0])

        await asyncio.gather(*(fetch(key) for key in unique))

    media_dir = output_dir / "media"
    records, pending = [], []
    for index, row in enumerate(media):
        payload, reason = results.get((row["type"], row["url"]), (None, "url_missing"))
        if payload is None:
            pending.append({"index": index, "type": row["type"], "role": row["role"], "reason": reason})
            continue
        try:
            fingerprint(payload, row["type"])
        except ValueError as exc:
            pending.append({"index": index, "type": row["type"], "role": row["role"], "reason": str(exc)})
            continue
        name = f"{_digest(row['type'] + chr(0) + row['url'])}.bin"
        media_dir.mkdir(parents=True, exist_ok=True)
        path = media_dir / name
        if not path.exists():
            path.write_bytes(payload)
        records.append(
            {
                "type": row["type"],
                "url": row["url"],
                "path": str(path.resolve()),
                "source": "follow_knowledge",
                "source_id": row["source_id"],
                "business_role": row["role"],
            }
        )
    roles = Counter(row["role"] for row in media)
    verified_roles = Counter(row["business_role"] for row in records)
    report = {
        "version": SNAPSHOT_VERSION,
        "directory_checksum": directory_checksum,
        "scripts": len(response.get("items") or []),
        "media_references": len(media),
        "unique_locators": len(unique),
        "verified_references": len(records),
        "pending_references": len(pending),
        "roles": dict(roles),
        "verified_roles": dict(verified_roles),
        "pending": pending,
    }
    _write_json(output_dir / "manifest.json", records)
    _write_json(output_dir / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--env-file", type=Path)
    source.add_argument("--catalog-json", type=Path, help="Ignored normalized Follow Knowledge script snapshot")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("--output-dir must be new or empty")
    report = asyncio.run(snapshot(args.env_file, args.catalog_json, args.output_dir.resolve(), args.concurrency))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not report["pending_references"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
