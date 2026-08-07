from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.policies.sales_flow import configure_precision_qa_playbook_path


_ALLOWED_MESSAGE_TYPES = {"text", "image", "image_reference", "video_reference", "media_reference"}


class PrecisionQaPlaybookService:
    def __init__(self, settings: Settings) -> None:
        self.path = settings.precision_qa_playbook_path
        self.default_path = Path(__file__).parents[1] / "policies" / "precision_qa_playbook.json"
        configure_precision_qa_playbook_path(self.path)

    def load(self) -> dict[str, Any]:
        path = self.path if self.path.exists() else self.default_path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            normalized = self._normalize(payload)
            normalized["audit"] = _audit_playbook(normalized)
            normalized["storage"] = {
                "source": "configured" if path == self.path else "bundled_default",
                "path": str(path),
            }
            return normalized
        except (OSError, json.JSONDecodeError, ValueError):
            payload = json.loads(self.default_path.read_text(encoding="utf-8"))
            normalized = self._normalize(payload)
            normalized["audit"] = _audit_playbook(normalized)
            normalized["storage"] = {"source": "bundled_default", "path": str(self.default_path)}
            return normalized

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(payload)
        audit = _audit_playbook(normalized)
        errors = [issue for issue in audit["issues"] if issue.get("severity") == "error"]
        if errors:
            summary = "; ".join(str(issue.get("message") or issue.get("code") or "") for issue in errors[:5])
            raise ValueError(f"precision QA playbook audit failed: {summary}")
        normalized["updated_at"] = datetime.now(UTC).isoformat()
        self._write_json(self.path, normalized)
        configure_precision_qa_playbook_path(self.path)
        result = deepcopy(normalized)
        result["audit"] = _audit_playbook(result)
        result["storage"] = {"source": "configured", "path": str(self.path)}
        return result

    def _normalize(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("items must be a list")

        result = deepcopy(payload)
        result.pop("audit", None)
        result.pop("storage", None)
        result["version"] = max(4, _positive_int(payload.get("version"), 4))
        result["updated_at"] = _text(payload.get("updated_at"))
        result.pop("purpose", None)
        result.pop("global_answer_policy", None)
        result.pop("questions", None)

        items = [self._normalize_item(item, index) for index, item in enumerate(raw_items)]
        seen_ids: set[str] = set()
        for item in items:
            content_id = item["content_id"]
            if content_id in seen_ids:
                raise ValueError(f"duplicated content id: {content_id}")
            seen_ids.add(content_id)
        result["items"] = items
        return result

    def _normalize_item(self, item: Any, index: int) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError(f"item #{index + 1} must be an object")
        content_id = _identifier(item.get("content_id"))
        blocker_type = _text(item.get("blocker_type"))
        applicable_scene = _text(item.get("applicable_scene"))
        if not content_id or not blocker_type or not applicable_scene:
            raise ValueError(f"item #{index + 1} requires blocker_type, applicable_scene and content_id")
        raw_messages = item.get("reply_messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError(f"item {content_id} reply_messages must be a non-empty list")
        messages: list[dict[str, Any]] = []
        for message_index, message in enumerate(raw_messages):
            if not isinstance(message, dict):
                raise ValueError(f"item {content_id} message #{message_index + 1} must be an object")
            message_type = _text(message.get("type"))
            content = _text(message.get("content"))
            if message_type not in _ALLOWED_MESSAGE_TYPES or not content:
                raise ValueError(f"item {content_id} message #{message_index + 1} is invalid")
            normalized = {"type": message_type, "content": content}
            if message.get("source_missing"):
                normalized["source_missing"] = True
            messages.append(normalized)
        return {
            "blocker_type": blocker_type,
            "applicable_scene": applicable_scene,
            "content_id": content_id,
            "reply_messages": messages,
        }

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        tmp_path.replace(path)


def _audit_playbook(playbook: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    for item in playbook.get("items") or []:
        if not isinstance(item, dict):
            continue
        content_id = _text(item.get("content_id"))
        for message in item.get("reply_messages") or []:
            if isinstance(message, dict) and message.get("source_missing"):
                issues.append(_issue("warning", "source_missing", content_id, "媒体源文件缺失，不会进入发送候选。"))
    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    return {
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "error_count": errors,
        "warning_count": warnings,
        "issues": issues,
    }


def _issue(severity: str, code: str, content_id: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "content_id": content_id, "message": message}


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _identifier(value: Any) -> str:
    text = _text(value)
    return "".join(char for char in text if char.isascii() and (char.isalnum() or char in {"_", "-"}))


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
