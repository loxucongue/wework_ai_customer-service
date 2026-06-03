from __future__ import annotations

import json
import re
from typing import Any


def extract_text(raw: dict[str, Any]) -> str:
    choices = raw.get("choices") or []
    if not choices:
        raise RuntimeError("Model response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {"output": parsed}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {"output": parsed}
