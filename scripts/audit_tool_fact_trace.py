from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


GENERIC_STORE_TERMS = {
    "这家门店",
    "那家门店",
    "最近门店",
    "附近门店",
    "对应门店",
    "具体门店",
    "真实门店",
    "推荐门店",
    "门店",
}


def load_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "results", "logs", "entries", "records"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [data]
    return []


def audit_entry(entry: dict[str, Any]) -> dict[str, Any]:
    reply_text = reply_text_from_entry(entry)
    store_facts = collect_store_facts(entry)
    distance_ok = has_successful_distance_lookup(entry)
    executed_tools = collect_executed_tools(entry)
    issues: list[str] = []

    if requires_store_lookup(entry) and "store_lookup" not in executed_tools:
        issues.append("missing_executed_store_lookup")
    if requires_case_lookup(entry) and not has_case_tool_call(executed_tools):
        issues.append("missing_executed_case_studies")
    if requires_time_lookup(entry) and "available_time" not in executed_tools:
        issues.append("missing_executed_available_time")

    fabricated_names = fabricated_store_names(reply_text, store_facts)
    if fabricated_names:
        issues.append(f"fabricated_store_name:{','.join(fabricated_names)}")

    unbacked_urls = unbacked_map_urls(reply_text, store_facts)
    if unbacked_urls:
        issues.append(f"unbacked_map_url:{','.join(unbacked_urls)}")

    if has_distance_claim(reply_text) and not distance_ok:
        issues.append("unbacked_distance_claim")

    return {
        "id": entry.get("id") or entry.get("log_id") or entry.get("trace_id") or entry.get("request_id") or "",
        "content": entry.get("content") or entry.get("input") or nested_get(entry, ("input_snapshot", "content")) or "",
        "executed_tools": sorted(executed_tools),
        "store_facts": [
            {
                "id": fact.get("id") or fact.get("store_id") or "",
                "name": fact.get("name") or fact.get("store_name") or "",
                "address": fact.get("address") or "",
                "map_url": fact.get("map_url") or fact.get("navigation_url") or "",
            }
            for fact in store_facts
        ],
        "reply_text": reply_text,
        "issues": issues,
        "passed": not issues,
    }


def reply_text_from_entry(entry: dict[str, Any]) -> str:
    messages = (
        entry.get("reply_messages")
        or nested_get(entry, ("response", "reply_messages"))
        or nested_get(entry, ("output_snapshot", "reply_messages"))
        or nested_get(entry, ("final_output", "reply_messages"))
        or []
    )
    texts: list[str] = []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, dict):
                text = content.get("text") or content.get("url") or ""
            else:
                text = content or ""
            if text:
                texts.append(str(text))
    output = entry.get("reply_text") or entry.get("answer") or ""
    if output:
        texts.append(str(output))
    return "\n".join(texts)


def collect_store_facts(value: Any) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("stores", "store_facts"):
                items = node.get(key)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            add_fact(item)
            recommended = node.get("recommended_store")
            if isinstance(recommended, dict):
                add_fact(recommended)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    def add_fact(item: dict[str, Any]) -> None:
        name = str(item.get("name") or item.get("store_name") or "").strip()
        store_id = str(item.get("id") or item.get("store_id") or "").strip()
        if not (name or store_id):
            return
        if any(term in name for term in ("其他门店", "医美外协", "测试")):
            return
        facts.append(item)

    visit(value)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for fact in facts:
        key = (str(fact.get("id") or fact.get("store_id") or ""), str(fact.get("name") or fact.get("store_name") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def collect_executed_tools(value: Any) -> set[str]:
    tools: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            calls = node.get("executed_tool_calls")
            if isinstance(calls, list):
                for call in calls:
                    if isinstance(call, str):
                        tools.add(normalize_tool_name(call))
                    elif isinstance(call, dict):
                        tools.add(normalize_tool_name(call.get("tool") or call.get("name") or call.get("type") or ""))
                        if str(call.get("kb_name") or "") == "case_studies":
                            tools.add("case_studies")
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return {tool for tool in tools if tool}


def normalize_tool_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text == "kb_search(case_studies)" or "case_studies" in text:
        return "case_studies"
    return text


def fabricated_store_names(reply_text: str, store_facts: list[dict[str, Any]]) -> list[str]:
    fact_names = {str(fact.get("name") or fact.get("store_name") or "").strip() for fact in store_facts}
    fact_names = {name for name in fact_names if name}
    names = set(re.findall(r"([\u4e00-\u9fffA-Za-z0-9]{2,18}(?:门店|店))", reply_text or ""))
    fabricated: list[str] = []
    for name in sorted(names):
        if name in GENERIC_STORE_TERMS or name.endswith("到店"):
            continue
        if any(generic in name for generic in ("这家门店", "那家门店", "附近门店", "推荐门店")):
            continue
        if fact_names and any(name == fact or name in fact or fact in name for fact in fact_names):
            continue
        fabricated.append(name)
    return fabricated


def unbacked_map_urls(reply_text: str, store_facts: list[dict[str, Any]]) -> list[str]:
    urls = set(re.findall(r"https?://[^\s，。)）]+", reply_text or ""))
    if not urls:
        return []
    fact_urls = {
        str(fact.get("map_url") or fact.get("navigation_url") or fact.get("parking_link") or "").strip()
        for fact in store_facts
    }
    fact_urls = {url for url in fact_urls if url}
    return sorted(url for url in urls if fact_urls and url not in fact_urls)


def has_distance_claim(reply_text: str) -> bool:
    text = reply_text or ""
    return bool(re.search(r"(\d+(?:\.\d+)?\s*(?:公里|km|KM))", text)) or any(term in text for term in ("最近", "更近", "离您近", "距离近"))


def has_successful_distance_lookup(value: Any) -> bool:
    found = False

    def visit(node: Any) -> None:
        nonlocal found
        if found:
            return
        if isinstance(node, dict):
            distance = node.get("distance_lookup")
            if isinstance(distance, dict):
                status = str(distance.get("status") or "").lower()
                if status in {"ok", "success"} or distance.get("distance_facts"):
                    found = True
                    return
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def requires_store_lookup(entry: dict[str, Any]) -> bool:
    marker = marker_text(entry)
    question = str(entry.get("content") or nested_get(entry, ("input_snapshot", "content")) or "")
    return any(term in marker for term in ("SF6", "STORE", "S2_STORE_ADDRESS", "门店", "地址")) or any(
        term in question for term in ("门店", "地址", "导航", "机场", "附近", "我在", "哪家")
    )


def requires_case_lookup(entry: dict[str, Any]) -> bool:
    marker = marker_text(entry)
    question = str(entry.get("content") or nested_get(entry, ("input_snapshot", "content")) or "")
    return any(term in marker for term in ("CASE", "EFFECT", "案例", "效果图")) or any(
        term in question for term in ("效果图", "案例", "做完后", "客户做完", "对比图")
    )


def requires_time_lookup(entry: dict[str, Any]) -> bool:
    marker = marker_text(entry)
    question = str(entry.get("content") or nested_get(entry, ("input_snapshot", "content")) or "")
    return any(term in marker for term in ("SF9", "APPOINTMENT")) and any(
        term in question for term in ("今天", "明天", "周", "星期", "上午", "下午", "几点")
    )


def has_case_tool_call(tools: set[str]) -> bool:
    return "case_studies" in tools or "kb_search" in tools


def marker_text(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("policy_family_id", "exact_policy_id", "active_scene_id", "sop_stage", "intent", "subflow"):
        value = entry.get(key) or nested_get(entry, ("output_snapshot", key)) or nested_get(entry, ("meta", key))
        if value:
            parts.append(str(value))
    route = entry.get("planner_route") or nested_get(entry, ("output_snapshot", "planner_route")) or {}
    if isinstance(route, dict):
        parts.extend(str(route.get(key) or "") for key in ("intent", "subflow", "sop_stage", "scene"))
    return " ".join(parts)


def nested_get(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit tool execution and customer-visible fact claims.")
    parser.add_argument("path", type=Path, help="JSON report/log file to audit")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path")
    args = parser.parse_args()

    entries = load_entries(args.path)
    results = [audit_entry(entry) for entry in entries]
    summary = {
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "failed": sum(1 for item in results if not item["passed"]),
        "issues": {},
    }
    for item in results:
        for issue in item["issues"]:
            key = issue.split(":", 1)[0]
            summary["issues"][key] = int(summary["issues"].get(key, 0)) + 1
    payload = {"summary": summary, "results": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
