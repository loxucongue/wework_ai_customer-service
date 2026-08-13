from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.config import Settings
from app.services.customer_scope import customer_scope_from_identity
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
    _attach_semantic_reviews,
    _configured_sop_media_urls,
    _load_resumable_checkpoint,
    _record_semantic_review_failure,
    _review_result,
    _semantic_review_fingerprint,
    _semantic_review_signature,
    _semantic_visible_payload,
    _semantic_scores,
    _simulation_runtime_fingerprint,
    load_suite,
    render_markdown,
    simulation_evaluation_scope,
    simulation_run_options,
)
from app.simulation.runtime import (
    _hard_check,
    _provider_incidents,
    _sales_contact_key,
    _unrecovered_infrastructure_errors,
)


def test_semantic_reviewer_reserves_critical_errors_for_release_blockers() -> None:
    import inspect

    source = inspect.getsource(_review_result)
    assert "Use critical_errors only for actual release-blocking semantic failures" in source
    assert "Tone that is slightly templated, conservative wording" in source
    assert "minor verbosity" in source
    assert "Both sync_reply_messages and simulated outbox reply_messages" in source
    assert "semantic_goal and expected as the authoritative evaluation contract" in source
    assert "Never attribute a message, image, store card, or payment card from a later turn" in source


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

    def test_seeded_memory_uses_the_same_sales_contact_key_as_production(self) -> None:
        identity = {
            "corp_id": "sim_corp",
            "wechat": "sim_wechat",
            "external_userid": "sim_external",
            "customer_id": "sim_customer",
        }

        self.assertEqual(
            _sales_contact_key(identity),
            customer_scope_from_identity(identity).sales_contact_key,
        )
        self.assertTrue(_sales_contact_key(identity).startswith("sales_contact:v2:"))

    def test_semantic_scores_accepts_nested_and_top_level_shapes(self) -> None:
        nested = {"scores": {"current_question": 4}}
        top_level = {"current_question": 5, "history_continuity": 4}

        self.assertEqual(_semantic_scores(nested), nested["scores"])
        self.assertEqual(_semantic_scores(top_level)["current_question"], 5)
        self.assertEqual(_semantic_scores(top_level)["history_continuity"], 4)

    def test_semantic_review_failure_is_infrastructure_failure(self) -> None:
        result = {"infrastructure_errors": []}

        _record_semantic_review_failure(result, RuntimeError("review model 401"))

        self.assertEqual(
            result["semantic_review"],
            {"available": False, "error": "RuntimeError: review model 401"},
        )
        self.assertEqual(result["infrastructure_errors"], ["semantic_review:RuntimeError: review model 401"])

    def test_runtime_fingerprint_changes_with_dirty_runtime_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_file = root / "ai_paths" / "app" / "runtime.py"
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text("VALUE = 1\n", encoding="utf-8")
            (root / "config").mkdir()
            fixture = root / "fixture.json"
            fixture.write_text('{"scenarios": []}', encoding="utf-8")
            settings = Settings()

            first = _simulation_runtime_fingerprint(repo_root=root, fixture=fixture, settings=settings)
            runtime_file.write_text("VALUE = 2\n", encoding="utf-8")
            second = _simulation_runtime_fingerprint(repo_root=root, fixture=fixture, settings=settings)

            self.assertNotEqual(first, second)

    def test_resume_checkpoint_requires_matching_fingerprint_and_clean_runtime(self) -> None:
        scenario = {"id": "resume_case"}
        runtime_fingerprint = "runtime-v1"
        review_fingerprint = _semantic_review_fingerprint(
            simulation_fingerprint=runtime_fingerprint,
            reviewer_model="gpt-5.4",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_dir = Path(temp_dir)
            checkpoint = checkpoint_dir / "resume_case-a1.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "scenario_id": "resume_case",
                        "attempt": 1,
                        "simulation_fingerprint": runtime_fingerprint,
                        "semantic_review_fingerprint": review_fingerprint,
                        "hard_pass": True,
                        "infrastructure_errors": [],
                        "semantic_review": {"available": True, "pass": True},
                    }
                ),
                encoding="utf-8",
            )

            resumed = _load_resumable_checkpoint(
                checkpoint_dir=checkpoint_dir,
                scenario=scenario,
                attempt=1,
                simulation_fingerprint=runtime_fingerprint,
                review_fingerprint=review_fingerprint,
            )
            self.assertIsNotNone(resumed)
            self.assertTrue(resumed["semantic_review"]["pass"])

            stale_review = _load_resumable_checkpoint(
                checkpoint_dir=checkpoint_dir,
                scenario=scenario,
                attempt=1,
                simulation_fingerprint=runtime_fingerprint,
                review_fingerprint="review-v2",
            )
            self.assertIsNotNone(stale_review)
            self.assertNotIn("semantic_review", stale_review)

            checkpoint.write_text(
                json.dumps(
                    {
                        "scenario_id": "resume_case",
                        "attempt": 1,
                        "simulation_fingerprint": runtime_fingerprint,
                        "hard_pass": False,
                        "hard_errors": ["scenario.missing_reply"],
                        "infrastructure_errors": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNotNone(
                _load_resumable_checkpoint(
                    checkpoint_dir=checkpoint_dir,
                    scenario=scenario,
                    attempt=1,
                    simulation_fingerprint=runtime_fingerprint,
                    review_fingerprint=review_fingerprint,
                )
            )
            self.assertIsNone(
                _load_resumable_checkpoint(
                    checkpoint_dir=checkpoint_dir,
                    scenario=scenario,
                    attempt=1,
                    simulation_fingerprint=runtime_fingerprint,
                    review_fingerprint=review_fingerprint,
                    retry_failed=True,
                )
            )

            checkpoint.write_text(
                json.dumps(
                    {
                        "scenario_id": "resume_case",
                        "attempt": 1,
                        "simulation_fingerprint": runtime_fingerprint,
                        "hard_pass": False,
                        "infrastructure_errors": ["provider timeout"],
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(
                _load_resumable_checkpoint(
                    checkpoint_dir=checkpoint_dir,
                    scenario=scenario,
                    attempt=1,
                    simulation_fingerprint=runtime_fingerprint,
                    review_fingerprint=review_fingerprint,
                )
            )

    def test_semantic_review_signature_ignores_attempt_and_transport_metadata(self) -> None:
        scenario = {"id": "same", "semantic_goal": "same goal", "expected": {"reply": True}}
        first = {
            "attempt": 1,
            "steps": [
                {
                    "kind": "customer_message",
                    "input": {"content": "好的"},
                    "sync_reply_messages": [{"type": "text", "content": "您脸上的斑大概有多久了？"}],
                    "new_outbox": [{"request_id": "volatile-a", "reply_messages": []}],
                }
            ],
        }
        second = deepcopy(first)
        second["attempt"] = 2
        second["steps"][0]["new_outbox"][0]["request_id"] = "volatile-b"

        self.assertEqual(
            _semantic_review_signature(scenario, first),
            _semantic_review_signature(scenario, second),
        )

    def test_semantic_visible_payload_preserves_turn_boundaries_and_expectations(self) -> None:
        result = {
            "steps": [
                {
                    "index": 1,
                    "kind": "customer_message",
                    "input": {"content": "这个活动多少钱"},
                    "expected": {"forbidden_types": ["payment_collection"]},
                    "sync_reply_messages": [{"type": "text", "content": "活动价是268元。"}],
                    "new_outbox": [],
                },
                {
                    "index": 2,
                    "kind": "customer_message",
                    "input": {"content": "那怎么报名"},
                    "expected": {"required_types": ["payment_collection"]},
                    "sync_reply_messages": [
                        {"type": "payment_collection", "content": {"amount": 10}}
                    ],
                    "new_outbox": [],
                },
            ]
        }

        visible = _semantic_visible_payload(result)

        self.assertEqual(visible[0]["turn_index"], 1)
        self.assertEqual(visible[0]["expected_for_this_turn"], {"forbidden_types": ["payment_collection"]})
        self.assertNotIn("payment_collection", [item["type"] for item in visible[0]["sync_reply_messages"]])
        self.assertEqual(visible[1]["turn_index"], 2)
        self.assertEqual(visible[1]["expected_for_this_turn"], {"required_types": ["payment_collection"]})
        self.assertIn("payment_collection", [item["type"] for item in visible[1]["sync_reply_messages"]])

    def test_identical_visible_results_share_one_semantic_review(self) -> None:
        class Reviewer:
            def __init__(self) -> None:
                self.calls = 0

            async def chat_json(self, *_args, **_kwargs):
                self.calls += 1
                return {
                    "scores": {key: 5 for key in (
                        "current_question",
                        "history_continuity",
                        "mainline_progression",
                        "conversion_naturalness",
                        "human_tone",
                        "fact_safety",
                    )},
                    "critical_errors": [],
                    "reasons": "ok",
                }

        scenario = {"id": "same", "semantic_goal": "same goal", "expected": {"reply": True}}
        result = {
            "hard_pass": True,
            "infrastructure_errors": [],
            "steps": [
                {
                    "kind": "customer_message",
                    "input": {"content": "好的"},
                    "sync_reply_messages": [{"type": "text", "content": "您脸上的斑大概有多久了？"}],
                    "new_outbox": [],
                }
            ],
        }
        results = [deepcopy(result), deepcopy(result)]
        reviewer = Reviewer()

        asyncio.run(
            _attach_semantic_reviews(
                reviewer=reviewer,
                jobs=[(scenario, 1), (scenario, 2)],
                results=results,
                concurrency=2,
            )
        )

        self.assertEqual(reviewer.calls, 1)
        self.assertEqual(results[0]["semantic_review"]["pass"], results[1]["semantic_review"]["pass"])
        self.assertEqual(results[0]["semantic_review"]["cache"]["sample_count"], 2)

    def test_failed_first_review_uses_three_vote_consensus(self) -> None:
        class Reviewer:
            def __init__(self) -> None:
                self.calls = 0

            async def chat_json(self, *_args, **_kwargs):
                self.calls += 1
                score = 3 if self.calls == 1 else 5
                return {
                    "scores": {key: score for key in (
                        "current_question",
                        "history_continuity",
                        "mainline_progression",
                        "conversion_naturalness",
                        "human_tone",
                        "fact_safety",
                    )},
                    "critical_errors": [] if score >= 4 else ["first vote rejected"],
                    "reasons": f"vote-{self.calls}",
                }

        scenario = {"id": "consensus", "semantic_goal": "goal", "expected": {"reply": True}}
        result = {
            "hard_pass": True,
            "infrastructure_errors": [],
            "steps": [
                {
                    "kind": "customer_message",
                    "input": {"content": "好的"},
                    "sync_reply_messages": [{"type": "text", "content": "收到"}],
                    "new_outbox": [],
                }
            ],
        }
        reviewer = Reviewer()

        asyncio.run(
            _attach_semantic_reviews(
                reviewer=reviewer,
                jobs=[(scenario, 1)],
                results=[result],
                concurrency=1,
            )
        )

        self.assertEqual(reviewer.calls, 3)
        self.assertTrue(result["semantic_review"]["pass"])
        self.assertEqual(result["semantic_review"]["consensus"]["pass_votes"], 2)

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

    def test_template_variant_can_override_its_followup_without_cross_region_history(self) -> None:
        scenarios = load_suite(REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json")
        province_variants = {
            item["id"]: [step.get("content") for step in item["timeline"]]
            for item in scenarios
            if str(item.get("id") or "").startswith("province_scope__")
        }

        self.assertEqual(
            province_variants,
            {
                "province_scope__v01": ["湖北省", "荆州市荆州区"],
                "province_scope__v02": ["我在广东", "广州市番禺区"],
                "province_scope__v03": ["四川这边有吗", "成都市双流区"],
                "province_scope__v04": ["贵州有店不", "贵阳市花溪区"],
            },
        )

    def test_v1_fixture_uses_current_payment_and_registration_contracts(self) -> None:
        fixture = REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        templates = {item["id"]: item for item in payload["templates"]}

        self.assertIn("普通文字声称已经转好仍不是权威已付", templates["manual_transfer"]["semantic_goal"])
        payment_followup_forbidden = templates["payment_after_activity"]["followup"]["expected"][
            "forbidden_phrases"
        ]
        self.assertNotIn("已到账", payment_followup_forbidden)
        self.assertNotIn("已经到账", payment_followup_forbidden)
        self.assertIn("支付已确认", payment_followup_forbidden)
        self.assertIn("不得重复索取", templates["paid_registration"]["semantic_goal"])
        self.assertIn("新的成交进展", templates["soft_refusal"]["semantic_goal"])
        self.assertEqual(
            templates["soft_refusal"]["expected"],
            {
                "must_reply": True,
                "required_types": ["payment_collection"],
                "payment_amount": 10,
            },
        )
        self.assertIn("权威预约金支付成功", templates["unknown_transfer_message"]["semantic_goal"])
        self.assertIn(
            "payment_collection",
            templates["unknown_transfer_message"]["expected"]["forbidden_types"],
        )
        self.assertEqual(
            templates["sop_duplicate_avoidance"]["expected"]["required_types"],
            ["payment_collection"],
        )
        self.assertIn("风险未解除前不操作", templates["health_risk"]["semantic_goal"])

    def test_aggregate_exposes_simulation_scope_and_run_options(self) -> None:
        scope = simulation_evaluation_scope(scenario_id="store_case", category="门店V2", max_cases=1)
        options = simulation_run_options(attempts=1, critical_attempts=1, concurrency=1, skip_review=True)
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "store_case", "category": "门店V2", "critical": True}],
            results=[
                {
                    "scenario_id": "store_case",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                }
            ],
            baseline={},
            evaluation_scope=scope,
            run_options=options,
        )

        self.assertEqual(
            report["evaluation_scope"],
            {
                "schema_version": "offline_simulation_scope_v1",
                "scenario_id": "store_case",
                "category": "门店V2",
                "max_cases": 1,
                "targeted_smoke": True,
                "full_release_gate_candidate": False,
            },
        )
        self.assertEqual(
            report["run_options"],
            {
                "schema_version": "offline_simulation_run_options_v1",
                "attempts": 1,
                "critical_attempts": 1,
                "concurrency": 1,
                "skip_review": True,
                "reviewer_model": "",
                "resume": False,
                "retry_failed": False,
            },
        )

    def test_aggregate_exposes_baseline_comparison_schema_and_regressions(self) -> None:
        scenarios = [
            {"id": "improved_case", "category": "门店V2", "critical": True},
            {"id": "regressed_case", "category": "门店V2", "critical": True},
            {"id": "unchanged_case", "category": "门店V2", "critical": True},
        ]
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=scenarios,
            results=[
                {
                    "scenario_id": "improved_case",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                },
                {
                    "scenario_id": "regressed_case",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": False},
                },
                {
                    "scenario_id": "unchanged_case",
                    "hard_pass": True,
                    "semantic_review": {"available": True, "pass": True},
                },
            ],
            baseline={
                "scenario_summary": {
                    "improved_case": {"semantic_passes": 0, "attempts": 1},
                    "regressed_case": {"semantic_passes": 1, "attempts": 1},
                    "unchanged_case": {"semantic_passes": 1, "attempts": 1},
                }
            },
        )

        self.assertEqual(
            report["baseline_comparison"]["schema_version"],
            "offline_simulation_baseline_comparison_v1",
        )
        self.assertTrue(report["baseline_comparison"]["available"])
        self.assertEqual(report["baseline_comparison"]["improved"], ["improved_case"])
        self.assertEqual(report["baseline_comparison"]["regressed"], ["regressed_case"])
        self.assertEqual(report["baseline_comparison"]["unchanged"], ["unchanged_case"])
        self.assertFalse(report["summary"]["acceptance"]["baseline_comparison_passed"])

    def test_aggregate_exposes_baseline_acceptance_when_available_and_not_regressed(self) -> None:
        scenarios = [{"id": "stable_case", "category": "门店V2", "critical": True}]
        result = {
            "scenario_id": "stable_case",
            "hard_pass": True,
            "semantic_review": {"available": True, "pass": True},
        }

        without_baseline = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=scenarios,
            results=[result],
            baseline={},
        )
        with_baseline = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=scenarios,
            results=[result],
            baseline={"scenario_summary": {"stable_case": {"semantic_passes": 1, "attempts": 1}}},
        )

        self.assertFalse(without_baseline["summary"]["acceptance"]["baseline_comparison_passed"])
        self.assertTrue(with_baseline["summary"]["acceptance"]["baseline_comparison_passed"])

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

    def test_aggregate_requires_semantic_review_for_every_attempt(self) -> None:
        report = _aggregate(
            fixture=REPO_ROOT / "workflow_tests" / "fixtures" / "full_chain_simulation_v1.json",
            scenarios=[{"id": "skip_review_case", "category": "sim", "critical": False}],
            results=[
                {
                    "scenario_id": "skip_review_case",
                    "hard_pass": True,
                    "duration_ms": 10,
                }
            ],
            baseline={},
        )

        self.assertEqual(report["summary"]["evaluable_attempts"], 0)
        self.assertFalse(report["summary"]["acceptance"]["semantic_review_complete"])
        self.assertFalse(report["summary"]["acceptance"]["semantic_at_least_90"])

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


    def test_render_markdown_keeps_human_review_headings_readable(self) -> None:
        markdown = render_markdown(
            {
                "scenario_count": 1,
                "attempt_count": 1,
                "git_commit": "abc123",
                "git_commit_set": ["abc123"],
                "fixture": "workflow_tests/fixtures/full_chain_simulation_v1.json",
                "evaluation_scope": {
                    "schema_version": "offline_simulation_scope_v1",
                    "scenario_id": "",
                    "category": "",
                    "max_cases": 0,
                    "targeted_smoke": False,
                    "full_release_gate_candidate": True,
                },
                "run_options": {
                    "schema_version": "offline_simulation_run_options_v1",
                    "attempts": 3,
                    "critical_attempts": 5,
                    "concurrency": 2,
                    "skip_review": False,
                    "reviewer_model": "gpt-5.4",
                },
                "summary": {
                    "hard_pass_rate": "100.0%",
                    "semantic_pass_rate": "100.0%",
                    "infrastructure_failures": 0,
                    "p50_ms": 10,
                    "p90_ms": 10,
                },
                "coverage": {
                    "missing_required_categories": [],
                    "missing_critical_required_categories": [],
                    "category_counts": {"\u95e8\u5e97V2": 1},
                    "critical_category_counts": {"\u95e8\u5e97V2": 1},
                },
                "scenario_summary": {
                    "store_case": {
                        "category": "\u95e8\u5e97V2",
                        "critical": True,
                        "attempts": 1,
                        "hard_passes": 1,
                        "semantic_passes": 1,
                        "infrastructure_failures": 0,
                    }
                },
                "effect_review": {
                    "issue_count": 0,
                    "low_score_count": 0,
                    "hard_or_infra_count": 0,
                    "items": [],
                },
                "review_artifacts": {
                    "result_count": 1,
                    "request_count": 1,
                    "event_count": 0,
                    "tool_call_count": 1,
                    "outbox_batch_count": 1,
                    "simulated_write_count": 0,
                    "results": [],
                },
                "results": [],
            }
        )

        for marker in [
            "# \u79bb\u7ebf\u5168\u94fe\u8def\u4eff\u771f\u62a5\u544a",
            "## \u7248\u672c\u8bc1\u636e",
            "Git commit\uff1aabc123",
            "Git commit set\uff1aabc123",
            "Fixture\uff1aworkflow_tests/fixtures/full_chain_simulation_v1.json",
            "## \u8fd0\u884c\u8303\u56f4\u4e0e\u9009\u9879",
            "\u53d1\u5e03\u95e8\u7981\u5019\u9009\uff1a\u662f",
            "\u5b9a\u5411 smoke\uff1a\u5426",
            "\u666e\u901a\u573a\u666f attempts\uff1a3",
            "\u5173\u952e\u573a\u666f attempts\uff1a5",
            "\u8df3\u8fc7\u8bed\u4e49\u8bc4\u5ba1\uff1a\u5426",
            "## \u573a\u666f\u8986\u76d6",
            "## \u573a\u666f\u7ed3\u679c",
            "## \u6548\u679c\u5ba1\u67e5\u6837\u672c",
            "## \u5ba1\u67e5\u8bc1\u636e",
            "## \u5931\u8d25\u8be6\u60c5",
            "\u5fc5\u6d4b\u5206\u7c7b\uff1a\u5b8c\u6574",
            "\u5173\u952e\u573a\u666f\u5206\u7c7b\uff1a\u5b8c\u6574",
            "| \u573a\u666f | \u5206\u7c7b | \u5173\u952e | \u786c\u901a\u8fc7 | \u8bed\u4e49\u901a\u8fc7 | \u57fa\u7840\u8bbe\u65bd\u5931\u8d25 |",
        ]:
            self.assertIn(marker, markdown)

        for mojibake in ["\u7ec2\u837b\u568e", "\u934f\u70d8\u6ad9", "\u701a\u00a7\u714f", "\u93c3\u72b1", "\u6b7f"]:
            self.assertNotIn(mojibake, markdown)

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

    def test_step_contract_detects_payment_card_sent_before_followup(self) -> None:
        errors = _hard_check(
            scenario={"expected": {"must_reply": True, "required_types": ["payment_collection"]}},
            step_results=[
                {
                    "index": 1,
                    "expected": {"must_reply": True, "forbidden_types": ["payment_collection"]},
                    "sync_reply_messages": [
                        {"type": "text", "content": "活动总价是268元。"},
                        {"type": "payment_collection", "content": {"amount": 10}},
                    ],
                },
                {
                    "index": 2,
                    "expected": {
                        "must_reply": True,
                        "required_types": ["payment_collection"],
                        "payment_amount": 10,
                    },
                    "sync_reply_messages": [
                        {"type": "text", "content": "可以，给您发预约金入口。"},
                        {"type": "payment_collection", "content": {"amount": 10}},
                    ],
                },
            ],
            outbox=[],
            stores=[],
            external_writes=[],
        )

        self.assertIn("step[1].forbidden_type:payment_collection", errors)
        self.assertNotIn("step[2].missing_required_type:payment_collection", errors)

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

    def test_unrecovered_infrastructure_errors_include_provider_failure_evidence(self) -> None:
        steps = [
            {
                "index": 1,
                "kind": "customer_message",
                "sync_reply_messages": [{"type": "text", "content": "您稍等一下"}],
                "response_meta": {
                    "tool_calls": [
                        {
                            "error": (
                                "RuntimeError: All JSON model candidates failed: "
                                "gemini-3.5-flash: RuntimeError: Model HTTP 503"
                            )
                        }
                    ]
                },
            }
        ]

        self.assertEqual(_unrecovered_infrastructure_errors(steps), ["step[1].provider_failure"])

    def test_unrecovered_infrastructure_errors_ignore_recovered_business_reply(self) -> None:
        steps = [
            {
                "index": 1,
                "kind": "customer_message",
                "sync_reply_messages": [
                    {
                        "type": "text",
                        "content": "湖北省这边可以帮您匹配门店，您是在湖北哪个城市、哪个区呢？",
                    }
                ],
                "response_meta": {
                    "sop_gate": {
                        "model_usage": {
                            "json_response_format_strict_error": (
                                "RuntimeError: All JSON model candidates failed: "
                                "gpt-5.4: RuntimeError: Model HTTP 400"
                            )
                        }
                    }
                },
            }
        ]

        self.assertEqual(_unrecovered_infrastructure_errors(steps), [])

    def test_hard_check_flags_utf8_neutral_wait_fallback(self) -> None:
        errors = _hard_check(
            scenario={"expected": {"must_reply": True}},
            step_results=[{"sync_reply_messages": [{"type": "text", "content": "您稍等一下"}]}],
            outbox=[],
            stores=[],
            external_writes=[],
        )

        self.assertIn("scenario.neutral_fallback_used", errors)

    def test_hard_check_flags_neutral_fallback_in_any_turn(self) -> None:
        errors = _hard_check(
            scenario={"expected": {"must_reply": True}},
            step_results=[
                {"sync_reply_messages": [{"type": "text", "content": "先解决当前问题。"}]},
                {"sync_reply_messages": [{"type": "text", "content": "您稍等一下"}]},
            ],
            outbox=[],
            stores=[],
            external_writes=[],
        )

        self.assertIn("batch[2].neutral_fallback_used", errors)

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
