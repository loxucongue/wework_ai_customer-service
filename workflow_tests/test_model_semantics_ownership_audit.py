from __future__ import annotations

from pathlib import Path

from ai_paths.scripts.audit_model_semantics_ownership import audit_model_semantics_ownership
from app.services.reply_chain_external_gate_evidence import model_semantics_ownership_report_blockers
from app.services.tool_plan_preview import tool_plan_preview_from_planner_output


ROOT = Path(__file__).resolve().parents[1]


def test_model_semantics_ownership_audit_produces_valid_external_report() -> None:
    report = audit_model_semantics_ownership(repo_root=ROOT, head_ref="HEAD")

    assert report["schema_version"] == "reply_chain_model_semantics_ownership_audit_v1"
    assert report["semantic_ownership_passed"] is True
    assert model_semantics_ownership_report_blockers(report) == []
    assert "customer_visible_text" in report["tool_planner_must_not_own"]
    assert "sales_psychology" in report["tool_planner_must_not_own"]
    assert "closing_move" in report["tool_planner_must_not_own"]
    assert "final_customer_visible_messages" in report["reply_owns"]
    assert report["join_final_customer_message_owner"] == "reply"
    assert report["join_generates_customer_visible_text"] is False
    assert report["join_decides_sales_psychology"] is False
    assert report["safety"]["does_not_call_models"] is True
    assert report["safety"]["does_not_call_external_tools"] is True


def test_tool_plan_preview_detects_sales_semantic_residue_before_audit_can_pass() -> None:
    preview = tool_plan_preview_from_planner_output(
        {
            "planner_tool_calls": [{"name": "customer_store_lookup", "query": "sim"}],
            "payment_decision": {"action": "send_now"},
            "planner_reply_messages": [{"type": "text", "content": "customer visible"}],
        }
    )

    migration = preview["migration_audit"]
    assert migration["tool_planner_only_ready"] is False
    assert migration["legacy_residue_count"] == 2
    assert "payment_decision" in migration["business_semantic_fields_present"]
    assert "planner_reply_messages" in migration["customer_visible_fields_present"]
