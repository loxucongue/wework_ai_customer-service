from __future__ import annotations

from typing import Any


def sales_talk_items(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    value = tool_results.get("sales_talk_qa") or {}
    if isinstance(value, dict):
        items = value.get("items") or value.get("outputList") or []
        if not items and (value.get("content") or value.get("output")):
            return [value]
        return items if isinstance(items, list) else []
    if isinstance(value, list):
        return value
    return []


def first_sales_talk_slice(tool_results: dict[str, Any]) -> dict[str, str]:
    items = sales_talk_items(tool_results)
    if not items:
        return {}
    content = str(items[0].get("content") or items[0].get("output") or "").strip()
    if not content:
        return {}
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        text = str(line or "").strip().lstrip("#").strip()
        if not text or "：" not in text:
            continue
        key, value = text.split("：", 1)
        parsed[key.strip()] = value.strip()
    return {
        "scene_id": parsed.get("场景ID", ""),
        "scene_type": parsed.get("场景类型", ""),
        "customer_intent": parsed.get("客户真实意图", ""),
        "target": parsed.get("承接目标", ""),
        "sample_reply": parsed.get("可参考话术", ""),
        "next_step": parsed.get("下一步建议", ""),
        "forbidden": parsed.get("禁用表达", ""),
    }
