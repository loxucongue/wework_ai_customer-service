from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
AI_PATHS_ROOT = REPO_ROOT / "ai_paths"
if str(AI_PATHS_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_PATHS_ROOT))

from app.config import Settings  # noqa: E402
from app.services.follow_knowledge_client import FollowKnowledgeClient  # noqa: E402


DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "follow_knowledge_cache"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync published follow-knowledge sequences and scripts to a local ignored cache."
    )
    parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        help="Optional .env file to load before Settings. Can be passed multiple times.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for local JSON cache files. Defaults to artifacts/follow_knowledge_cache.",
    )
    parser.add_argument(
        "--allow-disabled",
        action="store_true",
        help="Write a disabled manifest instead of failing when FOLLOW_KNOWLEDGE_TOKEN is missing.",
    )
    args = parser.parse_args()

    for env_file in args.env_file:
        _load_env_file(Path(env_file))

    return asyncio.run(_run(args.output_dir, allow_disabled=bool(args.allow_disabled)))


async def _run(output_dir: Path, *, allow_disabled: bool) -> int:
    settings = Settings()
    client = FollowKnowledgeClient(settings)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not client.available:
        manifest = {
            "schema_version": "follow_knowledge_local_cache_manifest_v1",
            "status": "disabled",
            "reason": "follow_knowledge_not_configured",
            "generated_at": now,
            "base_url_configured": bool(str(settings.follow_knowledge_base_url or "").strip()),
            "token_configured": bool(str(settings.follow_knowledge_token or "").strip()),
            "files": {},
        }
        if allow_disabled:
            _write_json(output_dir / "latest_manifest.json", manifest)
            print(json.dumps(_public_manifest(manifest), ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(_public_manifest(manifest), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    try:
        sequences, scripts = await asyncio.gather(
            client.query_all_sequences(),
            client.query_all_scripts(),
        )
        taxonomy = await client.query_script_taxonomy()
        raw_api = await _raw_follow_knowledge_export(settings)
    finally:
        await client.aclose()

    status = "ok" if all(
        str(item.get("status") or "") == "ok"
        for item in (sequences, scripts, taxonomy, raw_api)
    ) else "degraded"
    files = {
        "sequences": f"follow_sequences_{now}.json",
        "scripts": f"follow_scripts_{now}.json",
        "taxonomy": f"follow_taxonomy_{now}.json",
        "raw_api": f"follow_raw_api_{now}.json",
        "manifest": f"manifest_{now}.json",
    }
    manifest = {
        "schema_version": "follow_knowledge_local_cache_manifest_v1",
        "status": status,
        "generated_at": now,
        "source": "follow_knowledge_api",
        "base_url_configured": bool(str(settings.follow_knowledge_base_url or "").strip()),
        "token_configured": bool(str(settings.follow_knowledge_token or "").strip()),
        "counts": {
            "sequences": int(sequences.get("total") or len(sequences.get("items") or [])),
            "scripts": int(scripts.get("total") or len(scripts.get("items") or [])),
            "taxonomy_types": len(taxonomy.get("types") or []),
            "raw_sequences": int((raw_api.get("counts") or {}).get("sequences") or 0),
            "raw_scripts": int((raw_api.get("counts") or {}).get("scripts") or 0),
        },
        "statuses": {
            "sequences": sequences.get("status"),
            "scripts": scripts.get("status"),
            "taxonomy": taxonomy.get("status"),
            "raw_api": raw_api.get("status"),
        },
        "files": files,
    }

    _write_json(output_dir / files["sequences"], sequences)
    _write_json(output_dir / files["scripts"], scripts)
    _write_json(output_dir / files["taxonomy"], taxonomy)
    _write_json(output_dir / files["raw_api"], raw_api)
    _write_json(output_dir / files["manifest"], manifest)

    _write_json(output_dir / "latest_sequences.json", sequences)
    _write_json(output_dir / "latest_scripts.json", scripts)
    _write_json(output_dir / "latest_taxonomy.json", taxonomy)
    _write_json(output_dir / "latest_raw_api.json", raw_api)
    _write_json(output_dir / "latest_manifest.json", manifest)

    print(json.dumps(_public_manifest(manifest), ensure_ascii=False, indent=2))
    return 0 if status == "ok" else 1


async def _raw_follow_knowledge_export(settings: Settings) -> dict[str, Any]:
    base_url = str(settings.follow_knowledge_base_url or "").rstrip("/")
    token = str(settings.follow_knowledge_token or "").strip()
    if not base_url or not token:
        return {"schema_version": "follow_knowledge_raw_api_export_v1", "status": "disabled"}
    headers = {"Content-Type": "application/json", "x-event-token": token}
    timeout = max(1.0, float(settings.follow_knowledge_timeout_seconds or 4.0))
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as http:
        sequences = await _query_raw_pages(
            http,
            base_url=base_url,
            path="/event/trigger/follow-sequence",
            headers=headers,
            payload={"checkpointCode": "", "sequenceName": ""},
        )
        scripts = await _query_raw_pages(
            http,
            base_url=base_url,
            path="/event/trigger/follow-script",
            headers=headers,
            payload={
                "checkpointTypeId": 0,
                "checkpointTagId": 0,
                "checkpointCode": "",
                "actionCode": "",
                "scriptName": "",
            },
        )
    status = "ok" if sequences.get("status") == scripts.get("status") == "ok" else "degraded"
    return {
        "schema_version": "follow_knowledge_raw_api_export_v1",
        "status": status,
        "source": "follow_knowledge_api",
        "counts": {
            "sequences": len(sequences.get("items") or []),
            "scripts": len(scripts.get("items") or []),
        },
        "data": {
            "sequences": sequences,
            "scripts": scripts,
        },
    }


async def _query_raw_pages(
    http: httpx.AsyncClient,
    *,
    base_url: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    total = 0
    page = 1
    while True:
        body = dict(payload)
        body.update({"page": page, "pageSize": 100})
        response = await http.post(f"{base_url}{path}", headers=headers, json=body)
        response.raise_for_status()
        wrapper = response.json()
        if int(wrapper.get("code") or 0) != 200:
            return {
                "status": "error",
                "reason": str(wrapper.get("message") or wrapper.get("msg") or "business_error"),
                "items": items,
                "pages": pages,
            }
        data = wrapper.get("data") if isinstance(wrapper.get("data"), dict) else {}
        batch = [item for item in data.get("list") or [] if isinstance(item, dict)]
        total = max(total, int(data.get("total") or 0))
        pages.append({"page": page, "status": "ok", "count": len(batch), "total": total})
        items.extend(batch)
        if not batch or len(items) >= total or len(batch) < 100:
            break
        page += 1
        if page > 200:
            return {"status": "error", "reason": "too_many_pages", "items": items, "pages": pages}
    return {"status": "ok", "total": total, "items": items, "pages": pages}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"env file is missing: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest.get("schema_version"),
        "status": manifest.get("status"),
        "reason": manifest.get("reason", ""),
        "generated_at": manifest.get("generated_at"),
        "counts": manifest.get("counts", {}),
        "statuses": manifest.get("statuses", {}),
        "files": manifest.get("files", {}),
        "base_url_configured": manifest.get("base_url_configured"),
        "token_configured": manifest.get("token_configured"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
