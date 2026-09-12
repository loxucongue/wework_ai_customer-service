from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ai_paths.app.prompts.reply_sales_prompt_v4 import PARALLEL_REPLY_SYSTEM_PROMPT
from ai_paths.scripts.v3_reply_naturalness_cases import CASES
from scripts.v3_reply_sales_opportunity_cases import OPPORTUNITY_CASES
from scripts.evaluate_v3_reply_naturalness import (
    _mainline_text,
    _representative_probes,
    score_result,
)
from scripts.evaluate_v3_reply_naturalness import render_context


def test_l2_activity_contract_matches_production_explicit_request_trigger() -> None:
    rendered = _mainline_text(
        {
            "next_stage": "activity_offer",
            "allowed_actions": ["explain_activity", "keep_open"],
        }
    )

    assert "明确询问活动/价格" in rendered
    assert "必须 explain_activity 并在本轮完整交付活动" in rendered


def test_ablation_resolves_baseline_once_and_builds_all_variants_from_it(monkeypatch) -> None:
    from scripts import evaluate_v3_reply_naturalness_ablation as ablation

    full_sha = "a" * 40
    baseline_prompt = PARALLEL_REPLY_SYSTEM_PROMPT
    calls: list[tuple[str, ...]] = []

    class Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command, **_kwargs):
        calls.append(tuple(command))
        if command[1] == "rev-parse":
            return Result(full_sha + "\n")
        if command[1] == "show":
            return Result(f"PARALLEL_REPLY_SYSTEM_PROMPT = {baseline_prompt!r}\n")
        raise AssertionError(command)

    monkeypatch.setattr(ablation.subprocess, "run", fake_run)
    resolved_sha, prompt = ablation._resolve_baseline("refs/remotes/origin/main")
    jobs = ablation._jobs([], repetitions=3, baseline_prompt=prompt)

    assert resolved_sha == full_sha
    assert prompt == baseline_prompt
    assert jobs == []
    assert calls == [
        ("git", "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"),
        ("git", "show", f"{full_sha}:{ablation.PROMPT_PATH}"),
    ]
    report = ablation._base_report(
        type("Args", (), {"phase": "screen", "repetitions": 3, "baseline_ref": "origin/main"})(),
        case_count=24,
        baseline_sha=resolved_sha,
    )
    assert report["baseline_ref"] == "origin/main"
    assert report["baseline_sha"] == full_sha


def test_ablation_requires_explicit_baseline_ref() -> None:
    import pytest
    from scripts import evaluate_v3_reply_naturalness_ablation as ablation

    with pytest.raises(SystemExit):
        ablation.build_parser().parse_args(["--env-file", "local.env", "--output", "artifacts/test"])

    args = ablation.build_parser().parse_args(
        ["--env-file", "local.env", "--output", "artifacts/test", "--baseline-ref", "abc123"]
    )
    assert args.baseline_ref == "abc123"


def test_full_graph_http_harness_runs_route_replay_and_finalization(tmp_path) -> None:
    """L1 harness regression only; the actual L3 evaluator uses the real graph."""
    import asyncio
    from app.chat_runtime import ChatRuntime
    from app.config import Settings
    from app.services.memory_store import CustomerMemoryStore
    from app.services.storage import AppRepository, SQLiteStore
    from app.services.trace_logger import TraceLogger
    from scripts.evaluate_v3_naturalness_full_graph import run_http_lifecycle, SyntheticStatus

    class CountingGraph:
        calls = 0

        async def ainvoke(self, state):
            self.calls += 1
            state.update(reply_messages=[{"type": "text", "content": "好的", "order": 1}],
                         reply_source="main_model", decision_status="ok")
            return state

    settings = Settings(_env_file=None, AI_PATHS_SERVICE_ROLE="reply", SOP_PLATFORM_PULL_ENABLED=False,
                        AI_PATHS_BACKGROUND_WORKERS_ENABLED=False).model_copy(update={
                            "db_path": tmp_path / "state.db", "memory_dir": tmp_path / "memory",
                            "trace_log_dir": tmp_path / "trace", "aics_storage_backend": "sqlite"})
    store = SQLiteStore(settings)
    store.initialize()
    repository = AppRepository(store)
    graph = CountingGraph()
    runtime = ChatRuntime(full_graph=graph, commit_graph=None, repository=repository,
                          trace_logger=TraceLogger(settings), memory_store=CustomerMemoryStore(settings, repository),
                          outreach_system_client=SyntheticStatus(), settings=settings)
    try:
        first, replay, snapshot = asyncio.run(run_http_lifecycle(
            {"runtime": runtime, "settings": settings, "repository": repository},
            {"content": "谢谢", "customer_id": "900001", "corp_id": "synthetic-corp",
             "wechat": "synthetic-wechat", "external_userid": "synthetic-http-regression",
             "customer_add_wechat_id": "900002", "request_context": {"msgid": "synthetic-http-once"}},
        ))
        assert graph.calls == 1
        assert first["execute_id"] == replay["execute_id"]
        assert first["data"]["reply_messages"] == replay["data"]["reply_messages"]
        assert snapshot["post_reply_finalization"]["status"] == "completed"
        with store.connect() as connection:
            for table in ("message_dispatches", "strategy_data_outbox"):
                assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        store.close()


def test_full_graph_payment_cases_seed_structured_prerequisites_only() -> None:
    from scripts.evaluate_v3_naturalness_full_graph import (
        apply_explicit_payment_prerequisite_fixture,
    )

    sample: dict = {}
    apply_explicit_payment_prerequisite_fixture("action-07", sample)

    assert sample["confirmed_store_id"] == "900004"
    assert sample["prior_deliveries"][0]["reply_messages"][0]["type"] == "store_address"
    assert {item["event_type"] for item in sample["source_history_events"]} == {
        "case_image_sent",
        "activity_intro_image_sent",
    }
    untouched: dict = {}
    apply_explicit_payment_prerequisite_fixture("action-01", untouched)
    assert untouched == {}


def test_full_graph_mainline_fixture_uses_structured_delivery_events() -> None:
    from scripts.evaluate_v3_naturalness_full_graph import (
        apply_mainline_prerequisite_fixture,
    )

    activity_case = {"id": "activity-case", "next_stage": "activity_offer"}
    activity_sample: dict = {}
    apply_mainline_prerequisite_fixture(activity_case, activity_sample)
    assert [item["event_type"] for item in activity_sample["source_history_events"]] == [
        "case_image_sent"
    ]
    assert "prior_deliveries" not in activity_sample

    appointment_case = {"id": "appointment-case", "next_stage": "appointment"}
    appointment_sample: dict = {}
    apply_mainline_prerequisite_fixture(appointment_case, appointment_sample)
    assert {item["event_type"] for item in appointment_sample["source_history_events"]} == {
        "case_image_sent",
        "activity_intro_image_sent",
    }
    assert appointment_sample["prior_deliveries"][0]["reply_messages"][0]["type"] == "store_address"


def test_full_graph_history_uses_production_role_prefixes() -> None:
    from scripts.evaluate_v3_naturalness_full_graph import (
        render_synthetic_conversation_history,
    )

    rendered = render_synthetic_conversation_history(
        [
            {"role": "customer", "content": "我在云州市"},
            {"role": "assistant", "content": "地址发您了"},
        ],
        substitute=lambda value: value.replace("云州", "杭州"),
    )

    assert rendered == ["客户:我在杭州市", "小贝:地址发您了"]


def test_full_graph_keeps_semantic_labels_observational_after_l2_gate(tmp_path) -> None:
    from scripts.evaluate_v3_naturalness_full_graph import summarize

    case = next(item for item in CASES if item["l3"])
    case_dir = tmp_path / case["id"]
    case_dir.mkdir(parents=True)
    (case_dir / "full_state.json").write_text(
        json.dumps(
            {
                "reply_sales_judgment": {
                    "next_sales_action": {"type": "deliberately_observational"}
                },
                "policy_decision": {
                    "closing_decision": {"customer_state": "deliberately_observational"}
                },
                "trace": [],
            }
        ),
        encoding="utf-8",
    )
    row = {
        "case_id": case["id"],
        "reply_source": "main_model",
        "required_structures_present": True,
        "response_types": list(case["required_message_types"]),
        "replay_same_messages": True,
        "replay_same_request_id": True,
        "http_transport_verified": True,
        "finalization_verified": True,
        "counts": {"message_dispatches": 0, "strategy_data_outbox": 0},
        "reply_and_tool_model_calls": [],
        "router_and_retrieval_model_calls": [],
    }
    (tmp_path / "results.json").write_text(json.dumps([row]), encoding="utf-8")

    summary = summarize(tmp_path)

    assert summary["contract_passed"] == 1
    assert summary["semantic_observation_passed"] == 0
    assert summary["details"][0]["failures"] == []
    assert summary["details"][0]["semantic_observation_failures"] == [
        "unexpected_action",
        "unexpected_customer_state",
    ]


def test_naturalness_evaluation_matrix_has_required_coverage() -> None:
    counts = Counter(str(case["category"]) for case in CASES)

    assert len(CASES) == 60
    assert counts == {
        "short_relation": 12,
        "temporary_unavailable": 10,
        "soft_refusal": 12,
        "explicit_action": 12,
        "router_pollution": 8,
        "hard_safety": 6,
    }
    assert sum(bool(case["repeat_probe"]) for case in CASES) >= 20
    assert sum(bool(case["l3"]) for case in CASES) >= 20
    assert all("真实" not in case["id"] and case["current"] for case in CASES)


def test_naturalness_matrix_encodes_safety_and_direct_delivery_contracts() -> None:
    hard = [case for case in CASES if case["hard_safety"]]
    direct = [case for case in CASES if case["required_message_types"]]

    assert len(hard) >= 6
    assert {kind for case in direct for kind in case["required_message_types"]} == {
        "image",
        "store_address",
        "payment_collection",
    }
    assert all(case["expected_actions"] for case in CASES)
    assert all(set(case["expected_actions"]).issubset(set(case["allowed_actions"])) for case in CASES)


def test_prompt_has_no_minimum_length_or_forced_emoji_proxy() -> None:
    assert len(PARALLEL_REPLY_SYSTEM_PROMPT) <= 7_000
    assert "不设最低长度" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "短承接可以只有几个字" in PARALLEL_REPLY_SYSTEM_PROMPT
    assert "只有语境自然触发时整轮最多1个轻微信表情" in PARALLEL_REPLY_SYSTEM_PROMPT
    expression = PARALLEL_REPLY_SYSTEM_PROMPT.split("# 5. 真人微信表达", 1)[1].split("# 6.", 1)[0]
    assert "不强制问句、称呼、语气词或表情" in expression
    assert "必须添加表情" not in expression


def test_policy_normal_conversation_covers_keep_open_without_changing_schema() -> None:
    policy_path = Path(__file__).resolve().parents[1] / "ai_paths" / "app" / "policies" / "ai_sales_policy_v2.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    normal = next(item for item in policy["routing"]["business_tasks"] if item["key"] == "normal_conversation")

    assert "短回应" in normal["goal"]
    assert "临时不可交流" in normal["goal"]
    assert "重复暂缓" in normal["goal"]
    assert "keep_open" in normal["goal"]
    assert policy["decision_schema_version"] == "v3_policy_decision_v3"


def test_opportunity_matrix_covers_six_groups_and_requires_visible_delivery() -> None:
    assert Counter(case["opportunity_group"] for case in OPPORTUNITY_CASES) == {
        "opening": 5, "information": 5, "effect": 5, "price": 5, "resolved": 5, "booking": 5,
    }
    assert all("keep_open" not in case["expected_actions"] for case in OPPORTUNITY_CASES)
    assert all(case["required_text_groups"] for case in OPPORTUNITY_CASES)


def test_activity_action_label_does_not_mask_missing_offer_contents() -> None:
    case = next(case for case in OPPORTUNITY_CASES if case["activity_integrity"])
    value = {
        "reply_messages": [{"type": "text", "content": "活动268元，有需要随时找我"}],
        "sales_judgment": {"next_sales_action": {"type": "explain_activity"}},
        "policy_decision": {"closing_decision": {"customer_state": "continue_sales"}},
    }
    score = score_result(case, value)
    assert not score["passed"]
    assert not score["activity_integrity_pass"]
    assert score["passive_close_hits"]
    value["reply_messages"][0]["content"] = "新客268元，含肤况评估、一次面部护理和护理后注意事项指导，需提前预约。"
    assert score_result(case, value)["passed"]


def test_l2_activity_case_mirrors_dynamic_complete_offer_context() -> None:
    case = next(case for case in OPPORTUNITY_CASES if case["activity_integrity"])
    rendered = render_context(case, order="baseline")

    assert "【活动完整交付清单】" in rendered
    assert "选择 explain_activity 就表示本轮已经完成活动介绍" in rendered
    assert "预告代替交付" in rendered
    assert "认可具体效果/方案或明确说顾虑已解除" in rendered
    assert "肤况评估" in rendered
    assert "仅限新客" in rendered
    assert "不得自动附带付款卡" in rendered


def test_repeat_probes_include_all_six_opportunity_groups() -> None:
    probes = _representative_probes([*CASES, *OPPORTUNITY_CASES])
    assert len(probes) == 20
    assert len({case["id"] for case in probes}) == 20
    assert {case["opportunity_group"] for case in probes if case.get("opportunity_group")} == {
        "opening", "information", "effect", "price", "resolved", "booking",
    }


def test_short_relation_sales_question_is_not_missed_without_activity_keyword() -> None:
    case = CASES[5]
    value = {"reply_messages": [{"type": "text", "content": "哈哈，您脸上的斑是什么情况？"}],
             "sales_judgment": {"next_sales_action": {"type": "ask_missing_fact"}},
             "policy_decision": {"closing_decision": {"customer_state": "continue_sales"}}}
    assert score_result(case, value)["irrelevant_sales_insert"]
