from __future__ import annotations

from pathlib import Path

from ai_paths.scripts.audit_reply_chain_payload_isolation import (
    SHADOW_ONLY_FIELDS,
    audit_reply_chain_payload_isolation,
)


ROOT = Path(__file__).resolve().parents[1]


def test_payload_isolation_audit_checks_active_model_payloads_without_leaks() -> None:
    report = audit_reply_chain_payload_isolation(repo_root=ROOT, head_ref="HEAD")

    assert report["schema_version"] == "reply_chain_payload_isolation_audit_v1"
    assert report["shadow_only_fields"] == list(SHADOW_ONLY_FIELDS)
    assert report["payload_isolation_passed"] is True
    assert report["active_model_payloads_checked"] is True
    assert set(report["payloads_checked"]) == {
        "planner",
        "reply",
        "sop_chat_gate_selector",
        "sop_chat_gate_messages",
    }
    assert all(not fields for fields in report["leaked_fields_by_payload"].values())
    assert report["safety"]["does_not_call_models"] is True
