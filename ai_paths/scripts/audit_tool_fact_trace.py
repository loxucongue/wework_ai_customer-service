from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


MAX_REASONABLE_SAME_CITY_DISTANCE_METERS = 150_000
STORE_NAME_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,24}店")
DISTANCE_PATTERN = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>公里|千米|km|KM|米)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit AI Paths run logs for tool fact traceability.")
    parser.add_argument("paths", nargs="+", help="Run JSON files or directories containing run JSON files.")
    parser.add_argument("--limit", type=int, default=0, help="Max files per directory, newest first. 0 means all.")
    args = parser.parse_args()

    files = _collect_files(args.paths, limit=args.limit)
    if not files:
        print("No run JSON files found.", file=sys.stderr)
        return 2

    reports = [audit_run_file(path) for path in files]
    print(json.dumps({"count": len(reports), "reports": reports}, ensure_ascii=False, indent=2))
    return 1 if any(report["issues"] for report in reports) else 0


def _collect_files(paths: list[str], *, limit: int) -> list[Path]:
    files: list[Path] = []
    for value in paths:
        path = Path(value)
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            candidates = sorted(path.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
            files.extend(candidates[:limit] if limit > 0 else candidates)
    return files


def audit_run_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(path), "issues": [f"read_error:{type(exc).__name__}:{exc}"]}

    trace_source = data.get("raw_log") if isinstance(data.get("raw_log"), dict) else data
    trace = trace_source.get("trace") if isinstance(trace_source.get("trace"), list) else []
    execute = _find_node(trace, "execute_actions")
    synth = _find_node(trace, "synthesize_reply")
    executed_calls = _executed_tool_calls(execute)
    final_messages = _reply_messages(synth)
    store_outputs = [call.get("output") for call in executed_calls if call.get("name") == "store_lookup" and isinstance(call.get("output"), dict)]
    distance_outputs = [call.get("output") for call in executed_calls if call.get("name") == "distance_lookup" and isinstance(call.get("output"), dict)]

    store_facts = _store_facts(store_outputs)
    real_store_ids = {str(item.get("id") or item.get("store_id") or "").strip() for item in store_facts if isinstance(item, dict)}
    real_store_names = {str(item.get("name") or "").strip() for item in store_facts if isinstance(item, dict) and str(item.get("name") or "").strip()}

    issues: list[str] = []
    store_authorities = [str(output.get("data_authority") or output.get("store_data_authority") or "").strip() for output in store_outputs]
    has_store_message = any(_message_type(message) == "store_address" for message in final_messages)
    has_store_text = bool(_store_names_in_text(final_messages, real_store_names))

    if has_store_message or has_store_text:
        if not store_outputs:
            issues.append("store_fact_claim_without_store_lookup")
        elif not any(authority == "platform" for authority in store_authorities):
            issues.append(f"store_fact_claim_without_platform_authority:{store_authorities}")

    for message in final_messages:
        if _message_type(message) != "store_address":
            continue
        store_id = _message_content_value(message, "store_id")
        if not store_id:
            issues.append("store_address_missing_store_id")
        elif store_id not in real_store_ids:
            issues.append(f"store_address_untraceable:{store_id}")

    fabricated = _fabricated_store_mentions(final_messages, real_store_names)
    for name in fabricated:
        issues.append(f"fabricated_store_name:{name}")

    for claim in _distance_claims(final_messages):
        if not _distance_outputs_ok(distance_outputs):
            issues.append(f"unbacked_distance_claim:{claim}")
        elif claim["meters"] > MAX_REASONABLE_SAME_CITY_DISTANCE_METERS:
            issues.append(f"abnormal_distance_claim:{claim['text']}")

    for output in distance_outputs:
        for item in _distance_items(output):
            meters = _safe_int(item.get("distance_meters"))
            if meters and meters > MAX_REASONABLE_SAME_CITY_DISTANCE_METERS:
                issues.append(f"abnormal_distance_fact:{item.get('name') or item.get('id')}:{meters}")

    return {
        "path": str(path),
        "request_id": data.get("request_id") or data.get("run_id") or _wrapped_request_id(data) or path.stem,
        "executed_tool_calls": [str(call.get("name") or "") for call in executed_calls],
        "store_data_authority": store_authorities,
        "store_ids": sorted(real_store_ids),
        "store_names": sorted(real_store_names),
        "reply_messages": final_messages,
        "issues": issues,
    }


def _find_node(trace: list[Any], node_name: str) -> dict[str, Any]:
    for item in trace:
        if isinstance(item, dict) and item.get("node") == node_name:
            return item
    return {}


def _executed_tool_calls(execute_node: dict[str, Any]) -> list[dict[str, Any]]:
    output = execute_node.get("output_snapshot") if isinstance(execute_node.get("output_snapshot"), dict) else {}
    calls = output.get("executed_tool_calls")
    if isinstance(calls, list):
        return [call for call in calls if isinstance(call, dict)]
    calls = execute_node.get("tool_calls")
    if isinstance(calls, list):
        return [call for call in calls if isinstance(call, dict)]
    return []


def _reply_messages(synth_node: dict[str, Any]) -> list[dict[str, Any]]:
    output = synth_node.get("output_snapshot") if isinstance(synth_node.get("output_snapshot"), dict) else {}
    messages = output.get("reply_messages")
    if isinstance(messages, list):
        return [message for message in messages if isinstance(message, dict)]
    return []


def _store_facts(store_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for output in store_outputs:
        recommended = output.get("recommended_store")
        if isinstance(recommended, dict):
            facts.append(recommended)
        stores = output.get("stores")
        if isinstance(stores, list):
            facts.extend(item for item in stores if isinstance(item, dict))
    return facts


def _message_type(message: dict[str, Any]) -> str:
    return str(message.get("type") or "").strip()


def _message_content_value(message: dict[str, Any], key: str) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get(key) or "").strip()
    return ""


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "").strip()
    return str(content or "").strip()


def _store_names_in_text(messages: list[dict[str, Any]], real_names: set[str]) -> list[str]:
    text = "\n".join(_message_text(message) for message in messages if _message_type(message) == "text")
    return [name for name in real_names if name and name in text]


def _fabricated_store_mentions(messages: list[dict[str, Any]], real_names: set[str]) -> list[str]:
    text = "\n".join(_message_text(message) for message in messages if _message_type(message) == "text")
    matches = set(match.group(0).strip() for match in STORE_NAME_PATTERN.finditer(text))
    fabricated: list[str] = []
    for name in matches:
        if name in real_names:
            continue
        if any(real_name and real_name in name for real_name in real_names):
            continue
        fabricated.append(name)
    return sorted(fabricated)


def _distance_claims(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    text = "\n".join(_message_text(message) for message in messages if _message_type(message) == "text")
    for match in DISTANCE_PATTERN.finditer(text):
        num = float(match.group("num"))
        unit = match.group("unit").lower()
        meters = int(num if unit == "米" else num * 1000)
        claims.append({"text": match.group(0), "meters": meters})
    return claims


def _wrapped_request_id(data: dict[str, Any]) -> str:
    run = data.get("run")
    if isinstance(run, dict):
        value = str(run.get("request_id") or "").strip()
        if value:
            return value
    raw_log = data.get("raw_log")
    if isinstance(raw_log, dict):
        return str(raw_log.get("request_id") or "").strip()
    return ""


def _distance_outputs_ok(outputs: list[dict[str, Any]]) -> bool:
    return any(str(output.get("status") or "").strip().lower() == "ok" for output in outputs)


def _distance_items(output: dict[str, Any]) -> list[dict[str, Any]]:
    items = output.get("distances")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
