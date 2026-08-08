from __future__ import annotations

import json
from pathlib import Path

from app.services.outreach_assets import (
    appointment_blocker_materials,
    build_appointment_blocker_asset_catalog,
    build_appointment_blocker_scene_index,
)
from app.services.outreach_first_day_prompts import (
    FIRST_DAY_CONTRACT_VERIFIER_PROMPT,
    FIRST_DAY_PLAN_WRITER_PROMPT,
    FIRST_DAY_SCENE_ANALYST_PROMPT,
)
from app.services.outreach_service import _first_day_message_policy_error, _first_day_writer_payload


ROOT = Path(__file__).resolve().parents[1]


def _playbook() -> dict:
    return json.loads((ROOT / "config" / "precision_qa_playbook.json").read_text(encoding="utf-8"))


def test_appointment_blocker_playbook_is_the_only_configured_outreach_material_source() -> None:
    playbook = _playbook()

    assert len(playbook["items"]) == 104
    assert len(build_appointment_blocker_scene_index(playbook)) == 15
    assert len(build_appointment_blocker_asset_catalog(playbook)) == 64
    assert len(appointment_blocker_materials(playbook)) == 104

    searchable_roots = (ROOT / "ai_paths", ROOT / "projects", ROOT / "config")
    forbidden = (
        "outreach_asset_library_path",
        "OUTREACH_ASSET_LIBRARY_PATH",
        "/admin/outreach/assets",
        "/api/outreach/assets",
        'href: "/outreach/assets"',
        "scope=outreach",
        "ai-outreach/assets",
    )
    for base in searchable_roots:
        for path in base.rglob("*"):
            if ".next" in path.parts or "node_modules" in path.parts:
                continue
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".json"}:
                continue
            text = path.read_text(encoding="utf-8")
            assert not any(marker in text for marker in forbidden), path


def test_writer_receives_only_selected_appointment_blocker_entries() -> None:
    playbook = _playbook()
    materials = appointment_blocker_materials(playbook)
    selected = materials[0]
    analysis = {
        "writer_context_message_indexes": [0],
        "selected_source_ids": {"step1": [selected["source_id"]], "step2": []},
        "forbidden_repetitions": [],
        "required_assets": {
            "step1": {"strategy": "none", "asset_id": ""},
            "step2": {"strategy": "none", "asset_id": ""},
        },
        "payment_action": {"step": 0, "allowed": False},
    }
    payload = _first_day_writer_payload(
        {
            "recent_messages": [{"direction": "customer", "content": "有点远"}],
            "asset_catalog": [],
        },
        analysis,
        appointment_material_catalog=materials,
    )

    selected_materials = payload["writer_context"]["selected_materials"]
    assert [item["source_id"] for item in selected_materials] == [selected["source_id"]]
    assert len(selected_materials) == 1
    assert "https://" not in json.dumps(selected_materials, ensure_ascii=False)


def test_first_day_prompts_define_appointment_blocker_context_boundaries() -> None:
    assert "appointment_blocker_scene_index" in FIRST_DAY_SCENE_ANALYST_PROMPT
    assert "不包含客户可见话术正文" in FIRST_DAY_SCENE_ANALYST_PROMPT
    assert "selected_materials` 来自预约卡点话术库" in FIRST_DAY_PLAN_WRITER_PROMPT
    assert "禁止原样照抄整段话术" in FIRST_DAY_PLAN_WRITER_PROMPT
    assert "旧价格、绝对效果" in FIRST_DAY_PLAN_WRITER_PROMPT
    assert "缺失媒体" in FIRST_DAY_CONTRACT_VERIFIER_PROMPT
    assert "实际按付款记录核对" in FIRST_DAY_PLAN_WRITER_PROMPT
    assert "实际按付款记录核对" in FIRST_DAY_CONTRACT_VERIFIER_PROMPT
    assert "直接交付来自 `selected_sop_packs` 或 `selected_materials` 的具体价值" in FIRST_DAY_PLAN_WRITER_PROMPT
    assert "开放式尾巴" in FIRST_DAY_CONTRACT_VERIFIER_PROMPT


def test_first_day_policy_rejects_store_execution_implication() -> None:
    error, evidence = _first_day_message_policy_error(
        ["亲，武汉这边我先帮您把到店路径接上，您平时方便去哪个区呀？"],
        step_index=1,
        plan={},
        context={},
    )

    assert error == "first_day_unsupported_store_action"
    assert evidence == "把到店路径接上"


def test_first_day_policy_rejects_open_ended_process_tail_for_silent_customer() -> None:
    error, evidence = _first_day_message_policy_error(
        ["如果您想，我也可以顺着给您说下这次活动的安排。"],
        step_index=2,
        plan={},
        context={},
    )

    assert error == "first_day_process_tail"
    assert evidence == "如果您想"
