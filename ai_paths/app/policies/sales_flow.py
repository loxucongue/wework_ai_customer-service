from __future__ import annotations

import json
import hashlib
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
    scenes = precision_qa_index_for_gate()
    selected = precision_qa_for_id(question_id)
    selected_key = "selected_scene" if str(question_id or "").startswith("scene_") else "selected_question"
    return _drop_empty(
        {
            "purpose": "预约卡点适用场景索引；Planner 只使用场景，不读取参考话术。",
            "scene_index": scenes if include_answer_details_in_index else [],
            selected_key: selected,
        }
    )


def precision_qa_for_id(question_id: str) -> dict[str, Any]:
    target = str(question_id or "").strip()
    if not target:
        return {}
    grouped = _appointment_blocker_groups()
    if target in grouped:
        return {
            "id": target,
            "scene_id": target,
            "applicable_scene": grouped[target]["applicable_scene"],
        }
    if target in _HARD_PRECISION_QUESTION_IDS:
        return {"id": target, "hard_rule": True}
    return {}


def appointment_blocker_reference_for_reply(scene_id: str) -> dict[str, Any]:
    target = str(scene_id or "").strip()
    group = _appointment_blocker_groups().get(target)
    if not group:
        return {}
    candidates: list[dict[str, Any]] = []
    for item in group["items"]:
        sendable_messages = [
            message
            for message in item.get("reply_messages") or []
            if isinstance(message, dict) and not message.get("source_missing")
        ]
        missing_media = [
            {
                "type": str(message.get("type") or ""),
                "content": str(message.get("content") or ""),
            }
            for message in item.get("reply_messages") or []
            if isinstance(message, dict) and message.get("source_missing")
        ]
        candidates.append(
            _drop_empty(
                {
                    "content_id": item.get("content_id"),
                    "blocker_type": item.get("blocker_type"),
                    "reference_messages": sendable_messages,
                    "unavailable_media": missing_media,
                }
            )
        )
    return {
        "scene_id": target,
        "applicable_scene": group["applicable_scene"],
        "usage": "仅作语义与表达参考，必须依据当前事实改写，禁止照抄冲突价格、绝对承诺和性别称谓。",
        "candidates": candidates,
    }


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
    return [
        {"scene_id": scene_id, "applicable_scene": group["applicable_scene"]}
        for scene_id, group in _appointment_blocker_groups().items()
    ]


def _appointment_blocker_groups() -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in load_precision_qa_playbook().get("items") or []:
        if not isinstance(item, dict):
            continue
        scene = " ".join(str(item.get("applicable_scene") or "").split())
        if not scene:
            continue
        scene_id = f"scene_{hashlib.sha1(scene.encode('utf-8')).hexdigest()[:12]}"
        group = groups.setdefault(scene_id, {"applicable_scene": scene, "items": []})
        group["items"].append(item)
    return groups


_HARD_PRECISION_QUESTION_IDS = {
    "one_session_effect",
    "price_transparency",
    "rebound_and_safety",
    "effect_authenticity",
    "project_scope",
    "can_treat_spots",
    "body_area_and_price",
    "companion_party_size",
    "unsupported_online_projects",
    "aftercare_guidance",
    "maintenance_and_reappearance",
    "treatment_sensation_and_recovery",
    "treatment_method",
    "age_eligibility",
}


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
