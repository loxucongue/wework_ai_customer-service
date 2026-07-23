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


def precision_qa_context_for_planner(
    question_id: str = "",
    *,
    include_answer_details_in_index: bool = True,
) -> dict[str, Any]:
    playbook = load_precision_qa_playbook()
    questions: list[dict[str, Any]] = []
    for item in playbook.get("questions") or []:
        if not isinstance(item, dict):
            continue
        question = {
            "id": item.get("id"),
            "intent_definition": item.get("intent_definition"),
            "customer_psychology": item.get("customer_psychology"),
            "question_role": item.get("question_role"),
            "must_not_substitute": item.get("must_not_substitute") or [],
            "first_ask_strategy": item.get("first_ask_strategy"),
            "allowed_confidence": item.get("allowed_confidence") or [],
            "evidence_requirement": item.get("evidence_requirement"),
            "resume_mainline_stage": item.get("resume_mainline_stage"),
        }
        if include_answer_details_in_index:
            question["must_answer"] = item.get("must_answer") or []
        questions.append(_drop_empty(question))
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
                    "intent_definition": _compact_text(item.get("intent_definition"), max_chars=120),
                    "customer_psychology": _compact_text(item.get("customer_psychology"), max_chars=90),
                    "question_role": item.get("question_role"),
                    "must_not_substitute": [
                        _compact_text(value, max_chars=80)
                        for value in (item.get("must_not_substitute") or [])[:2]
                        if _compact_text(value, max_chars=80)
                    ],
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


EVENT_MAINLINE_STAGE_BY_PACK_ID = {
    "s10_new_customer_opening": "opening_and_positioning",
    "s10_need_and_case": "need_and_case",
    "s10_activity_intro": "activity_and_price",
    "event_s10_store_prompt_5min": "location_capture",
    "event_s10_effect_warmup_30min": "need_and_case",
    "event_s10_deposit_push_70min": "deposit_decision",
    "event_s10_unpaid_effect_1h": "deposit_decision",
    "event_s10_unpaid_video_2h": "deposit_decision",
    "event_s10_day1_final_close": "deposit_decision",
}


EVENT_MAINLINE_STAGE_BY_CATEGORY = {
    "opening": "opening_and_positioning",
    "s10_new_customer_opening": "opening_and_positioning",
    "store_prompt": "location_capture",
    "store_address": "location_capture",
    "effect_case": "need_and_case",
    "s10_need_and_case": "need_and_case",
    "activity_intro": "activity_and_price",
    "s10_activity_intro": "activity_and_price",
    "price_quote": "activity_and_price",
    "deposit_push": "deposit_decision",
    "payment_followup": "deposit_decision",
    "operation_video": "deposit_decision",
    "final_close": "deposit_decision",
}


EVENT_MAINLINE_STAGE_BY_STAGE_TAG = {
    "first_add_ai_notice": "opening_and_positioning",
    "store_prompt": "location_capture",
    "effect_warmup": "need_and_case",
    "price_quote": "activity_and_price",
    "deposit_push": "deposit_decision",
    "payment_followup": "deposit_decision",
    "operation_video": "deposit_decision",
    "final_close": "deposit_decision",
}


def mainline_stage_for_event_pack(pack: dict[str, Any]) -> str:
    explicit = str(pack.get("mainline_stage") or "").strip()
    if explicit:
        return explicit
    return mainline_stage_for_event_values(
        pack_id=pack.get("id"),
        category=pack.get("sop_category"),
        stage_tag=pack.get("stage_tag"),
    )


def mainline_stage_for_event_values(
    *,
    pack_id: Any = "",
    category: Any = "",
    stage_tag: Any = "",
) -> str:
    target = str(pack_id or "").strip()
    if target:
        mapped = mainline_stage_for_pack(target) or EVENT_MAINLINE_STAGE_BY_PACK_ID.get(target, "")
        if mapped:
            return mapped
    category_text = str(category or "").strip()
    if category_text:
        mapped = EVENT_MAINLINE_STAGE_BY_CATEGORY.get(category_text, "")
        if mapped:
            return mapped
    stage_tag_text = str(stage_tag or "").strip()
    if stage_tag_text:
        mapped = EVENT_MAINLINE_STAGE_BY_STAGE_TAG.get(stage_tag_text, "")
        if mapped:
            return mapped
    return ""


def mainline_stage_order(stage_id: str) -> int:
    target = str(stage_id or "").strip()
    if not target:
        return 9999
    for stage in load_sales_mainline().get("stages") or []:
        if isinstance(stage, dict) and str(stage.get("id") or "").strip() == target:
            try:
                return int(stage.get("order") or 9999)
            except (TypeError, ValueError):
                return 9999
    return 9999


def mainline_pack_sort_key(pack: dict[str, Any]) -> tuple[int, int, int, str]:
    stage_order = mainline_stage_order(mainline_stage_for_event_pack(pack))
    try:
        delay = int(pack.get("delay_minutes") or 0)
    except (TypeError, ValueError):
        delay = 0
    try:
        order = int(pack.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    return stage_order, order, delay, str(pack.get("id") or "")


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


def _compact_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"
