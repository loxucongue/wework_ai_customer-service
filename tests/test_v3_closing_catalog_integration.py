from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "ai_paths"))

from app.graph.nodes.reply_nodes import (  # noqa: E402
    _normalized_policy_decision,
    _validate_policy_reply_consistency,
)
from app.config import Settings  # noqa: E402
from app.services.follow_knowledge_client import (  # noqa: E402
    FollowKnowledgeClient,
    _closing_timing,
    _normalize_closing_rules,
    _normalize_closing_sequences,
)
from app.services.v3_semantic_router_service import (  # noqa: E402
    V3SemanticRouterService,
    _closing_catalog_evidence,
    _normalize_semantic_route,
)


def _fixture() -> dict[str, Any]:
    return json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "closing_catalog_simulation.json").read_text(
            encoding="utf-8"
        )
    )


def _catalog(*, empty_rules: bool = False) -> dict[str, Any]:
    raw = _fixture()
    rules = _normalize_closing_rules(raw["rule_response"]["data"])
    if empty_rules:
        rules["rules"]["triggers"] = []
    sequences = _normalize_closing_sequences(raw["sequence_response"]["data"])
    return {
        "schema_version": "closing_catalog_v1",
        "status": "ok",
        "checksum": "catalog-checksum",
        "source": "simulation",
        "rules": rules["rules"],
        "sequences": sequences["sequences"],
        "quality_flags": [*rules["quality_flags"], *sequences["quality_flags"]],
    }


def _policy_state(evidence: dict[str, Any]) -> dict[str, Any]:
    policy = json.loads(
        (PROJECT_ROOT / "ai_paths" / "app" / "policies" / "ai_sales_policy_v1.json").read_text(
            encoding="utf-8"
        )
    )
    policy["runtime_mode"] = "active"
    return {
        "ai_sales_policy": policy,
        "closing_catalog_evidence": evidence,
        "previous_policy_state": {
            "closing_actions_today": 0,
            "minutes_since_last_closing_action": 120,
        },
        "sales_strategy_catalog": {"categories": [], "tactic_tags": []},
        "shared_context": {"conversation": []},
    }


def _decision() -> dict[str, Any]:
    return {
        "primary_task": {
            "type": "answer_current_question",
            "goal": "回答后确认下一步",
            "basis": [],
        },
        "secondary_tasks": [
            {"type": "closing_progression", "goal": "低压推进", "basis": []}
        ],
        "realtime_intent": {
            "type": "transaction_progress",
            "secondary_types": ["fact_inquiry"],
            "confidence": "high",
            "evidence_refs": ["current_message"],
            "basis": [],
        },
        "emotion_decision": {
            "label": "curious",
            "confidence": "high",
            "pressure": "normal",
            "evidence_refs": ["current_message"],
            "basis": [],
        },
        "closing_decision": {
            "action": "enter",
            "rule_ids": ["external:rule:102"],
            "sequence_key": "external:sequence:201",
            "node_key": "external:node:2011",
            "trigger": "business_rule",
            "customer_state": "engaged",
            "pressure": "normal",
            "satisfied_prerequisite_ids": ["external:prerequisite:1"],
            "blocking_taboo_ids": [],
            "evidence_refs": ["current_message"],
            "basis": [],
        },
        "cardpoint_decision": {},
    }


def test_external_interface_shapes_normalize_without_script_body() -> None:
    catalog = _catalog()

    assert len(catalog["rules"]["triggers"]) == 4
    assert catalog["rules"]["constraints"]["max_per_day"] == 2
    assert catalog["sequences"][0]["nodes"][0]["timing"] == "immediate"
    assert catalog["sequences"][0]["nodes"][1]["delay_minutes"] == 30
    assert catalog["sequences"][0]["nodes"][0]["script_type"]["id"] == 14
    assert "combined_group_unspecified:104" in catalog["quality_flags"]
    assert "body_text" not in catalog["sequences"][0]["nodes"][0]


def test_zero_delay_unknown_event_is_not_treated_as_realtime() -> None:
    assert _closing_timing("客户回复后", delay_minutes=0) == "event_driven"
    assert _closing_timing("进入逼单后", delay_minutes=0) == "immediate"


def test_closing_catalog_queries_both_read_only_endpoints_and_uses_cache() -> None:
    fixture = _fixture()
    client = FollowKnowledgeClient(
        Settings(
            FOLLOW_KNOWLEDGE_ENABLED=True,
            FOLLOW_KNOWLEDGE_BASE_URL="https://example.invalid",
            FOLLOW_KNOWLEDGE_TOKEN="test-only",
            FOLLOW_KNOWLEDGE_CACHE_TTL_SECONDS=60,
        )
    )
    calls: list[str] = []

    async def request(path: str, _payload: dict[str, Any]) -> httpx.Response:
        calls.append(path)
        body = fixture["rule_response"] if path.endswith("closing-rule") else fixture["sequence_response"]
        return httpx.Response(200, json=body)

    client._request_with_retry = request  # type: ignore[method-assign]
    first = asyncio.run(client.query_closing_catalog())
    second = asyncio.run(client.query_closing_catalog())

    assert first["status"] == "ok"
    assert first["trigger_count"] == 4
    assert first["sequence_count"] == 3
    assert second["cache_hit"] is True
    assert calls == ["event/follow/closing-rule", "event/follow/closing-sequence"]


def test_closing_catalog_singleflight_and_stale_last_good() -> None:
    fixture = _fixture()
    client = FollowKnowledgeClient(
        Settings(
            FOLLOW_KNOWLEDGE_ENABLED=True,
            FOLLOW_KNOWLEDGE_BASE_URL="https://example.invalid",
            FOLLOW_KNOWLEDGE_TOKEN="test-only",
            FOLLOW_KNOWLEDGE_CACHE_TTL_SECONDS=60,
        )
    )
    calls: list[str] = []

    async def request(path: str, _payload: dict[str, Any]) -> httpx.Response:
        await asyncio.sleep(0.01)
        calls.append(path)
        body = fixture["rule_response"] if path.endswith("closing-rule") else fixture["sequence_response"]
        return httpx.Response(200, json=body)

    async def run() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        client._request_with_retry = request  # type: ignore[method-assign]
        concurrent = await asyncio.gather(*[client.query_closing_catalog() for _ in range(6)])
        client._cache.clear()

        async def fail(_path: str, _payload: dict[str, Any]) -> httpx.Response:
            raise httpx.ConnectError("offline")

        client._request_with_retry = fail  # type: ignore[method-assign]
        stale = await client.query_closing_catalog()
        return concurrent, stale

    concurrent, stale = asyncio.run(run())
    assert all(item["status"] == "ok" for item in concurrent)
    assert sorted(calls) == ["event/follow/closing-rule", "event/follow/closing-sequence"]
    assert stale["status"] == "ok"
    assert stale["freshness_status"] == "stale"
    assert "stale_after_refresh_error" in stale["quality_flags"]


def test_local_closing_catalog_runs_without_external_token() -> None:
    client = FollowKnowledgeClient(
        Settings(
            FOLLOW_KNOWLEDGE_ENABLED=False,
            FOLLOW_KNOWLEDGE_TOKEN="",
            AI_CLOSING_CATALOG_SOURCE="local",
        )
    )

    catalog = asyncio.run(client.query_closing_catalog())
    scripts = asyncio.run(
        client.query_closing_scripts(
            checkpoint_type_id=9101,
            catalog_source="local_closing_catalog",
        )
    )

    assert client.available is False
    assert client.closing_catalog_available is True
    assert catalog["status"] == "ok"
    assert catalog["source"] == "local_closing_catalog"
    assert catalog["trigger_count"] == 5
    assert catalog["sequence_count"] == 5
    assert catalog["script_count"] == 13
    assert scripts["total"] == 1
    assert scripts["items"][0]["script_name"] == "认可承接"


def test_empty_external_closing_catalog_falls_back_to_local_config() -> None:
    client = FollowKnowledgeClient(
        Settings(
            FOLLOW_KNOWLEDGE_ENABLED=True,
            FOLLOW_KNOWLEDGE_BASE_URL="https://example.invalid",
            FOLLOW_KNOWLEDGE_TOKEN="test-only",
            AI_CLOSING_CATALOG_SOURCE="external_then_local",
        )
    )

    async def request(path: str, _payload: dict[str, Any]) -> httpx.Response:
        data = (
            {"triggers": [], "aiConfirm": {}, "constraints": {}}
            if path.endswith("closing-rule")
            else {"total": 0, "list": []}
        )
        return httpx.Response(200, json={"code": 200, "message": "ok", "data": data})

    client._request_with_retry = request  # type: ignore[method-assign]
    catalog = asyncio.run(client.query_closing_catalog())

    assert catalog["status"] == "ok"
    assert catalog["source"] == "local_closing_catalog"
    assert catalog["fallback_used"] is True
    assert catalog["fallback_reason"] == "external_closing_catalog_empty"
    assert catalog["external_status"] == "ok"


def test_external_closing_catalog_remains_preferred_when_configured() -> None:
    fixture = _fixture()
    client = FollowKnowledgeClient(
        Settings(
            FOLLOW_KNOWLEDGE_ENABLED=True,
            FOLLOW_KNOWLEDGE_BASE_URL="https://example.invalid",
            FOLLOW_KNOWLEDGE_TOKEN="test-only",
            AI_CLOSING_CATALOG_SOURCE="external_then_local",
        )
    )

    async def request(path: str, _payload: dict[str, Any]) -> httpx.Response:
        body = fixture["rule_response"] if path.endswith("closing-rule") else fixture["sequence_response"]
        return httpx.Response(200, json=body)

    client._request_with_retry = request  # type: ignore[method-assign]
    catalog = asyncio.run(client.query_closing_catalog())

    assert catalog["source"] == "follow_knowledge_api"
    assert catalog.get("fallback_used") is not True


def test_local_closing_ids_and_script_type_are_valid_reply_evidence() -> None:
    client = FollowKnowledgeClient(
        Settings(FOLLOW_KNOWLEDGE_ENABLED=False, AI_CLOSING_CATALOG_SOURCE="local")
    )
    catalog = asyncio.run(client.query_closing_catalog())
    evidence = _closing_catalog_evidence(
        catalog,
        {
            "status": "matched",
            "selected_rule_ids": ["local:rule:recognized_pending_time"],
            "sequence_candidate_ids": ["local:sequence:confirm_time_range"],
        },
    )
    decision = _decision()
    decision["closing_decision"].update(
        {
            "rule_ids": ["local:rule:recognized_pending_time"],
            "sequence_key": "local:sequence:confirm_time_range",
            "node_key": "local:node:acknowledge_acceptance",
            "satisfied_prerequisite_ids": [
                "local:prerequisite:customer_progress_signal",
                "local:prerequisite:no_unresolved_blocker",
            ],
        }
    )

    result = _normalized_policy_decision(decision, state=_policy_state(evidence))

    assert result["closing_decision"]["action"] == "enter"
    assert result["closing_decision"]["sequence_source_id"] == "confirm_time_range"
    assert result["closing_decision"]["script_type_id"] == 9101


def test_router_retrieves_local_script_from_the_same_catalog_source() -> None:
    class SemanticClient:
        available = True
        last_usage: dict[str, Any] = {}

        async def chat_json(self, _messages: list[dict[str, str]]) -> dict[str, Any]:
            return {
                "current_intent": {
                    "summary": "客户认可方案并愿意继续确认时间",
                    "evidence_refs": ["current_message"],
                },
                "current_friction": {"status": "none"},
                "closing_catalog_match": {
                    "selected_rule_ids": ["local:rule:recognized_pending_time"],
                    "sequence_candidate_ids": ["local:sequence:confirm_time_range"],
                    "evidence_refs": ["current_message"],
                },
                "store_query": {"required": False},
            }

    client = FollowKnowledgeClient(
        Settings(FOLLOW_KNOWLEDGE_ENABLED=False, AI_CLOSING_CATALOG_SOURCE="local")
    )
    service = V3SemanticRouterService(
        semantic_client=SemanticClient(),  # type: ignore[arg-type]
        knowledge_client=client,
    )
    catalog = asyncio.run(service.load_closing_catalog())
    output = asyncio.run(
        service.route(
            shared_context={
                "current_message": {"content": "这个方案可以，周末可能有空"},
                "conversation": [],
            },
            sequence_result={"status": "disabled", "items": [], "total": 0},
            taxonomy_result={"status": "disabled", "types": []},
            closing_catalog_result=catalog,
        )
    )

    evidence = output["semantic_route"]["closing_catalog_evidence"]
    assert evidence["source"] == "local_closing_catalog"
    assert evidence["match_status"] == "matched"
    assert output["knowledge_evidence"]["candidate_count"] == 3
    assert {
        item["checkpoint_type"]["id"]
        for item in output["knowledge_evidence"]["candidates"]
    } == {9101, 9102, 9103}
    assert all(
        item["source"] == "local_closing_catalog"
        for item in output["knowledge_evidence"]["script_query_results"]
    )


def test_router_keeps_only_real_rule_and_sequence_candidates() -> None:
    catalog = _catalog()
    route = _normalize_semantic_route(
        {
            "current_intent": {
                "summary": "客户在问怎么预约",
                "evidence_refs": ["current_message"],
            },
            "current_friction": {"status": "none"},
            "closing_catalog_match": {
                "selected_rule_ids": ["external:rule:102", "external:rule:999"],
                "sequence_candidate_ids": [
                    "external:sequence:201",
                    "external:sequence:999",
                ],
                "evidence_refs": ["current_message", "assistant:old"],
            },
        },
        shared_context={"conversation": []},
        sequences=[],
        checkpoint_taxonomy=[],
        fact_topic_catalog=[],
        closing_catalog=catalog,
    )

    assert route["closing_catalog_match"] == {
        "status": "matched",
        "selected_rule_ids": ["external:rule:102"],
        "sequence_candidate_ids": ["external:sequence:201"],
        "evidence_refs": ["current_message"],
        "reason": "",
    }


def test_router_rejects_closing_match_without_customer_evidence() -> None:
    route = _normalize_semantic_route(
        {
            "current_intent": {"summary": "怎么预约", "evidence_refs": ["current_message"]},
            "current_friction": {"status": "none"},
            "closing_catalog_match": {
                "selected_rule_ids": ["external:rule:102"],
                "sequence_candidate_ids": ["external:sequence:201"],
                "evidence_refs": [],
            },
        },
        shared_context={"conversation": []},
        sequences=[],
        checkpoint_taxonomy=[],
        fact_topic_catalog=[],
        closing_catalog=_catalog(),
    )

    assert route["closing_catalog_match"]["status"] == "none"
    assert route["closing_catalog_match"]["selected_rule_ids"] == []


def test_successful_empty_rule_catalog_never_exposes_demo_sequence() -> None:
    catalog = _catalog(empty_rules=True)
    route = _normalize_semantic_route(
        {
            "current_intent": {"summary": "怎么预约", "evidence_refs": ["current_message"]},
            "current_friction": {"status": "none"},
            "closing_catalog_match": {
                "selected_rule_ids": ["external:rule:102"],
                "sequence_candidate_ids": ["external:sequence:201"],
            },
        },
        shared_context={"conversation": []},
        sequences=[],
        checkpoint_taxonomy=[],
        fact_topic_catalog=[],
        closing_catalog=catalog,
    )

    assert route["closing_catalog_match"]["status"] == "catalog_empty"
    assert route["closing_catalog_match"]["sequence_candidate_ids"] == []


def test_reply_adopts_valid_external_rule_and_derives_node_types() -> None:
    catalog = _catalog()
    match = {
        "status": "matched",
        "selected_rule_ids": ["external:rule:102"],
        "sequence_candidate_ids": ["external:sequence:201"],
    }
    evidence = _closing_catalog_evidence(catalog, match)

    result = _normalized_policy_decision(_decision(), state=_policy_state(evidence))

    closing = result["closing_decision"]
    assert result["decision_status"] == "ok"
    assert closing["action"] == "enter"
    assert closing["sequence_source_id"] == "201"
    assert closing["node_source_id"] == "2011"
    assert closing["action_type_id"] == 3
    assert closing["script_type_id"] == 14
    assert closing["catalog_checksum"] == "catalog-checksum"
    assert closing["constraint_status"] == "passed"


def test_reply_blocks_missing_prerequisite_and_daily_frequency() -> None:
    catalog = _catalog()
    evidence = _closing_catalog_evidence(
        catalog,
        {
            "status": "matched",
            "selected_rule_ids": ["external:rule:102"],
            "sequence_candidate_ids": ["external:sequence:201"],
        },
    )
    decision = _decision()
    decision["closing_decision"]["satisfied_prerequisite_ids"] = []
    state = _policy_state(evidence)
    state["previous_policy_state"]["closing_actions_today"] = 2

    result = _normalized_policy_decision(decision, state=state)

    assert result["closing_decision"]["action"] == "pause"
    assert result["closing_decision"]["node_key"] == ""
    assert "closing_prerequisite_not_confirmed" in result["decision_reasons"]
    assert "closing_daily_limit_reached" in result["decision_reasons"]


def test_reply_cannot_use_local_demo_sequence_when_tenant_rule_catalog_is_empty() -> None:
    catalog = _catalog(empty_rules=True)
    evidence = _closing_catalog_evidence(
        catalog,
        {"status": "catalog_empty", "selected_rule_ids": [], "sequence_candidate_ids": []},
    )
    decision = _decision()
    decision["closing_decision"].update(
        {
            "rule_ids": [],
            "sequence_key": "gentle_invite",
            "node_key": "confirm_visit",
        }
    )

    result = _normalized_policy_decision(decision, state=_policy_state(evidence))

    assert result["closing_decision"]["action"] == "pause"
    assert result["closing_decision"]["sequence_key"] == "none"
    assert result["closing_decision"]["rule_match_status"] == "catalog_empty"
    assert "closing_rule_not_matched" in result["decision_reasons"]


def test_reply_blocks_delayed_node_as_realtime_action() -> None:
    catalog = _catalog()
    evidence = _closing_catalog_evidence(
        catalog,
        {
            "status": "matched",
            "selected_rule_ids": ["external:rule:102"],
            "sequence_candidate_ids": ["external:sequence:201"],
        },
    )
    decision = _decision()
    decision["closing_decision"]["node_key"] = "external:node:2012"

    result = _normalized_policy_decision(decision, state=_policy_state(evidence))

    assert result["closing_decision"]["action"] == "pause"
    assert "closing_delayed_node_not_realtime" in result["decision_reasons"]


def test_reply_requires_router_friction_to_be_explicitly_resolved() -> None:
    catalog = _catalog()
    evidence = _closing_catalog_evidence(
        catalog,
        {
            "status": "matched",
            "selected_rule_ids": ["external:rule:102"],
            "sequence_candidate_ids": ["external:sequence:201"],
        },
    )
    state = _policy_state(evidence)
    state["semantic_route"] = {"current_friction": {"status": "explicit"}}

    blocked = _normalized_policy_decision(_decision(), state=state)
    assert blocked["closing_decision"]["action"] == "pause"
    assert "current_friction_requires_resolved_cardpoint" in blocked["decision_reasons"]

    resolved_decision = _decision()
    resolved_decision["cardpoint_decision"] = {
        "category_key": "price",
        "scenario_query": "价格问题已经解释清楚",
        "tactic_tags": [],
        "state": "resolved",
        "confidence": "high",
        "basis": [],
    }
    state["sales_strategy_catalog"]["categories"] = [{"category_key": "price"}]
    resolved = _normalized_policy_decision(resolved_decision, state=state)
    assert resolved["closing_decision"]["action"] == "enter"


def test_reply_rejects_selected_script_from_wrong_closing_node_type() -> None:
    evidence = _closing_catalog_evidence(
        _catalog(),
        {
            "status": "matched",
            "selected_rule_ids": ["external:rule:102"],
            "sequence_candidate_ids": ["external:sequence:201"],
        },
    )
    state = _policy_state(evidence)
    state["sales_recall"] = {
        "candidates": [
            {
                "script_id": "wrong-script",
                "source_id": "wrong-script",
                "checkpoint_type": {"id": 15},
                "sequence_links": [
                    {
                        "sequence_id": "external:sequence:202",
                        "step_id": "external:node:2021",
                        "query_source": "closing_catalog_node",
                    }
                ],
            }
        ]
    }
    payload = {
        "policy_decision": _decision(),
        "selected_content_ids": ["follow_script:wrong-script:p1"],
        "knowledge_use": {"script_id": "wrong-script"},
    }

    with pytest.raises(ValueError, match="closing_selected_script_type_mismatch"):
        _validate_policy_reply_consistency(payload, state)


def test_closing_recall_uses_existing_router_call_only() -> None:
    class SemanticClient:
        available = True
        last_usage: dict[str, Any] = {}

        def __init__(self) -> None:
            self.calls = 0

        async def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
            self.calls += 1
            assert "external:rule:102" in messages[-1]["content"]
            return {
                "current_intent": {
                    "summary": "客户询问怎么预约",
                    "evidence_refs": ["current_message"],
                },
                "current_friction": {"status": "none"},
                "closing_catalog_match": {
                    "selected_rule_ids": ["external:rule:102"],
                    "sequence_candidate_ids": ["external:sequence:201"],
                    "evidence_refs": ["current_message"],
                },
                "store_query": {"required": False},
            }

    semantic = SemanticClient()

    class KnowledgeClient:
        available = True

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def query_all_scripts(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {
                "status": "ok",
                "total": 1,
                "items": [
                    {
                        "id": "501",
                        "script_code": "closing-appointment-501",
                        "source_ref": "follow_script:501",
                        "script_name": "低压确认到店时间",
                        "body_text": "您周六哪个时间段更方便？我按您的时间帮您看一下。",
                        "checkpoint_type": {"id": 14, "name": "逼单-约时间类"},
                        "checkpoint_tag": {"id": 0, "name": ""},
                        "action_code": "",
                        "action_name": "预约确认",
                        "paragraphs": [],
                    }
                ],
                "duration_ms": 1,
            }

    knowledge = KnowledgeClient()
    service = V3SemanticRouterService(
        semantic_client=semantic,  # type: ignore[arg-type]
        knowledge_client=knowledge,  # type: ignore[arg-type]
    )
    output = asyncio.run(
        service.route(
            shared_context={
                "current_message": {"content": "怎么预约"},
                "conversation": [],
            },
            sequence_result={"status": "ok", "items": [], "total": 0},
            taxonomy_result={"status": "ok", "types": []},
            closing_catalog_result=_catalog(),
        )
    )

    assert semantic.calls == 1
    assert [item["checkpoint_type_id"] for item in knowledge.calls] == [14]
    assert output["semantic_route"]["closing_catalog_match"]["status"] == "matched"
    assert output["semantic_route"]["closing_catalog_evidence"]["candidate_sequences"][0][
        "sequence_key"
    ] == "external:sequence:201"
    script = output["knowledge_evidence"]["candidates"][0]
    assert script["checkpoint_type"]["id"] == 14
    assert script["retrieval_match_scope"] == "closing_script_type"
    assert script["sequence_links"][0]["sequence_id"] == "external:sequence:201"
    assert script["sequence_links"][0]["step_id"] == "external:node:2011"
    assert output["knowledge_evidence"]["selector"]["reason"] == (
        "closing_type_candidates_deferred_to_reply"
    )


def test_post_store_selector_rechecks_closing_catalog_match() -> None:
    class SemanticClient:
        available = True
        last_usage: dict[str, Any] = {}

        async def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
            assert "本轮权威门店查询结果" in messages[-1]["content"]
            assert "external:rule:102" in messages[-1]["content"]
            return {
                "closing_catalog_match": {
                    "selected_rule_ids": ["external:rule:102"],
                    "sequence_candidate_ids": ["external:sequence:201"],
                    "evidence_refs": ["current_message"],
                },
                "sequence_match": {},
                "store_result_interpretation": {
                    "resolved_current_request": True,
                    "remaining_customer_concern_refs": [],
                    "reason": "已查到可用门店",
                },
            }

    service = V3SemanticRouterService(
        semantic_client=SemanticClient(),  # type: ignore[arg-type]
        knowledge_client=None,
    )
    output = asyncio.run(
        service.route_after_store(
            shared_context={
                "current_message": {"content": "周六能去哪个店"},
                "conversation": [],
            },
            pre_route={
                "current_intent": {
                    "summary": "客户询问到店安排",
                    "evidence_refs": ["current_message"],
                },
                "current_friction": {"status": "none"},
                "checkpoint": {},
                "closing_catalog_match": {"status": "none"},
            },
            store_resolution_fact={"status": "resolved", "stores": [{"store_id": "s1"}]},
            sequence_result={"status": "ok", "items": [], "total": 0},
            taxonomy_result={"status": "ok", "types": []},
            closing_catalog_result=_catalog(),
        )
    )

    assert output["semantic_route"]["closing_catalog_match"]["status"] == "matched"
    assert output["semantic_route"]["closing_catalog_evidence"]["candidate_sequences"][0][
        "sequence_key"
    ] == "external:sequence:201"
