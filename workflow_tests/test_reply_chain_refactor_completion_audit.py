from __future__ import annotations

from pathlib import Path

from ai_paths.scripts.audit_reply_chain_refactor_completion import audit_reply_chain_refactor_completion


ROOT = Path(__file__).resolve().parents[1]


def test_reply_chain_refactor_completion_audit_marks_shadow_architecture_complete() -> None:
    report = audit_reply_chain_refactor_completion(repo_root=ROOT, head_ref="HEAD")

    assert report["schema_version"] == "reply_chain_refactor_completion_audit_v1"
    assert report["branch"] == "codex/reply-chain-refactor"
    assert report["completion_passed"] is True
    assert report["blockers"] == []
    assert report["ownership_summary"]["semantic_ownership_passed"] is True
    assert report["ownership_summary"]["normalizer_boundary_passed"] is True
    assert report["ownership_summary"]["tool_planner_only_ready"] is True
    assert report["ownership_summary"]["join_final_customer_message_owner"] == "reply"
    assert report["shadow_bundle_summary"]["structural_blockers"] == []
    assert report["shadow_bundle_summary"]["release_gate_blockers"]
    assert report["behavior_switch_summary"]["requested"] is False
    assert report["behavior_switch_summary"]["can_enable_behavior_switch"] is False
    assert "full_offline_simulation_report" in report["remaining_release_gates"]
    assert "three_model_matrix_report" in report["remaining_release_gates"]
    assert report["model_choice"]["selected_candidate"] == "gpt-5.4"


def test_reply_chain_refactor_completion_audit_all_components_are_valid() -> None:
    report = audit_reply_chain_refactor_completion(repo_root=ROOT, head_ref="HEAD")

    for component_name, status in report["architecture_status"].items():
        assert status["valid"] is True, component_name

    safety = report["safety"]
    assert safety["does_not_change_runtime_behavior"] is True
    assert safety["does_not_send_customer_messages"] is True
    assert safety["does_not_write_database"] is True
    assert safety["does_not_call_models"] is True
    assert safety["does_not_call_external_tools"] is True
    assert safety["does_not_deploy"] is True
    assert safety["does_not_merge_main"] is True
