from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.policies.sales_flow import configure_precision_qa_playbook_path


_POLICY_FIELDS = (
    "first_answer",
    "confidence",
    "mainline_resume",
    "variation",
    "facts",
)
_QUESTION_TEXT_FIELDS = (
    "intent_definition",
    "customer_psychology",
    "question_role",
    "first_ask_strategy",
    "repeated_ask_strategy",
    "evidence_requirement",
    "resume_mainline_stage",
)
_QUESTION_LIST_FIELDS = (
    "must_answer",
    "must_not_substitute",
    "allowed_confidence",
    "forbidden_claims",
)


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
        raw_questions = payload.get("questions")
        if not isinstance(raw_questions, list):
            raise ValueError("questions must be a list")

        result = deepcopy(payload)
        result.pop("audit", None)
        result.pop("storage", None)
        result["version"] = _positive_int(payload.get("version"), 1)
        result["purpose"] = _text(payload.get("purpose"))
        result["updated_at"] = _text(payload.get("updated_at"))

        raw_policy = payload.get("global_answer_policy")
        policy = deepcopy(raw_policy) if isinstance(raw_policy, dict) else {}
        for field in _POLICY_FIELDS:
            policy[field] = _text(policy.get(field))
        result["global_answer_policy"] = policy

        questions = [self._normalize_question(item, index) for index, item in enumerate(raw_questions)]
        seen_ids: set[str] = set()
        for question in questions:
            question_id = question["id"]
            if question_id in seen_ids:
                raise ValueError(f"duplicated precision QA id: {question_id}")
            seen_ids.add(question_id)
        result["questions"] = questions
        return result

    def _normalize_question(self, item: Any, index: int) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError(f"question #{index + 1} must be an object")
        result = deepcopy(item)
        question_id = _identifier(item.get("id"))
        if not question_id:
            raise ValueError(f"question #{index + 1} id is required")
        result["id"] = question_id
        for field in _QUESTION_TEXT_FIELDS:
            result[field] = _text(item.get(field))
        for field in _QUESTION_LIST_FIELDS:
            result[field] = _text_list(item.get(field))

        raw_examples = item.get("reply_examples")
        if raw_examples is None:
            raw_examples = []
        if not isinstance(raw_examples, list):
            raise ValueError(f"question {question_id} reply_examples must be a list")
        examples: list[dict[str, Any]] = []
        for example_index, example in enumerate(raw_examples):
            if not isinstance(example, dict):
                raise ValueError(f"question {question_id} example #{example_index + 1} must be an object")
            normalized_example = deepcopy(example)
            normalized_example["context"] = _text(example.get("context"))
            normalized_example["reply"] = _text_list(example.get("reply"))
            examples.append(normalized_example)
        result["reply_examples"] = examples
        return result

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        tmp_path.replace(path)


def _audit_playbook(playbook: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not _text(playbook.get("purpose")):
        issues.append(_issue("warning", "purpose_empty", "", "精准回复库未填写总体目的。"))
    policy = playbook.get("global_answer_policy") if isinstance(playbook.get("global_answer_policy"), dict) else {}
    for field in _POLICY_FIELDS:
        if not _text(policy.get(field)):
            issues.append(_issue("warning", "global_policy_empty", field, f"全局策略 {field} 为空。"))
    for question in playbook.get("questions") or []:
        if not isinstance(question, dict):
            continue
        question_id = _text(question.get("id"))
        if not _text(question.get("intent_definition")):
            issues.append(_issue("error", "intent_definition_empty", question_id, "意图定义不能为空。"))
        if not _text_list(question.get("must_answer")):
            issues.append(_issue("error", "must_answer_empty", question_id, "必须回答至少需要一条。"))
        if not _text(question.get("resume_mainline_stage")):
            issues.append(_issue("warning", "resume_mainline_stage_empty", question_id, "未配置回答后的主线恢复阶段。"))
        if not question.get("reply_examples"):
            issues.append(_issue("warning", "reply_examples_empty", question_id, "没有优秀回复示例。"))
    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    return {
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "error_count": errors,
        "warning_count": warnings,
        "issues": issues,
    }


def _issue(severity: str, code: str, question_id: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "question_id": question_id, "message": message}


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
