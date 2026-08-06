from __future__ import annotations

from pathlib import Path

from app.services.reply_chain_external_gate_evidence import (
    model_matrix_report_blockers,
    simulation_report_blockers,
)


ROOT = Path(__file__).resolve().parents[1]


def _simulation_ready() -> dict:
    return {
        "schema_version": "offline_reply_chain_simulation_report_v1",
        "hard_error_count": 0,
        "semantic_pass_rate": 0.93,
        "failed_critical_scenarios": [],
        "safety": {
            "production_customer_messages_sent": False,
            "production_writes_allowed": False,
            "virtual_outbox_only": True,
            "production_write_count": 0,
        },
    }


def _model_matrix_ready() -> dict:
    return {
        "schema_version": "reply_chain_refactor_model_matrix_v1",
        "profiles_requested": ["claude", "gemini", "openai"],
        "profiles": [
            {
                "status": "completed",
                "model_profile": {"name": "claude"},
                "profile_summary": {
                    "semantic_pass_rate": 0.91,
                    "p50_ms": 6200,
                    "p90_ms": 11000,
                    "accepted_by_release_thresholds": True,
                },
            },
            {
                "status": "completed",
                "model_profile": {"name": "gemini"},
                "profile_summary": {
                    "semantic_pass_rate": 0.9,
                    "p50_ms": 3900,
                    "p90_ms": 7600,
                    "accepted_by_release_thresholds": True,
                },
            },
            {
                "status": "completed",
                "model_profile": {"name": "openai"},
                "profile_summary": {
                    "semantic_pass_rate": 0.94,
                    "p50_ms": 4800,
                    "p90_ms": 8200,
                    "accepted_by_release_thresholds": True,
                },
            },
        ],
        "safety": {
            "api_keys_written_to_report": False,
            "production_customer_messages_sent": False,
            "production_writes_allowed": False,
        },
    }


def test_external_gate_evidence_accepts_complete_reports() -> None:
    assert simulation_report_blockers(_simulation_ready()) == []
    assert model_matrix_report_blockers(_model_matrix_ready()) == []


def test_external_gate_evidence_blocks_unsafe_or_incomplete_reports() -> None:
    simulation = _simulation_ready()
    simulation["semantic_pass_rate"] = 0.89
    simulation["safety"]["production_customer_messages_sent"] = True

    model_matrix = _model_matrix_ready()
    model_matrix["profiles"] = [
        item
        for item in model_matrix["profiles"]
        if item["model_profile"]["name"] != "gemini"
    ]

    assert "simulation_semantic_pass_rate_below_90:0.890" in simulation_report_blockers(simulation)
    assert "simulation_missing_no_customer_send_safety" in simulation_report_blockers(simulation)
    assert "model_matrix_profile_not_completed:gemini" in model_matrix_report_blockers(model_matrix)


def test_bundle_audit_does_not_import_final_behavior_switch_guard_private_helpers() -> None:
    source = (ROOT / "ai_paths/app/services/reply_chain_shadow_bundle_audit.py").read_text(encoding="utf-8")

    assert "from app.services.reply_chain_behavior_switch_guard import" not in source
    assert "_simulation_blockers" not in source
    assert "_model_matrix_blockers" not in source
