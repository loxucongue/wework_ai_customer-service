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
    _apply_deterministic_sequence_top_k,
    _closing_catalog_evidence,
    _normalize_semantic_route,
    _rank_script_groups,
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
        (PROJECT_ROOT / "ai_paths" / "app" / "policies" / "ai_sales_policy_v2.json").read_text(
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
            checkpoint_type_id=9201,
            catalog_source="local_closing_catalog",
        )
    )

    assert client.available is False
    assert client.closing_catalog_available is True
    assert catalog["status"] == "ok"
    assert catalog["source"] == "local_closing_catalog"
    assert catalog["catalog_version"] == "2026-09-05.business-workbook-real-deal-v1"
    assert catalog["trigger_count"] == 9
    assert catalog["sequence_count"] == 16
    assert catalog["node_count"] == 37
    assert catalog["script_count"] == 42
    assert scripts["total"] == 2
    assert all(
        item["checkpoint_type"]["name"] == "直接回答预约方式并发卡"
        for item in scripts["items"]
    )


def test_business_workbook_catalog_has_complete_rule_sequence_script_links() -> None:
    client = FollowKnowledgeClient(
        Settings(FOLLOW_KNOWLEDGE_ENABLED=False, AI_CLOSING_CATALOG_SOURCE="local")
    )
    catalog = asyncio.run(client.query_closing_catalog())

    rule_keys = {
        item["rule_key"]
        for item in catalog["rules"]["triggers"]
    }
    script_type_ids = {
        int(item["checkpoint_type"]["id"])
        for item in catalog["scripts"]
    }
    node_type_ids = {
        int(node["script_type"]["id"])
        for sequence in catalog["sequences"]
        for node in sequence["nodes"]
    }

    assert all(
        set(sequence["rule_keys"]).issubset(rule_keys)
        for sequence in catalog["sequences"]
    )
    assert node_type_ids == script_type_ids
    assert all(
        item["workbook_source_ref"].startswith("business_workbook:逼单话术!")
        for item in catalog["scripts"]
    )
    assert all(item["hard_fact_authority"] is False for item in catalog["scripts"])

    rules_by_key = {
        item["rule_key"]: item
        for item in catalog["rules"]["triggers"]
    }
    assert rules_by_key["local:rule:deposit_policy_concern"]["judge_note"].startswith(
        "PURPOSE=resolve_only"
    )
    assert rules_by_key["local:rule:visit_intent_no_prepay"]["judge_note"].startswith(
        "PURPOSE=resolve_only"
    )
    assert rules_by_key["local:rule:payment_operation_blocked"]["judge_note"].startswith(
        "PURPOSE=payment_assist"
    )
    assert "仅凭客户说‘这家可以’不能证明已核验" in rules_by_key[
        "local:rule:store_or_transport_accepted"
    ]["judge_note"]

    scripts_by_code = {item["script_code"]: item for item in catalog["scripts"]}
    assert "requires_authoritative_slot_facts" in scripts_by_code[
        "local_business_closing_010"
    ]["data_quality_flags"]
    assert "requires_authoritative_deposit_validity_fact" in scripts_by_code[
        "local_business_closing_017"
    ]["data_quality_flags"]
    assert "requires_authoritative_refund_policy_fact" in scripts_by_code[
        "local_business_closing_020"
    ]["data_quality_flags"]
    assert "requires_authoritative_store_fact" in scripts_by_code[
        "local_business_closing_031"
    ]["data_quality_flags"]


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
            "selected_rule_ids": ["local:rule:explicit_registration_or_payment_query"],
            "sequence_candidate_ids": ["local:sequence:direct_deposit_entry"],
        },
    )
    decision = _decision()
    decision["closing_decision"].update(
        {
            "rule_ids": ["local:rule:explicit_registration_or_payment_query"],
            "sequence_key": "local:sequence:direct_deposit_entry",
            "node_key": "local:node:direct_deposit_entry:step_1",
            "satisfied_prerequisite_ids": [
                "local:prerequisite:customer_progress_signal",
                "local:prerequisite:no_unresolved_blocker",
            ],
        }
    )

    result = _normalized_policy_decision(decision, state=_policy_state(evidence))

    assert result["closing_decision"]["action"] == "enter"
    assert result["closing_decision"]["sequence_source_id"] == "direct_deposit_entry"
    assert result["closing_decision"]["node_name"] == "直接回答预约方式并发卡"
    assert result["closing_decision"]["script_type_id"] == 9201


def test_reply_repair_context_keeps_exact_closing_catalog_references() -> None:
    from app.graph.nodes.reply_nodes import _parallel_reply_repair_context

    client = FollowKnowledgeClient(
        Settings(FOLLOW_KNOWLEDGE_ENABLED=False, AI_CLOSING_CATALOG_SOURCE="local")
    )
    catalog = asyncio.run(client.query_closing_catalog())
    evidence = _closing_catalog_evidence(
        catalog,
        {
            "status": "matched",
            "selected_rule_ids": ["local:rule:explicit_registration_or_payment_query"],
            "sequence_candidate_ids": ["local:sequence:direct_deposit_entry"],
            "evidence_refs": ["current_message"],
        },
    )
    state = _policy_state(evidence)
    state["evidence_join"] = {
        "shared_context": {
            "current_message": {"message_ref": "current_message", "content": "怎么预约"},
            "conversation": [],
        },
        "semantic_route": {"closing_catalog_evidence": evidence},
        "content_candidates": [],
    }
    state["semantic_route"] = {"closing_catalog_evidence": evidence}

    context = _parallel_reply_repair_context(state)

    assert context["closing_catalog_evidence"]["selected_rules"][0]["rule_key"] == (
        "local:rule:explicit_registration_or_payment_query"
    )
    assert context["closing_catalog_evidence"]["candidate_sequences"][0]["sequence_key"] == (
        "local:sequence:direct_deposit_entry"
    )


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
                    "selected_rule_ids": ["local:rule:explicit_registration_or_payment_query"],
                    "sequence_candidate_ids": ["local:sequence:direct_deposit_entry"],
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
                "current_message": {"content": "给我登记一个，预约金怎么付"},
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
    assert output["knowledge_evidence"]["candidate_count"] == 4
    assert {
        item["checkpoint_type"]["id"]
        for item in output["knowledge_evidence"]["candidates"]
    } == {9201, 9202}
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


def test_router_recovers_exact_business_configured_closing_candidate() -> None:
    client = FollowKnowledgeClient(
        Settings(FOLLOW_KNOWLEDGE_ENABLED=False, AI_CLOSING_CATALOG_SOURCE="local")
    )
    catalog = asyncio.run(client.query_closing_catalog())
    route = _normalize_semantic_route(
        {
            "current_intent": {
                "summary": "客户询问如何预约",
                "evidence_refs": ["current_message"],
            },
            "current_friction": {"status": "none"},
            "closing_catalog_match": {
                "status": "none",
                "selected_rule_ids": [],
                "sequence_candidate_ids": [],
                "evidence_refs": [],
            },
        },
        shared_context={
            "current_message": {"content": "怎么预约"},
            "conversation": [],
        },
        sequences=[],
        checkpoint_taxonomy=[],
        fact_topic_catalog=[],
        closing_catalog=catalog,
    )

    assert route["closing_catalog_match"] == {
        "status": "matched",
        "selected_rule_ids": ["local:rule:explicit_registration_or_payment_query"],
        "sequence_candidate_ids": [
            "local:sequence:direct_deposit_entry",
            "local:sequence:fee_then_deposit",
        ],
        "evidence_refs": ["current_message"],
        "reason": "configured_catalog_exact_candidate",
    }


def test_router_exact_candidate_does_not_turn_phrase_containment_into_decision() -> None:
    client = FollowKnowledgeClient(
        Settings(FOLLOW_KNOWLEDGE_ENABLED=False, AI_CLOSING_CATALOG_SOURCE="local")
    )
    catalog = asyncio.run(client.query_closing_catalog())
    route = _normalize_semantic_route(
        {
            "current_intent": {
                "summary": "客户限制继续讲预约",
                "evidence_refs": ["current_message"],
            },
            "current_friction": {"status": "none"},
            "closing_catalog_match": {"status": "none"},
        },
        shared_context={
            "current_message": {"content": "不要再问我怎么预约了"},
            "conversation": [],
        },
        sequences=[],
        checkpoint_taxonomy=[],
        fact_topic_catalog=[],
        closing_catalog=catalog,
    )

    assert route["closing_catalog_match"]["status"] == "none"
    assert route["closing_catalog_match"]["selected_rule_ids"] == []


def test_router_exact_candidate_respects_model_safety_block() -> None:
    client = FollowKnowledgeClient(
        Settings(FOLLOW_KNOWLEDGE_ENABLED=False, AI_CLOSING_CATALOG_SOURCE="local")
    )
    catalog = asyncio.run(client.query_closing_catalog())
    route = _normalize_semantic_route(
        {
            "current_intent": {
                "summary": "当前不可推进",
                "evidence_refs": ["current_message"],
            },
            "current_friction": {"status": "none"},
            "closing_catalog_match": {"status": "blocked"},
        },
        shared_context={
            "current_message": {"content": "怎么预约"},
            "conversation": [],
        },
        sequences=[],
        checkpoint_taxonomy=[],
        fact_topic_catalog=[],
        closing_catalog=catalog,
    )

    assert route["closing_catalog_match"]["status"] == "none"
    assert route["closing_catalog_match"]["selected_rule_ids"] == []


def test_router_drops_unsupported_historical_friction_without_degrading_turn() -> None:
    route = _normalize_semantic_route(
        {
            "current_intent": {
                "summary": "客户询问如何预约",
                "evidence_refs": ["current_message"],
            },
            "current_friction": {"status": "none"},
            "historical_unresolved_friction": {
                "checkpoint_code": "price",
                "summary": "历史上可能有价格顾虑",
                "evidence_refs": [],
            },
        },
        shared_context={
            "current_message": {"content": "怎么预约"},
            "conversation": [],
        },
        sequences=[],
        checkpoint_taxonomy=[{"id": 8, "code": "price", "name": "价格"}],
        fact_topic_catalog=[],
        closing_catalog={"status": "unavailable"},
    )

    assert route["historical_unresolved_friction"] == {
        "checkpoint_code": "",
        "summary": "",
        "evidence_refs": [],
    }


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
    assert closing["primary_rule_name"] == "到店意向确认"
    assert closing["sequence_name"] == "温和邀约序列"
    assert closing["node_name"] == "预约确认 / 逼单-约时间类 / 进入逼单后"
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
    state["semantic_route"] = {
        "current_friction": {
            "status": "explicit",
            "checkpoint_code": "price",
            "summary": "客户仍有价格顾虑",
        }
    }

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
    assert output["knowledge_evidence"]["selector"]["reason"] == "deterministic_top_k"


def test_post_store_retrieval_does_not_repeat_sales_semantic_decision() -> None:
    class SemanticClient:
        available = True
        last_usage: dict[str, Any] = {}
        calls = 0

        async def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
            self.calls += 1
            raise AssertionError("post-store retrieval must not call the sales semantic model")

    semantic = SemanticClient()
    service = V3SemanticRouterService(
        semantic_client=semantic,  # type: ignore[arg-type]
        knowledge_client=None,
    )
    output = asyncio.run(
        service.complete_after_store(
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
                "closing_catalog_match": {
                    "status": "matched",
                    "selected_rule_ids": ["external:rule:102"],
                    "sequence_candidate_ids": ["external:sequence:201"],
                    "evidence_refs": ["current_message"],
                },
            },
            store_resolution_fact={"status": "resolved", "stores": [{"store_id": "s1"}]},
            sequence_result={"status": "ok", "items": [], "total": 0},
            taxonomy_result={"status": "ok", "types": []},
            closing_catalog_result=_catalog(),
        )
    )

    assert semantic.calls == 0
    assert output["semantic_route"]["closing_catalog_match"]["status"] == "matched"
    assert output["semantic_route"]["closing_catalog_evidence"]["candidate_sequences"][0][
        "sequence_key"
    ] == "external:sequence:201"
    assert output["timings"]["checkpoint_router_ms"] == 0


def test_deterministic_top_k_is_stable_and_bounded() -> None:
    route = {
        "current_intent": {"summary": "客户觉得价格高，想先了解低压方案"},
        "current_friction": {"status": "explicit", "summary": "价格犹豫"},
        "checkpoint": {
            "primary_code": "price",
            "primary_type_id": 8,
            "primary_tag_id": 2,
        },
    }
    sequences = [
        {
            "id": f"sequence-{index}",
            "checkpoint_code": "price",
            "checkpoint_name": "价格",
            "sequence_name": f"价格方案 {index}",
            "description": "低压解释价格和价值",
            "steps": [
                {
                    "id": f"step-{index}-1",
                    "sort_order": 1,
                    "action_code": "empathy",
                    "action_name": "低压承接",
                    "trigger_base": "current_message",
                    "relative_value": 0,
                },
                {
                    "id": f"step-{index}-2",
                    "sort_order": 2,
                    "action_code": "value_add",
                    "action_name": "价值说明",
                    "trigger_base": "customer_reply",
                    "relative_value": 0,
                },
            ],
        }
        for index in range(1, 6)
    ]
    shared_context = {
        "current_message": {"content": "价格有点高，先给我低压讲讲价值"},
        "conversation": [],
    }

    first = _apply_deterministic_sequence_top_k(
        route,
        shared_context=shared_context,
        sequences=sequences,
    )
    second = _apply_deterministic_sequence_top_k(
        route,
        shared_context=shared_context,
        sequences=list(reversed(sequences)),
    )

    assert first["sequence_match"] == second["sequence_match"]
    assert len(first["sequence_match"]["sequence_ids"]) == 3
    assert len(first["sequence_match"]["relevant_step_ids"]) == 4
    assert all(
        query["query_source"] == "deterministic_top_k_step"
        for query in first["script_queries"]
    )


def test_sequence_top_k_prefers_steps_with_published_script_coverage() -> None:
    route = {
        "current_intent": {"summary": "客户担心第一次做没有效果"},
        "current_friction": {"status": "explicit", "summary": "效果顾虑"},
        "checkpoint": {
            "primary_code": "effect",
            "primary_type_id": 11,
            "primary_tag_id": 376,
        },
    }
    sequences = [
        {
            "id": "sequence-a",
            "checkpoint_code": "effect",
            "sequence_name": "首次效果解释",
            "description": "回应客户效果顾虑",
            "steps": [
                {
                    "id": "step-a",
                    "action_code": "act013",
                    "action_name": "共情引导",
                    "trigger_base": "current_message",
                    "relative_value": 0,
                }
            ],
        },
        {
            "id": "sequence-b",
            "checkpoint_code": "effect",
            "sequence_name": "首次效果解释",
            "description": "回应客户效果顾虑",
            "steps": [
                {
                    "id": "step-b",
                    "action_code": "act010",
                    "action_name": "项目说明",
                    "trigger_base": "current_message",
                    "relative_value": 0,
                }
            ],
        },
    ]
    taxonomy = [
        {
            "id": 11,
            "action_counts": {"act004": 9, "act010": 5, "act015": 10},
            "tags": [{"id": 376, "action_counts": {"act010": 5}}],
        }
    ]

    result = _apply_deterministic_sequence_top_k(
        route,
        shared_context={"current_message": {"content": "一次能好吗"}},
        sequences=sequences,
        checkpoint_taxonomy=taxonomy,
    )

    assert result["sequence_match"]["sequence_ids"][0] == "sequence-b"
    assert result["sequence_match"]["step_script_coverage"] == {
        "step-b": True,
        "step-a": False,
    }


def test_ordinary_script_pool_keeps_type_hard_and_action_soft() -> None:
    class KnowledgeClient:
        available = True

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def query_all_scripts(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {
                "status": "ok",
                "total": 4,
                "items": [
                    {
                        "script_code": "script-proof",
                        "script_name": "效果信任背书",
                        "checkpoint_type": {"id": 8, "name": "效果"},
                        "checkpoint_tag": {"id": 2, "name": "担心无效果"},
                        "action_code": "act004",
                        "action_name": "信任背书",
                        "paragraphs": [],
                    },
                    {
                        "script_code": "script-explain",
                        "script_name": "项目效果说明",
                        "checkpoint_type": {"id": 8, "name": "效果"},
                        "checkpoint_tag": {"id": 3, "name": "单次效果"},
                        "action_code": "act010",
                        "action_name": "项目说明",
                        "paragraphs": [],
                    },
                    {
                        "script_code": "script-invite",
                        "script_name": "低门槛检测",
                        "checkpoint_type": {"id": 8, "name": "效果"},
                        "checkpoint_tag": {"id": 4, "name": "首次体验"},
                        "action_code": "act015",
                        "action_name": "低门槛邀请",
                        "paragraphs": [],
                    },
                    {
                        "script_code": "wrong-type",
                        "script_name": "距离说明",
                        "checkpoint_type": {"id": 9, "name": "距离"},
                        "checkpoint_tag": {"id": 2, "name": "担心太远"},
                        "action_code": "act007",
                        "action_name": "到店指引",
                        "paragraphs": [],
                    },
                ],
            }

    knowledge = KnowledgeClient()
    service = V3SemanticRouterService(
        semantic_client=None,  # type: ignore[arg-type]
        knowledge_client=knowledge,  # type: ignore[arg-type]
    )
    result = asyncio.run(
        service._script_candidates(
            {
                "script_queries": [
                    {
                        "checkpoint_type_id": 8,
                        "checkpoint_tag_id": 2,
                        "checkpoint_code": "effect",
                        "action_code": "act013",
                        "sequence_id": "sequence-53",
                        "step_id": "step-299",
                        "query_source": "deterministic_top_k_step",
                    }
                ]
            }
        )
    )

    assert len(knowledge.calls) == 1
    assert knowledge.calls[0] == {
        "checkpoint_type_id": 8,
        "checkpoint_tag_id": None,
        "checkpoint_code": "",
        "action_code": "",
    }
    assert result["query_results"][0]["adaptive_pool_used"] is True
    assert result["query_results"][0]["rejected_type_mismatch_count"] == 1
    assert {item["script_code"] for item in result["items"]} == {
        "script-proof",
        "script-explain",
        "script-invite",
    }
    proof = next(item for item in result["items"] if item["script_code"] == "script-proof")
    assert proof["retrieval_match_scope"] == "checkpoint_type_tag"
    assert proof["sequence_links"] == []
    assert proof["sequence_script_alignment"] == "independent"


def test_checkpoint_type_pool_is_used_even_without_sequence_action_query() -> None:
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
                        "script_code": "effect-proof",
                        "script_name": "首次效果证明",
                        "checkpoint_type": {"id": 11, "name": "效果"},
                        "checkpoint_tag": {"id": 376, "name": "担心无效果"},
                        "action_code": "act004",
                        "action_name": "信任背书",
                        "body_text": "不少客户第一次体验后的反馈都挺好。",
                        "paragraphs": [],
                    }
                ],
            }

    knowledge = KnowledgeClient()
    service = V3SemanticRouterService(
        semantic_client=None,  # type: ignore[arg-type]
        knowledge_client=knowledge,  # type: ignore[arg-type]
    )
    result = asyncio.run(
        service._script_candidates(
            {
                "current_friction": {"status": "explicit"},
                "checkpoint": {
                    "primary_type_id": 11,
                    "primary_tag_id": 0,
                    "primary_code": "effect",
                },
                "script_queries": [],
            }
        )
    )

    assert len(knowledge.calls) == 1
    assert result["status"] == "ok"
    assert result["items"][0]["retrieval_match_scope"] == "checkpoint_type_semantic"


def test_script_pool_ranking_is_diverse_and_bounded() -> None:
    candidates = []
    for index, (action_code, tag_id) in enumerate(
        [
            ("act004", 1),
            ("act004", 2),
            ("act004", 3),
            ("act010", 4),
            ("act010", 5),
            ("act015", 6),
        ],
        start=1,
    ):
        candidates.append(
            {
                "script_code": f"script-{index}",
                "script_name": "单次效果说明",
                "body_text": f"一次活动效果与皮肤情况有关，可以先做检测了解。参考{index}",
                "checkpoint_type": {"id": 11, "name": "效果"},
                "checkpoint_tag": {"id": tag_id, "name": f"效果场景{tag_id}"},
                "action_code": action_code,
                "action_name": action_code,
                "retrieval_match_scope": "checkpoint_type_semantic",
                "paragraphs": [],
            }
        )

    selected = _rank_script_groups(
        candidates,
        query_text="一次能好吗，想先了解效果",
        max_groups=6,
    )

    assert len(selected) == 6
    assert sum(item["action_code"] == "act004" for item in selected) == 3
    assert {item["action_code"] for item in selected} == {"act004", "act010", "act015"}


def test_script_pool_keeps_three_strong_same_tag_variants_within_top_six() -> None:
    candidates = [
        {
            "script_code": f"same-tag-{index}",
            "script_name": name,
            "body_text": body,
            "checkpoint_type": {"id": 3, "name": "健康限制"},
            "checkpoint_tag": {"id": 293, "name": "高血压，没降下来，不敢来门店做"},
            "action_code": "act004",
            "action_name": "信任背书",
            "retrieval_match_scope": "checkpoint_type_semantic",
            "paragraphs": [],
        }
        for index, (name, body) in enumerate(
            [
                ("先关心身体", "先把血压稳定下来，身体安全更重要。"),
                ("说明暂缓原因", "血压没有控制好时先不操作。"),
                ("恢复后再评估", "等血压稳定后再由老师评估是否适合。"),
            ],
            start=1,
        )
    ]
    candidates.extend(
        {
            "script_code": f"other-{index}",
            "script_name": "其他健康限制",
            "body_text": f"其他场景参考{index}",
            "checkpoint_type": {"id": 3, "name": "健康限制"},
            "checkpoint_tag": {"id": 290 + index, "name": f"其他场景{index}"},
            "action_code": "act007",
            "action_name": "到店指引",
            "retrieval_match_scope": "checkpoint_type_semantic",
            "paragraphs": [],
        }
        for index in range(1, 5)
    )

    selected = _rank_script_groups(
        candidates,
        query_text="高血压还没降下来，不敢去门店",
        max_groups=6,
    )

    selected_ids = {item["script_code"] for item in selected}
    assert {"same-tag-1", "same-tag-2", "same-tag-3"} <= selected_ids
    assert len(selected) == 6


def test_closing_script_type_never_relaxes() -> None:
    class KnowledgeClient:
        available = True

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def query_all_scripts(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"status": "ok", "total": 0, "items": []}

    knowledge = KnowledgeClient()
    service = V3SemanticRouterService(
        semantic_client=None,  # type: ignore[arg-type]
        knowledge_client=knowledge,  # type: ignore[arg-type]
    )
    result = asyncio.run(
        service._script_candidates(
            {
                "script_queries": [
                    {
                        "checkpoint_type_id": 14,
                        "checkpoint_tag_id": 0,
                        "checkpoint_code": "",
                        "action_code": "",
                        "query_source": "closing_catalog_node",
                    }
                ]
            }
        )
    )

    assert len(knowledge.calls) == 1
    assert result["status"] == "empty"
    assert result["query_results"][0]["fallback_used"] is False
