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
from app.simulation.runner import _configured_sop_media_urls, _semantic_scores, load_suite
from app.simulation.runtime import _hard_check, _provider_incidents


REPO_ROOT = Path(__file__).resolve().parents[1]


class FullChainSimulationTests(unittest.TestCase):
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
