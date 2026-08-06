from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.simulation.adapters import (
    SimulationCozeClient,
    SimulationModelClient,
    SimulationOutreachClient,
    SimulationVoiceTranscriptionClient,
    SimulationWorld,
)
from app.simulation.isolation import SimulationIsolationError, assert_simulation_identity, assert_simulation_isolated
from app.simulation.runner import (
    REQUIRED_SIMULATION_CATEGORIES,
    _aggregate,
    _configured_sop_media_urls,
    _semantic_scores,
    load_suite,
    render_markdown,
)
from app.simulation.runtime import _hard_check, _provider_incidents


REPO_ROOT = Path(__file__).resolve().parents[1]


def _isolation_audit(passed: bool = True) -> dict:
    return {
        "schema_version": "offline_simulation_isolation_audit_v1",
        "passed": passed,
        "run_dir_under_tmp_simulation": passed,
        "paths_within_run_dir": passed,
        "real_connector_credentials_present": False,
        "connector_urls_simulation_only": passed,
        "adapters_simulation_only": passed,
        "adapter_types": ["SimulationOutreachClient"],
        "identity_simulation_scoped": passed,
        "guarded_path_labels": ["database", "logs", "memory", "store_snapshot"],
    }


class FullChainSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        (REPO_ROOT / ".tmp_runtime").mkdir(exist_ok=True)

    def test_configured_sop_media_urls_are_unique_structured_assets(self) -> None:
        configured_urls = _configured_sop_media_urls()

        self.assertTrue(configured_urls)
        self.assertTrue(all(url.startswith(("http://", "https://")) for url in configured_urls))
        self.assertEqual(len(configured_urls), len(set(configured_urls)))

    def test_semantic_scores_accepts_nested_and_top_level_shapes(self) -> None:
        nested = {"scores": {"current_question": 4}}
        top_level = {"current_question": 5, "history_continuity": 4}

        self.assertEqual(_semantic_scores(nested), nested["scores"])
        self.assertEqual(_semantic_scores(top_level)["current_question"], 5)
        self.assertEqual(_semantic_scores(top_level)["history_continuity"], 4)

    def test_v1_fixture_expands_to_at_least_one_hundred_scenarios(self) -> None:
        scenarios = load_suite(REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json")

        self.assertGreaterEqual(len(scenarios), 100)
        self.assertEqual(len({item["id"] for item in scenarios}), len(scenarios))
        self.assertTrue(all(item.get("timeline") for item in scenarios))
        self.assertGreaterEqual(sum(len(item["timeline"]) >= 2 for item in scenarios), 100)
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=scenarios,
            results=[
                {
                    "scenario_id": item["id"],
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                }
                for item in scenarios
            ],
            baseline={},
        )
        self.assertEqual(report["coverage"]["schema_version"], "offline_simulation_coverage_audit_v1")
        self.assertEqual(report["coverage"]["missing_required_categories"], [])
        self.assertEqual(report["coverage"]["missing_critical_required_categories"], [])
        self.assertTrue(report["summary"]["acceptance"]["scenario_coverage_complete"])

    def test_aggregate_blocks_acceptance_when_required_simulation_category_is_missing(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "only_store", "category": "门店V2", "critical": True}],
            results=[
                {
                    "scenario_id": "only_store",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                }
            ],
            baseline={},
        )

        self.assertIn("精准问答", report["coverage"]["missing_required_categories"])
        self.assertFalse(report["summary"]["acceptance"]["scenario_coverage_complete"])
        markdown = render_markdown(report)
        self.assertIn("## 场景覆盖", markdown)
        self.assertIn("缺失必测分类", markdown)

    def test_aggregate_blocks_acceptance_when_required_category_has_no_critical_scenario(self) -> None:
        scenarios = [
            {"id": f"regular_{index}", "category": category, "critical": False, "timeline": [{"kind": "customer_message", "content": "你好"}]}
            for index, category in enumerate(REQUIRED_SIMULATION_CATEGORIES, start=1)
        ]
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=scenarios,
            results=[
                {
                    "scenario_id": item["id"],
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                }
                for item in scenarios
            ],
            baseline={},
        )

        self.assertEqual(report["coverage"]["missing_required_categories"], [])
        self.assertIn("门店V2", report["coverage"]["missing_critical_required_categories"])
        self.assertFalse(report["summary"]["acceptance"]["scenario_coverage_complete"])
        markdown = render_markdown(report)
        self.assertIn("缺失关键场景分类", markdown)

    def test_aggregate_emits_behavior_switch_gate_fields(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[
                {"id": "critical_ok", "category": "sim", "critical": True},
                {"id": "regular_fail", "category": "sim", "critical": False},
            ],
            results=[
                {
                    "scenario_id": "critical_ok",
                    "hard_pass": True,
                    "duration_ms": 10,
                    "semantic_review": {"available": True, "pass": True},
                },
                {
                    "scenario_id": "regular_fail",
                    "hard_pass": False,
                    "hard_errors": ["scenario.missing_reply"],
                    "duration_ms": 20,
                    "semantic_review": {"available": True, "pass": False},
                },
            ],
            baseline={},
        )

        self.assertEqual(report["schema_version"], "offline_reply_chain_simulation_report_v1")
        self.assertEqual(report["hard_error_count"], 1)
        self.assertEqual(report["semantic_pass_rate"], 0.5)
        self.assertEqual(report["failed_critical_scenarios"], [])
        self.assertEqual(report["review_artifacts"]["schema_version"], "offline_simulation_review_artifacts_v1")
        self.assertEqual(report["review_artifacts"]["result_count"], 2)
        self.assertFalse(report["summary"]["acceptance"]["hard_errors_zero"])
        self.assertFalse(report["summary"]["acceptance"]["semantic_at_least_90"])
        self.assertTrue(report["summary"]["acceptance"]["infrastructure_failures_zero"])
        self.assertEqual(
            report["safety"],
            {
                "production_customer_messages_sent": False,
                "production_writes_allowed": False,
                "virtual_outbox_only": True,
                "production_write_count": 0,
                "virtual_outbox_message_count": 0,
                "simulated_write_count": 0,
            },
        )

    def test_aggregate_reports_simulation_isolation_safety(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "safe", "category": "sim"}],
            results=[
                {
                    "scenario_id": "safe",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                    "outbox": [{"transport": "simulation_outbox"}],
                    "simulated_platform_writes": [{"transport": "simulation_only"}],
                    "isolation_audit": _isolation_audit(),
                }
            ],
            baseline={},
        )

        self.assertFalse(report["safety"]["production_customer_messages_sent"])
        self.assertFalse(report["safety"]["production_writes_allowed"])
        self.assertTrue(report["safety"]["virtual_outbox_only"])
        self.assertEqual(report["safety"]["production_write_count"], 0)
        self.assertEqual(report["safety"]["virtual_outbox_message_count"], 1)
        self.assertEqual(report["safety"]["simulated_write_count"], 1)
        self.assertTrue(report["isolation_audit"]["passed"])
        self.assertEqual(report["isolation_audit"]["result_count"], 1)
        self.assertEqual(report["isolation_audit"]["missing_result_count"], 0)
        self.assertEqual(report["isolation_audit"]["failed_result_count"], 0)

    def test_aggregate_marks_missing_or_failed_isolation_audit(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "safe", "category": "sim"}, {"id": "unsafe", "category": "sim"}],
            results=[
                {
                    "scenario_id": "safe",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                    "isolation_audit": _isolation_audit(False),
                },
                {
                    "scenario_id": "unsafe",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                },
            ],
            baseline={},
        )

        self.assertFalse(report["isolation_audit"]["passed"])
        self.assertEqual(report["isolation_audit"]["result_count"], 1)
        self.assertEqual(report["isolation_audit"]["missing_result_count"], 1)
        self.assertEqual(report["isolation_audit"]["failed_result_count"], 1)
        self.assertFalse(report["summary"]["acceptance"]["isolation_audit_passed"])

    def test_aggregate_marks_non_simulation_transport_as_unsafe(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "unsafe", "category": "sim"}],
            results=[
                {
                    "scenario_id": "unsafe",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                    "outbox": [{"transport": "real_platform"}],
                    "simulated_platform_writes": [{"transport": "real_write"}],
                }
            ],
            baseline={},
        )

        self.assertTrue(report["safety"]["production_customer_messages_sent"])
        self.assertTrue(report["safety"]["production_writes_allowed"])
        self.assertFalse(report["safety"]["virtual_outbox_only"])
        self.assertEqual(report["safety"]["production_write_count"], 1)

    def test_aggregate_marks_critical_semantic_failure_for_release_gate(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "critical_fail", "category": "sim", "critical": True}],
            results=[
                {
                    "scenario_id": "critical_fail",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": False},
                }
            ],
            baseline={},
        )

        self.assertEqual(report["hard_error_count"], 0)
        self.assertEqual(report["semantic_pass_rate"], 0.0)
        self.assertEqual(report["failed_critical_scenarios"], ["critical_fail"])
        self.assertFalse(report["summary"]["acceptance"]["critical_all_pass"])
        self.assertTrue(report["summary"]["acceptance"]["infrastructure_failures_zero"])

    def test_aggregate_exposes_infrastructure_failure_acceptance_gate(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "provider_timeout", "category": "model_failure", "critical": True}],
            results=[
                {
                    "scenario_id": "provider_timeout",
                    "hard_pass": False,
                    "hard_errors": [],
                    "infrastructure_errors": ["TimeoutError: model timeout"],
                    "duration_ms": 30000,
                }
            ],
            baseline={},
        )

        self.assertEqual(report["summary"]["infrastructure_failures"], 1)
        self.assertFalse(report["summary"]["acceptance"]["infrastructure_failures_zero"])
        self.assertFalse(report["summary"]["acceptance"]["hard_errors_zero"])

    def test_review_artifacts_summarize_traces_tools_and_outbox_for_human_review(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "artifact_case", "category": "sim", "critical": False}],
            results=[
                {
                    "scenario_id": "artifact_case",
                    "attempt": 1,
                    "run_dir": "C:/tmp/sim/artifact_case",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                    "steps": [
                        {
                            "request_id": "sim_request_1",
                            "sync_reply_messages": [{"type": "text", "content": "亲，可以的"}],
                            "new_outbox": [{"reply_messages": [{"type": "image"}]}],
                            "new_simulated_writes": [{"transport": "simulation_only"}],
                            "run": {
                                "node_traces": [
                                    {"node_name": "sop_chat_gate"},
                                    {"node_name": "planner"},
                                    {"node_name": "reply"},
                                ]
                            },
                        },
                        {"event_id": "sim_event_1", "new_outbox": []},
                    ],
                    "tool_calls": [{"name": "customer_store_lookup"}],
                }
            ],
            baseline={},
        )

        artifacts = report["review_artifacts"]
        self.assertEqual(artifacts["request_count"], 1)
        self.assertEqual(artifacts["event_count"], 1)
        self.assertEqual(artifacts["tool_call_count"], 1)
        self.assertEqual(artifacts["outbox_batch_count"], 1)
        self.assertEqual(artifacts["simulated_write_count"], 1)
        row = artifacts["results"][0]
        self.assertEqual(row["request_ids"], ["sim_request_1"])
        self.assertEqual(row["event_ids"], ["sim_event_1"])
        self.assertEqual(row["node_trace_names"], ["sop_chat_gate", "planner", "reply"])
        self.assertEqual(row["tool_call_names"], ["customer_store_lookup"])
        markdown = render_markdown(report)
        self.assertIn("## 审查证据", markdown)
        self.assertIn("sim_request_1", markdown)
        self.assertIn("customer_store_lookup", markdown)

    def test_effect_review_exposes_customer_input_reply_and_review_reason(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "low_effect", "category": "精准问答", "critical": False}],
            results=[
                {
                    "scenario_id": "low_effect",
                    "category": "精准问答",
                    "attempt": 1,
                    "hard_pass": True,
                    "semantic_review": {
                        "available": True,
                        "pass": False,
                        "scores": {
                            "current_question": 3,
                            "history_continuity": 4,
                            "mainline_progression": 2,
                            "conversion_naturalness": 4,
                            "human_tone": 4,
                            "fact_safety": 5,
                        },
                        "reasons": "只答疑，没有回到主线。",
                    },
                    "steps": [
                        {
                            "request_id": "sim_request_effect",
                            "kind": "customer_message",
                            "input": {"content": "做一次就能干净吗"},
                            "sync_reply_messages": [
                                {"type": "text", "content": "这个要看您斑点情况。"}
                            ],
                        }
                    ],
                }
            ],
            baseline={},
        )

        effect = report["effect_review"]
        self.assertEqual(effect["schema_version"], "offline_simulation_effect_review_v1")
        self.assertEqual(effect["issue_count"], 1)
        self.assertEqual(effect["low_score_count"], 1)
        row = effect["items"][0]
        self.assertEqual(row["scenario_id"], "low_effect")
        self.assertIn("semantic_low_score", row["issue_types"])
        self.assertIn("做一次就能干净吗", row["customer_input_excerpt"])
        self.assertIn("这个要看您斑点情况", row["assistant_reply_excerpt"])
        self.assertIn("只答疑", row["review_reasons"])
        markdown = render_markdown(report)
        self.assertIn("## 效果审查样本", markdown)
        self.assertIn("做一次就能干净吗", markdown)
        self.assertIn("这个要看您斑点情况", markdown)

    def test_identity_must_be_simulation_scoped(self) -> None:
        with self.assertRaises(SimulationIsolationError):
            assert_simulation_identity(
                {
                    "customer_id": "22016906",
                    "external_userid": "sim_external",
                    "corp_id": "sim_corp",
                    "wechat": "sim_wechat",
                }
            )

    def test_isolation_rejects_business_connector_credentials(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / ".tmp_runtime") as temp_dir:
            run_dir = Path(temp_dir) / "simulation" / "run"
            run_dir.mkdir(parents=True)
            settings = Settings(
                _env_file=None,
                AI_PATHS_DB_PATH=run_dir / "state.db",
                memory_dir=run_dir / "memory",
                log_dir=run_dir / "logs",
                store_snapshot_path=run_dir / "store.json",
                platform_agent_token="real-token",
                outreach_send_agent_token="",
                outreach_system_token="",
                coze_oauth_client_id="",
            )
            adapter = type("Adapter", (), {"simulation_adapter": True})()

            with self.assertRaises(SimulationIsolationError):
                assert_simulation_isolated(
                    settings=settings,
                    run_dir=run_dir,
                    adapters=[adapter],
                    identity={
                        "customer_id": "sim_customer",
                        "external_userid": "sim_external",
                        "corp_id": "sim_corp",
                        "wechat": "sim_wechat",
                    },
                )

    def test_isolation_rejects_production_url_and_real_adapter(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / ".tmp_runtime") as temp_dir:
            run_dir = Path(temp_dir) / "simulation" / "run"
            run_dir.mkdir(parents=True)
            common = {
                "_env_file": None,
                "AI_PATHS_DB_PATH": run_dir / "state.db",
                "memory_dir": run_dir / "memory",
                "log_dir": run_dir / "logs",
                "store_snapshot_path": run_dir / "store.json",
                "platform_agent_token": "",
                "outreach_send_agent_token": "",
                "outreach_system_token": "",
                "coze_oauth_client_id": "",
            }
            identity = {
                "customer_id": "sim_customer",
                "external_userid": "sim_external",
                "corp_id": "sim_corp",
                "wechat": "sim_wechat",
            }
            adapter = type("Adapter", (), {"simulation_adapter": True})()
            production_url_settings = Settings(
                **common,
                platform_agent_base_url="https://platform.example.invalid",
                outreach_send_base_url="simulation://outbox",
                outreach_system_base_url="simulation://system",
            )
            with self.assertRaises(SimulationIsolationError):
                assert_simulation_isolated(
                    settings=production_url_settings,
                    run_dir=run_dir,
                    adapters=[adapter],
                    identity=identity,
                )

            isolated_settings = Settings(
                **common,
                platform_agent_base_url="simulation://platform",
                outreach_send_base_url="simulation://outbox",
                outreach_system_base_url="simulation://system",
            )
            with self.assertRaises(SimulationIsolationError):
                assert_simulation_isolated(
                    settings=isolated_settings,
                    run_dir=run_dir,
                    adapters=[object()],
                    identity=identity,
                )

    def test_scenario_contract_checks_required_and_forbidden_messages(self) -> None:
        errors = _hard_check(
            scenario={
                "expected": {
                    "must_reply": True,
                    "required_types": ["image", "payment_collection"],
                    "forbidden_phrases": ["翻一下前面的入口"],
                    "payment_amount": 10,
                }
            },
            step_results=[
                {
                    "sync_reply_messages": [
                        {"type": "text", "order": 1, "content": "您翻一下前面的入口。"},
                        {"type": "payment_collection", "order": 2, "content": {"amount": 20}},
                    ]
                }
            ],
            outbox=[],
            stores=[],
            external_writes=[],
        )

        self.assertIn("scenario.missing_required_type:image", errors)
        self.assertIn("scenario.missing_payment_amount:10", errors)
        self.assertIn("scenario.forbidden_phrase:翻一下前面的入口", errors)

    def test_provider_incidents_only_read_actual_error_evidence(self) -> None:
        steps = [
            {
                "index": 1,
                "run": {"config": {"total_timeout_seconds": 45}},
                "response_meta": {"tool_calls": [{"error": ""}]},
            },
            {
                "index": 2,
                "response_meta": {
                    "tool_calls": [
                        {"usage": {"fallback_errors": ["gpt-5.4: JSONDecodeError: malformed json"]}}
                    ]
                },
            },
        ]

        self.assertEqual(
            _provider_incidents(steps),
            ["step[2].provider_retry_or_network_incident"],
        )

    def test_outbox_captures_without_external_transport(self) -> None:
        identity = {
            "customer_id": "sim_customer",
            "external_userid": "sim_external",
            "corp_id": "sim_corp",
            "wechat": "sim_wechat",
            "user_id": 1,
        }
        world = SimulationWorld(scenario_id="sim", identity=identity)
        client = SimulationOutreachClient(world)

        result = asyncio.run(
            client.send_reply_messages(
                request_id="sim_request",
                request_context=identity,
                fallback_customer_id=identity["customer_id"],
                fallback_corp_id=identity["corp_id"],
                fallback_user_id=identity["user_id"],
                fallback_wechat=identity["wechat"],
                fallback_external_userid=identity["external_userid"],
                reply_messages=[{"type": "text", "order": 1, "content": {"text": "仿真回复"}}],
            )
        )

        self.assertEqual(result["status"], "sent")
        self.assertTrue(result["simulation"])
        self.assertEqual(len(world.outbox), 1)
        self.assertEqual(world.outbox[0]["transport"], "simulation_outbox")
        self.assertEqual(world.external_writes, [])

    def test_model_fault_fixture_is_consumed_before_real_provider_call(self) -> None:
        identity = {
            "customer_id": "sim_customer",
            "external_userid": "sim_external",
            "corp_id": "sim_corp",
            "wechat": "sim_wechat",
            "user_id": 1,
        }
        world = SimulationWorld(
            scenario_id="sim",
            identity=identity,
            faults={"model:planner": [{"mode": "malformed_json"}]},
        )
        client = SimulationModelClient(
            Settings(_env_file=None, model_relay_api_key="simulation-model-key"),
            world,
        )

        raw = asyncio.run(
            client._post_chat(
                {"model": "sim-model"},
                tier="planner",
                fallback_index=0,
                errors=[],
            )
        )

        self.assertEqual(raw["choices"][0]["message"]["content"], "{malformed json")
        self.assertEqual(world.faults["model:planner"], [])

    def test_voice_transcription_uses_only_local_fixture(self) -> None:
        audio_url = "https://example.invalid/simulation/customer-voice.mp3"
        world = SimulationWorld(
            scenario_id="sim",
            identity={
                "customer_id": "sim_customer",
                "external_userid": "sim_external",
                "corp_id": "sim_corp",
                "wechat": "sim_wechat",
                "user_id": 1,
            },
            voice_transcripts={audio_url: "东莞高埗有门店吗"},
        )
        client = SimulationVoiceTranscriptionClient(world)

        result = asyncio.run(client.transcribe(audio_url, uid="sim_customer"))

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], "东莞高埗有门店吗")
        self.assertEqual(world.tool_calls[0]["name"], "voice_transcription")

    def test_geocode_fixture_accepts_unique_location_with_province_prefix(self) -> None:
        world = SimulationWorld(
            scenario_id="sim",
            identity={
                "customer_id": "sim_customer",
                "external_userid": "sim_external",
                "corp_id": "sim_corp",
                "wechat": "sim_wechat",
                "user_id": 1,
            },
            geocodes={
                "甲良镇": {
                    "province": "贵州省",
                    "city": "黔南布依族苗族自治州",
                    "district": "荔波县",
                    "location": "107.7,25.4",
                }
            },
        )
        client = SimulationCozeClient(
            world,
            geocode_workflow_id="sim_geocode",
            distance_workflow_id="sim_distance",
        )

        result = asyncio.run(client.run_workflow("sim_geocode", {"address": "贵州甲良镇"}))

        self.assertEqual(result["data"]["district"], "荔波县")


if __name__ == "__main__":
    unittest.main()
