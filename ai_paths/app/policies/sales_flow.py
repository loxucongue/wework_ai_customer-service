from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_precision_qa_playbook_path: Path | None = None
_precision_qa_playbook_cache: tuple[str, int, dict[str, Any]] | None = None
_sales_mainline_cache: tuple[str, int, dict[str, Any]] | None = None


def configure_precision_qa_playbook_path(path: Path | str | None) -> None:
    global _precision_qa_playbook_path, _precision_qa_playbook_cache
    _precision_qa_playbook_path = Path(path) if path else None
    _precision_qa_playbook_cache = None


def load_precision_qa_playbook() -> dict[str, Any]:
    global _precision_qa_playbook_cache
    path = _json_policy_path("precision_qa_playbook.json")
    try:
        modified_at = path.stat().st_mtime_ns
    except OSError:
        modified_at = 0
    signature = str(path.resolve())
    if _precision_qa_playbook_cache:
        cached_path, cached_modified_at, cached_payload = _precision_qa_playbook_cache
        if cached_path == signature and cached_modified_at == modified_at:
            return cached_payload
    payload = _load_json_policy_from_path(path)
    _precision_qa_playbook_cache = (signature, modified_at, payload)
    return payload


def precision_qa_context_for_planner(question_id: str = "") -> dict[str, Any]:
    playbook = load_precision_qa_playbook()
    questions: list[dict[str, Any]] = []
    for item in playbook.get("questions") or []:
        if not isinstance(item, dict):
            continue
        questions.append(
            _drop_empty(
                {
                    "id": item.get("id"),
                    "intent_definition": item.get("intent_definition"),
                    "customer_psychology": item.get("customer_psychology"),
                    "question_role": item.get("question_role"),
                    "must_answer": item.get("must_answer") or [],
                    "must_not_substitute": item.get("must_not_substitute") or [],
                    "evidence_requirement": item.get("evidence_requirement"),
                    "resume_mainline_stage": item.get("resume_mainline_stage"),
                }
            )
        )
    return _drop_empty(
        {
            "purpose": playbook.get("purpose"),
            "global_answer_policy": playbook.get("global_answer_policy") or {},
            "question_index": questions,
            "selected_question": precision_qa_for_id(question_id),
        }
    )


def precision_qa_for_id(question_id: str) -> dict[str, Any]:
    target = str(question_id or "").strip()
    if not target:
        return {}
    for item in load_precision_qa_playbook().get("questions") or []:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == target:
            return item
    return {}


def load_sales_mainline() -> dict[str, Any]:
    global _sales_mainline_cache
    path = _json_policy_path("sales_mainline.json")
    try:
        modified_at = path.stat().st_mtime_ns
    except OSError:
        modified_at = 0
    signature = str(path.resolve())
    if _sales_mainline_cache:
        cached_path, cached_modified_at, cached_payload = _sales_mainline_cache
        if cached_path == signature and cached_modified_at == modified_at:
            return cached_payload
    payload = _load_json_policy_from_path(path)
    _sales_mainline_cache = (signature, modified_at, payload)
    return payload


def sales_mainline_for_model() -> dict[str, Any]:
    mainline = load_sales_mainline()
    return _drop_empty(
        {
            "purpose": mainline.get("purpose"),
            "priority": mainline.get("priority") or [],
            "stages": mainline.get("stages") or [],
            "conditional_support_stages": mainline.get("conditional_support_stages") or [],
            "resume_policy": mainline.get("resume_policy") or {},
        }
    )


def precision_qa_index_for_gate() -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for item in load_precision_qa_playbook().get("questions") or []:
        if not isinstance(item, dict):
            continue
        questions.append(
            _drop_empty(
                {
                    "id": item.get("id"),
                    "intent_definition": item.get("intent_definition"),
                    "customer_psychology": item.get("customer_psychology"),
                    "question_role": item.get("question_role"),
                    "must_not_substitute": item.get("must_not_substitute") or [],
                    "evidence_requirement": item.get("evidence_requirement"),
                    "resume_mainline_stage": item.get("resume_mainline_stage"),
                }
            )
        )
    return questions


def mainline_stage_for_pack(pack_id: str) -> str:
    target = str(pack_id or "").strip()
    if not target:
        return ""
    for stage in load_sales_mainline().get("stages") or []:
        if not isinstance(stage, dict):
            continue
        pack_ids = {str(item or "").strip() for item in stage.get("candidate_pack_ids") or []}
        if target in pack_ids:
            return str(stage.get("id") or "").strip()
    for stage in load_sales_mainline().get("conditional_support_stages") or []:
        if not isinstance(stage, dict):
            continue
        pack_ids = {str(item or "").strip() for item in stage.get("candidate_pack_ids") or []}
        if target in pack_ids:
            return str(stage.get("id") or "").strip()
    return ""


def _json_policy_path(filename: str) -> Path:
    path = Path(__file__).with_name(filename)
    if filename == "precision_qa_playbook.json" and _precision_qa_playbook_path:
        if _precision_qa_playbook_path.exists():
            path = _precision_qa_playbook_path
    return path


def _load_json_policy_from_path(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}
