from __future__ import annotations

from pathlib import Path

from app.graph.nodes.reply_context import reply_user_payload_for_model
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
    assert len(index) == 17
    reserved = [item for item in index if item.get("priority") == "reserved"]
    configured = [item for item in index if item.get("priority") != "reserved"]
    assert {item["scene_id"] for item in reserved} == {
        "effect_definition_trust",
        "one_session_effect",
    }
    assert all(set(item) == {"scene_id", "applicable_scene"} for item in configured)
    assert len({item["scene_id"] for item in index}) == 17
    assert all("YYHF" not in str(item) and "http" not in str(item) for item in index)
    assert any("暂时不交预约金" in item["applicable_scene"] for item in index)


def test_reply_receives_candidates_for_the_selected_scene() -> None:
    scene = next(
        item
        for item in precision_qa_index_for_gate()
        if appointment_blocker_reference_for_reply(item["scene_id"]).get("candidates")
    )
    payload = reply_user_payload_for_model({
        "normalized_content": "???????",
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
        for candidate in reference.get("candidates") or []:
            assert all(not message.get("source_missing") for message in candidate.get("reference_messages", []))
            assert all(item.get("content") for item in candidate.get("unavailable_media", []))






def test_sop_page_exposes_appointment_blocker_workbench() -> None:
    page = (ROOT / "projects/src/app/sop/page.tsx").read_text(encoding="utf-8")
    workbench = (ROOT / "projects/src/components/sop/precision-qa-playbook-workbench.tsx").read_text(encoding="utf-8")
    route = (ROOT / "projects/src/app/api/precision-qa-playbook/route.ts").read_text(encoding="utf-8")
    assert "SopConfigWorkbench" in page
    assert "预约卡点话术库" in workbench
    assert "/admin/precision-qa-playbook" in route


def test_visit_intent_deposit_objection_playbook_uses_unified_refund_wording() -> None:
    configure_precision_qa_playbook_path(None)
    scene = next(
        item
        for item in precision_qa_index_for_gate()
        if "暂时不交预约金" in item["applicable_scene"]
    )
    reference = appointment_blocker_reference_for_reply(scene["scene_id"])
    candidate = next(item for item in reference["candidates"] if item["content_id"] == "YYHF-0027")
    serialized = str(candidate)
    assert candidate["blocker_type"] == "没有时间/预约金顾虑"
    assert "想到店再付" in reference["applicable_scene"]
    assert "10元到店抵扣；未做或不满意可退，实际按付款记录核对" in serialized
    assert "随时退还" not in serialized
