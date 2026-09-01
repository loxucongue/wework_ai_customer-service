from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from app.config import Settings
from app.graph.nodes.reply_context import reply_user_payload_for_model
from app.graph.nodes.reply_nodes import _filter_unsupported_media, _normalized_policy_decision
from app.services.outreach_service import OutreachService, _scheduled_at_for_strategy_step, _selected_strategy_steps
from app.services.sales_strategy_service import SalesStrategyService


CATALOG_PATH = Path(__file__).resolve().parents[1] / "ai_paths" / "app" / "policies" / "sales_strategy_catalog_v1.json"
POLICY_PATH = Path(__file__).resolve().parents[1] / "ai_paths" / "app" / "policies" / "ai_sales_policy_v1.json"
GOLD_CANDIDATES_PATH = Path(__file__).resolve().parent / "fixtures" / "sales_strategy_gold_candidates_20260831.json"


def _settings(path: Path = CATALOG_PATH) -> Settings:
    return Settings(
        _env_file=None,
        SALES_STRATEGY_CATALOG_PATH=path,
        SALES_STRATEGY_CATALOG_ENABLED=True,
        AI_SALES_POLICY_PATH=POLICY_PATH,
        AI_SALES_POLICY_ENABLED=True,
    )


def _catalog_summary() -> dict:
    return SalesStrategyService(_settings()).runtime_summary()


def _policy() -> dict:
    from app.services.ai_sales_policy_service import AiSalesPolicyService

    return AiSalesPolicyService(_settings()).runtime_snapshot()


def test_compiled_catalog_has_expected_complete_contract() -> None:
    service = SalesStrategyService(_settings())
    view = service.admin_view()

    assert view["schema_version"] == "sales_strategy_catalog_v1"
    assert view["runtime_mode"] == "shadow"
    assert view["counts"] == {
        "categories": 13,
        "scenarios": 143,
        "strategies": 92,
        "contents": 522,
        "images": 226,
        "videos": 61,
    }
    assert view["audit"]["error_count"] == 0
    assert len({item["strategy_key"] for item in view["strategies"]}) == 92
    assert len({item["content_id"] for item in view["contents"]}) == 522
    assert all(item["content_types"] for item in view["contents"])
    assert all(len(item["steps"]) <= 5 for item in view["strategies"])
    assert any(len(item.get("image_urls") or []) > 1 for item in view["contents"])
    assert any(len(item.get("video_urls") or []) > 1 for item in view["contents"])
    assert all(
        "\n" not in url and "\r" not in url
        for item in view["contents"]
        for url in [*(item.get("image_urls") or []), *(item.get("video_urls") or [])]
    )
    assert all(
        len(item.get("asset_ids") or []) == len(item.get("asset_fingerprints") or [])
        for item in view["contents"]
    )


def test_dynamic_facts_are_filtered_before_reply_candidates() -> None:
    service = SalesStrategyService(_settings())
    without_facts = service.retrieve(
        category_key="visit_blocked",
        scenario_query="今天暴雨没法过去",
        tactic_tags=["预约确认"],
        fact_context={},
    )
    assert any("weather_facts" in item["reason"] for item in without_facts["filtered"])
    assert all("dynamic_weather" not in item["risk_flags"] for item in without_facts["candidates"])

    with_facts = service.retrieve(
        category_key="visit_blocked",
        scenario_query="今天暴雨没法过去",
        tactic_tags=["预约确认"],
        fact_context={"weather_facts": {"source": "authoritative"}},
    )
    assert any("dynamic_weather" in item["risk_flags"] for item in with_facts["candidates"])


def test_hard_risk_never_reaches_reply_candidates_even_with_facts() -> None:
    service = SalesStrategyService(_settings())
    result = service.retrieve(
        category_key="effect_objection",
        scenario_query="能不能保证效果",
        fact_context={"compliance_verified_claim": True},
    )
    assert all("prohibited_absolute_or_medical_claim" not in item["risk_flags"] for item in result["candidates"])


def test_last_known_good_is_used_after_provider_breaks(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(CATALOG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    service = SalesStrategyService(_settings(path))
    first = service.load()
    path.write_text("{broken", encoding="utf-8")

    degraded = service.load()

    assert degraded["checksum"] == first["checksum"]
    assert degraded["runtime_health"]["status"] == "degraded"
    assert degraded["runtime_health"]["using_last_known_good"] is True


def test_reply_receives_only_sanitized_candidate_contract() -> None:
    state = {
        "normalized_content": "有点贵",
        "cardpoint_decision": {"category_key": "price_objection", "state": "active"},
        "cardpoint_candidates": [
            {
                "content_id": "content_1",
                "scenario_name": "觉得价格高",
                "tactic_tag": "价值补充",
                "solution_idea": "解释价值",
                "reference_text": "参考表达",
                "image_url": "https://test.by4dev.4ba.cn/ai-paths/cardpoint-media/x.png",
                "source": {"workbook": "secret.xlsx", "row": 1},
                "risk_flags": ["internal"],
            }
        ],
    }

    payload = reply_user_payload_for_model(state)

    assert payload["cardpoint_candidates"][0]["content_id"] == "content_1"
    assert payload["cardpoint_candidates"][0]["usage"] == "reference_only_rephrase_do_not_copy"
    assert "source" not in payload["cardpoint_candidates"][0]
    assert "risk_flags" not in payload["cardpoint_candidates"][0]


def test_reply_media_filter_allows_only_current_authoritative_candidates() -> None:
    image = "https://test.by4dev.4ba.cn/ai-paths/cardpoint-media/allowed.png"
    video = "https://test.by4dev.4ba.cn/ai-paths/cardpoint-media/allowed.mp4"
    warnings: list[dict] = []
    filtered = _filter_unsupported_media(
        [
            {"type": "image", "order": 1, "content": image},
            {"type": "video", "order": 2, "content": video},
            {"type": "image", "order": 3, "content": "https://untrusted.example/fake.png"},
        ],
        {"cardpoint_candidates": [{"image_urls": [image], "video_urls": [video]}]},
        warnings,
    )

    assert [item["type"] for item in filtered] == ["image", "video"]
    assert warnings and "https://untrusted.example/fake.png" in warnings[0]["detail"]["removed_urls"]


def test_active_v3_reply_policy_decision_is_schema_normalized() -> None:
    normalized = _normalized_policy_decision(
        {
            "primary_task": {"type": "resolve_blocker", "goal": "处理价格顾虑", "basis": ["客户说贵"]},
            "realtime_intent": {"type": "blocker_expression", "confidence": "high", "basis": ["客户说贵"]},
            "emotion_decision": {"label": "hesitant", "pressure": "low", "flow_action": "invented"},
            "closing_decision": {
                "action": "enter",
                "sequence_key": "price_hesitation",
                "node_key": "value_reframe",
                "trigger": "positive_progress",
                "customer_state": "hesitant",
                "pressure": "low",
            },
            "cardpoint_decision": {
                "category_key": "price_objection",
                "scenario_query": "客户觉得贵",
                "tactic_tags": ["价值补充", "不存在标签"],
                "state": "active",
                "confidence": "high",
            },
        },
        state={"ai_sales_policy": _policy(), "sales_strategy_catalog": _catalog_summary()},
    )

    assert normalized["primary_task"]["type"] == "resolve_blocker"
    assert normalized["emotion_decision"]["flow_action"] == "lower_pressure"
    assert normalized["closing_decision"]["node_key"] == "value_reframe"
    assert normalized["cardpoint_decision"]["tactic_tags"] == ["价值补充"]


def test_strategy_step_selection_and_fixed_scheduling() -> None:
    strategy = {
        "steps": [
            {"step_key": "step_1", "trigger_base": "customer_reply", "delay_minutes": 5},
            {"step_key": "step_2", "trigger_base": "same_day_20_00", "delay_minutes": 0},
        ]
    }
    selected = _selected_strategy_steps({"selected_step_keys": ["step_2"]}, strategy)
    assert [item["step_key"] for item in selected] == ["step_2"]
    scheduled = _scheduled_at_for_strategy_step(
        "2026-08-31T10:00:00+00:00",
        selected[0],
    )
    assert scheduled == "2026-08-31T12:00:00+00:00"


def test_catalog_checksum_ignores_generated_timestamp() -> None:
    from app.services.sales_strategy_service import _checksum

    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    changed = deepcopy(payload)
    changed["generated_at"] = "2099-01-01T00:00:00+00:00"
    assert _checksum(payload) == _checksum(changed)


def test_gold_candidate_matrix_meets_coverage_floor_but_is_not_marked_reviewed() -> None:
    fixture = json.loads(GOLD_CANDIDATES_PATH.read_text(encoding="utf-8"))
    coverage = fixture["coverage"]
    assert fixture["gold_status"] == "pending_human_review"
    assert coverage["cases"] == 400
    assert len(coverage["category_counts"]) == 13
    assert min(coverage["category_counts"].values()) >= 20
    assert len(coverage["intent_counts"]) == 7
    assert min(coverage["intent_counts"].values()) >= 20
    assert len(coverage["emotion_counts"]) == 8
    assert min(coverage["emotion_counts"].values()) >= 15
    assert coverage["multi_signal_count"] >= 80


class _StrategyStub:
    strategy = {
        "strategy_key": "strategy_price",
        "category_key": "price_objection",
        "scenario_keys": ["scenario_price"],
        "name": "价格高需要考虑",
        "version": "test",
        "steps": [
            {
                "step_key": "step_1",
                "trigger_base": "customer_reply",
                "delay_minutes": 5,
                "node_goal": "解释价值并降低决策压力",
                "tactic_tags": ["价值补充"],
            }
        ],
    }

    def runtime_summary(self) -> dict:
        return {"runtime_mode": "shadow", "catalog_version": "test", "checksum": "abc"}

    def retrieve_strategy_pool(self, **_: object) -> list[dict]:
        return [deepcopy(self.strategy)]

    def retrieve(self, **_: object) -> dict:
        return {
            "candidates": [
                {
                    "content_id": "content_price",
                    "scenario_name": "价格高需要考虑",
                    "tactic_tag": "价值补充",
                    "reference_text": "参考表达",
                }
            ],
            "filtered": [],
        }


class _OutreachRepositoryStub:
    def __init__(self) -> None:
        self.created: dict = {}
        self.events: list[dict] = []
        self.updates: list[tuple[str, dict]] = []
        self.existing: dict = {}
        self.cancel_calls: list[dict] = []

    def recent_customer_context(self, *args: object, **kwargs: object) -> dict:
        return {"memory": {"last_customer_message_at": "2026-08-31T10:00:00+00:00"}, "recent_messages": [{"role": "user", "content": "有点贵"}]}

    def add_outreach_event(self, **kwargs: object) -> None:
        self.events.append(dict(kwargs))

    def create_outreach_plan(self, **kwargs: object) -> dict:
        self.created = dict(kwargs)
        self.existing = {"id": "plan-1", "source_snapshot": kwargs["source_snapshot"]}
        return {"plan": self.existing, "tasks": kwargs["tasks"]}

    def find_open_outreach_plan_by_sop_plan_id(self, *args: object, **kwargs: object) -> dict:
        return self.existing

    def cancel_open_closing_sequence_plans(self, **kwargs: object) -> int:
        self.cancel_calls.append(dict(kwargs))
        self.existing = {}
        return 1


class _PlanModelStub:
    available = True

    async def chat_json(self, *args: object, **kwargs: object) -> dict:
        return {
            "should_create_plan": True,
            "conversion_stage": "P2_OBJECTION",
            "stall_reason": "price_worry",
            "customer_psychology": "担心价格",
            "plan_goal": "低压力承接",
            "selected_strategy_key": "strategy_price",
            "selected_step_keys": ["step_1"],
        }


class _NoSendSystemStub:
    async def send(self, **kwargs: object) -> dict:
        raise AssertionError("shadow plan must never call the real send adapter")


def test_outreach_uses_configured_strategy_and_persists_shadow_snapshot() -> None:
    repository = _OutreachRepositoryStub()
    service = OutreachService(
        repository=repository,  # type: ignore[arg-type]
        model_client=_PlanModelStub(),  # type: ignore[arg-type]
        system_client=_NoSendSystemStub(),  # type: ignore[arg-type]
        sales_strategy_service=_StrategyStub(),  # type: ignore[arg-type]
    )

    result = asyncio.run(
        service.generate_configured_strategy_shadow_plan(
            customer_id="customer-1",
            corp_id="corp-1",
            wechat="wechat-a",
            external_userid="external-1",
            query="客户觉得价格高，想再考虑",
            memory={"last_customer_message_at": "2026-08-31T10:00:00+00:00"},
        )
    )

    assert result["created"] is True
    snapshot = repository.created["source_snapshot"]
    assert snapshot["plan_type"] == "followup_strategy"
    assert snapshot["runtime_mode"] == "shadow"
    assert snapshot["selected_strategy"]["strategy_key"] == "strategy_price"
    assert repository.created["tasks"][0]["intent"] == "step_1"
    assert repository.created["tasks"][0]["should_send_payment_collection"] is False
    assert repository.created["wechat"] == "wechat-a"


def test_closing_sequence_writes_shadow_tasks_and_never_authorizes_send() -> None:
    repository = _OutreachRepositoryStub()
    service = OutreachService(
        repository=repository,  # type: ignore[arg-type]
        model_client=_PlanModelStub(),  # type: ignore[arg-type]
        system_client=_NoSendSystemStub(),  # type: ignore[arg-type]
        sales_strategy_service=_StrategyStub(),  # type: ignore[arg-type]
    )
    state = {
        "request_id": "request-1",
        "customer_id": "customer-1",
        "external_userid": "external-1",
        "corp_id": "corp-1",
        "wechat": "wechat-a",
        "memory_persist_allowed": True,
        "ai_sales_policy": _policy(),
        "closing_decision": {
            "action": "enter",
            "sequence_key": "price_hesitation",
            "node_key": "value_reframe",
            "customer_state": "hesitant",
        },
        "cardpoint_decision": {"scenario_query": "觉得价格高"},
        "emotion_decision": {"label": "hesitant"},
    }

    result = service.record_closing_sequence_shadow(state)

    assert result["created"] is True
    assert repository.created["source_snapshot"]["plan_type"] == "closing_sequence"
    assert repository.created["source_snapshot"]["runtime_mode"] == "shadow"
    assert len(repository.created["tasks"]) == 2
    assert all(task["should_send_payment_collection"] is False for task in repository.created["tasks"])
    assert repository.created["sop_plan_id"].startswith("closing_sequence:")


def test_new_customer_reply_cancels_old_closing_tasks_on_hard_stop() -> None:
    repository = _OutreachRepositoryStub()
    repository.existing = {"id": "old-plan"}
    service = OutreachService(
        repository=repository,  # type: ignore[arg-type]
        model_client=_PlanModelStub(),  # type: ignore[arg-type]
        system_client=_NoSendSystemStub(),  # type: ignore[arg-type]
    )
    result = service.record_closing_sequence_shadow(
        {
            "request_id": "request-2",
            "customer_id": "customer-1",
            "external_userid": "external-1",
            "corp_id": "corp-1",
            "wechat": "wechat-a",
            "memory_persist_allowed": True,
            "ai_sales_policy": _policy(),
            "closing_decision": {"action": "complete", "customer_state": "hard_stop"},
        }
    )
    assert result["created"] is False
    assert result["cancelled"] == 1
    assert repository.cancel_calls[0]["wechat"] == "wechat-a"
