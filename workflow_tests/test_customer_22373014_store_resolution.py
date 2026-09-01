from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.graph.nodes.action_nodes import _customer_store_lookup, _geocode_explicit_region_conflict
from app.services.store_resolution import build_location_evidence


class _GeocodeClient:
    def __init__(self, results: dict[str, dict[str, object]]) -> None:
        self.settings = SimpleNamespace(geocode_workflow_id="geo", distance_workflow_id="route")
        self.results = results

    async def run_workflow(self, workflow_id: str, parameters: dict[str, object]) -> dict[str, object]:
        del workflow_id
        return {"data": self.results.get(str(parameters.get("address") or ""), {})}


def _chuxiong_store() -> dict[str, object]:
    return {
        "store_id": "398",
        "store_name": "楚雄鹿城店",
        "province": "云南省",
        "city": "楚雄市",
        "district": "高新区",
        "store_address": "云南省楚雄市高新区永安路1号",
        "location": "101.555300,25.049510",
        "store_fact_integrity": "valid",
    }


@pytest.mark.parametrize(
    ("province", "store_city", "geocode_city", "geocode_district", "query"),
    [
        ("云南省", "楚雄市", "楚雄彝族自治州", "楚雄市", "楚雄市阳光水城"),
        ("云南省", "大理市", "大理白族自治州", "大理市", "大理市下关街道"),
        ("湖北省", "恩施市", "恩施土家族苗族自治州", "恩施市", "恩施市金桂大道"),
    ],
)
def test_autonomous_prefecture_and_county_city_are_compatible_location_facts(
    province: str,
    store_city: str,
    geocode_city: str,
    geocode_district: str,
    query: str,
) -> None:
    stores = [
        {
            "store_id": "test-store",
            "store_name": "测试门店",
            "province": province,
            "city": store_city,
            "district": "中心区",
            "store_address": f"{province}{store_city}中心区测试路1号",
        }
    ]
    geocode = {
        "province": province,
        "city": geocode_city,
        "district": geocode_district,
        "location": "101.511918,25.059475",
    }

    assert not _geocode_explicit_region_conflict(query, geocode, stores)


def test_autonomous_prefecture_alias_does_not_hide_a_real_region_conflict() -> None:
    geocode = {
        "province": "云南省",
        "city": "红河哈尼族彝族自治州",
        "district": "蒙自市",
        "location": "103.364905,23.396201",
    }

    assert _geocode_explicit_region_conflict("楚雄市阳光水城", geocode, [_chuxiong_store()])


def test_platform_location_card_coordinates_are_authoritative() -> None:
    evidence = build_location_evidence(
        {
            "normalized_content": "定位卡片：彝人古镇(永安路)",
            "location_card": {
                "msgtype": "location",
                "title": "彝人古镇(永安路)",
                "address": "楚雄市永安路549号",
                "coordinates": "101.518013,25.051612854",
            },
        },
        raw_text="楚雄市永安路549号 彝人古镇(永安路)",
        query="楚雄市永安路549号 彝人古镇(永安路)",
        geocode={
            "province": "云南省",
            "city": "楚雄彝族自治州",
            "district": "楚雄市",
            "location": "101.518013,25.051612854",
            "candidate_count": 1,
        },
    )

    assert evidence["confirmation_status"] == "confirmed"
    assert evidence["confirmation_mode"] == "authoritative_location_card"
    assert evidence["confirmation_required_before_match"] is False
    assert evidence["longitude"] == 101.518013
    assert evidence["latitude"] == 25.051612854


def test_chuxiong_confirmed_location_returns_store_instead_of_reasking() -> None:
    query = "楚雄市阳光水城(华竹路)"
    state = {
        "normalized_content": "是",
        "conversation_history": [
            "用户: 门店位置：阳光水城(华竹路)",
            "小贝: 您说的是云南楚雄这边的阳光水城对吧？",
            "用户: 是",
        ],
        "customer_store_knowledge": {"stores": [_chuxiong_store()]},
    }
    client = _GeocodeClient(
        {
            query: {
                "province": "云南省",
                "city": "楚雄彝族自治州",
                "district": "楚雄市",
                "formatted_address": "云南省楚雄彝族自治州楚雄市阳光水城",
                "location": "101.511918,25.059475",
                "candidate_count": 1,
            }
        }
    )

    result = asyncio.run(
        _customer_store_lookup(
            {
                "name": "customer_store_lookup",
                "query": query,
                "purpose": "existence",
                "location_specificity": "confirmed_region",
                "confirmed_by_customer": True,
            },
            state,
            client,  # type: ignore[arg-type]
        )
    )

    assert result["status"] == "ok"
    assert [store["store_id"] for store in result["stores"]] == ["398"]
    assert result["location_evidence"]["confirmation_status"] == "confirmed"
