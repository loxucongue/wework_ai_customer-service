from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.model_client import ModelClient
from app.services.store_destination_resolver import resolve_active_store_destination


DEFAULT_FIXTURE = Path("workflow_tests/fixtures/v3_store_address_matrix_20260814.json")
DEFAULT_OUTPUT = Path(".tmp_runtime/v3_store_address_matrix_20260814.json")


async def _run_case(
    *,
    index: int,
    address: str,
    client: ModelClient,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    state = {
        "normalized_content": address,
        "shared_context": {
            "current_message": {"message_type": "text", "content": address},
            "conversation": [],
            "authoritative_facts": {},
        },
    }
    async with semaphore:
        started = time.perf_counter()
        result = await resolve_active_store_destination(
            model_client=client,
            state=state,
            tool={"name": "resolve_customer_store", "purpose": "nearest_store"},
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    query = str(result.get("destination_query") or "").strip()
    needs_clarification = bool(result.get("needs_clarification"))
    refs = [str(item) for item in result.get("evidence_refs") or []]
    violations: list[str] = []
    if str(result.get("resolver_status") or "") != "ok":
        violations.append("resolver_not_ok")
    if not query and not needs_clarification:
        violations.append("missing_destination_without_clarification")
    if "current_message" not in refs:
        violations.append("missing_current_message_evidence")
    review_flags: list[str] = []
    if needs_clarification:
        review_flags.append("needs_clarification")
    if str(result.get("confidence") or "") == "low":
        review_flags.append("low_confidence")
    if str(result.get("destination_precision") or "") == "unknown":
        review_flags.append("unknown_precision")
    if query and query != address:
        review_flags.append("query_normalized")
    return {
        "case_id": f"address_{index:03d}",
        "address": address,
        "elapsed_ms": elapsed_ms,
        "hard_pass": not violations,
        "violations": violations,
        "review_flags": review_flags,
        "result": result,
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[position]


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# V3 Store Address Matrix",
        "",
        f"- Total: {summary['total']}",
        f"- Hard passed: {summary['hard_passed']}",
        f"- Hard failed: {summary['hard_failed']}",
        f"- Needs clarification: {summary['needs_clarification']}",
        f"- P50/P90: {summary['p50_ms']}ms / {summary['p90_ms']}ms",
        "",
        "| Case | Input | Destination | Precision | Status | Flags |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["cases"]:
        result = item["result"]
        status = "PASS" if item["hard_pass"] else "FAIL"
        flags = ", ".join([*item["violations"], *item["review_flags"]])
        lines.append(
            "| {case} | {address} | {query} | {precision} | {status} | {flags} |".format(
                case=item["case_id"],
                address=str(item["address"]).replace("|", "\\|"),
                query=str(result.get("destination_query") or "").replace("|", "\\|"),
                precision=result.get("destination_precision") or "",
                status=status,
                flags=flags,
            )
        )
    return "\n".join(lines) + "\n"


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run the V3 Haiku destination resolver address matrix once.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--case-ids", default="", help="Comma-separated case ids to rerun.")
    parser.add_argument("--enable-fallback", action="store_true")
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    addresses = [str(item).strip() for item in fixture.get("addresses") or [] if str(item).strip()]
    selected_case_ids = {item.strip() for item in args.case_ids.split(",") if item.strip()}
    indexed_addresses = [
        (index, address)
        for index, address in enumerate(addresses, start=1)
        if not selected_case_ids or f"address_{index:03d}" in selected_case_ids
    ]
    overrides: dict[str, Any] = {
            "model_store_destination_fallbacks": "",
            "model_emergency_fallbacks": "",
            "model_request_retry_attempts": 1,
            "model_store_destination_hedge_delay_seconds": 0.0,
            "model_store_destination_total_timeout_seconds": 45.0,
    }
    if args.enable_fallback:
        overrides = {
            "model_request_retry_attempts": 1,
            "model_store_destination_total_timeout_seconds": 45.0,
        }
    settings = Settings().model_copy(update=overrides)
    client = ModelClient(settings)
    semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))
    try:
        cases = await asyncio.gather(
            *(
                _run_case(index=index, address=address, client=client, semaphore=semaphore)
                for index, address in indexed_addresses
            )
        )
    finally:
        await client.aclose()

    durations = [int(item["elapsed_ms"]) for item in cases]
    hard_failed = [item for item in cases if not item["hard_pass"]]
    report = {
        "fixture": str(args.fixture),
        "model": settings.model_store_destination,
        "test_mode": (
            "single_attempt_with_configured_fallback_no_reviewer"
            if args.enable_fallback
            else "single_attempt_no_fallback_no_reviewer"
        ),
        "summary": {
            "total": len(cases),
            "hard_passed": len(cases) - len(hard_failed),
            "hard_failed": len(hard_failed),
            "needs_clarification": sum(
                bool(item["result"].get("needs_clarification")) for item in cases
            ),
            "low_confidence": sum(item["result"].get("confidence") == "low" for item in cases),
            "mean_ms": int(statistics.mean(durations)) if durations else 0,
            "p50_ms": _percentile(durations, 0.50),
            "p90_ms": _percentile(durations, 0.90),
            "max_ms": max(durations, default=0),
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    print(f"JSON: {args.output}", flush=True)
    print(f"Markdown: {markdown_path}", flush=True)
    return 1 if hard_failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
