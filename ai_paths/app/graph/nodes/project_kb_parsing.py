from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.kb_slice_parsing import extract_label_block


def project_slices_from_tool_results(tool_results: dict[str, Any]) -> list[dict[str, str]]:
    project_qa = tool_results.get("project_qa") or {}
    if isinstance(project_qa, dict):
        items = project_qa.get("items") or project_qa.get("outputList") or []
        if not items and (project_qa.get("content") or project_qa.get("output")):
            items = [project_qa]
    elif isinstance(project_qa, list):
        items = project_qa
    else:
        items = []
    parsed: list[dict[str, str]] = []
    for item in items[:3]:
        if isinstance(item, dict):
            content = str(item.get("content") or item.get("output") or "")
        elif isinstance(item, str):
            content = item
        else:
            continue
        if not content:
            continue
        title_match = re.search(r"(?:##\s*)?(切片\d+\s*\|[^\n\r]+)", content)
        parsed_item = {
            "title": title_match.group(1).strip() if title_match else "",
            "scene_type": extract_label_block(content, "场景类型")[:80],
            "replacement_name": extract_label_block(content, "替换词名称")[:80],
            "direction": (
                extract_label_block(content, "可考虑方向")
                or extract_label_block(content, "项目定位")
                or extract_label_block(content, "核心逻辑")
            )[:220],
            "reply_point": extract_label_block(content, "回复要点")[:220],
            "say": extract_label_block(content, "可说话术")[:220],
            "follow_up": extract_label_block(content, "下一步追问")[:120],
        }
        parsed_item = {key: _clean_project_slice_text(value) for key, value in parsed_item.items()}
        if any(parsed_item.values()):
            parsed.append(parsed_item)
    return parsed


def _clean_project_slice_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value)
    value = value.replace("类项目类方向", "类项目方向")
    value = value.replace("类方向类方向", "类方向")
    parts = re.split(r"([、，,；;])", value)
    cleaned: list[str] = []
    seen_phrases: set[str] = set()
    pending_separator = ""
    for part in parts:
        if part in {"、", "，", ",", "；", ";"}:
            pending_separator = part
            continue
        phrase = part.strip()
        if not phrase:
            continue
        normalized = re.sub(r"\s+", "", phrase)
        if normalized in seen_phrases:
            continue
        if cleaned and pending_separator:
            cleaned.append(pending_separator)
        cleaned.append(phrase)
        seen_phrases.add(normalized)
        pending_separator = ""
    return "".join(cleaned) if cleaned else value
