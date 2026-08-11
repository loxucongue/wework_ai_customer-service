from __future__ import annotations

import json
from pathlib import Path

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import planner_v2_messages_for_model
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.policies.sales_flow import (
    appointment_blocker_reference_for_reply,
    configure_precision_qa_playbook_path,
    precision_qa_context_for_planner,
    precision_qa_index_for_gate,
)


ROOT = Path(__file__).resolve().parents[1]


def test_gate_only_receives_deduplicated_applicable_scenes() -> None:
    configure_precision_qa_playbook_path(None)
    index = precision_qa_index_for_gate()
    assert len(index) == 15
    assert all(set(item) == {"scene_id", "applicable_scene"} for item in index)
    assert len({item["scene_id"] for item in index}) == 15
    assert all("YYHF" not in str(item) and "http" not in str(item) for item in index)
    assert any("暂时不交预约金" in item["applicable_scene"] for item in index)


def test_planner_only_receives_selected_scene_and_reply_receives_candidates() -> None:
    scene = precision_qa_index_for_gate()[0]
    messages = planner_v2_messages_for_model({
        "normalized_content": "这个门店太远了",
        "conversation_history": [],
        "sop_gate_decision": {"selected_scene_id": scene["scene_id"], "route": "ai_only"},
    })
    serialized = "\n".join(str(item.get("content") or "") for item in messages)
    assert scene["scene_id"] in serialized
    assert "YYHF-0001" not in serialized
    assert "reference_messages" not in serialized

    payload = reply_user_payload_for_model({
        "normalized_content": "这个门店太远了",
        "conversation_history": [],
        "sop_gate_decision": {"selected_scene_id": scene["scene_id"]},
    })
    reference = payload["appointment_blocker_reference"]
    assert reference["scene_id"] == scene["scene_id"]
    assert reference["candidates"]
    assert all(
        not message.get("source_missing")
        for candidate in reference["candidates"]
        for message in candidate.get("reference_messages", [])
    )


def test_missing_media_is_never_sendable_reference() -> None:
    for scene in precision_qa_index_for_gate():
        reference = appointment_blocker_reference_for_reply(scene["scene_id"])
        for candidate in reference["candidates"]:
            assert all(not message.get("source_missing") for message in candidate.get("reference_messages", []))
            assert all(item.get("content") for item in candidate.get("unavailable_media", []))


def test_hard_precision_rules_remain_accepted() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "是不是做一次就可以了"},
        {
            "decision": "direct_reply",
            "precision_qa_decision": {"question_id": "one_session_effect", "confidence": "high"},
            "reply_messages": [{"type": "text", "order": 1, "content": "我先给您说清楚。"}],
            "tool_calls": [],
        },
    )
    assert plan["precision_qa_decision"]["question_id"] == "one_session_effect"


def test_unknown_precision_question_id_is_not_accepted_as_fact() -> None:
    plan = build_planner_plan_v2(
        {"normalized_content": "普通问题"},
        {
            "decision": "direct_reply",
            "precision_qa_decision": {"question_id": "not_configured", "confidence": "high"},
            "reply_messages": [{"type": "text", "order": 1, "content": "我给您说明一下。"}],
            "tool_calls": [],
        },
    )
    assert plan["precision_qa_decision"]["question_id"] == ""
    assert plan["precision_qa_decision"]["confidence"] == "low"


def test_sop_page_exposes_appointment_blocker_workbench() -> None:
    page = (ROOT / "projects/src/app/sop/page.tsx").read_text(encoding="utf-8")
    workbench = (ROOT / "projects/src/components/sop/precision-qa-playbook-workbench.tsx").read_text(encoding="utf-8")
    route = (ROOT / "projects/src/app/api/precision-qa-playbook/route.ts").read_text(encoding="utf-8")
    assert "SopConfigWorkbench" in page
    assert "预约卡点话术库" in workbench
    assert "/admin/precision-qa-playbook" in route


def test_latest_deposit_timing_blocker_is_synced_without_changing_neighbor() -> None:
    paths = [
        ROOT / "ai_paths/app/policies/precision_qa_playbook.json",
        ROOT / "config/precision_qa_playbook.json",
    ]
    loaded = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert loaded[0] == loaded[1]

    contents = {
        item["content_id"]: item
        for item in loaded[0]["items"]
        if item.get("content_id") in {"YYHF-0026", "YYHF-0027"}
    }
    assert contents["YYHF-0026"]["blocker_type"] == "没有时间"
    assert contents["YYHF-0027"]["blocker_type"] == "没有时间/预约金顾虑"
    assert "暂时不交预约金" in contents["YYHF-0027"]["applicable_scene"]
    text = contents["YYHF-0027"]["reply_messages"][0]["content"]
    assert "10元锁住优惠名额" in text
    assert "未做或不满意可退" in text
