from __future__ import annotations

import re
from typing import Any

from app.graph.nodes.common import clean_model_text
from app.graph.nodes.kb_slice_parsing import extract_label_block


def project_slices_from_fact_envelope(fact_envelope: dict[str, Any]) -> list[dict[str, str]]:
    structured = fact_envelope.get("structured_facts") or {}
    if not isinstance(structured, dict):
        return []
    knowledge_facts = structured.get("knowledge_facts") or []
    items: list[dict[str, Any]] = []
    for item in knowledge_facts if isinstance(knowledge_facts, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("source") or "").strip() != "project_qa":
            continue
        items.append(
            {
                "title": item.get("title") or "",
                "content": item.get("content") or "",
            }
        )
    return _parse_project_items(items)


def _parse_project_items(items: list[Any]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for item in items[:5]:
        if isinstance(item, dict):
            content = clean_model_text(str(item.get("content") or item.get("output") or ""))
            title = clean_model_text(str(item.get("title") or ""))
        elif isinstance(item, str):
            content = clean_model_text(item)
            title = ""
        else:
            continue
        if not content:
            continue
        title_match = re.search(r"(?:##\s*)?(切片\d+\s*\|[^\n\r]+)", content)
        parsed_item = {
            "title": _clean_project_slice_text(title or (title_match.group(1).strip() if title_match else "")),
            "scene_type": _clean_project_slice_text(extract_label_block(content, "场景类型")[:80]),
            "replacement_name": _clean_project_slice_text(extract_label_block(content, "替换名称")[:80]),
            "direction": _clean_project_slice_text(
                (
                    extract_label_block(content, "可考虑方向")
                    or extract_label_block(content, "项目定位")
                    or extract_label_block(content, "核心逻辑")
                )[:220]
            ),
            "reply_point": _clean_project_slice_text(extract_label_block(content, "回复要点")[:220]),
            "say": _clean_project_slice_text(extract_label_block(content, "可说话术")[:220]),
            "follow_up": _clean_project_slice_text(extract_label_block(content, "下一步追问")[:120]),
        }
        if any(parsed_item.values()):
            parsed.append(parsed_item)
    return parsed


def _clean_project_slice_text(text: str) -> str:
    value = clean_model_text(str(text or "")).strip()
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value)
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
