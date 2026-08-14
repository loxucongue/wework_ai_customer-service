from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.graph.nodes.action_module_outputs import _distance_city_fallback_should_send_multiple
from app.graph.nodes.action_nodes import (
    _distance_calculate,
    _distance_origin_is_broad_lookup_scope,
    _first_geocode_candidate,
    _resolve_customer_store_workflow,
)
from app.prompts.store_destination_resolver import STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
from app.services.model_selection import model_names
from app.services.store_destination_resolver import _normalize_resolution, resolve_active_store_destination


def test_v3_store_address_fixture_contains_122_unique_addresses() -> None:
    fixture = json.loads(
        Path("workflow_tests/fixtures/v3_store_address_matrix_20260814.json").read_text(
            encoding="utf-8"
        )
    )
    addresses = fixture["addresses"]

    assert len(addresses) == 122
    assert len(set(addresses)) == 122


class _DestinationModel:
    available = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat_json(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {
            "request_kind": "nearest",
            "destination_query": "广东省广州市番禺区市桥",
            "destination_precision": "poi",
            "administrative_context": {
                "province": "广东省",
                "city": "广州市",
                "district": "番禺区",
            },
            "destination_subject": "customer",
            "named_store": "",
            "detail_kind": "none",
            "evidence_refs": ["current_message", "conv_001"],
            "superseded_location_refs": ["conv_000"],
            "confidence": "high",
            "needs_clarification": False,
            "reason": "客户先说广州，当前补充番禺市桥并询问最近门店",
        }


class _GeocodeClient:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(geocode_workflow_id="geo")

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]):
        raise AssertionError("cached lookup and store coordinates should avoid geocoding")


def test_destination_resolver_uses_haiku_tier_and_full_timed_history() -> None:
    model = _DestinationModel()
    state = {
        "normalized_content": "番禺市桥这边哪个近",
        "shared_context": {
            "current_message": {
                "message_type": "text",
                "content": "番禺市桥这边哪个近",
            },
            "conversation": [
                {
                    "message_ref": "conv_000",
                    "role": "customer",
                    "timestamp": "2026-08-14T10:00:00+08:00",
                    "content": "我之前在佛山",
                },
                {
                    "message_ref": "conv_001",
                    "role": "customer",
                    "timestamp": "2026-08-14T10:01:00+08:00",
                    "content": "现在在广州",
                },
            ],
            "authoritative_facts": {},
        },
    }

    result = asyncio.run(
        resolve_active_store_destination(
            model_client=model,
            state=state,
            tool={"name": "resolve_customer_store", "purpose": "nearest_store"},
        )
    )

    assert result["resolver_status"] == "ok"
    assert result["destination_query"] == "广东省广州市番禺区市桥"
    assert result["source_query"] == "番禺市桥这边哪个近"
    assert result["administrative_context"]["district"] == "番禺区"
    assert result["superseded_location_refs"] == ["conv_000"]
    assert model.calls[0]["tier"] == "store_destination"
    assert model.calls[0]["max_parallel_candidates"] == 3
    prompt = str(model.calls[0]["messages"])
    assert "我之前在佛山" in prompt
    assert "现在在广州" in prompt
    assert "番禺市桥这边哪个近" in prompt
    assert "这是补充而不是改口" in prompt


def test_named_store_detail_is_a_valid_location_anchor() -> None:
    normalized, violations = _normalize_resolution(
        {
            "request_kind": "store_detail",
            "destination_query": "",
            "destination_precision": "unknown",
            "destination_subject": "customer",
            "named_store": "上海浦东二店",
            "detail_kind": "address",
            "evidence_refs": ["current_message"],
            "superseded_location_refs": [],
            "confidence": "high",
            "needs_clarification": False,
            "reason": "客户明确点名门店并索要具体地址",
        },
        valid_refs={"current_message"},
        customer_refs={"current_message"},
    )

    assert violations == []
    assert normalized["named_store"] == "上海浦东二店"


def test_store_destination_prompt_does_not_treat_colloquial_region_as_district() -> None:
    assert "历史地名、口语城市片区或商圈不等于现行行政区" in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
    assert "不能填入 administrative_context.district" in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT


def test_store_workflow_honors_model_location_clarification_before_geocode() -> None:
    class _Model:
        available = True

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "request_kind": "clarify",
                "destination_query": "武汉汉口",
                "destination_precision": "unknown",
                "administrative_context": {"province": "湖北省", "city": "武汉市"},
                "destination_subject": "customer",
                "named_store": "",
                "detail_kind": "none",
                "evidence_refs": ["current_message", "conv_001"],
                "superseded_location_refs": [],
                "confidence": "high",
                "needs_clarification": True,
                "geocode_before_clarification": False,
                "reason": "汉口是城市片区，仍需现行区县或定位才能准确匹配",
            }

    class _Client:
        settings = SimpleNamespace(geocode_workflow_id="geo")

        async def run_workflow(self, workflow_id, parameters):
            del workflow_id, parameters
            raise AssertionError("clarification must stop before geocode")

    result = asyncio.run(
        _resolve_customer_store_workflow(
            {"name": "resolve_customer_store", "purpose": "nearest"},
            {
                "normalized_content": "汉口",
                "shared_context": {
                    "current_message": {"message_type": "text", "content": "汉口"},
                    "conversation": [
                        {
                            "message_ref": "conv_001",
                            "role": "customer",
                            "content": "武汉有门店吗",
                        }
                    ],
                    "authoritative_facts": {},
                },
            },
            _Client(),
            model_client=_Model(),
        )
    )

    assert result["status"] == "need_location_confirmation"
    assert result["destination_resolution"]["destination_query"] == "武汉汉口"
    assert result["customer_store_lookup"]["missing"] == ["confirmed_location"]


def test_distance_ranks_all_visible_stores_across_district_boundary() -> None:
    state = {
        "normalized_content": "温州龙湾滨海路",
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "101",
                    "store_name": "龙湾店",
                    "province": "浙江省",
                    "city": "温州市",
                    "district": "龙湾区",
                    "store_address": "浙江省温州市龙湾区远端路1号",
                    "location": "120.9000,27.9000",
                    "store_fact_integrity": "valid",
                },
                {
                    "store_id": "202",
                    "store_name": "瓯海邻区店",
                    "province": "浙江省",
                    "city": "温州市",
                    "district": "瓯海区",
                    "store_address": "浙江省温州市瓯海区交界路2号",
                    "location": "120.8010,27.9010",
                    "store_fact_integrity": "valid",
                },
            ]
        },
    }
    lookup = {
        "status": "ok",
        "query": "浙江省温州市龙湾区滨海路",
        "resolved_admin_level": "district",
        "exact_scope_has_store": True,
        "location_evidence": {
            "confirmation_status": "confirmed",
            "longitude": 120.8000,
            "latitude": 27.9000,
            "province": "浙江省",
            "city": "温州市",
            "district": "龙湾区",
        },
        "geocode": {
            "location": "120.8000,27.9000",
            "province": "浙江省",
            "city": "温州市",
            "district": "龙湾区",
        },
    }

    result = asyncio.run(
        _distance_calculate(
            {
                "name": "distance_calculate",
                "origin": "浙江省温州市龙湾区滨海路",
                "candidate_source": "customer_scope_all",
                "origin_precision": "exact_address",
                "ranking_claim_level": "relative_near",
            },
            state,
            _GeocodeClient(),
            {"customer_store_lookup": lookup},
        )
    )

    assert result["candidate_store_count"] == 2
    assert result["ranking_complete"] is True
    assert result["ranking_claim_level"] == "relative_near"
    assert result["ranked_stores"][0]["store_id"] == "202"
    assert {item["district"] for item in result["ranked_stores"]} == {"龙湾区", "瓯海区"}


def test_distance_ranks_complete_visible_scope_beyond_legacy_200_limit() -> None:
    stores = []
    for index in range(237):
        is_nearest = index == 236
        stores.append(
            {
                "store_id": str(index + 1),
                "store_name": f"测试门店{index + 1}",
                "province": "测试省",
                "city": "测试市",
                "district": f"测试区{index + 1}",
                "store_address": f"测试市测试路{index + 1}号",
                "location": "120.0001,30.0001" if is_nearest else f"{121 + index / 1000:.4f},31.0000",
                "store_fact_integrity": "valid",
            }
        )
    state = {
        "normalized_content": "测试地址",
        "customer_store_knowledge": {"stores": stores},
    }
    lookup = {
        "status": "ok",
        "query": "测试地址",
        "location_evidence": {
            "confirmation_status": "confirmed",
            "longitude": 120.0,
            "latitude": 30.0,
        },
    }

    result = asyncio.run(
        _distance_calculate(
            {
                "name": "distance_calculate",
                "origin": "测试地址",
                "candidate_source": "customer_scope_all",
                "origin_precision": "exact_address",
                "ranking_claim_level": "relative_near",
            },
            state,
            _GeocodeClient(),
            {"customer_store_lookup": lookup},
        )
    )

    assert result["candidate_source_count"] == 237
    assert result["candidate_store_count"] == 237
    assert result["ranked_candidate_count"] == 237
    assert result["candidate_scope_truncated"] is False
    assert result["ranking_complete"] is True
    assert result["ranked_stores"][0]["store_id"] == "237"


def test_geocode_candidate_prefers_structured_admin_match() -> None:
    result = _first_geocode_candidate(
        [
            {
                "formatted_address": "江苏省苏州市工业园区测试路",
                "province": "江苏省",
                "city": "苏州市",
                "district": "工业园区",
                "location": "120.7000,31.3000",
            },
            {
                "formatted_address": "湖北省武汉市硚口区古田四路",
                "province": "湖北省",
                "city": "武汉市",
                "district": "硚口区",
                "location": "114.2000,30.6000",
            },
        ],
        expected_admin={"province": "湖北省", "city": "武汉市", "district": "硚口区"},
    )

    assert result["city"] == "武汉市"
    assert result["district"] == "硚口区"
    assert result["expected_admin_conflict"] is False


def test_geocode_candidate_rejects_cross_city_result() -> None:
    result = _first_geocode_candidate(
        [
            {
                "formatted_address": "江苏省苏州市工业园区测试路",
                "province": "江苏省",
                "city": "苏州市",
                "district": "工业园区",
                "location": "120.7000,31.3000",
            }
        ],
        expected_admin={"province": "湖北省", "city": "武汉市", "district": "硚口区"},
    )

    assert result["expected_admin_conflict"] is True
    assert "location" not in result


def test_geocode_candidate_accepts_autonomous_region_alias() -> None:
    result = _first_geocode_candidate(
        [
            {
                "formatted_address": "广西壮族自治区南宁市青秀区东葛路118号",
                "province": "广西壮族自治区",
                "city": "南宁市",
                "district": "青秀区",
                "location": "108.3800,22.8300",
            }
        ],
        expected_admin={"province": "广西", "city": "南宁市", "district": "青秀区"},
    )

    assert result["province"] == "广西壮族自治区"
    assert result["expected_admin_conflict"] is False
    assert result["location"] == "108.3800,22.8300"


def test_store_workflow_does_not_rank_cross_city_geocode_result() -> None:
    class _Model:
        available = True

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "request_kind": "nearest",
                "destination_query": "湖北省武汉市硚口区古田四路",
                "destination_precision": "exact_address",
                "administrative_context": {
                    "province": "湖北省",
                    "city": "武汉市",
                    "district": "硚口区",
                },
                "destination_subject": "customer",
                "named_store": "",
                "detail_kind": "none",
                "evidence_refs": ["current_message"],
                "superseded_location_refs": [],
                "confidence": "high",
                "needs_clarification": False,
                "reason": "客户明确给出武汉硚口区地址",
            }

    class _Client:
        settings = SimpleNamespace(geocode_workflow_id="geo")

        async def run_workflow(self, workflow_id, parameters):
            del workflow_id, parameters
            return {
                "data": [
                    {
                        "formatted_address": "江苏省苏州市工业园区测试路",
                        "province": "江苏省",
                        "city": "苏州市",
                        "district": "工业园区",
                        "location": "120.7000,31.3000",
                    }
                ]
            }

    result = asyncio.run(
        _resolve_customer_store_workflow(
            {"name": "resolve_customer_store", "purpose": "nearest"},
            {
                "normalized_content": "武汉硚口区古田四路附近哪家近",
                "shared_context": {
                    "current_message": {
                        "message_type": "text",
                        "content": "武汉硚口区古田四路附近哪家近",
                    },
                    "conversation": [],
                    "authoritative_facts": {},
                },
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "9",
                            "store_name": "苏州测试店",
                            "province": "江苏省",
                            "city": "苏州市",
                            "district": "工业园区",
                            "store_address": "江苏省苏州市工业园区测试路",
                            "location": "120.7000,31.3000",
                            "store_fact_integrity": "valid",
                        }
                    ]
                },
            },
            _Client(),
            model_client=_Model(),
        )
    )

    assert result["status"] == "geocode_query_conflict"
    assert result["customer_store_lookup"]["candidate_stores"] == []
    assert "distance_calculate" not in result


def test_exact_poi_origin_is_not_downgraded_to_broad_city_scope() -> None:
    lookup = {
        "customer_store_lookup": {
            "status": "ok",
            "resolved_admin_level": "city",
            "geocode": {
                "province": "陕西省",
                "city": "西安市",
                "district": "",
                "location": "108.969,34.218",
            },
            "location_evidence": {
                "province": "陕西省",
                "city": "西安市",
                "district": "",
                "confirmation_mode": "informational_echo",
            },
        }
    }

    assert _distance_origin_is_broad_lookup_scope(
        lookup,
        candidate_count=5,
        origin_precision="poi",
    ) is False
    assert _distance_origin_is_broad_lookup_scope(
        lookup,
        candidate_count=5,
        origin_precision="city",
    ) is True


def test_exact_poi_distance_result_is_not_expanded_to_city_fallback_cards() -> None:
    assert _distance_city_fallback_should_send_multiple(
        has_real_ranking=True,
        scope_match_level="city_fallback",
        exact_scope_has_store=False,
        candidate_store_ids=["1", "2", "3"],
        origin_precision="poi",
    ) is False
    assert _distance_city_fallback_should_send_multiple(
        has_real_ranking=True,
        scope_match_level="city_fallback",
        exact_scope_has_store=False,
        candidate_store_ids=["1", "2", "3"],
        origin_precision="city",
    ) is True


def test_relay_model_tiers_append_gpt_emergency_fallbacks() -> None:
    settings = Settings(
        _env_file=None,
        model_provider="relay",
        model_store_destination="claude-haiku-4-5-20251001",
        model_store_destination_fallbacks="",
        model_fast="custom-fast",
        model_fast_fallbacks="",
        model_vision="custom-vision",
        model_vision_fallbacks="",
        model_emergency_fallbacks="gpt-5.4,gpt-5.4-mini",
    )

    assert model_names(settings, "store_destination") == [
        "claude-haiku-4-5-20251001",
        "gpt-5.4",
        "gpt-5.4-mini",
    ]
    assert model_names(settings, "fast") == ["custom-fast", "gpt-5.4", "gpt-5.4-mini"]
    assert model_names(settings, "vision") == ["custom-vision", "gpt-5.4", "gpt-5.4-mini"]


def test_composite_store_workflow_uses_destination_then_ranks_visible_scope() -> None:
    class _Model:
        available = True

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "request_kind": "nearest",
                "destination_query": "浙江省温州市龙湾区滨海路",
                "destination_precision": "exact_address",
                "destination_subject": "customer",
                "named_store": "",
                "detail_kind": "none",
                "evidence_refs": ["current_message"],
                "superseded_location_refs": [],
                "confidence": "high",
                "needs_clarification": False,
                "reason": "当前消息给出明确道路",
            }

    class _Client:
        settings = SimpleNamespace(geocode_workflow_id="geo")

        async def run_workflow(self, workflow_id, parameters):
            del workflow_id
            address = str(parameters.get("address") or "")
            if address == "浙江省温州市龙湾区滨海路":
                return {
                    "data": {
                        "formatted_address": address,
                        "province": "浙江省",
                        "city": "温州市",
                        "district": "龙湾区",
                        "location": "120.8000,27.9000",
                    }
                }
            raise AssertionError(f"unexpected geocode: {address}")

    state = {
        "content": "滨海路这边哪个店近",
        "normalized_content": "滨海路这边哪个店近",
        "conversation_history": ["用户: 我在温州龙湾", "用户: 滨海路这边哪个店近"],
        "shared_context": {
            "current_message": {"message_type": "text", "content": "滨海路这边哪个店近"},
            "conversation": [],
            "authoritative_facts": {},
        },
        "customer_store_knowledge": {
            "stores": [
                {
                    "store_id": "101",
                    "store_name": "龙湾远端店",
                    "province": "浙江省",
                    "city": "温州市",
                    "district": "龙湾区",
                    "store_address": "浙江省温州市龙湾区远端路1号",
                    "location": "120.9000,27.9000",
                    "store_fact_integrity": "valid",
                },
                {
                    "store_id": "202",
                    "store_name": "瓯海交界店",
                    "province": "浙江省",
                    "city": "温州市",
                    "district": "瓯海区",
                    "store_address": "浙江省温州市瓯海区交界路2号",
                    "location": "120.8010,27.9010",
                    "store_fact_integrity": "valid",
                },
            ]
        },
    }

    result = asyncio.run(
        _resolve_customer_store_workflow(
            {"name": "resolve_customer_store", "purpose": "nearest"},
            state,
            _Client(),
            model_client=_Model(),
        )
    )

    assert result["destination_resolution"]["destination_query"] == "浙江省温州市龙湾区滨海路"
    assert result["customer_store_lookup"]["status"] == "ok"
    assert result["distance_calculate"]["ranking_complete"] is True
    assert result["distance_calculate"]["ranked_stores"][0]["store_id"] == "202"


def test_composite_store_workflow_geocodes_query_before_asking_for_clarification() -> None:
    class _Model:
        available = True

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "request_kind": "clarify",
                "destination_query": "东坑",
                "destination_precision": "unknown",
                "destination_subject": "customer",
                "named_store": "",
                "detail_kind": "none",
                "evidence_refs": ["current_message"],
                "superseded_location_refs": [],
                "confidence": "medium",
                "needs_clarification": True,
                "reason": "缺少上级行政区，但仍可先查询原始地点",
            }

    class _Client:
        settings = SimpleNamespace(geocode_workflow_id="geo")

        def __init__(self) -> None:
            self.queries: list[str] = []

        async def run_workflow(self, workflow_id, parameters):
            del workflow_id
            self.queries.append(str(parameters.get("address") or ""))
            return {
                "data": {
                    "formatted_address": "广东省东莞市东坑镇",
                    "province": "广东省",
                    "city": "东莞市",
                    "district": "东坑镇",
                    "location": "113.939,22.993",
                }
            }

    client = _Client()
    result = asyncio.run(
        _resolve_customer_store_workflow(
            {"name": "resolve_customer_store", "purpose": "match_location"},
            {
                "normalized_content": "东坑有吗",
                "shared_context": {
                    "current_message": {"message_type": "text", "content": "东坑有吗"},
                    "conversation": [],
                    "authoritative_facts": {},
                },
                "customer_store_knowledge": {"stores": []},
            },
            client,
            model_client=_Model(),
        )
    )

    assert set(client.queries) == {"东坑", "东坑有吗"}
    assert result["customer_store_lookup"]["destination_resolution"]["needs_clarification"] is True


def test_composite_store_workflow_preserves_raw_query_alongside_model_normalization() -> None:
    class _Model:
        available = True

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "request_kind": "match_location",
                "destination_query": "北京市海淀区五道口",
                "destination_precision": "district",
                "destination_subject": "customer",
                "named_store": "",
                "detail_kind": "none",
                "evidence_refs": ["current_message"],
                "superseded_location_refs": [],
                "confidence": "high",
                "needs_clarification": False,
                "reason": "保留客户原文，同时给出纠错候选",
            }

    class _Client:
        settings = SimpleNamespace(geocode_workflow_id="geo")

        def __init__(self) -> None:
            self.queries: list[str] = []

        async def run_workflow(self, workflow_id, parameters):
            del workflow_id
            query = str(parameters.get("address") or "")
            self.queries.append(query)
            return {
                "data": {
                    "formatted_address": "北京市海淀区五道口",
                    "province": "北京市",
                    "city": "北京市",
                    "district": "海淀区",
                    "location": "116.337,39.992",
                }
            }

    client = _Client()
    result = asyncio.run(
        _resolve_customer_store_workflow(
            {"name": "resolve_customer_store", "purpose": "match_location"},
            {
                "normalized_content": "被京市海淀区五道口",
                "shared_context": {
                    "current_message": {"message_type": "text", "content": "被京市海淀区五道口"},
                    "conversation": [],
                    "authoritative_facts": {},
                },
                "customer_store_knowledge": {"stores": []},
            },
            client,
            model_client=_Model(),
        )
    )

    assert set(client.queries) == {"被京市海淀区五道口", "北京市海淀区五道口"}
    assert result["destination_resolution"]["source_query"] == "被京市海淀区五道口"
