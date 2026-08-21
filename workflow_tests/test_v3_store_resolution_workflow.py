from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.graph.nodes.action_module_outputs import (
    _distance_city_fallback_should_send_multiple,
    _store_resolution_status,
)
from app.graph.nodes.action_nodes import (
    _customer_store_lookup,
    _distance_calculate,
    _distance_origin_is_broad_lookup_scope,
    _explicit_parent_admin_from_store_scope,
    _first_geocode_candidate,
    _geocode_explicit_region_conflict,
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


def test_v3_resolver_admin_fact_recovers_visible_city_stores_without_geocode() -> None:
    stores = [
        {
            "store_id": str(index),
            "store_name": f"长沙门店{index}",
            "province": "湖南省",
            "city": "长沙市",
            "district": district,
            "store_address": f"湖南省长沙市{district}测试路{index}号",
            "location": f"112.{index:03d},28.{index:03d}",
            "store_fact_integrity": "valid",
        }
        for index, district in enumerate(("岳麓区", "雨花区", "望城区", "长沙县"), start=1)
    ]
    state = {"customer_store_knowledge": {"stores": stores}}
    client = SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id=""))

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": "长沙",
                "expected_admin": {"province": "湖南省", "city": "长沙市"},
                "use_resolver_admin_fallback": True,
                "allow_broad_scope_delivery": True,
            },
            state,
            client,
        )
    )

    assert result["status"] == "ok"
    assert result["source"] == "customer_scope_resolver_admin"
    assert result["candidate_store_count"] == 4
    assert result["resolved_admin_level"] == "city"
    assert result["allow_broad_scope_delivery"] is True
    assert (
        _store_resolution_status(
            tool_status="ok",
            resolved_level="city",
            visible_candidate_count=4,
            allow_broad_scope_delivery=True,
        )
        == "send_multiple"
    )


def test_v3_resolver_admin_city_ignores_unstated_finer_geocode_scope() -> None:
    stores = [
        {
            "store_id": "1",
            "store_name": "武汉一店",
            "province": "湖北省",
            "city": "武汉市",
            "district": "江汉区",
            "store_address": "湖北省武汉市江汉区测试路1号",
            "location": "114.2710,30.5910",
            "store_fact_integrity": "valid",
        },
        {
            "store_id": "2",
            "store_name": "武汉二店",
            "province": "湖北省",
            "city": "武汉市",
            "district": "江岸区",
            "store_address": "湖北省武汉市江岸区测试路2号",
            "location": "114.2800,30.6000",
            "store_fact_integrity": "valid",
        },
    ]

    class _Client:
        settings = SimpleNamespace(geocode_workflow_id="geo")

        async def run_workflow(self, workflow_id, parameters):
            del workflow_id, parameters
            return {
                "data": {
                    "formatted_address": "湖北省武汉市黄陂区汉口",
                    "province": "湖北省",
                    "city": "武汉市",
                    "district": "黄陂区",
                    "location": "114.2700,30.5900",
                }
            }

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": "汉口",
                "expected_admin": {"province": "湖北省", "city": "武汉市"},
                "destination_precision": "unknown",
                "request_kind": "nearest",
                "use_resolver_admin_fallback": True,
                "allow_broad_scope_delivery": True,
                "confirmed_by_customer": True,
            },
            {"normalized_content": "汉口", "customer_store_knowledge": {"stores": stores}},
            _Client(),
        )
    )

    assert result["status"] == "ok", result
    assert result["source"] == "customer_scope_resolver_admin"
    assert result["resolved_admin_level"] == "city"
    assert [item["store_id"] for item in result["stores"]] == ["1", "2"]


def test_province_without_visible_store_is_final_no_candidate_not_location_question() -> None:
    assert (
        _store_resolution_status(
            tool_status="no_match",
            resolved_level="province",
            visible_candidate_count=0,
            allow_broad_scope_delivery=True,
        )
        == "no_valid_candidate"
    )


def test_province_with_visible_stores_still_requires_city_for_selection() -> None:
    assert (
        _store_resolution_status(
            tool_status="ok",
            resolved_level="province",
            visible_candidate_count=2,
            allow_broad_scope_delivery=True,
        )
        == "need_location"
    )


def test_v3_composite_store_workflow_reads_nested_tool_arguments() -> None:
    class _Model:
        available = True

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "request_kind": "match_location",
                "destination_query": "长沙",
                "destination_precision": "city",
                "administrative_context": {"province": "湖南省", "city": "长沙市"},
                "destination_subject": "customer",
                "named_store": "",
                "detail_kind": "none",
                "evidence_refs": ["current_message"],
                "superseded_location_refs": [],
                "confidence": "high",
                "needs_clarification": False,
                "reason": "客户当前询问长沙门店",
            }

    stores = [
        {
            "store_id": str(index),
            "store_name": f"长沙门店{index}",
            "province": "湖南省",
            "city": "长沙市",
            "district": district,
            "store_address": f"湖南省长沙市{district}测试路{index}号",
            "location": f"112.{index:03d},28.{index:03d}",
            "store_fact_integrity": "valid",
        }
        for index, district in enumerate(("岳麓区", "雨花区", "望城区", "长沙县"), start=1)
    ]
    state = {
        "normalized_content": "长沙有门店吗",
        "shared_context": {
            "current_message": {"message_ref": "current_message", "content": "长沙有门店吗"},
            "conversation": [],
        },
        "customer_store_knowledge": {"stores": stores},
    }
    client = SimpleNamespace(settings=SimpleNamespace(geocode_workflow_id=""))

    result = asyncio.run(
        _resolve_customer_store_workflow(
            {
                "name": "resolve_customer_store",
                "arguments": {
                    "purpose": "查询长沙门店",
                    "destination_hint": "长沙",
                    "use_resolver_admin_fallback": True,
                    "allow_broad_scope_delivery": True,
                },
                "evidence_refs": ["current_message"],
            },
            state,
            client,
            model_client=_Model(),
        )
    )

    lookup = result["customer_store_lookup"]
    assert result["status"] == "ok"
    assert "distance_calculate" not in result
    assert lookup["source"] == "customer_scope_resolver_admin"
    assert lookup["candidate_store_count"] == 4
    assert lookup["allow_broad_scope_delivery"] is True


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
    assert "先设置 geocode_before_clarification=true" in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
    assert 'destination_query="武汉汉口"' in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
    assert 'request_kind="nearest"' in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT


def test_store_workflow_ranks_visible_stores_for_colloquial_city_region() -> None:
    class _Model:
        available = True

        async def chat_json(self, messages, **kwargs):
            del messages, kwargs
            return {
                "request_kind": "nearest",
                "destination_query": "武汉汉口",
                "destination_precision": "unknown",
                "administrative_context": {"province": "湖北省", "city": "武汉市"},
                "destination_subject": "customer",
                "named_store": "",
                "detail_kind": "none",
                "evidence_refs": ["current_message", "conv_001"],
                "superseded_location_refs": [],
                "confidence": "high",
                "needs_clarification": False,
                "geocode_before_clarification": True,
                "reason": "汉口是可查询的城市片区，先排序客户可见门店",
            }

    class _Client:
        settings = SimpleNamespace(geocode_workflow_id="geo")

        async def run_workflow(self, workflow_id, parameters):
            del workflow_id
            query = str(parameters.get("address") or "")
            assert query in {"武汉汉口", "就是汉口"}
            return {
                "data": {
                    "formatted_address": "湖北省武汉市黄陂区汉口",
                    "province": "湖北省",
                    "city": "武汉市",
                    "district": "黄陂区",
                    "location": "114.2700,30.5900",
                }
            }

    result = asyncio.run(
        _resolve_customer_store_workflow(
            {
                "name": "resolve_customer_store",
                "purpose": "nearest",
                "use_resolver_admin_fallback": True,
                "allow_broad_scope_delivery": True,
            },
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
                "customer_store_knowledge": {
                    "stores": [
                        {
                            "store_id": "1",
                            "store_name": "武汉一店",
                            "province": "湖北省",
                            "city": "武汉市",
                            "district": "江汉区",
                            "store_address": "湖北省武汉市江汉区测试路1号",
                            "location": "114.2710,30.5910",
                            "store_fact_integrity": "valid",
                        },
                        {
                            "store_id": "2",
                            "store_name": "武汉二店",
                            "province": "湖北省",
                            "city": "武汉市",
                            "district": "江岸区",
                            "store_address": "湖北省武汉市江岸区测试路2号",
                            "location": "114.2800,30.6000",
                            "store_fact_integrity": "valid",
                        },
                    ]
                },
            },
            _Client(),
            model_client=_Model(),
        )
    )

    assert result["status"] == "ok", result
    assert result["destination_resolution"]["destination_query"] == "武汉汉口"
    assert "distance_calculate" not in result
    assert result["customer_store_lookup"]["source"] == "customer_scope_resolver_admin"
    assert result["customer_store_lookup"]["same_city_has_store"] is True
    assert [item["store_id"] for item in result["customer_store_lookup"]["stores"]] == ["1", "2"]


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


def test_explicit_parent_admin_does_not_infer_district_from_poi_name() -> None:
    stores = [
        {
            "province": "\u5e7f\u4e1c\u7701",
            "city": "\u5e7f\u5dde\u5e02",
            "district": "\u767d\u4e91\u533a",
        },
        {
            "province": "\u5e7f\u4e1c\u7701",
            "city": "\u5e7f\u5dde\u5e02",
            "district": "\u82b1\u90fd\u533a",
        },
    ]

    expected = _explicit_parent_admin_from_store_scope(
        "\u5e7f\u5dde\u767d\u4e91\u56fd\u9645\u673a\u573aT2\u5230\u8fbe\u5385",
        stores,
    )

    assert expected == {"city": "\u5e7f\u5dde\u5e02"}


def test_poi_name_district_token_does_not_conflict_with_authoritative_city() -> None:
    conflict = _geocode_explicit_region_conflict(
        "\u5e7f\u5dde\u767d\u4e91\u56fd\u9645\u673a\u573aT2\u5230\u8fbe\u5385",
        {
            "province": "\u5e7f\u4e1c\u7701",
            "city": "\u5e7f\u5dde\u5e02",
            "district": "\u82b1\u90fd\u533a",
            "location": "113.306585,23.389258",
        },
        [
            {
                "province": "\u5e7f\u4e1c\u7701",
                "city": "\u5e7f\u5dde\u5e02",
                "district": "\u767d\u4e91\u533a",
            }
        ],
        expected_admin={"city": "\u5e7f\u5dde\u5e02"},
    )

    assert conflict is False


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
    ) is False


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
def test_store_destination_prompt_keeps_last_customer_location_for_distance_feedback() -> None:
    assert "距离评价不等于地点变更" in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
    assert "继续沿用最近一次由客户明确给出的地点或定位卡" in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
    assert "不得因此要求客户重复发送同一位置" in STORE_DESTINATION_RESOLVER_SYSTEM_PROMPT
