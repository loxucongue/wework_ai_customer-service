from __future__ import annotations

from pathlib import Path

from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.planner.brain_v2 import planner_v2_messages_for_model
from app.graph.planner.brain_v2_normalizer import build_planner_plan_v2
from app.policies.sales_flow import (
    configure_precision_qa_playbook_path,
    precision_qa_context_for_planner,
)


ROOT = Path(__file__).resolve().parents[1]


def test_precision_playbook_is_available_to_planner_and_reply() -> None:
    configure_precision_qa_playbook_path(None)
    context = precision_qa_context_for_planner()
    assert len(context["question_index"]) == 14
    assert any(item["id"] == "age_eligibility" for item in context["question_index"])
    assert any(item["id"] == "treatment_method" for item in context["question_index"])
    assert any(item["id"] == "aftercare_guidance" for item in context["question_index"])
    assert any(item["id"] == "companion_party_size" for item in context["question_index"])

    messages = planner_v2_messages_for_model(
        {
            "normalized_content": "是不是做一次就可以了",
            "conversation_history": [],
        }
    )
    serialized = "\n".join(str(item.get("content") or "") for item in messages)
    assert "precision_qa_playbook" in serialized
    assert "one_session_effect" in serialized

    plan = build_planner_plan_v2(
        {"normalized_content": "是不是做一次就可以了"},
        {
            "decision": "direct_reply",
            "precision_qa_decision": {
                "question_id": "one_session_effect",
                "confidence": "high",
                "answer_depth": "standard",
                "basis": ["客户关心单次改善预期"],
            },
            "reply_messages": [{"type": "text", "order": 1, "content": "我先给您说清楚。"}],
            "tool_calls": [],
        },
    )
    assert plan["precision_qa_decision"]["question_id"] == "one_session_effect"

    payload = reply_user_payload_for_model(
        {
            "normalized_content": "是不是做一次就可以了",
            "conversation_history": [],
            "precision_qa_decision": plan["precision_qa_decision"],
        }
    )
    selected = payload["precision_qa_playbook"]["selected_question"]
    assert selected["id"] == "one_session_effect"
    assert selected["must_answer"]
    assert selected["reply_examples"]
    assert selected["evidence_requirement"] == "case_image"
    serialized_selected = str(selected)
    assert "历史里出现过手部、脸部或多个部位" in serialized_selected
    assert "不要把收口改成部位选择" in serialized_selected
    assert "绝大多数客户都是一次就好" in serialized_selected
    assert "完成线上活动登记后" in serialized_selected
    assert "单次单部位" in serialized_selected


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


def test_sop_page_exposes_precision_reply_workbench() -> None:
    page = (ROOT / "projects/src/app/sop/page.tsx").read_text(encoding="utf-8")
    workbench = (ROOT / "projects/src/components/sop/sop-config-workbench.tsx").read_text(encoding="utf-8")
    route = (ROOT / "projects/src/app/api/precision-qa-playbook/route.ts").read_text(encoding="utf-8")
    assert "SopConfigWorkbench" in page
    assert "精准回复" in workbench
    assert "/admin/precision-qa-playbook" in route
