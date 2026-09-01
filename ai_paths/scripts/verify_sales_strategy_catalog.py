from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.services.sales_strategy_service import SalesStrategyService


async def _check_url(client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        error = ""
        status = 0
        try:
            response = await client.head(url)
            status = response.status_code
            if status in {400, 403, 405}:
                response = await client.get(url, headers={"Range": "bytes=0-0"})
                status = response.status_code
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        return {"url": url, "ok": 200 <= status < 400, "status": status, "error": error}


async def _run(args: argparse.Namespace) -> int:
    settings = Settings(
        _env_file=None,
        SALES_STRATEGY_CATALOG_PATH=args.catalog,
        SALES_STRATEGY_CATALOG_ENABLED=True,
    )
    view = SalesStrategyService(settings).admin_view()
    contents = view.get("contents") or []
    urls = sorted({str(url) for item in contents for field in ("image_urls", "video_urls") for url in item.get(field) or [] if str(url or "")})
    url_results: list[dict[str, Any]] = []
    if args.check_urls:
        timeout = httpx.Timeout(args.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            semaphore = asyncio.Semaphore(max(1, min(args.concurrency, 32)))
            url_results = list(await asyncio.gather(*(_check_url(client, url, semaphore) for url in urls)))
    dynamic_without_facts = [
        item.get("content_id")
        for item in contents
        if any(str(flag).startswith("dynamic_") or flag in {"transaction_claim"} for flag in item.get("risk_flags") or [])
        and not item.get("required_facts")
    ]
    asset_ids_by_fingerprint: dict[str, set[str]] = {}
    for item in contents:
        for fingerprint, asset_id in zip(item.get("asset_fingerprints") or [], item.get("asset_ids") or []):
            if str(fingerprint or ""):
                asset_ids_by_fingerprint.setdefault(str(fingerprint), set()).add(str(asset_id or ""))
    inconsistent_assets = [key for key, values in asset_ids_by_fingerprint.items() if len(values) != 1 or "" in values]
    summary = {
        "schema_error_count": int((view.get("audit") or {}).get("error_count") or 0),
        "missing_id_count": sum(not str(item.get("content_id") or "") for item in contents),
        "missing_content_type_count": sum(not item.get("content_types") for item in contents),
        "dynamic_without_required_facts_count": len(dynamic_without_facts),
        "inconsistent_duplicate_asset_count": len(inconsistent_assets),
        "unique_url_count": len(urls),
        "checked_url_count": len(url_results),
        "available_url_count": sum(bool(item.get("ok")) for item in url_results),
        "url_availability_rate": (
            round(sum(bool(item.get("ok")) for item in url_results) / len(url_results), 4)
            if url_results
            else None
        ),
    }
    summary["passed"] = bool(
        summary["schema_error_count"] == 0
        and summary["missing_id_count"] == 0
        and summary["missing_content_type_count"] == 0
        and summary["dynamic_without_required_facts_count"] == 0
        and summary["inconsistent_duplicate_asset_count"] == 0
        and (not args.check_urls or summary["url_availability_rate"] == 1.0)
    )
    report = {
        "summary": summary,
        "dynamic_without_required_facts": dynamic_without_facts,
        "inconsistent_assets": inconsistent_assets,
        "url_failures": [item for item in url_results if not item.get("ok")],
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--check-urls", action="store_true")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
